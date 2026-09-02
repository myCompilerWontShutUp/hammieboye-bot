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
from responses.engine import get_admin_command_response

_KST = timezone(timedelta(hours=9))
_INITIAL_AFFECTION = 10
_ABUSE_RESPONSE = "주인님이 아니시네요!! (콱)"

# 트리거: "{호출 단어} 주인님 가라사대"(세션 열기) 또는 "{호출 단어} 주인님 가라사대
# --{명령어}"(1회성 즉시실행, 세션 미변경). 호출 단어를 뗀 나머지는 정규화 없이 문자
# 그대로 정확히 일치해야 한다 — 안 맞으면 응답도 페널티도 없이 완전히 무시.
# "{호출 단어} 주인님 가라사대 {텍스트}"는 텍스트가 "--"로 시작하지 않는 한 텍스트가
# 등록된 명령어 이름과 같아도 항상 자연어 질문으로 취급한다(권한자 전용).
_PROMPT_PHRASE = "주인님 가라사대"
_PROMPT_PREFIX = "주인님 가라사대 "
_ONESHOT_MARKER = "--"
_SESSION_TIMEOUT = timedelta(seconds=60)
_SESSION_OPEN_MESSAGE = "넵! 명령을 내려주세요!"
_SESSION_TIMEOUT_MESSAGE = "아무 명령이 없어서 놀러가볼게요!!"
_REST_MESSAGE = "넵! 전 놀러가볼게요~!"

# 명령어 이름이 여러 단어일 수 있어(예: "sh db list") ":" 앞부분 전체를 이름 후보로
# 삼는다 — 접두어 일치가 아니라 전체 문자열 일치라서 "sh db"와 "sh db list"가 안 헷갈린다.
_ARG_SEPARATOR = ":"

# 이 채널에서는 권한자(prime/op)가 등록된 명령어 이름을 그대로 치면 접두어/세션 없이 즉시
# 실행된다. 세션/타임아웃 개념 자체가 없다. 트리거 문구는 이 방에서도 기존대로 동작한다.
_COMMAND_ROOM_CHANNEL_ID = 1544276052757708831

_client: discord.Client | None = None

# 권한자(prime + op 부여 유저) id 캐시. should_intercept가 메시지마다 호출되므로 DB
# 왕복 없이 O(1)로 판정해야 한다 — bootstrap()에서 채우고 grant/revoke 시 write-through로
# 갱신한다. prime 여부는 이 캐시와 무관하게 항상 user_id == ADMIN_USER_ID로 고정 판정한다
# (op 관리 권한 자체를 캐시 신선도에 의존시키지 않기 위함).
_authorized_ids: set[int] = set()


def init(client: discord.Client) -> None:
    global _client
    _client = client


async def bootstrap() -> None:
    await seed_prime(ADMIN_USER_ID)
    ops = await list_ops()
    global _authorized_ids
    _authorized_ids = {ADMIN_USER_ID} | {row["user_id"] for row in ops}


def _is_authorized(user_id: int) -> bool:
    return user_id in _authorized_ids


def is_authorized(user_id: int) -> bool:
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


# 유저(권한자) 단위 — 여러 명이 각자 독립적인 60초 쿨타임을 갖는다.
_sessions: dict[int, _Session] = {}


async def _session_timeout(user_id: int) -> None:
    """60초 대기 후 작별 인사를 보내고 세션을 정리한다. 세션이 그사이 연장/종료되면
    이 태스크가 취소되어(_open_or_extend_session/_close_session) 아래 코드는 안 돈다."""
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
    return datetime.now(timezone.utc) < session.expires_at


def _split_command_and_args(content: str) -> tuple[str, list[str]]:
    """":" 앞부분(공백 정규화)을 명령어 이름으로, 뒷부분을 인자 토큰으로 나눈다."""
    name_part, _, arg_part = content.partition(_ARG_SEPARATOR)
    name = " ".join(name_part.split())
    args = arg_part.split()
    return name, args


def _strip_any_call_prefix(content: str) -> str | None:
    for prefix in CALL_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix) :].strip()
    return None


