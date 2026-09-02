import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Union

import discord

import achievements
from command.info import handle as info_handle
from config import ADMIN_USER_ID, CALL_PREFIXES
from admin.version import get_commit_hash, get_last_updated_iso
from core.discord_names import resolve_real_name
from events.call_event import MIN_GAP_MINUTES, WINDOW_END, WINDOW_START, schedule_one
from events.scheduler import SLEEP_START, WAKE_TIME, is_sleep_time_for
from events.sleep_guard import SLEEP_REPLY
from db.achievements import award as award_achievement
from db.achievements import revoke as revoke_achievement
from db.admin import (
    KNOWN_TABLES,
    dump_table,
    get_last_event,
    get_next_event,
    get_today_events,
    log_command,
    set_affection,
)
from db.admin_ops import grant as grant_op
from db.admin_ops import has_op
from db.admin_ops import list_all as list_ops
from db.admin_ops import revoke as revoke_op
from db.admin_ops import seed_prime
from db.admin_sessions import clear_session as clear_session_row
from db.admin_sessions import save_session as save_session_row
from db.affection import add_affection, add_affection_uncapped, format_affection_notice
from db.call_events import (
    delete_event,
    delete_unposted_after,
    get_nearest_after,
    get_nearest_before,
)
from db.daily_stats import ensure_nl_cap, update_daily_stats
from db.users import ensure_user, get_user

_KST = timezone(timedelta(hours=9))
_INITIAL_AFFECTION = 10
# 관리자 콘솔에서 햄미가 직접 "말하는" 문구는 (일반 대화의 반말과 달리) 존댓말로 쓴다 —
# 말투 자체(발음 뭉개기, !!/??)는 그대로 유지하고 어미만 존댓말로 바꾼다 (사용자 확정).
_ABUSE_RESPONSE = "주인님이 아니시네요!! (콱)"

# 신규(2026-08-25): 매 메시지 접두어 방식을 폐기하고 "토글로 켜고 끄는 세션" 방식으로 전환.
# 신규(2026-08-27): 트리거는 호출 단어(CALL_PREFIXES) 뒤에 이 문구가 와야 인정된다
# (예: "해미야 주인님 가라사대", "햄미보이야 주인님 가라사대 c"). 호출 단어를 뗀 나머지는
# 정규화 없이 문자 그대로 정확히 일치해야 한다 ("주인님가라사대"처럼 공백이 없거나
# "해미야 주인님 가라사대 테스트"처럼 등록 안 된 명령어가 뒤에 붙으면 둘 다 트리거로 인정 안 됨,
# 호출 단어 자체가 없어도 인정 안 됨 — 이 경우 응답도 페널티도 없이 완전히 무시한다).
_PROMPT_PHRASE = "주인님 가라사대"
_PROMPT_PREFIX = "주인님 가라사대 "
_SESSION_TIMEOUT = timedelta(seconds=60)
_SESSION_OPEN_MESSAGE = "넵! 명령을 내려주세요!"
# 신규(2026-09-01): 60초 동안 명령이 없으면 능동적으로 이 문구를 보내고 세션을 닫는다
# (기존엔 조용히 닫혀서 유저가 세션이 끝난 걸 알 방법이 없었다). "쉬어" 명령어로 직접
# 닫을 때는 이 문구 대신 _REST_MESSAGE를 쓴다.
_SESSION_TIMEOUT_MESSAGE = "아무 명령이 없어서 놀러가볼게요!!"
_REST_MESSAGE = "넵!! 전 놀러가볼게요"

# 신규(2026-08-27): 명령어 이름을 하이픈("la-up" 등)에서 띄어쓰기("la up")로 바꾸면서,
# 이름이 여러 단어가 되어 "첫 토큰 = 명령어"라는 예전 가정이 깨졌다. 그래서 ":" 토큰을
# 기준으로 앞부분 전체를 명령어 이름 후보로 삼는다 — 부분/접두어 일치가 아니라 전체 문자열
# 일치라서 "sh db"와 "sh db list"처럼 한쪽이 다른 쪽의 접두어인 이름들도 서로 안 헷갈린다.
# ":"가 없으면 인자 없이 전체 문자열이 명령어 이름 후보가 된다(=인자가 없는 명령어만 실행됨).
_ARG_SEPARATOR = ":"

# 명령어 전용 방(신규, 2026-09-01): 테스트 서버의 이 채널에서는 권한자(prime/op)라면
# 접두어("{호출 단어} 주인님 가라사대")도 세션도 필요 없이, 등록된 명령어 이름을 그대로
# 치면 즉시 실행된다. 트리거 문구로 오는 메시지는 이 방에서도 기존 로직 그대로 정상
# 동작한다(둘은 서로 다른 분기라 자동으로 공존함). 이 방에는 세션/60초 타임아웃 개념
# 자체가 없다 — 항상 명령을 받을 준비가 돼 있다(사용자 확정).
_COMMAND_ROOM_CHANNEL_ID = 1544276052757708831

_client: discord.Client | None = None

# 권한자(prime + op로 부여된 유저) id의 인메모리 캐시. should_intercept()가 메시지마다
# 무조건 호출되므로 DB 왕복 없이 O(1)로 판정해야 한다 — 부팅 시(bootstrap()) 채우고,
# op grant/revoke 성공 시 즉시(write-through) 갱신한다. prime 여부 자체는 이 캐시와
# 무관하게 항상 `user_id == ADMIN_USER_ID`로 고정 판정한다 — 가장 민감한 권한(op 관리
# 권한 자체)을 캐시 신선도에 의존시키지 않기 위한 의도적 결정. admin_ops.prime 컬럼은
# sh db 등 조회용일 뿐이다.
_authorized_ids: set[int] = set()


