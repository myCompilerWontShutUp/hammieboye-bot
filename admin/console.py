import asyncio
import contextvars
import io
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Union

import discord
import emoji as emoji_lib

import achievements
from command.info import handle as info_handle
from config import ADMIN_USER_ID, ALLOWED_GUILD_IDS, CALL_PREFIXES
from admin.version import (
    get_last_updated_iso,
    get_previous_commit,
    get_recent_commits,
    get_version_label,
)
from core.discord_names import resolve_real_name
import documents.update_announcement as update_announcement
from events.help_me_event import WINDOW_END, WINDOW_START
from events.scheduler import (
    SLEEP_START,
    WAKE_TIME,
    broadcast_to_guilds,
    format_footer_time,
    is_sleep_time_for,
)
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
    set_coins,
    set_max_coins,
)
from db.admin_history import get_recent_turns as get_recent_admin_turns
from db.admin_history import log as log_admin_turn
from db.admin_ops import grant as grant_op
from db.admin_ops import has_op
from db.admin_ops import list_all as list_ops
from db.admin_ops import revoke as revoke_op
from db.admin_ops import seed_prime
from db.admin_sessions import clear_session as clear_session_row
from db.admin_sessions import save_session as save_session_row
from db.affection import add_affection, add_affection_uncapped, format_affection_notice
from db.daily_stats import ensure_nl_cap, update_daily_stats
from db.emoji_tags import clear_tags as clear_emoji_tags_row
from db.emoji_tags import get_all as get_all_emoji_tags
from db.emoji_tags import set_tags as set_emoji_tags_row
from db.guild_channels import (
    add_sub_channel,
    clear_main_channel,
    get_main_channel,
    get_sub_channel_ids,
    load_channel_caches,
    remove_sub_channel,
    set_main_channel,
)
from db.users import ensure_user, get_user
from db.wallet import add_coins, decrease_max_coins, deduct_coins_clamped, increase_max_coins
from responses.engine import get_admin_command_response
from core.base import EMBED_COLOR

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

# 자연어 응답만 실제로 API 호출이 걸려 지연이 체감된다(명령어 실행은 순수 DB/문자열
# 처리라 필요 없음) — core/dispatcher.py와 동일한 패턴으로 그 호출 구간에만 띄운다.
_THINKING_PLACEHOLDER = "_답변중..._"

# "주인님 가라사대" 자연어 히스토리(core/chat.py의 일반 자연어와 동일한 30분/최대 5턴
# 윈도우를 쓰되, chat_history와는 완전히 별개인 admin_chat_history에 저장돼 섞이지 않는다).
_ADMIN_HISTORY_WINDOW = timedelta(minutes=30)
_ADMIN_CONTEXT_TURN_LIMIT = 5

# 명령어 이름이 여러 단어일 수 있어(예: "sh db list") ":" 앞부분 전체를 이름 후보로
# 삼는다 — 접두어 일치가 아니라 전체 문자열 일치라서 "sh db"와 "sh db list"가 안 헷갈린다.
_ARG_SEPARATOR = ":"

# 이 채널에서는 권한자(prime/op)가 등록된 명령어 이름을 그대로 치면 접두어/세션 없이 즉시
# 실행된다. 세션/타임아웃 개념 자체가 없다. 트리거 문구는 이 방에서도 기존대로 동작한다.
_COMMAND_ROOM_CHANNEL_ID = 1544276052757708831

_EMOJI_NAME_SEPARATOR = ","

_client: discord.Client | None = None

# _dispatch()가 핸들러 호출 범위 동안만 채워두는, 지금 처리 중인 명령어가 어느 서버에서
# 왔는지("서버 별명 우선" 이름 표시용). 전역 변수 대신 ContextVar를 쓰는 이유는 asyncio
# 태스크가 겹쳐 돌아도(다른 서버의 명령어가 거의 동시에 들어와도) 서로 안 섞이기 위함이다.
_current_guild: "contextvars.ContextVar[discord.Guild | None]" = contextvars.ContextVar(
    "_current_guild", default=None
)

# "des main"/"des sub"/"des void"가 "지금 이 채널"을 알아야 하는데, 명령어 핸들러 시그니처가
# (args: list[str]) -> str라 메시지 객체 자체를 안 받는다 — _current_guild와 동일한
# 패턴으로 _dispatch()가 핸들러 호출 범위 동안만 채워준다.
_current_channel: "contextvars.ContextVar[int | None]" = contextvars.ContextVar(
    "_current_channel", default=None
)

# 권한자(prime + op 부여 유저) id 캐시. should_intercept가 메시지마다 호출되므로 DB
# 왕복 없이 O(1)로 판정해야 한다 — bootstrap()에서 채우고 grant/revoke 시 write-through로
# 갱신한다. prime 여부는 이 캐시와 무관하게 항상 user_id == ADMIN_USER_ID로 고정 판정한다
# (op 관리 권한 자체를 캐시 신선도에 의존시키지 않기 위함).
_authorized_ids: set[int] = set()

# user_id -> 순서 있는 이모지 문자 리스트("emj set"으로 등록). on_message마다 조회하므로
# _authorized_ids와 동일하게 DB 왕복 없이 O(1) 캐시로 유지하고, emj set/stop 시 write-through.
_emoji_tags: dict[int, list[str]] = {}


def init(client: discord.Client) -> None:
    global _client
    _client = client


async def bootstrap() -> None:
    await seed_prime(ADMIN_USER_ID)
    ops = await list_ops()
    global _authorized_ids
    _authorized_ids = {ADMIN_USER_ID} | {row["user_id"] for row in ops}
    global _emoji_tags
    _emoji_tags = await get_all_emoji_tags()
    try:
        # main_channel_id 컬럼/guild_sub_channels 테이블이 아직 SQL.md 마이그레이션
        # 적용 전이면 여기서 실패할 수 있다 — 그렇다고 부팅 시퀀스 전체(슬래시 커맨드
        # 동기화/스케줄러 등, bootstrap() 뒤에 이어지는 on_ready() 나머지 부분)를 막으면
        # 안 되므로, 메인/서브 채널 기능만 "설정된 곳 없음"으로 조용히 비활성화하고
        # 계속 진행한다.
        await load_channel_caches()
    except Exception:
        logging.exception("Failed to load main/sub channel caches (migration not applied?)")


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