def _match_open_trigger(content: str) -> tuple[str, str, list[str]] | None:
    """"open"(단독) 또는 "oneshot"("--{명령어}", "--" 뒤 공백 없이 바로 붙어야 함)과
    정확히 일치하면 (모드, 이름, 인자)를 반환한다. 어디에도 안 맞으면 None."""
    stripped = _strip_any_call_prefix(content.strip())
    if stripped is None:
        return None
    if stripped == _PROMPT_PHRASE:
        return "open", "", []
    if stripped.startswith(_PROMPT_PREFIX):
        remainder = stripped[len(_PROMPT_PREFIX) :]
        if remainder.startswith(_ONESHOT_MARKER):
            command_part = remainder[len(_ONESHOT_MARKER) :]
            if command_part and not command_part.startswith(" "):
                name, args = _split_command_and_args(command_part)
                if name in _COMMANDS:
                    return "oneshot", name, args
    return None


def _extract_freeform_admin_text(content: str) -> str | None:
    """"{호출 단어} 주인님 가라사대 {텍스트}"이고 텍스트가 "--"로 시작하지 않으면 텍스트
    전체를 자연어 질문으로 반환한다 — 텍스트가 등록된 명령어 이름과 같아도 항상 자연어다."""
    stripped = _strip_any_call_prefix(content.strip())
    if stripped is None or not stripped.startswith(_PROMPT_PREFIX):
        return None
    remainder = stripped[len(_PROMPT_PREFIX) :]
    if not remainder or remainder.startswith(_ONESHOT_MARKER):
        return None
    return remainder


def _is_bare_registered_command(content: str) -> bool:
    name, _args = _split_command_and_args(content.strip())
    return name in _COMMANDS


def should_intercept(message: discord.Message) -> bool:
    """관리자 콘솔이 이 메시지를 가로챌지 판단한다. 다음 중 하나면 True:
    1. 명령어 전용 방에서 권한자가 등록된 명령어를 그대로 침.
    2. 세션 활성 채널에서 권한자가 접두어 없이 등록된 명령어를 그대로 침.
    3. 트리거 패턴("open" 또는 "oneshot")과 정확히 일치.
    4. 권한자가 "{호출 단어} 주인님 가라사대 {텍스트}"(원샷 아님)를 침 — 자연어 응답 대상."""
    is_authorized = _is_authorized(message.author.id)

    if (
        message.channel.id == _COMMAND_ROOM_CHANNEL_ID
        and is_authorized
        and _is_bare_registered_command(message.content)
    ):
        return True

    if is_authorized and _session_active_in(message.author.id, message.channel.id):
        if _is_bare_registered_command(message.content):
            return True

    if _match_open_trigger(message.content) is not None:
        return True
    return is_authorized and _extract_freeform_admin_text(message.content) is not None


def _parse_int(token: str, label: str) -> int:
    try:
        return int(token)
    except ValueError:
        raise _AdminError(f"{label}은(는) 정수여야 해: {token}") from None


# 햄미 자신의 Discord 유저(봇) ID.
_HAMMIE_USER_ID = 1541339665708228648
_SELF_TARGET_MESSAGE = "그건 저라서 진행할 수 없어요!!"

_SELF_ALIAS = "m"  # {user_id}에 "m"을 넣으면 관리자 본인(ADMIN_USER_ID)을 가리킨다.


def _parse_user_id(token: str) -> int:
    if token.strip().lower() == _SELF_ALIAS:
        user_id = ADMIN_USER_ID
    else:
        user_id = _parse_int(token, "user_id")
    if user_id == _HAMMIE_USER_ID:
        raise _AdminError(_SELF_TARGET_MESSAGE)
    return user_id


def _format_event_time(iso_str: str) -> str:
    # 시각이 초 단위까지 무작위로 산출되므로 초까지 표시해야 서로 다른 이벤트가 안 겹쳐 보인다.
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
    # 관리자의 직접 수치 조작으로는 업적이 달성되면 안 된다(la set/reset은 별도 RPC라 원래 안전).
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
    """상한 미만으로 내려가면 over_cap_attempts도 리셋해 다음 초과 시 1회차부터 다시 센다."""
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
    # "*"는 전체 — dump_table에 None을 넘기면 limit을 생략해 PostgREST 기본 최대치까지 반환.
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