def init(client: discord.Client) -> None:
    """sh event all/op list가 실제 이름(멘션 아님)을 조회할 때, 세션 타임아웃 메시지를
    보낼 때 쓴다."""
    global _client
    _client = client


async def bootstrap() -> None:
    """부팅 시 1회 호출: prime 행을 시드하고, 권한자 인메모리 캐시를 DB에서 채운다."""
    await seed_prime(ADMIN_USER_ID)
    ops = await list_ops()
    global _authorized_ids
    _authorized_ids = {ADMIN_USER_ID} | {row["user_id"] for row in ops}


def _is_authorized(user_id: int) -> bool:
    return user_id in _authorized_ids


def is_authorized(user_id: int) -> bool:
    """공개 버전 — core/chat.py의 관리자 명령어 자연어 설명 기능(§6)에서 권한 확인용."""
    return _is_authorized(user_id)


def _is_prime(user_id: int) -> bool:
    return user_id == ADMIN_USER_ID


class _AdminError(Exception):
    pass


@dataclass
class _Session:
    channel_id: int
    expires_at: datetime
    task: "asyncio.Task"


# 세션은 이제 채널이 아니라 유저(주인/권한자) 단위다 — 여러 명이 각자 독립적인 60초
# 쿨타임을 갖는다(사용자 확정, op 권한자가 여러 명일 수 있게 되면서 필요해짐). 관리자는
# 소수라 딕셔너리 하나로 충분하다.
_sessions: dict[int, _Session] = {}


async def _session_timeout(user_id: int) -> None:
    """60초 대기 후 능동적으로 작별 인사를 보내고 세션을 정리한다. 세션이 그사이 연장되거나
    ("쉬어"로) 닫히면 이 태스크 자체가 취소된다(_open_or_extend_session/_close_session의
    task.cancel()) — asyncio.sleep 도중 취소되면 그 아래 코드는 실행되지 않으므로, 별도
    "그사이 바뀌었는지" 확인이 따로 필요 없다."""
    await asyncio.sleep(_SESSION_TIMEOUT.total_seconds())
    session = _sessions.pop(user_id, None)
    if session is None:
        return
    await clear_session_row(user_id)
    if _client is not None:
        channel = _client.get_channel(session.channel_id)
        if channel is not None:
            try:
                await channel.send(_SESSION_TIMEOUT_MESSAGE)
            except discord.HTTPException:
                logging.exception("Failed to send admin session timeout message")


async def _open_or_extend_session(user_id: int, channel_id: int) -> None:
    existing = _sessions.get(user_id)
    if existing is not None:
        existing.task.cancel()
    expires_at = datetime.now(timezone.utc) + _SESSION_TIMEOUT
    task = asyncio.create_task(_session_timeout(user_id))
    _sessions[user_id] = _Session(channel_id=channel_id, expires_at=expires_at, task=task)
    await save_session_row(user_id, channel_id, expires_at)


async def _close_session(user_id: int) -> None:
    session = _sessions.pop(user_id, None)
    if session is not None:
        session.task.cancel()
    await clear_session_row(user_id)


def _session_active_in(user_id: int, channel_id: int) -> bool:
    session = _sessions.get(user_id)
    if session is None or session.channel_id != channel_id:
        return False
    # 능동 타이머가 정상적으로 정리하므로 이 시점엔 거의 항상 True다 — 방어적으로만 재확인.
    return datetime.now(timezone.utc) < session.expires_at


def _split_command_and_args(content: str) -> tuple[str, list[str]]:
    """content를 (명령어 이름, 인자 토큰들)로 나눈다. ":" 앞부분 전체(공백 개수와 무관하게
    단일 공백으로 정규화)를 명령어 이름 후보로, 뒷부분을 인자 토큰으로 삼는다. ":"가 없으면
    인자 없이 전체 문자열이 명령어 이름 후보다."""
    name_part, _, arg_part = content.partition(_ARG_SEPARATOR)
    name = " ".join(name_part.split())
    args = arg_part.split()
    return name, args


def _strip_any_call_prefix(content: str) -> str | None:
    """CALL_PREFIXES(해미야 등) 중 하나로 시작하면 그 뒤 나머지를 반환한다. 등록된 호출
    단어가 여러 개일 수 있어 전부 시도한다. 어느 것과도 안 맞으면 None."""
    for prefix in CALL_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix) :].strip()
    return None


def _match_open_trigger(content: str) -> tuple[bool, str, list[str]] | None:
    """content가 "{호출 단어} 주인님 가라사대"(패턴 1) 또는 "{호출 단어} 주인님 가라사대
    {등록된 명령어} : {인자}"(패턴 2)와 정확히 일치하면 (즉시 실행 여부, 명령어 이름,
    인자들)을 반환한다. 호출 단어가 없거나 그 뒤가 정확히 일치하지 않으면 None (완전히
    무시 대상)."""
    stripped = _strip_any_call_prefix(content.strip())
    if stripped is None:
        return None
    if stripped == _PROMPT_PHRASE:
        return False, "", []
    if stripped.startswith(_PROMPT_PREFIX):
        remainder = stripped[len(_PROMPT_PREFIX) :]
        name, args = _split_command_and_args(remainder)
        if name in _COMMANDS:
            return True, name, args
    return None


def _is_malformed_command_trigger(content: str) -> bool:
    """content가 "{호출 단어} 주인님 가라사대 {무언가}" 형태(패턴 2와 같은 모양)이지만
    그 "무언가"가 등록된 명령어 이름이 아닌 경우 True. 관리자 전용 "잘못된 명령어입니다!!"
    응답 대상 판정에 쓴다 — 비관리자에게는 이 판정 자체를 적용하지 않고 기존처럼 자연어로
    넘어가게 둔다(사용자 확정, 2026-08-28)."""
    stripped = _strip_any_call_prefix(content.strip())
    if stripped is None or not stripped.startswith(_PROMPT_PREFIX):
        return False
    remainder = stripped[len(_PROMPT_PREFIX) :]
    name, _args = _split_command_and_args(remainder)
    return name not in _COMMANDS