async def apply_emoji_tags(message: discord.Message) -> None:
    """"emj set"으로 등록된 유저가 말한 메시지엔 무조건 반응을 단다 — 호출 단어/명령어
    처리와 완전히 독립적이라, 다른 어떤 처리보다도 먼저(그리고 그 결과와 무관하게)
    호출돼야 한다. 순서를 보장하려고 순차적으로(gather 아님) 하나씩 반응을 단다."""
    emojis = _emoji_tags.get(message.author.id)
    if not emojis:
        return
    # emj set이 저장 시점에 이미 중복을 제거하지만, 예전에 저장된 데이터 등 만약을 대비해
    # 여기서도 한 번 더 걸러 같은 이모지로 add_reaction을 두 번 호출하지 않게 한다.
    for character in dict.fromkeys(emojis):
        try:
            await message.add_reaction(character)
        except discord.HTTPException:
            logging.exception("Failed to add emoji tag reaction for user %s", message.author.id)


def _parse_int(token: str, label: str) -> int:
    try:
        return int(token)
    except ValueError:
        raise _AdminError(f"{label}은(는) 정수여야 해: {token}") from None


# 햄미 자신의 Discord 유저(봇) ID.
_HAMMIE_USER_ID = 1541339665708228648
_SELF_TARGET_MESSAGE = "그건 저라서 진행할 수 없어요!!"

_SELF_ALIAS = "me"  # {user_id}에 "me"를 넣으면 관리자 본인(ADMIN_USER_ID)을 가리킨다.


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