# "*"는 전체(기본값) — string을 생략하면(콜론 자체를 안 쓰면) 자동으로 "*"로 취급되어
# `c`와 `c : *`가 동일하게 동작한다. boolean을 같이 넣으려면 "c : * true"처럼 명시해야 한다.
_WILDCARD = "*"


def _filter_commands(keyword: str) -> "list[_CommandSpec]":
    """keyword가 "*"면 전체를, 그 외엔 이름을 공백으로 나눈 단어 중 하나와 완전히
    일치하는 것만 남긴다 (부분 문자열 매칭 아님)."""
    if keyword == _WILDCARD:
        return list(_COMMAND_LIST)
    return [spec for spec in _COMMAND_LIST if keyword in spec.name.split()]


def _resolve_filter_keyword(args: list[str]) -> str:
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


# op grant/revoke의 대상이 햄미 자신이면 _parse_user_id가 이미 막는다 — 여기선 대상이
# prime 자신인 경우만 막는다. prime의 권한은 코드에 고정이라 admin_ops로 다룰 대상이 아니고,
# 특히 자기 자신의 op를 revoke하면 아무도 다시 op를 못 주게 잠긴다.
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


_REST_COMMAND_NAME = "done"


async def _handle_done(args: list[str]) -> str:
    return _REST_MESSAGE


_Handler = Callable[[list[str]], Awaitable[Union[str, tuple[str, discord.Embed]]]]


@dataclass(frozen=True)
class _CommandSpec:
    name: str
    arity: int  # boolean을 제외한 필수 인자 개수
    params: str
    description: str
    handler: _Handler
    requires_prime: bool = False  # True면 최초 주인(ADMIN_USER_ID)만 실행 가능


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
    _CommandSpec(_REST_COMMAND_NAME, 0, "{boolean}", "세션을 즉시 종료", _handle_done),
    _CommandSpec("c", 1, "{string} {boolean}", "이름에 string 단어가 있는 명령어만 매개변수 포함해서 나열 (string 생략 시 전체, \"*\"와 동일)", _handle_c),
    _CommandSpec("c hp", 1, "{string} {boolean}", "이름에 string 단어가 있는 명령어만 설명과 함께 나열 (string 생략 시 전체)", _handle_c_hp),
    _CommandSpec("c np", 1, "{string} {boolean}", "이름에 string 단어가 있는 명령어만 이름으로 나열 (string 생략 시 전체)", _handle_c_np),
)

_COMMANDS = {spec.name: spec for spec in _COMMAND_LIST}


# 관리자 명령어 자연어 설명 기능의 RAG 문서 본문. documents/admin_commands.py는 이 함수를
# 그대로 부르는 얇은 래퍼(admin.console은 documents를 안 쳐다봐서 순환 import 없음).
# {boolean}의 뜻을 문서에 직접 안 넣으면 모델이 몰라서 메타 발언으로 새는 문제가 있었다.
_COMMANDS_DOC_PREAMBLE = (
    "관리자 콘솔에서 쓸 수 있는 명령어 전부 (\"{호출 단어} 주인님 가라사대\"로 세션을 열고 "
    "그 안에서 실행함, 형식은 \"이름 : 파라미터 - 설명\"):\n\n"
    "모든 명령어의 마지막 파라미터인 {boolean}은 공통 옵션이다 — true면 결과를 DM으로 "
    "보내고, false거나 생략하면 지금 이 채널에 그대로 보여준다. true/false만 인정하고 "
    "1/0은 인정하지 않는다.\n\n"
)


def all_commands_text() -> str:
    lines = "\n".join(f"{spec.name} : {spec.params} - {spec.description}" for spec in _COMMAND_LIST)
    return _COMMANDS_DOC_PREAMBLE + lines


_TRUE_TOKENS = ("true",)
_FALSE_TOKENS = ("false",)

_MAX_MESSAGE_LENGTH = 2000
_TOO_LONG_NOTICE = "내용이 너무 길어서 파일로 첨부했어요!!"