def _is_bare_registered_command(content: str) -> bool:
    """접두어/트리거 없이 명령어 이름 그 자체(+ ":" 인자)만 온 경우인지. 명령어 전용 방
    전용 판정 — _split_command_and_args를 그대로 재사용해서 ":" 문법과 완전히 동일하게
    맞물린다."""
    name, _args = _split_command_and_args(content.strip())
    return name in _COMMANDS


def should_intercept(message: discord.Message) -> bool:
    """관리자 세션이 채널에서 활성 상태이거나(접두어 없이 오는 다음 메시지들), 이번 메시지
    자체가 트리거 패턴과 정확히 일치하거나, 명령어 전용 방에서 권한자가 명령어 이름을
    그대로 쳤을 때만 True. 권한자가 "{호출 단어} 주인님 가라사대 {잘못된 명령어}"를
    쳤을 때도(패턴은 맞지만 명령어가 없음) 가로채서 안내해야 하므로 이때도 True —
    비권한자는 이 경우 가로채지 않고 자연어로 넘어가게 둔다. dispatcher가 이 메시지를
    admin.handle로 보낼지 말지 결정하는 게이트. 채널 ID 비교가 항상 가장 먼저라 명령어
    전용 방이 아닌 채널의 메시지엔 이 분기 자체가 비용을 더하지 않는다."""
    is_authorized = _is_authorized(message.author.id)
    if is_authorized and _session_active_in(message.author.id, message.channel.id):
        return True
    if (
        message.channel.id == _COMMAND_ROOM_CHANNEL_ID
        and is_authorized
        and _is_bare_registered_command(message.content)
    ):
        return True
    if _match_open_trigger(message.content) is not None:
        return True
    return is_authorized and _is_malformed_command_trigger(message.content)


def _parse_int(token: str, label: str) -> int:
    try:
        return int(token)
    except ValueError:
        raise _AdminError(f"{label}은(는) 정수여야 해: {token}") from None


# 햄미 자신의 Discord 유저(봇) ID — §0의 초대 링크 client_id와 동일한 값. {user_id}를 받는
# 명령어에서 이 값이 들어오면 정상 처리(등록 조회 등) 대신 전용 메시지로 막는다(사용자 확정,
# 2026-08-27).
_HAMMIE_USER_ID = 1541339665708228648
_SELF_TARGET_MESSAGE = "그건 저라서 진행할 수 없어요!!"

# {user_id}에 "m"을 넣으면 관리자 본인(ADMIN_USER_ID)을 가리킨다 — 관리자가 매번 자기
# 자신의 긴 ID를 직접 칠 필요 없게 하는 단축 표기 (사용자 확정).
_SELF_ALIAS = "m"


def _parse_user_id(token: str) -> int:
    """{user_id} 인자 전용 파싱. "m"이면 관리자 본인 ID로, 그 외엔 정수로 해석한다.
    결과가 햄미 자신의 ID면 전용 메시지로 막는다."""
    if token.strip().lower() == _SELF_ALIAS:
        user_id = ADMIN_USER_ID
    else:
        user_id = _parse_int(token, "user_id")
    if user_id == _HAMMIE_USER_ID:
        raise _AdminError(_SELF_TARGET_MESSAGE)
    return user_id


def _format_event_time(iso_str: str) -> str:
    # 부름 이벤트 시각은 초 단위까지 무작위로 산출되므로(random_times_in_window), 시/분만
    # 보여주면 서로 다른 이벤트가 같은 시각처럼 보일 수 있다 — 초까지 표시한다(사용자 확정).
    dt = datetime.fromisoformat(iso_str).astimezone(_KST)
    return f"{dt.hour}시 {dt.minute}분 {dt.second}초"


async def _require_registered(user_id: int) -> dict:
    user = await get_user(user_id)
    if user is None:
        raise _AdminError(f"아직 등록 안 되신 유저예요!! ({user_id})")
    return user