async def _handle_fl_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: fl up : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    # 관리자의 직접 수치 조작으로는 업적이 달성되면 안 된다(fl set/reset은 별도 RPC라 원래 안전).
    result = await add_affection_uncapped(user_id, amount, "admin_fl_up", check_achievements=False)
    new_affection = result["new_affection"]
    await log_command("fl up", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 호감도를 +{amount} 올려드렸어요!! ({user['affection']} → {new_affection})"


async def _handle_fl_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: fl down : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    result = await add_affection_uncapped(user_id, -amount, "admin_fl_down", check_achievements=False)
    new_affection = result["new_affection"]
    await log_command("fl down", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 호감도를 -{amount} 내렸어요!! ({user['affection']} → {new_affection})"


async def _handle_fl_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: fl set : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    new_affection = await set_affection(user_id, amount)
    await log_command("fl set", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 호감도를 {amount}로 맞춰드렸어요!! ({user['affection']} → {new_affection})"


async def _handle_fl_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: fl reset : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    user = await _require_registered(user_id)
    await set_affection(user_id, _INITIAL_AFFECTION)
    await log_command("fl reset", str(user_id), str(user["affection"]), str(_INITIAL_AFFECTION))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 호감도를 초기값으로 되돌려드렸어요!! ({user['affection']} → {_INITIAL_AFFECTION})"


_INITIAL_COINS = 0


async def _handle_co_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: co up : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    if amount <= 0:
        raise _AdminError("amount는 1 이상이어야 해!!")
    user = await _require_registered(user_id)
    # 관리자 지급은 "번 것"이 아니므로 count_as_earned=False — lifetime_coins_earned를
    # 안 늘려서 "티끌 모아 티끌" 업적이 관리자 조작으로 달성되지 않게 막는다(fl up/down이
    # check_achievements=False로 막는 것과 동일한 원칙). add_coins RPC 자체가 이미
    # max_coins 클램프를 하므로 "최대 수치를 뚫을 수 없다"는 별도 처리 없이 보장된다.
    result = await add_coins(user_id, amount, method="admin_co_up", count_as_earned=False)
    new_coins = result["new_coins"]
    await log_command("co up", f"{user_id} {amount}", str(user["coins"]), str(new_coins))
    name = await _resolve_name(user_id)
    if result["applied_amount"] < amount:
        return (
            f"네!! {name}님의 동전을 +{result['applied_amount']} 드렸어요!! "
            f"({user['coins']} → {new_coins}) (최대 보유량이라 {amount}만큼 다 못 드렸어요!!)"
        )
    return f"네!! {name}님의 동전을 +{amount} 드렸어요!! ({user['coins']} → {new_coins})"


async def _handle_co_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: co down : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    if amount <= 0:
        raise _AdminError("amount는 1 이상이어야 해!!")
    user = await _require_registered(user_id)
    result = await deduct_coins_clamped(user_id, amount)
    new_coins = result["new_coins"]
    await log_command("co down", f"{user_id} {amount}", str(user["coins"]), str(new_coins))
    name = await _resolve_name(user_id)
    if result["deducted"] < amount:
        return (
            f"네!! {name}님의 동전을 -{result['deducted']} 내렸어요!! "
            f"({user['coins']} → {new_coins}) (원래 {amount}만큼 없어서 있는 만큼만 뗐어요!!)"
        )
    return f"네!! {name}님의 동전을 -{amount} 내렸어요!! ({user['coins']} → {new_coins})"


async def _handle_co_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: co set : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    new_coins = await set_coins(user_id, amount)
    await log_command("co set", f"{user_id} {amount}", str(user["coins"]), str(new_coins))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 동전을 {amount}로 맞춰드렸어요!! ({user['coins']} → {new_coins})"


async def _handle_co_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: co reset : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    user = await _require_registered(user_id)
    new_coins = await set_coins(user_id, _INITIAL_COINS)
    await log_command("co reset", str(user_id), str(user["coins"]), str(_INITIAL_COINS))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 동전을 0으로 리셋했어요!! ({user['coins']} → {_INITIAL_COINS})"


# users.max_coins의 DEFAULT와 동일 — fl reset이 _INITIAL_AFFECTION(users.affection
# DEFAULT)으로 되돌리는 것과 동일한 원칙("리셋 = 시작 상태로 되돌림", 0이 아님 — max_coins가
# 0이면 이 유저는 동전을 영영 못 받는 상태가 되어버린다).
_INITIAL_MAX_COINS = 20


async def _handle_vol_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: vol up : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    if amount <= 0:
        raise _AdminError("amount는 1 이상이어야 해!!")
    user = await _require_registered(user_id)
    # max_coins는 자판기 용량 업그레이드로 반복 누적 가능해 하드 캡이 없다 — 기존
    # increase_max_coins(단순 누적)를 그대로 재사용.
    new_max = await increase_max_coins(user_id, amount)
    await log_command("vol up", f"{user_id} {amount}", str(user["max_coins"]), str(new_max))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 최대 동전 보유량을 +{amount} 늘려드렸어요!! ({user['max_coins']} → {new_max})"


async def _handle_vol_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: vol down : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    if amount <= 0:
        raise _AdminError("amount는 1 이상이어야 해!!")
    user = await _require_registered(user_id)
    result = await decrease_max_coins(user_id, amount)
    new_max = result["new_max_coins"]
    await log_command("vol down", f"{user_id} {amount}", str(user["max_coins"]), str(new_max))
    name = await _resolve_name(user_id)
    if result["deducted"] < amount:
        return (
            f"네!! {name}님의 최대 동전 보유량을 -{result['deducted']} 줄였어요!! "
            f"({user['max_coins']} → {new_max}) (원래 {amount}만큼 없어서 있는 만큼만 뗐어요!!)"
        )
    return f"네!! {name}님의 최대 동전 보유량을 -{amount} 줄였어요!! ({user['max_coins']} → {new_max})"


async def _handle_vol_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: vol set : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    new_max = await set_max_coins(user_id, amount)
    await log_command("vol set", f"{user_id} {amount}", str(user["max_coins"]), str(new_max))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 최대 동전 보유량을 {amount}로 맞춰드렸어요!! ({user['max_coins']} → {new_max})"


async def _handle_vol_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: vol reset : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    user = await _require_registered(user_id)
    new_max = await set_max_coins(user_id, _INITIAL_MAX_COINS)
    await log_command("vol reset", str(user_id), str(user["max_coins"]), str(_INITIAL_MAX_COINS))
    name = await _resolve_name(user_id)
    return (
        f"네!! {name}님의 최대 동전 보유량을 초기값으로 되돌렸어요!! "
        f"({user['max_coins']} → {_INITIAL_MAX_COINS})"
    )


async def _handle_cnt_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: cnt up : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = min(max(before + amount, 0), stats["nl_cap"])
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("cnt up", f"{user_id} {amount}", str(before), str(new_count))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 오늘 대화 횟수를 +{amount} 올려드렸어요!! ({before} → {new_count}/{stats['nl_cap']})"


async def _handle_cnt_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: cnt down : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = max(before - amount, 0)
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("cnt down", f"{user_id} {amount}", str(before), str(new_count))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 오늘 대화 횟수를 -{amount} 내려드렸어요!! ({before} → {new_count}/{stats['nl_cap']})"


async def _handle_cnt_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: cnt set : {user_id} {amount} {boolean}")
    user_id = _parse_user_id(args[0])
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = min(max(amount, 0), stats["nl_cap"])
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("cnt set", f"{user_id} {amount}", str(before), str(new_count))
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 오늘 대화 횟수를 {amount}로 맞춰드렸어요!! ({before} → {new_count}/{stats['nl_cap']})"


async def _handle_cnt_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: cnt reset : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    await _update_nl_count(user_id, 0, stats["nl_cap"])
    await log_command("cnt reset", str(user_id), str(before), "0")
    name = await _resolve_name(user_id)
    return f"네!! {name}님의 오늘 대화 횟수를 0으로 되돌려드렸어요!! ({before} → 0/{stats['nl_cap']})"


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


async def _handle_v(args: list[str]) -> str:
    updated_dt = datetime.fromisoformat(get_last_updated_iso()).astimezone(_KST)
    updated_label = updated_dt.strftime("%Y-%m-%d %H:%M")
    return f"지금 버전은 {get_version_label()}이에요!! 마지막 업데이트는 {updated_label}이에요!!"


def _format_commit_date(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).astimezone(_KST).strftime("%Y-%m-%d %H:%M")


async def _handle_v_last(args: list[str]) -> str:
    previous = get_previous_commit()
    if previous is None:
        return "이전 커밋이 없어요!! (배포 환경의 git 히스토리가 부족할 수도 있어요)"
    h, iso = previous
    return f"이전 커밋은 {h}이에요!! ({_format_commit_date(iso)})"


async def _handle_v_list(args: list[str]) -> str:
    commits = get_recent_commits(30)
    if not commits:
        return "최근 30일 커밋 기록을 가져올 수 없어요!! (배포 환경의 git 히스토리가 부족할 수도 있어요)"
    return "\n".join(f"{h} - {_format_commit_date(iso)} - {subject}" for h, iso, subject in commits)


async def _handle_sh_hammie_runtime(args: list[str]) -> str:
    sleep_label = f"{SLEEP_START.hour:02d}:{SLEEP_START.minute:02d}"
    wake_label = f"{WAKE_TIME.hour:02d}:{WAKE_TIME.minute:02d}"
    call_start_label = f"{WINDOW_START.hour:02d}:{WINDOW_START.minute:02d}"
    call_end_label = f"{WINDOW_END.hour:02d}:{WINDOW_END.minute:02d}"
    return (
        f"햄미는 {wake_label}~{sleep_label}에 활동해요!! 그 시간 외엔 완전히 잠들어서 아무 반응도 안 해요!!\n"
        f"호출 이벤트는 {call_start_label}~{call_end_label} 사이에만 발생할 수 있어요!!"
    )


async def _handle_ach_list(args: list[str]) -> str:
    return "\n".join(achievements.format_name(module) for module in achievements.REGISTRY.values())


async def _handle_ach_help(args: list[str]) -> str:
    return "\n".join(
        f"{achievements.format_name(module)} - {module.HOW_TO_EARN}"
        for module in achievements.REGISTRY.values()
    )


async def _handle_ach_code(args: list[str]) -> str:
    return "\n".join(
        f"{achievements.format_name(module)} - {module.CODE}"
        for module in achievements.REGISTRY.values()
    )


async def _handle_ach_grant(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: ach grant : {user_id} {code} {boolean}")
    user_id = _parse_user_id(args[0])
    code = args[1]
    module = achievements.CODE_REGISTRY.get(code)
    if module is None:
        return f"그런 업적 코드는 없어요!!\n{await _handle_ach_code([])}"
    await _require_registered(user_id)
    result = await award_achievement(user_id, module.ID)
    name = await _resolve_name(user_id)
    if not result["earned"]:
        return f"{name}님은 이미 '{achievements.format_name(module)}' 업적을 가지고 있어요!!"
    await log_command("ach grant", f"{user_id} {code}", "미보유", module.ID)
    return (
        f"네!! {name}님에게 '{achievements.format_name(module)}' 업적을 부여했어요!! "
        f"(호감도 +{result['applied_amount']}, 현재 {result['new_affection']})"
    )


async def _handle_ach_revoke(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: ach revoke : {user_id} {code} {boolean}")
    user_id = _parse_user_id(args[0])
    code = args[1]
    module = achievements.CODE_REGISTRY.get(code)
    if module is None:
        return f"그런 업적 코드는 없어요!!\n{await _handle_ach_code([])}"
    await _require_registered(user_id)
    revoked = await revoke_achievement(user_id, module.ID)
    name = await _resolve_name(user_id)
    if not revoked:
        return f"{name}님은 원래 '{achievements.format_name(module)}' 업적이 없었어요!!"
    await log_command("ach revoke", f"{user_id} {code}", module.ID, "미보유")
    return f"네!! {name}님의 '{achievements.format_name(module)}' 업적을 제거했어요!!"


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


async def _handle_c_help(args: list[str]) -> str:
    matched = _filter_commands(_resolve_filter_keyword(args))
    if not matched:
        return _NO_MATCH_MESSAGE
    return "\n".join(f"{spec.name} : {spec.params} - {spec.description}" for spec in matched)


# op grant/revoke의 대상이 햄미 자신이면 _parse_user_id가 이미 막는다 — 여기선 대상이
# prime 자신인 경우만 막는다. prime의 권한은 코드에 고정이라 admin_ops로 다룰 대상이 아니고,
# 특히 자기 자신의 op를 revoke하면 아무도 다시 op를 못 주게 잠긴다.
_ALREADY_PRIME_MESSAGE = "그분은 이미 최초 주인님이세요!!"
_CANNOT_REVOKE_PRIME_MESSAGE = "그분의 권한은 제거할 수 없어요!!"


async def _resolve_name(user_id: int) -> str:
    """명령어가 실행된 서버에 대상이 있으면 그 서버 별명을, 없으면 실제(글로벌) 이름을
    반환한다 — 멘션은 절대 안 한다(events/sleep_event.py::_resolve_display_name과 동일 원칙)."""
    guild = _current_guild.get()
    if guild is not None:
        member = guild.get_member(user_id)
        if member is not None:
            return member.display_name
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


async def _user_exists(user_id: int) -> bool:
    """햄미 가입 여부와 무관하게, 그 ID가 실제 Discord 계정인지만 확인한다."""
    if _client is None:
        return True
    try:
        await _client.fetch_user(user_id)
        return True
    except discord.NotFound:
        return False
    except discord.HTTPException as e:
        logging.exception("Failed to verify user existence for %s", user_id)
        raise _AdminError("지금은 사용자 확인이 어려워요!! 잠시 후 다시 시도해줘.") from e


def _parse_emoji_tokens(token: str) -> list[str]:
    return [t for t in token.split(_EMOJI_NAME_SEPARATOR) if t]


# Discord의 실제 이모지 셋(5,721개 공개 shortcode 기준으로 대조 확인)과 emoji_lib를 비교했을
# 때, "regional_indicator_a"~"z"(U+1F1E6~U+1F1FF, 낱개 알파벳 리액션) 26개만 emoji_lib에
# 통째로 없다 — 국기 이모지(예: 🇰🇷)를 만드는 조합용 문자라 CLDR 별칭 목록에서 빠져있을 뿐
# Discord 리액션 자체는 정상 지원한다. 그 외엔 실제로 존재하지 않는 이름/별칭 차이일 뿐이라
# 추가로 보완할 진짜 누락은 없었다.
_EXTRA_EMOJI_ALIASES = {
    f"regional_indicator_{chr(letter)}": chr(0x1F1E6 + letter - ord("a"))
    for letter in range(ord("a"), ord("z") + 1)
}
# emoji_lib.is_emoji()도 EMOJI_DATA 기반이라 낱개 리전 인디케이터 문자(🇦 등)는 리터럴로
# 입력해도 인식 못 한다 — 이름/문자 둘 다 이 보완 테이블로 커버해야 한다.
_EXTRA_EMOJI_CHARS = set(_EXTRA_EMOJI_ALIASES.values())


def _resolve_emoji(token: str) -> str | None:
    """이모지 문자 그대로(예: 👍)를 받거나, 표준 유니코드 이모지의 영문 별칭(예: thumbsup)을
    받아 실제 이모지 문자로 바꾼다. 어느 쪽도 아니면 None."""
    if emoji_lib.is_emoji(token) or token in _EXTRA_EMOJI_CHARS:
        return token
    placeholder = f":{token}:"
    resolved = emoji_lib.emojize(placeholder, language="alias")
    if resolved != placeholder:
        return resolved
    return _EXTRA_EMOJI_ALIASES.get(token)


async def _handle_emj_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: emj set : {user_id} {emojis} {boolean}")
    user_id = _parse_user_id(args[0])
    tokens = _parse_emoji_tokens(args[1])
    if not tokens:
        raise _AdminError("emojis가 비어 있어요!!")
    if not await _user_exists(user_id):
        raise _AdminError(f"그런 사용자는 없는 것 같아요!! ({user_id})")

    resolved: list[str] = []
    invalid: list[str] = []
    for t in tokens:
        emoji_char = _resolve_emoji(t)
        if emoji_char is None:
            invalid.append(t)
        else:
            resolved.append(emoji_char)
    if invalid:
        raise _AdminError(f"존재하지 않는 이모지예요!! ({', '.join(invalid)})")

    # 같은 이모지를 이름/리터럴 혼용 등으로 중복 지정해도(예: "fire,🔥") 한 번만 남긴다 —
    # 순서(첫 등장 기준)는 그대로 유지. 중복 반응 시도 자체를 원천 차단해 add_reaction이
    # 같은 이모지로 반복 호출될 일이 없게 한다.
    resolved = list(dict.fromkeys(resolved))

    # 완전 리셋 — 기존에 걸려 있던 이모지 목록은 유지하지 않고 통째로 대체한다.
    await set_emoji_tags_row(user_id, resolved)
    _emoji_tags[user_id] = resolved
    await log_command("emj set", f"{user_id} {args[1]}", "-", " ".join(resolved))
    name = await _resolve_name(user_id)
    return f"네!! {name}님한테 이모지 태그를 걸었어요!! ({' '.join(resolved)})"


async def _handle_emj_stop(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: emj stop : {user_id} {boolean}")
    user_id = _parse_user_id(args[0])
    had = await clear_emoji_tags_row(user_id)
    _emoji_tags.pop(user_id, None)
    await log_command("emj stop", str(user_id), "있음" if had else "없음", "-")
    name = await _resolve_name(user_id)
    if not had:
        return f"{name}님한테는 원래 이모지 태그가 없었어요!!"
    return f"네!! {name}님의 이모지 태그를 전부 제거했어요!!"


def _parse_channel_id(token: str) -> int:
    """디스코드 채널 멘션(<#1234567890>) 또는 순수 숫자 채널 ID를 정수로 변환한다."""
    stripped = token.strip()
    if stripped.startswith("<#") and stripped.endswith(">"):
        stripped = stripped[2:-1]
    try:
        return int(stripped)
    except ValueError:
        raise _AdminError(f"채널을 못 찾겠어요!! (#채널로 멘션해줘: {token})") from None


def _resolve_location(args: list[str]) -> int:
    """{location} 생략 시 지금 이 채널을 기본값으로 쓴다("des main"/"des sub"/"des void"
    공통)."""
    if args:
        return _parse_channel_id(args[0])
    channel_id = _current_channel.get()
    if channel_id is None:
        raise _AdminError("서버 채널에서만 쓸 수 있어요!!")
    return channel_id


async def _handle_des_main(args: list[str]) -> str:
    if len(args) > 1:
        raise _AdminError("사용법: des main : {location} {boolean}")
    guild = _current_guild.get()
    if guild is None:
        raise _AdminError("서버 채널에서만 쓸 수 있어요!!")
    channel_id = _resolve_location(args)
    before = get_main_channel(guild.id)
    was_sub = channel_id in get_sub_channel_ids(guild.id)
    await set_main_channel(guild.id, channel_id)
    await log_command("des main", str(guild.id), str(before) if before else "없음", str(channel_id))
    note = " (서브 채널이었어서 서브 목록에서도 뺐어요!!)" if was_sub else ""
    return f"네!! <#{channel_id}>을(를) 이 서버의 메인 채널로 설정했어요!!{note}"


async def _handle_des_sub(args: list[str]) -> str:
    if len(args) > 1:
        raise _AdminError("사용법: des sub : {location} {boolean}")
    guild = _current_guild.get()
    if guild is None:
        raise _AdminError("서버 채널에서만 쓸 수 있어요!!")
    main = get_main_channel(guild.id)
    if main is None:
        raise _AdminError("메인을 먼저 지정하세요!!")
    channel_id = _resolve_location(args)
    if channel_id == main:
        return "그 채널은 이미 메인 채널이에요!!"
    added = await add_sub_channel(guild.id, channel_id)
    if not added:
        return f"<#{channel_id}>은(는) 이미 서브 채널이에요!!"
    await log_command("des sub", str(guild.id), "-", str(channel_id))
    return f"네!! <#{channel_id}>에서 명령어를 쓸 수 있게 했어요!!"


async def _handle_des_list(args: list[str]) -> str:
    guild = _current_guild.get()
    if guild is None:
        raise _AdminError("서버 채널에서만 쓸 수 있어요!!")
    main = get_main_channel(guild.id)
    subs = get_sub_channel_ids(guild.id)
    lines = [f"메인: {f'<#{main}>' if main else '지정 안 됨'}"]
    if subs:
        lines.append("서브:")
        lines.extend(f"- <#{channel_id}>" for channel_id in sorted(subs))
    else:
        lines.append("서브: 없음")
    return "\n".join(lines)


async def _handle_des_void(args: list[str]) -> str:
    if len(args) > 1:
        raise _AdminError("사용법: des void : {location} {boolean}")
    guild = _current_guild.get()
    if guild is None:
        raise _AdminError("서버 채널에서만 쓸 수 있어요!!")
    channel_id = _resolve_location(args)
    main = get_main_channel(guild.id)
    subs = get_sub_channel_ids(guild.id)
    if channel_id == main:
        if subs:
            raise _AdminError("서브 채널이 존재해요! 메인을 옮기고 다시 진행해주세요!!")
        await clear_main_channel(guild.id)
        await log_command("des void", str(guild.id), str(channel_id), "없음")
        return f"네!! <#{channel_id}>의 메인 지정을 해제했어요!!"
    if channel_id in subs:
        await remove_sub_channel(guild.id, channel_id)
        await log_command("des void", str(guild.id), str(channel_id), "없음")
        return f"네!! <#{channel_id}>의 서브 지정을 해제했어요!!"
    return f"<#{channel_id}>은(는) 원래 지정 안 된 채널이에요!!"


_UPDATE_ANNOUNCE_LINES = (
    "안뇽!! 나는 햄미야! 아까 전에 햄미가 새로운걸 배웠는데, 한번 볼랭? _(신남)_",
    "얘들아!! 햄미가 조은 소식 가져와써!! 구경하고 가!! _(들뜸)_",
    "잠깐!! 햄미한테 새로운 게 생겨써!! 얼른 봐봐!! _(신남)_",
    "짜잔!! 햄미가 업데이트 소식을 들고 와써!! _(자랑)_",
    "다들 주목!! 햄미가 뭔가 달라져써!! _(기대)_",
    "헤헤, 햄미 오늘 새 소식 가져와써!! 구경해줘!! _(방실)_",
    "얘들아 이거 봐!! 햄미한테 새 기능이 생겨써!! _(들뜸)_",
    "오늘의 조은 소식이야!! 햄미가 알려줄게!! _(신남)_",
    "짜란!! 햄미 업데이트 완료했다구!! 확인해봐!! _(뿌듯)_",
    "다들 이리 와봐!! 햄미가 새 소식 준비해써!! _(설렘)_",
    "햄미 소식 하나 알려줄게!! 잘 들어봐!! _(진지)_",
    "얘들아!! 방금 햄미한테 무슨 일이 있었게!! _(궁금)_",
    "짜잔!! 오늘부터 조금 달라진 햄미야!! _(자신감)_",
    "이거 알려주고 시퍼써!! 햄미 새 소식이야!! _(신남)_",
    "다들 잘 들어봐!! 햄미가 업데이트됬어!! _(뿌듯)_",
    "헐랭, 햄미한테 새로운 게 생겨써!! 구경 조!! _(놀람)_",
    "오늘은 특별한 날이야!! 햄미가 달라져써!! _(설렘)_",
    "짜잔짜잔!! 햄미의 새 소식 대공개!! _(신남)_",
    "얘들아 이것도 봐줘!! 햄미가 배운 거야!! _(자랑)_",
    "다들 조은 소식이야!! 햄미가 한 단계 업그레이드 됬어!! _(뿌듯)_",
)


def _build_update_embed(entry: update_announcement.UpdateEntry) -> discord.Embed:
    # 날짜/버전은 entry에 박아둔 값이 아니라 "ann update"를 실제로 실행하는 지금 시점의
    # 것을 그대로 보여준다 — v 명령어가 보여주는 것과 항상 같은 소스(get_version_label).
    date_label = datetime.now(_KST).strftime("%Y.%m.%d.")
    description = (
        f"날짜: {date_label}\n버전: {get_version_label()}\n\n"
        + "\n".join(f"- {c}" for c in entry.changes)
    )
    embed = discord.Embed(title="🔔 햄미의 업데이트 소식", description=description, color=EMBED_COLOR)
    embed.set_footer(text=format_footer_time(datetime.now(_KST)))
    return embed


def _parse_trailing_boolean(raw: str) -> bool:
    """raw_args 명령어 중 자유 텍스트 없이 {boolean}만 받는 것들(현재 "ann update")이
    쓰는 파서 — _extract_boolean과 동일한 규칙(true/false만, 대소문자 무관, 생략 시 false)."""
    token = raw.strip().lower()
    if token in ("", "false"):
        return False
    if token == "true":
        return True
    raise _AdminError("boolean은 true 또는 false만 가능해요!!")


def _parse_an_msg(raw: str) -> tuple[str, bool]:
    """'"{text}" {boolean}' 형태를 파싱한다. text 안의 "\\"(백슬래시) 한 글자는 줄바꿈으로
    치환한다 — 다른 이스케이프 시퀀스는 지원하지 않는다."""
    raw = raw.strip()
    if not raw.startswith('"'):
        raise _AdminError('사용법: ann msg : "text" {boolean} (text는 큰따옴표로 감싸야 해요)')
    end = raw.find('"', 1)
    if end == -1:
        raise _AdminError('큰따옴표가 안 닫혔어요!! "text" 형태로 써주세요.')
    text = raw[1:end].replace("\\", "\n")
    if not text.strip():
        raise _AdminError("보낼 text가 비어 있어요!!")
    return text, _parse_trailing_boolean(raw[end + 1 :])


async def _handle_ann_msg(raw: str) -> tuple[str, bool]:
    text, dm = _parse_an_msg(raw)
    if dm:
        return text, True
    if _client is not None:
        await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=text)
    return "네!! 모든 서버에 공지했어요!!", False


async def _handle_ann_update(raw: str) -> tuple[str | tuple[str, discord.Embed], bool]:
    dm = _parse_trailing_boolean(raw)
    entry = update_announcement.latest()
    if entry is None:
        return "아직 공지할 업데이트가 없어요!!", dm
    intro = random.choice(_UPDATE_ANNOUNCE_LINES)
    embed = _build_update_embed(entry)
    if dm:
        return (intro, embed), True
    if _client is not None:
        await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=intro, embed=embed)
    return "네!! 모든 서버에 업데이트 소식을 공지했어요!!", False


_REST_COMMAND_NAME = "done"


async def _handle_done(args: list[str]) -> str:
    return _REST_MESSAGE


_Handler = Callable[[list[str]], Awaitable[Union[str, tuple[str, discord.Embed]]]]


@dataclass(frozen=True)
class _CommandSpec:
    name: str
    arity: int  # boolean을 제외한 필수 인자 개수 (raw_args면 무시됨)
    params: str
    description: str
    handler: _Handler
    requires_prime: bool = False  # True면 최초 주인(ADMIN_USER_ID)만 실행 가능
    # True면 handler가 list[str] 대신 (raw: str)을 받고 str 대신 (응답, dm: bool)을
    # 반환하는 raw 전용 핸들러다(현재 "ann msg"/"ann update") — _extract_boolean의 arity
    # 기반 토큰 분리를 건너뛴다. "ann msg"는 자유 텍스트 파싱이 필요해서, "ann update"는
    # boolean의 의미가 반대(§14-9)라 핸들러가 직접 dm을 결정해야 해서 필요하다.
    raw_args: bool = False


_COMMAND_LIST = (
    _CommandSpec("fl up", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도 +amount (일일 상한 미적용)", _handle_fl_up),
    _CommandSpec("fl down", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도 -amount", _handle_fl_down),
    _CommandSpec("fl set", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도를 amount로 절대값 설정", _handle_fl_set),
    _CommandSpec("fl reset", 1, "{user_id} {boolean}", "해당 유저 호감도를 초기값(10)으로 리셋", _handle_fl_reset),
    _CommandSpec("co up", 2, "{user_id} {amount} {boolean}", "해당 유저 동전 +amount (최대 보유량 클램프, lifetime_coins_earned 미반영)", _handle_co_up),
    _CommandSpec("co down", 2, "{user_id} {amount} {boolean}", "해당 유저 동전 -amount (0 미만 방지)", _handle_co_down),
    _CommandSpec("co set", 2, "{user_id} {amount} {boolean}", "해당 유저 동전을 amount로 절대값 설정 (0~최대 보유량 클램프)", _handle_co_set),
    _CommandSpec("co reset", 1, "{user_id} {boolean}", "해당 유저 동전을 0으로 리셋", _handle_co_reset),
    _CommandSpec("vol up", 2, "{user_id} {amount} {boolean}", "해당 유저 최대 동전 보유량 +amount (상한 없음)", _handle_vol_up),
    _CommandSpec("vol down", 2, "{user_id} {amount} {boolean}", "해당 유저 최대 동전 보유량 -amount (0 미만 방지)", _handle_vol_down),
    _CommandSpec("vol set", 2, "{user_id} {amount} {boolean}", "해당 유저 최대 동전 보유량을 amount로 절대값 설정 (0 미만 방지, 상한 없음)", _handle_vol_set),
    _CommandSpec("vol reset", 1, "{user_id} {boolean}", "해당 유저 최대 동전 보유량을 초기값(10)으로 리셋", _handle_vol_reset),
    _CommandSpec("cnt up", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수 +amount (0~당일 상한 클램프)", _handle_cnt_up),
    _CommandSpec("cnt down", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수 -amount (0 미만 방지)", _handle_cnt_down),
    _CommandSpec("cnt set", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수를 amount로 절대값 설정 (0~당일 상한 클램프)", _handle_cnt_set),
    _CommandSpec("cnt reset", 1, "{user_id} {boolean}", "해당 유저 오늘 대화 횟수를 0으로 리셋", _handle_cnt_reset),
    _CommandSpec("sh event all", 0, "{boolean}", "오늘 헬프 미 이벤트 전부와 결과 표시", _handle_sh_event_all),
    _CommandSpec("sh event next", 0, "{boolean}", "다음으로 남은 헬프 미 이벤트 시각 표시", _handle_sh_event_next),
    _CommandSpec("sh event last", 0, "{boolean}", "가장 최근에 지난 헬프 미 이벤트 시각 표시", _handle_sh_event_last),
    _CommandSpec("sh user stats", 1, "{user_id} {boolean}", "해당 유저의 일반 정보(=/내정보) 표시", _handle_sh_user_stats),
    _CommandSpec("sh db list", 0, "{boolean}", "등록된 테이블 이름 전부 표시", _handle_sh_db_list),
    _CommandSpec("sh db", 2, "{name} {amount|*} {boolean}", "해당 테이블 최근 amount개 행(amount가 *면 전체) 표시", _handle_sh_db),
    _CommandSpec("sh hammie runtime", 0, "{boolean}", "햄미 활동 시간 및 이벤트 발생 가능 시간 표시", _handle_sh_hammie_runtime),
    _CommandSpec("v", 0, "{boolean}", "현재 버전(커밋)과 마지막 업데이트 일시 표시", _handle_v),
    _CommandSpec("v last", 0, "{boolean}", "바로 이전 커밋과 그 일시 표시", _handle_v_last),
    _CommandSpec("v list", 0, "{boolean}", "최근 30일간의 커밋을 일시와 함께 나열", _handle_v_list),
    _CommandSpec("ach list", 0, "{boolean}", "업적 이름(희귀도 포함) 목록 표시", _handle_ach_list),
    _CommandSpec("ach help", 0, "{boolean}", "업적 이름 + 획득 방법 표시", _handle_ach_help),
    _CommandSpec("ach code", 0, "{boolean}", "업적 이름 + 코드 표시", _handle_ach_code),
    _CommandSpec("ach grant", 2, "{user_id} {code} {boolean}", "해당 유저에게 코드로 업적을 부여", _handle_ach_grant),
    _CommandSpec("ach revoke", 2, "{user_id} {code} {boolean}", "해당 유저의 업적을 코드로 제거", _handle_ach_revoke),
    _CommandSpec("op grant", 1, "{user_id} {boolean}", "해당 유저에게 관리자 권한을 부여 (최초 주인 전용)", _handle_op_grant, requires_prime=True),
    _CommandSpec("op revoke", 1, "{user_id} {boolean}", "해당 유저의 관리자 권한을 제거 (최초 주인 전용)", _handle_op_revoke, requires_prime=True),
    _CommandSpec("op list", 0, "{boolean}", "권한을 가진 사용자 전부 표시 (최초 주인 전용)", _handle_op_list, requires_prime=True),
    _CommandSpec("emj set", 2, "{user_id} {emojis} {boolean}", "해당 유저가 말할 때마다 emojis(콤마 구분, 이모지 문자 또는 영문 별칭 둘 다 가능 — 예: 👍,👎 또는 thumbsup,thumbsdown)를 순서대로 반응으로 건다 — 완전 리셋", _handle_emj_set),
    _CommandSpec("emj stop", 1, "{user_id} {boolean}", "해당 유저에게 걸린 이모지 태그를 전부 제거", _handle_emj_stop),
    _CommandSpec("des main", 1, "{location} {boolean}", "location(생략 시 현재 채널)을 이 서버의 메인 채널로 지정 — 기존 메인은 교체되고, 서브였다면 서브 목록에서도 자동으로 빠짐. 메인 채널에선 명령어는 물론 이벤트/공지 등 시스템 메시지도 뜸", _handle_des_main),
    _CommandSpec("des sub", 1, "{location} {boolean}", "location(생략 시 현재 채널)에서 명령어(호출 단어/슬래시)만 허용 — 이벤트/공지 등 시스템 메시지는 안 뜸. 메인이 먼저 지정돼 있어야 함", _handle_des_sub),
    _CommandSpec("des list", 0, "{boolean}", "이 서버의 메인/서브 채널 목록 표시 (메인이 맨 위)", _handle_des_list),
    _CommandSpec("des void", 1, "{location} {boolean}", "location(생략 시 현재 채널)의 메인/서브 지정 해제 — 서브가 남아있는데 메인을 해제하려 하면 거부됨", _handle_des_void),
    _CommandSpec("ann msg", 0, '"{text}" {boolean}', 'text를 그대로 공지 — {boolean}이 다른 명령어와 반대: false(기본)면 모든 서버의 메인/마지막 채널에 방송, true면 관리자 DM으로만 미리보기(방송 안 함). text는 큰따옴표로 감싸고, 안의 "\\"(백슬래시) 한 글자는 줄바꿈으로 바뀜', _handle_ann_msg, raw_args=True),
    _CommandSpec("ann update", 0, "{boolean}", "최신 업데이트 기록을 공지 — {boolean} 의미는 ann msg와 동일(false=방송, true=DM 미리보기). LLM을 쓰지 않고 기록을 그대로 읽어 보냄", _handle_ann_update, raw_args=True),
    _CommandSpec("c", 1, "{string} {boolean}", "이름에 string 단어가 있는 명령어만 매개변수 포함해서 나열 (string 생략 시 전체, \"*\"와 동일)", _handle_c),
    _CommandSpec("c help", 1, "{string} {boolean}", "이름에 string 단어가 있는 명령어만 설명과 함께 나열 (string 생략 시 전체)", _handle_c_help),
    _CommandSpec(_REST_COMMAND_NAME, 0, "{boolean}", "세션을 즉시 종료", _handle_done),
)

_COMMANDS = {spec.name: spec for spec in _COMMAND_LIST}


# 관리자 명령어 자연어 설명 기능의 RAG 문서 본문. documents/admin_commands.py는 이 함수를
# 그대로 부르는 얇은 래퍼(admin.console은 documents를 안 쳐다봐서 순환 import 없음).
# {boolean}의 뜻을 문서에 직접 안 넣으면 모델이 몰라서 메타 발언으로 새는 문제가 있었다.
_COMMANDS_DOC_PREAMBLE = (
    "관리자 콘솔에서 쓸 수 있는 명령어 전부 (\"{호출 단어} 주인님 가라사대\"로 세션을 열고 "
    "그 안에서 실행함, 형식은 \"이름 : 파라미터 - 설명\"):\n\n"
    "대부분 명령어의 마지막 파라미터인 {boolean}은 공통 옵션이다 — true면 결과를 DM으로 "
    "보내고, false거나 생략하면 지금 이 채널에 그대로 보여준다. true/false만 인정하고 "
    "1/0은 인정하지 않는다.\n\n"
    "단, \"ann msg\"/\"ann update\"(공지 관련 명령어) 두 개는 {boolean}의 의미가 반대다 — "
    "false(기본)면 햄미가 있는 모든 서버에 실제로 공지를 방송하고, true면 방송하지 않고 "
    "관리자 DM으로만 미리보기를 보낸다. 이 두 명령어를 설명할 때는 반드시 이 예외를 "
    "언급해야 한다.\n\n"
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


async def _send_placeholder(message: discord.Message) -> discord.Message | None:
    try:
        return await message.reply(_THINKING_PLACEHOLDER)
    except discord.HTTPException:
        logging.exception("Failed to send admin console thinking placeholder")
        return None


async def _delete_placeholder(placeholder: discord.Message | None) -> None:
    if placeholder is None:
        return
    try:
        await placeholder.delete()
    except discord.HTTPException:
        logging.exception("Failed to delete admin console thinking placeholder")


async def _dispatch(message: discord.Message, spec: _CommandSpec, tokens: list[str]) -> None:
    if spec.requires_prime and not _is_prime(message.author.id):
        await _send(message, False, "그건 사용하실 수 없어요!!")
        return

    # raw_args 명령어("ann msg"/"ann update")는 arity 기반 _extract_boolean을 안 거치고,
    # 원문을 그대로 넘긴 뒤 핸들러가 직접 (응답, dm) 튜플로 boolean까지 함께 반환한다 —
    # 나머지 전부는 기존처럼 arity로 boolean을 분리한다.
    dm = False
    guild_token = _current_guild.set(message.guild)
    channel_token = _current_channel.set(message.channel.id)
    try:
        if spec.raw_args:
            response, dm = await spec.handler(" ".join(tokens))
        else:
            dm, remaining_args = _extract_boolean(tokens, spec.arity)
            response = await spec.handler(remaining_args)
    except _AdminError as e:
        response = str(e)
    except Exception:
        # _AdminError는 사용법/검증 오류라 그대로 보여주면 되지만, 그 외 예외(DB 순단
        # 등)를 여기서 못 잡으면 handle()까지 그대로 새서 discord.py 기본 에러 핸들러가
        # 로그만 남기고 아무 응답도 안 보낸다 — 사용자 입장에선 "아무 반응 없음"으로
        # 보여 명령어가 조용히 실패한 것처럼 느껴진다. 최소한 실패했다는 건 알려준다.
        logging.exception("Admin command '%s' failed unexpectedly", spec.name)
        response = "어라, 처리하다가 문제가 생겼어요!! 잠시 후 다시 시도해줘."
    finally:
        _current_guild.reset(guild_token)
        _current_channel.reset(channel_token)
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
    전혀 열지도 연장하지도 않는 완전히 독립적인 1회성 응답이다. 생성 중엔 "답변중..."
    플레이스홀더를 띄우고, 최근 최대 5턴(30분 이내)의 대화 맥락을 같이 넣어준다 — 이
    히스토리는 일반 자연어(chat_history)와 완전히 별개 테이블(admin_chat_history)에
    저장돼 서로 섞이지 않는다. 즉시 명령을 실행하려면 "--{명령어}"(공백 없이)를 쓴다.

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
                user_id = message.author.id
                placeholder = await _send_placeholder(message)
                try:
                    now = datetime.now(timezone.utc)
                    context_turns = await get_recent_admin_turns(
                        user_id,
                        since=now - _ADMIN_HISTORY_WINDOW,
                        limit=_ADMIN_CONTEXT_TURN_LIMIT,
                    )
                    response = await get_admin_command_response(
                        freeform_text, all_commands_text(), history=context_turns
                    )
                finally:
                    await _delete_placeholder(placeholder)
                await _send(message, False, response)
                # 히스토리는 일반 자연어(chat_history)와 완전히 별개 테이블에 남겨서 섞이지 않는다.
                await log_admin_turn(user_id, freeform_text)
                await log_admin_turn(user_id, response, role="assistant")
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