def _extract_boolean(tokens: list[str], arity: int) -> tuple[bool, list[str]]:
    """boolean은 true/false로만 받고(대소문자 무관) 생략 가능(기본 False=채널)하다. 토큰이
    정확히 arity+1개이고 마지막이 true/false일 때만 분리한다 (그 외엔 생략으로 간주)."""
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
        # Discord 메시지 하드 제한(2000자)을 넘으면 파일 첨부로 우회한다.
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
        await _send(message, False, "그건 사용하실 수 없어요!!")
        return
    dm, remaining_args = _extract_boolean(tokens, spec.arity)
    try:
        response = await spec.handler(remaining_args)
    except _AdminError as e:
        response = str(e)
    await _send(message, dm, response)


async def _after_dispatch(user_id: int, channel_id: int, name: str) -> None:
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
    """관리자 콘솔 진입점.

    권한자(prime 또는 op)는 "{호출 단어} 주인님 가라사대"로 유저 단위 세션을 연다. 세션이
    열려있으면 접두어 없이 보낸 등록된 명령어도 그대로 실행되며 60초 타이머가 연장된다.
    등록 안 된 메시지가 오면 이 함수 자체가 안 불려서(should_intercept가 안 가로챔) 세션은
    그대로 두고 자연어 파이프라인이 처리한다. 60초 동안 명령이 없으면 능동적으로 작별
    인사를 보내고 세션을 닫는다("done"으로 즉시 닫을 수도 있음).

    "{호출 단어} 주인님 가라사대 {텍스트}"는 "--"로 시작하지 않는 한 항상 자연어 질문으로
    취급해 권한자에게 존댓말+완화된 토큰 예산으로 답한다 — oneshot과 마찬가지로 세션을
    전혀 열지도 연장하지도 않는 완전히 독립적인 1회성 응답이다. 즉시 명령을 실행하려면
    "--{명령어}"(공백 없이)를 쓴다.

    명령어 전용 방에서는 권한자가 등록된 명령어를 그대로 쳐도 세션/트리거 없이 즉시 실행된다.

    관리자 명령어 실행 자체는 LLM 호출 없이 순수 문자열 매칭 + DB 조작이다(자연어 응답
    분기만 예외). 비권한자가 트리거 문구를 정확히 치면 깨물기+호감도 -1로 응징한다 — 단,
    취침 시간대엔 다른 기능과 동일하게 자고 있다는 반응만 보이고 호감도는 건드리지 않는다.
    권한자 본인의 콘솔 접근은 취침 시간대와 무관하게 항상 동작한다.
    """
    is_authorized = _is_authorized(message.author.id)

    if message.channel.id == _COMMAND_ROOM_CHANNEL_ID and is_authorized:
        name, args = _split_command_and_args(message.content.strip())
        spec = _COMMANDS.get(name)
        if spec is not None:
            await _dispatch(message, spec, args)
            return

    if is_authorized and _session_active_in(message.author.id, message.channel.id):
        name, args = _split_command_and_args(message.content.strip())
        spec = _COMMANDS.get(name)
        if spec is not None:
            await _dispatch(message, spec, args)
            await _after_dispatch(message.author.id, message.channel.id, name)
            return
        # 등록된 명령어가 아니면 세션은 그대로 두고 아래 트리거/자연어 판정으로 넘어간다.

    match = _match_open_trigger(message.content)
    if match is None:
        if is_authorized:
            freeform_text = _extract_freeform_admin_text(message.content)
            if freeform_text is not None:
                # oneshot과 동일하게 완전히 독립적인 1회성 응답 — 세션을 열지도 연장하지도
                # 않는다. 열려 있던 세션이 있어도 그대로 두고(건드리지 않음), 타이머와
                # 무관하게 매번 답한다.
                response = await get_admin_command_response(freeform_text, all_commands_text())
                await _send(message, False, response)
        return

    if not is_authorized:
        if is_sleep_time_for(message.channel.id):
            await _send(message, False, SLEEP_REPLY)
            return
        await _penalize_abuse(message)
        return

    mode, name, args = match
    if mode == "open":
        await _open_or_extend_session(message.author.id, message.channel.id)
        await _send(message, False, _SESSION_OPEN_MESSAGE)
        return

    # oneshot: 세션을 열지도 연장하지도 닫지도 않는 완전히 독립적인 1회성 실행.
    spec = _COMMANDS[name]
    await _dispatch(message, spec, args)