async def _handle_la_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la up : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    # check_achievements=False: 관리자 콘솔은 실제 서비스 흐름과 독립적이어야 하므로,
    # 직접 수치를 조작하는 이 명령어로는 업적이 달성되면 안 된다(사용자 확정, 2026-08-27
    # 버그 신고 — la up으로 호감도를 올렸는데 업적이 뜸). la set/la reset은 애초에 이
    # 함수를 안 쓰는 별도 RPC라 원래도 안전했다.
    result = await add_affection_uncapped(user_id, amount, "admin_la_up", check_achievements=False)
    new_affection = result["new_affection"]
    await log_command("la up", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    return f"네!! {user_id}님의 호감도를 +{amount} 올려드렸어요!! ({user['affection']} → {new_affection})"


async def _handle_la_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la down : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    result = await add_affection_uncapped(user_id, -amount, "admin_la_down", check_achievements=False)
    new_affection = result["new_affection"]
    await log_command("la down", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    return f"네!! {user_id}님의 호감도를 -{amount} 내렸어요!! ({user['affection']} → {new_affection})"


async def _handle_la_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la set : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    new_affection = await set_affection(user_id, amount)
    await log_command("la set", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    return f"네!! {user_id}님의 호감도를 {amount}로 맞춰드렸어요!! ({user['affection']} → {new_affection})"


async def _handle_la_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: la reset : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    user = await _require_registered(user_id)
    await set_affection(user_id, _INITIAL_AFFECTION)
    await log_command("la reset", str(user_id), str(user["affection"]), str(_INITIAL_AFFECTION))
    return f"네!! {user_id}님의 호감도를 초기값으로 되돌려드렸어요!! ({user['affection']} → {_INITIAL_AFFECTION})"


async def _handle_tc_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: tc up : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = min(max(before + amount, 0), stats["nl_cap"])
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("tc up", f"{user_id} {amount}", str(before), str(new_count))
    return f"네!! {user_id}님의 오늘 대화 횟수를 +{amount} 올려드렸어요!! ({before} → {new_count}/{stats['nl_cap']})"


async def _handle_tc_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: tc down : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = max(before - amount, 0)
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("tc down", f"{user_id} {amount}", str(before), str(new_count))
    return f"네!! {user_id}님의 오늘 대화 횟수를 -{amount} 내려드렸어요!! ({before} → {new_count}/{stats['nl_cap']})"


async def _handle_tc_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: tc set : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = min(max(amount, 0), stats["nl_cap"])
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("tc set", f"{user_id} {amount}", str(before), str(new_count))
    return f"네!! {user_id}님의 오늘 대화 횟수를 {amount}로 맞춰드렸어요!! ({before} → {new_count}/{stats['nl_cap']})"


async def _handle_tc_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: tc reset : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    await _update_nl_count(user_id, 0, stats["nl_cap"])
    await log_command("tc reset", str(user_id), str(before), "0")
    return f"네!! {user_id}님의 오늘 대화 횟수를 0으로 되돌려드렸어요!! ({before} → 0/{stats['nl_cap']})"


async def _update_nl_count(user_id: int, new_count: int, nl_cap: int) -> None:
    """nl_count를 갱신한다. 상한 미만으로 내려가면 over_cap_attempts도 같이 리셋해서,
    다시 상한을 넘길 때 1회차부터 새로 시작하도록 한다."""
    updates = {"nl_count": new_count}
    if new_count < nl_cap:
        updates["over_cap_attempts"] = 0
    await update_daily_stats(user_id, updates)


async def _handle_sh_event_all(args: list[str]) -> str:
    events = await get_today_events()
    if not events:
        return "오늘 등록된 이벤트가 없어요!!"
    lines = []
    for event in events:
        time_label = _format_event_time(event["scheduled_at"])
        claimed_by = event.get("claimed_by")
        if claimed_by is not None:
            name = await resolve_real_name(_client, claimed_by) if _client is not None else str(claimed_by)
            lines.append(f"{time_label} - {name}")
        elif event.get("penalty_applied"):
            lines.append(f"{time_label} - 획득 실패")
        else:
            lines.append(time_label)
    return "\n".join(lines)


async def _handle_sh_event_next(args: list[str]) -> str:
    event = await get_next_event()
    if event is None:
        return "오늘 남은 이벤트가 없어요!!"
    return f"다음 호출 이벤트는 {_format_event_time(event['scheduled_at'])}에 있어요!!"


async def _handle_sh_event_last(args: list[str]) -> str:
    event = await get_last_event()
    if event is None:
        return "아직 지난 이벤트가 없어요!!"
    return f"가장 최근 호출 이벤트는 {_format_event_time(event['scheduled_at'])}에 있었어요!!"


async def _handle_sh_user_stats(args: list[str]) -> tuple[str, discord.Embed]:
    if len(args) != 1:
        raise _AdminError("사용법: sh user stats : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    await _require_registered(user_id)
    return await info_handle(user_id)


async def _handle_sh_db_list(args: list[str]) -> str:
    return "\n".join(KNOWN_TABLES)


async def _handle_sh_db(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: sh db : {name} {amount|*} {boolean}")
    name = args[0]
    # amount에 "*"가 들어가면 전체를 의미한다(신규) — dump_table에 None으로 넘기면
    # limit 파라미터 자체를 생략해서 PostgREST 기본 최대치까지 반환한다.
    amount = None if args[1] == _WILDCARD else _parse_int(args[1], "amount")
    if name not in KNOWN_TABLES:
        raise _AdminError(f"모르는 테이블이에요!! ({name}, 가능: {', '.join(KNOWN_TABLES)})")
    rows = await dump_table(name, amount)
    return "\n".join(str(row) for row in rows) if rows else f"{name}: 데이터 없음"


async def _handle_gn_call_event(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: gn call event : {time} {boolean}")
    minutes = _parse_int(args[0], "time")
    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    min_gap = timedelta(minutes=MIN_GAP_MINUTES)

    before = await get_nearest_before(scheduled_at)
    if before is not None and scheduled_at - datetime.fromisoformat(before["scheduled_at"]) < min_gap:
        raise _AdminError(f"최소 간격({MIN_GAP_MINUTES}분) 때문에 생성할 수 없습니다!!")

    after = await get_nearest_after(scheduled_at)
    if after is not None and datetime.fromisoformat(after["scheduled_at"]) - scheduled_at < min_gap:
        raise _AdminError(f"최소 간격({MIN_GAP_MINUTES}분) 때문에 생성할 수 없습니다!!")

    await schedule_one(scheduled_at)
    time_label = _format_event_time(scheduled_at.isoformat())
    await log_command("gn call event", str(minutes), "없음", time_label)
    return f"네!! {minutes}분 뒤인 {time_label}에 호출 이벤트를 새로 만들었어요!!"


async def _handle_rm_call_event(args: list[str]) -> str:
    event = await get_next_event()
    if event is None:
        return "삭제할 예정된 이벤트가 없어요!!"
    await delete_event(event["id"])
    time_label = _format_event_time(event["scheduled_at"])
    await log_command("rm call event", "", time_label, "삭제됨")
    return f"네!! 가장 가까운 호출 이벤트({time_label})를 삭제했어요!!"


async def _handle_rm_call_event_all(args: list[str]) -> str:
    deleted = await delete_unposted_after(datetime.now(timezone.utc))
    if not deleted:
        return "삭제할 예정된 이벤트가 없어요!!"
    await log_command("rm call event all", "", f"{len(deleted)}개 예정", "전부 삭제됨")
    return f"네!! 아직 시작 안 한 호출 이벤트 {len(deleted)}개를 전부 삭제했어요!!"


async def _handle_sh_version(args: list[str]) -> str:
    commit = get_commit_hash()
    updated_dt = datetime.fromisoformat(get_last_updated_iso()).astimezone(_KST)
    updated_label = updated_dt.strftime("%Y-%m-%d %H:%M")
    return f"지금 버전은 커밋 {commit}이에요!! 마지막 업데이트는 {updated_label}이에요!!"


async def _handle_sh_hammie_runtime(args: list[str]) -> str:
    sleep_label = f"{SLEEP_START.hour:02d}:{SLEEP_START.minute:02d}"
    wake_label = f"{WAKE_TIME.hour:02d}:{WAKE_TIME.minute:02d}"
    call_start_label = f"{WINDOW_START.hour:02d}:{WINDOW_START.minute:02d}"
    call_end_label = f"{WINDOW_END.hour:02d}:{WINDOW_END.minute:02d}"
    return (
        f"햄미는 {wake_label}~{sleep_label}에 활동해요!! 그 시간 외엔 완전히 잠들어서 아무 반응도 안 해요!!\n"
        f"호출 이벤트는 {call_start_label}~{call_end_label} 사이에만 발생할 수 있어요!!"
    )


async def _handle_ac_list(args: list[str]) -> str:
    return "\n".join(achievements.format_name(module) for module in achievements.REGISTRY.values())


async def _handle_ac_list_hp(args: list[str]) -> str:
    return "\n".join(
        f"{achievements.format_name(module)} - {module.HOW_TO_EARN}"
        for module in achievements.REGISTRY.values()
    )


async def _handle_ac_list_cd(args: list[str]) -> str:
    return "\n".join(
        f"{achievements.format_name(module)} - {module.CODE}"
        for module in achievements.REGISTRY.values()
    )


async def _handle_ac_grant(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: ac grant : {user_id} {code} {boolean}")
    user_id = _parse_user_id(args[0])
    code = args[1]
    module = achievements.CODE_REGISTRY.get(code)
    if module is None:
        return f"그런 업적 코드는 없어요!!\n{await _handle_ac_list_cd([])}"
    await _require_registered(user_id)
    granted = await award_achievement(user_id, module.ID)
    if not granted:
        return f"{user_id}님은 이미 '{achievements.format_name(module)}' 업적을 가지고 있어요!!"
    await log_command("ac grant", f"{user_id} {code}", "미보유", module.ID)
    return f"네!! {user_id}님에게 '{achievements.format_name(module)}' 업적을 부여했어요!!"


async def _handle_ac_revoke(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: ac revoke : {user_id} {code} {boolean}")
    user_id = _parse_user_id(args[0])
    code = args[1]
    module = achievements.CODE_REGISTRY.get(code)
    if module is None:
        return f"그런 업적 코드는 없어요!!\n{await _handle_ac_list_cd([])}"
    await _require_registered(user_id)
    revoked = await revoke_achievement(user_id, module.ID)
    if not revoked:
        return f"{user_id}님은 원래 '{achievements.format_name(module)}' 업적이 없었어요!!"
    await log_command("ac revoke", f"{user_id} {code}", module.ID, "미보유")
    return f"네!! {user_id}님의 '{achievements.format_name(module)}' 업적을 제거했어요!!"


_NO_MATCH_MESSAGE = "일치하는 명령어가 없어요!!"

# "*"는 "전체"를 뜻하는 기본값이다 — string 자리를 비우면(즉, 콜론 자체를 안 쓰면) 자동으로
# "*"로 취급되어 `c`와 `c : *`가 동일하게 동작한다. "*"를 명시적으로 써야 하는 목적은 오직
# boolean을 같이 넣기 위해서다 — string은 필수 인자라 생략하면서 boolean만 넣을 방법이
# 없으므로, "c : * true"처럼 자리를 채워야 한다(사용자 확정).
_WILDCARD = "*"


def _filter_commands(keyword: str) -> "list[_CommandSpec]":
    """keyword가 "*"면 전체(기본값)를, 그 외엔 명령어 이름을 공백으로 나눈 단어들 중
    하나와 완전히 일치하는 것만 남긴다 (부분 문자열 포함 아님 — 예: "a"는 "la"/"ac"의
    부분 문자열이라 해당 안 됨, 사용자 확정)."""
    if keyword == _WILDCARD:
        return list(_COMMAND_LIST)
    return [spec for spec in _COMMAND_LIST if keyword in spec.name.split()]


def _resolve_filter_keyword(args: list[str]) -> str:
    """string 인자는 생략 가능하다 — 생략하면(콜론 없이 "c"만 친 경우) "*"(전체)로
    취급한다. 2개 이상 들어오면 사용법 오류로 처리한다."""
    if len(args) > 1:
        raise _AdminError("사용법: c : {string} {boolean}")
    return args[0] if args else _WILDCARD


async def _handle_c(args: list[str]) -> str:
    matched = _filter_commands(_resolve_filter_keyword(args))
    if not matched:
        return _NO_MATCH_MESSAGE
    return "\n".join(f"{spec.name} : {spec.params}" for spec in matched)


async def _handle_c_hp(args: list[str]) -> str:
    matched = _filter_commands(_resolve_filter_keyword(args))
    if not matched:
        return _NO_MATCH_MESSAGE
    return "\n".join(f"{spec.name} : {spec.params} - {spec.description}" for spec in matched)


async def _handle_c_np(args: list[str]) -> str:
    matched = _filter_commands(_resolve_filter_keyword(args))
    if not matched:
        return _NO_MATCH_MESSAGE
    return "\n".join(spec.name for spec in matched)


# op grant/revoke의 대상이 햄미 자신이면 _parse_user_id가 이미 _SELF_TARGET_MESSAGE로
# 막는다 — 여기서는 대상이 최초 주인(ADMIN_USER_ID) 자신인 경우만 별도로 막는다. prime의
# 권한은 코드에 고정돼 있어 admin_ops로 부여/회수할 대상이 아니고, 특히 자기 자신의 op를
# revoke하면 아무도 다시 op를 부여할 수 없게 잠기므로(prime 자기잠금 방지) 반드시 막아야
# 한다.
_ALREADY_PRIME_MESSAGE = "그분은 이미 최초 주인님이세요!!"
_CANNOT_REVOKE_PRIME_MESSAGE = "그분의 권한은 제거할 수 없어요!!"


async def _resolve_name(user_id: int) -> str:
    return await resolve_real_name(_client, user_id) if _client is not None else str(user_id)


async def _handle_op_grant(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: op grant : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    if user_id == ADMIN_USER_ID:
        return _ALREADY_PRIME_MESSAGE
    await _require_registered(user_id)
    granted = await grant_op(user_id)
    name = await _resolve_name(user_id)
    if not granted:
        return f"{name}님은 이미 권한이 있으세요!!"
    _authorized_ids.add(user_id)
    await log_command("op grant", str(user_id), "미보유", "보유")
    return f"넵! {name}님께 권한을 드렸어요!"


async def _handle_op_revoke(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: op revoke : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    if user_id == ADMIN_USER_ID:
        return _CANNOT_REVOKE_PRIME_MESSAGE
    name = await _resolve_name(user_id)
    revoked = await revoke_op(user_id)
    if not revoked:
        return f"{name}님은 원래 권한이 없으셨어요!!"
    _authorized_ids.discard(user_id)
    await log_command("op revoke", str(user_id), "보유", "미보유")
    return f"넵! {name}님의 권한을 제거했어요!!"


async def _handle_op_list(args: list[str]) -> str:
    rows = await list_ops()
    lines = []
    for row in rows:
        name = await _resolve_name(row["user_id"])
        marker = " (최초 주인)" if row["prime"] else ""
        lines.append(f"{name}{marker}")
    return "\n".join(lines) if lines else "권한을 가진 사용자가 없어요!!"


_REST_COMMAND_NAME = "쉬어"


async def _handle_rest(args: list[str]) -> str:
    return _REST_MESSAGE


_Handler = Callable[[list[str]], Awaitable[Union[str, tuple[str, discord.Embed]]]]


@dataclass(frozen=True)
class _CommandSpec:
    name: str
    arity: int  # boolean을 제외한 필수 인자 개수
    params: str
    description: str
    handler: _Handler
    requires_prime: bool = False  # True면 최초 주인(ADMIN_USER_ID)만 실행 가능 (op 명령어 전용)


_COMMAND_LIST = (
    _CommandSpec("la up", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도 +amount (일일 상한 미적용)", _handle_la_up),
    _CommandSpec("la down", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도 -amount", _handle_la_down),
    _CommandSpec("la set", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도를 amount로 절대값 설정", _handle_la_set),
    _CommandSpec("la reset", 1, "{user_id} {boolean}", "해당 유저 호감도를 초기값(10)으로 리셋", _handle_la_reset),
    _CommandSpec("tc up", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수 +amount (0~당일 상한 클램프)", _handle_tc_up),
    _CommandSpec("tc down", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수 -amount (0 미만 방지)", _handle_tc_down),
    _CommandSpec("tc set", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수를 amount로 절대값 설정 (0~당일 상한 클램프)", _handle_tc_set),
    _CommandSpec("tc reset", 1, "{user_id} {boolean}", "해당 유저 오늘 대화 횟수를 0으로 리셋", _handle_tc_reset),
    _CommandSpec("sh event all", 0, "{boolean}", "오늘 부름 이벤트 전부와 결과 표시", _handle_sh_event_all),
    _CommandSpec("sh event next", 0, "{boolean}", "다음으로 남은 부름 이벤트 시각 표시", _handle_sh_event_next),
    _CommandSpec("sh event last", 0, "{boolean}", "가장 최근에 지난 부름 이벤트 시각 표시", _handle_sh_event_last),
    _CommandSpec("sh user stats", 1, "{user_id} {boolean}", "해당 유저의 일반 정보(=/내정보) 표시", _handle_sh_user_stats),
    _CommandSpec("sh db list", 0, "{boolean}", "등록된 테이블 이름 전부 표시", _handle_sh_db_list),
    _CommandSpec("sh db", 2, "{name} {amount|*} {boolean}", "해당 테이블 최근 amount개 행(amount가 *면 전체) 표시", _handle_sh_db),
    _CommandSpec("sh version", 0, "{boolean}", "현재 버전(커밋)과 마지막 업데이트 일시 표시", _handle_sh_version),
    _CommandSpec("sh hammie runtime", 0, "{boolean}", "햄미 활동 시간 및 이벤트 발생 가능 시간 표시", _handle_sh_hammie_runtime),
    _CommandSpec("gn call event", 1, "{time} {boolean}", "time분 뒤에 호출 이벤트 1개를 수동 생성(최소 간격 30분 준수)", _handle_gn_call_event),
    _CommandSpec("rm call event", 0, "{boolean}", "가장 가까운(아직 시작 안 한) 호출 이벤트 삭제", _handle_rm_call_event),
    _CommandSpec("rm call event all", 0, "{boolean}", "아직 시작 안 한 호출 이벤트 전부 삭제", _handle_rm_call_event_all),
    _CommandSpec("ac list", 0, "{boolean}", "업적 이름(희귀도 포함) 목록 표시", _handle_ac_list),
    _CommandSpec("ac list hp", 0, "{boolean}", "업적 이름 + 획득 방법 표시", _handle_ac_list_hp),
    _CommandSpec("ac list cd", 0, "{boolean}", "업적 이름 + 코드 표시", _handle_ac_list_cd),
    _CommandSpec("ac grant", 2, "{user_id} {code} {boolean}", "해당 유저에게 코드로 업적을 부여", _handle_ac_grant),
    _CommandSpec("ac revoke", 2, "{user_id} {code} {boolean}", "해당 유저의 업적을 코드로 제거", _handle_ac_revoke),
    _CommandSpec("op grant", 1, "{user_id} {boolean}", "해당 유저에게 관리자 권한을 부여 (최초 주인 전용)", _handle_op_grant, requires_prime=True),
    _CommandSpec("op revoke", 1, "{user_id} {boolean}", "해당 유저의 관리자 권한을 제거 (최초 주인 전용)", _handle_op_revoke, requires_prime=True),
    _CommandSpec("op list", 0, "{boolean}", "권한을 가진 사용자 전부 표시 (최초 주인 전용)", _handle_op_list, requires_prime=True),
    _CommandSpec("c", 1, "{string} {boolean}", "이름에 string 단어가 있는 명령어만 매개변수 포함해서 나열 (string 생략 시 전체, \"*\"와 동일)", _handle_c),
    _CommandSpec("c hp", 1, "{string} {boolean}", "이름에 string 단어가 있는 명령어만 설명과 함께 나열 (string 생략 시 전체)", _handle_c_hp),
    _CommandSpec("c np", 1, "{string} {boolean}", "이름에 string 단어가 있는 명령어만 이름으로 나열 (string 생략 시 전체)", _handle_c_np),
)

# "쉬어"는 세션을 끄는 조작일 뿐 "명령어" 취급이 아니다(사용자 확정, 2026-09-01) — c/c hp/c np
# 목록과 관리자 명령어 자연어 설명(all_commands_text())에서 둘 다 빠져야 하므로 _COMMAND_LIST에
# 넣지 않는다. 대신 실제 실행(트리거/세션 중 bare command/명령어 전용 방)은 계속 동작해야
# 하므로, 조회용 _COMMAND_LIST와는 별개로 디스패치용 _COMMANDS에만 합류시킨다.
_REST_SPEC = _CommandSpec(_REST_COMMAND_NAME, 0, "{boolean}", "세션을 즉시 종료", _handle_rest)

_COMMANDS = {spec.name: spec for spec in (*_COMMAND_LIST, _REST_SPEC)}


def all_commands_text() -> str:
    """관리자 명령어 전체를 "이름 : 파라미터 - 설명" 형식으로 나열한다. 관리자 명령어
    자연어 설명 기능(documents/admin_commands.py)의 RAG 문서 본문으로 그대로 쓰인다."""
    return "\n".join(f"{spec.name} : {spec.params} - {spec.description}" for spec in _COMMAND_LIST)


_TRUE_TOKENS = ("true",)
_FALSE_TOKENS = ("false",)

# Discord 메시지 하드 제한(2000자)을 넘으면 응답이 그냥 조용히 실패했다 (sh db 버그 발견·
# 수정, 2026-08-27) — 넘으면 메시지 대신 파일 첨부로 보낸다.
_MAX_MESSAGE_LENGTH = 2000
_TOO_LONG_NOTICE = "내용이 너무 길어서 파일로 첨부했어요!!"


def _extract_boolean(tokens: list[str], arity: int) -> tuple[bool, list[str]]:
    """boolean은 true/false로만 받고(대소문자 무관), 생략 가능(기본값 False=채널)하다.
    필수 인자 개수(arity)를 기준으로 판단한다: 토큰이 정확히 arity+1개이고 마지막이
    명시적으로 true/false일 때만 분리한다 (그 외엔 boolean 생략으로 보고 채널 기본값)."""
    if len(tokens) == arity + 1:
        last = tokens[-1].strip().lower()
        if last in _TRUE_TOKENS:
            return True, tokens[:-1]
        if last in _FALSE_TOKENS:
            return False, tokens[:-1]
    return False, tokens


async def _send(
    message: discord.Message, dm: bool, response: str | tuple[str, discord.Embed]
) -> None:
    if isinstance(response, tuple):
        text, embed = response
        kwargs = {"content": text, "embed": embed}
    else:
        kwargs = {"content": response}

    content = kwargs.get("content")
    if isinstance(content, str) and len(content) > _MAX_MESSAGE_LENGTH:
        # sh db처럼 출력이 길어질 수 있는 명령어 전체에 적용되는 일반적인 안전장치 —
        # 메시지로는 안 나가고 조용히 실패하던 걸(2000자 하드 제한) 파일 첨부로 우회한다.
        kwargs["content"] = _TOO_LONG_NOTICE
        kwargs["file"] = discord.File(io.BytesIO(content.encode("utf-8")), filename="result.txt")

    try:
        if dm:
            await message.author.send(**kwargs)
        else:
            await message.reply(**kwargs)
    except discord.HTTPException:
        logging.exception("Failed to send admin console response")


async def _dispatch(message: discord.Message, spec: _CommandSpec, tokens: list[str]) -> None:
    if spec.requires_prime and not _is_prime(message.author.id):
        # op 부여받은 권한자는 다른 모든 명령어는 최초 주인과 동일하게 쓸 수 있지만,
        # op grant/revoke/list 자체는 최초 주인 전용이다(사용자 확정).
        await _send(message, False, "그건 사용하실 수 없어요!!")
        return
    dm, remaining_args = _extract_boolean(tokens, spec.arity)
    try:
        response = await spec.handler(remaining_args)
    except _AdminError as e:
        response = str(e)
    await _send(message, dm, response)


async def _after_dispatch(user_id: int, channel_id: int, name: str) -> None:
    """명령 실행 후 세션 상태를 정리한다 — "쉬어"면 세션을 닫고, 그 외 명령이면 60초
    타이머를 새로 걸거나 연장한다."""
    if name == _REST_COMMAND_NAME:
        await _close_session(user_id)
    else:
        await _open_or_extend_session(user_id, channel_id)


async def _penalize_abuse(message: discord.Message) -> None:
    await ensure_user(message.author.id)
    result = await add_affection(message.author.id, -1)
    notice = format_affection_notice(result["applied_amount"], result["new_affection"])
    await _send(message, False, f"{_ABUSE_RESPONSE}{notice}")


async def handle(message: discord.Message) -> None:
    """관리자 콘솔(§13-F, 2026-09-01 유저별 세션 + 명령어 전용 방 + op 권한 시스템으로 확장)을
    처리한다.

    권한자(최초 주인 또는 op로 권한을 부여받은 유저)는 `{호출 단어} 주인님 가라사대`로
    세션을 **유저 단위**로 연다(여러 명이 각자 독립적인 60초 쿨타임을 가짐). 세션이
    열려있는 동안은 접두어 없이 보낸 메시지도 그대로 명령어로 해석한다 — 등록된 명령어면
    실행하고 60초 타이머를 연장하고, 등록 안 된 말이면 세션/타이머를 그대로 둔 채 조용히
    무시한다(신규 — 예전엔 즉시 세션이 닫혀서, 그다음 유효한 명령까지 응답이 안 갔다).
    60초 동안 유효한 명령이 없으면 능동적으로 작별 인사(_SESSION_TIMEOUT_MESSAGE)를 보내고
    세션을 닫는다(_session_timeout). "쉬어" 명령어로 즉시 세션을 닫을 수도 있다.

    명령어 전용 방(_COMMAND_ROOM_CHANNEL_ID)에서는 권한자라면 세션/트리거 문구 없이
    등록된 명령어 이름을 그대로 쳐도 즉시 실행된다 — 이 방은 세션 개념 자체가 없다.
    트리거 문구로 오는 메시지는 이 방에서도 기존 로직 그대로 정상 동작한다.

    LLM/OpenAI API 호출 없이 순수 문자열 매칭 + DB 조작으로만 처리한다.
    권한자가 아니면(트리거 문구를 정확히 쳤을 때만) 명령을 실행하지 않고 깨물기 +
    호감도 -1로 응징한다 — 단, 취침 시간대(00:00~06:30)엔 이 오용 감지도 다른 모든 기능과
    동일하게 자고 있다는 반응만 보이고 호감도는 건드리지 않는다. 권한자 본인의 콘솔
    접근은 §13-F대로 취침 시간대와 무관하게 항상 그대로 동작한다.
    """
    is_authorized = _is_authorized(message.author.id)

    # 명령어 전용 방: 권한자가 등록된 명령어 이름을 그대로 치면 세션/타이머와 무관하게
    # 즉시 실행한다. 채널 ID 비교가 항상 먼저라 다른 채널의 메시지엔 비용이 없다.
    if message.channel.id == _COMMAND_ROOM_CHANNEL_ID and is_authorized:
        name, args = _split_command_and_args(message.content.strip())
        spec = _COMMANDS.get(name)
        if spec is not None:
            await _dispatch(message, spec, args)
            return

    if is_authorized and _session_active_in(message.author.id, message.channel.id):
        name, args = _split_command_and_args(message.content)
        spec = _COMMANDS.get(name)
        if spec is None:
            return  # 등록 안 된 말 -> 세션/타이머 그대로 두고 조용히 무시 (신규, 버그 수정)
        await _dispatch(message, spec, args)
        await _after_dispatch(message.author.id, message.channel.id, name)
        return

    match = _match_open_trigger(message.content)
    if match is None:
        if is_authorized and _is_malformed_command_trigger(message.content):
            # "{호출 단어} 주인님 가라사대 {등록 안 된 명령어}" — 권한자가 트리거는
            # 정확히 쳤지만 명령어를 잘못 쓴 경우, 자연어로 새지 않고 바로 안내한다
            # (세션은 열지 않는다 — 실행할 명령이 없었으므로).
            await _send(message, False, "잘못된 명령어입니다!!")
        return  # 트리거 패턴과 정확히 일치하지 않음 -> 완전히 무시 (응답도 페널티도 없음)

    if not is_authorized:
        if is_sleep_time_for(message.channel.id):
            # 권한자가 아닌 사람이 취침 중에 트리거를 쳐도 깨물지 않는다 — 다른 모든 기능과
            # 동일한 "자고 있다" 반응만 보이고 호감도는 그대로 둔다.
            await _send(message, False, SLEEP_REPLY)
            return
        await _penalize_abuse(message)
        return

    immediate, name, args = match
    if not immediate:
        await _open_or_extend_session(message.author.id, message.channel.id)
        await _send(message, False, _SESSION_OPEN_MESSAGE)
        return

    spec = _COMMANDS[name]  # _match_open_trigger에서 이미 검증됨
    await _dispatch(message, spec, args)
    await _after_dispatch(message.author.id, message.channel.id, name)
