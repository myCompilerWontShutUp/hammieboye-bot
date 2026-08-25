import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Union

import discord

from command.info.info import handle as info_handle
from config import ADMIN_USER_ID
from core.call_event import MIN_GAP_MINUTES, WINDOW_END, WINDOW_START, schedule_one
from core.scheduler import SLEEP_START, WAKE_TIME
from core.version import get_commit_hash, get_last_updated_iso
from db.admin import (
    KNOWN_TABLES,
    dump_table,
    get_last_event,
    get_next_event,
    get_today_events,
    log_command,
    set_affection,
)
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
_PREFIX = "주인님-가라사대 "
_INITIAL_AFFECTION = 10
# 관리자 콘솔에서 햄미가 직접 "말하는" 문구는 (일반 대화의 반말과 달리) 존댓말로 쓴다 —
# 말투 자체(발음 뭉개기, !!/??)는 그대로 유지하고 어미만 존댓말로 바꾼다 (사용자 확정).
_ABUSE_RESPONSE = "주인님이 아니시네요!! (콱)"


class _AdminError(Exception):
    pass


def is_admin_command(content: str) -> bool:
    return content.startswith(_PREFIX)


def _parse_int(token: str, label: str) -> int:
    try:
        return int(token)
    except ValueError:
        raise _AdminError(f"{label}은(는) 정수여야 해: {token}") from None


def _format_event_time(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str).astimezone(_KST)
    return f"{dt.hour}시"


async def _require_registered(user_id: int) -> dict:
    user = await get_user(user_id)
    if user is None:
        raise _AdminError(f"아직 등록 안 되신 유저예요!! ({user_id})")
    return user


async def _handle_la_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la-up {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    new_affection = await add_affection_uncapped(user_id, amount, "admin_la_up")
    await log_command("la-up", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    return f"네!! {user_id}님의 호감도를 +{amount} 올려드렸어요!! (현재 {new_affection})"


async def _handle_la_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la-down {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    new_affection = await add_affection_uncapped(user_id, -amount, "admin_la_down")
    await log_command("la-down", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    return f"네!! {user_id}님의 호감도를 -{amount} 내렸어요!! (현재 {new_affection})"


async def _handle_la_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la-set {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    new_affection = await set_affection(user_id, amount)
    await log_command("la-set", f"{user_id} {amount}", str(user["affection"]), str(new_affection))
    return f"네!! {user_id}님의 호감도를 {amount}로 맞춰드렸어요!! (현재 {new_affection})"


async def _handle_la_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: la-reset {user_id} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    user = await _require_registered(user_id)
    await set_affection(user_id, _INITIAL_AFFECTION)
    await log_command("la-reset", str(user_id), str(user["affection"]), str(_INITIAL_AFFECTION))
    return f"네!! {user_id}님의 호감도를 초기값({_INITIAL_AFFECTION})으로 되돌려드렸어요!!"


async def _handle_tc_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: tc-up {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = min(max(before + amount, 0), stats["nl_cap"])
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("tc-up", f"{user_id} {amount}", str(before), str(new_count))
    return f"네!! {user_id}님의 오늘 대화 횟수를 {new_count}/{stats['nl_cap']}로 올려드렸어요!!"


async def _handle_tc_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: tc-down {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = max(before - amount, 0)
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("tc-down", f"{user_id} {amount}", str(before), str(new_count))
    return f"네!! {user_id}님의 오늘 대화 횟수를 {new_count}/{stats['nl_cap']}로 내려드렸어요!!"


async def _handle_tc_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: tc-set {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    new_count = min(max(amount, 0), stats["nl_cap"])
    await _update_nl_count(user_id, new_count, stats["nl_cap"])
    await log_command("tc-set", f"{user_id} {amount}", str(before), str(new_count))
    return f"네!! {user_id}님의 오늘 대화 횟수를 {new_count}/{stats['nl_cap']}로 맞춰드렸어요!!"


async def _handle_tc_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: tc-reset {user_id} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    user = await _require_registered(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    before = stats["nl_count"]
    await _update_nl_count(user_id, 0, stats["nl_cap"])
    await log_command("tc-reset", str(user_id), str(before), "0")
    return f"네!! {user_id}님의 오늘 대화 횟수를 0/{stats['nl_cap']}로 되돌려드렸어요!!"


async def _update_nl_count(user_id: int, new_count: int, nl_cap: int) -> None:
    """nl_count를 갱신한다. 상한 미만으로 내려가면 over_cap_attempts도 같이 리셋해서,
    다시 상한을 넘길 때 1회차부터 새로 시작하도록 한다."""
    updates = {"nl_count": new_count}
    if new_count < nl_cap:
        updates["over_cap_attempts"] = 0
    await update_daily_stats(user_id, updates)


async def _handle_s_event_all(args: list[str]) -> str:
    events = await get_today_events()
    if not events:
        return "오늘 등록된 이벤트가 없어요!!"
    lines = []
    for event in events:
        time_label = _format_event_time(event["scheduled_at"])
        if event.get("claimed_by") is not None:
            lines.append(f"{time_label} - <@{event['claimed_by']}>")
        elif event.get("penalty_applied"):
            lines.append(f"{time_label} - 획득 실패")
        else:
            lines.append(time_label)
    return "\n".join(lines)


async def _handle_s_event_next(args: list[str]) -> str:
    event = await get_next_event()
    return _format_event_time(event["scheduled_at"]) if event else "오늘 남은 이벤트가 없어요!!"


async def _handle_s_event_last(args: list[str]) -> str:
    event = await get_last_event()
    return _format_event_time(event["scheduled_at"]) if event else "아직 지난 이벤트가 없어요!!"


async def _handle_s_user_stats(args: list[str]) -> tuple[str, discord.Embed]:
    if len(args) != 1:
        raise _AdminError("사용법: s-user-stats {user_id} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    await _require_registered(user_id)
    return await info_handle(user_id)


async def _handle_s_db_list(args: list[str]) -> str:
    return "\n".join(KNOWN_TABLES)


async def _handle_s_db(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: s-db {name} {amount} {boolean}")
    name = args[0]
    amount = _parse_int(args[1], "amount")
    if name not in KNOWN_TABLES:
        raise _AdminError(f"모르는 테이블이에요!! ({name}, 가능: {', '.join(KNOWN_TABLES)})")
    rows = await dump_table(name, amount)
    return "\n".join(str(row) for row in rows) if rows else f"{name}: 데이터 없음"


async def _handle_g_call_event(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: g-call-event {time} {boolean}")
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
    dt = scheduled_at.astimezone(_KST)
    time_label = f"{dt.hour}시 {dt.minute}분"
    await log_command("g-call-event", str(minutes), "없음", time_label)
    return f"네!! {minutes}분 뒤인 {time_label}에 호출 이벤트를 새로 만들었어요!!"


async def _handle_r_call_event(args: list[str]) -> str:
    event = await get_next_event()
    if event is None:
        return "삭제할 예정된 이벤트가 없어요!!"
    await delete_event(event["id"])
    time_label = _format_event_time(event["scheduled_at"])
    await log_command("r-call-event", "", time_label, "삭제됨")
    return f"네!! 가장 가까운 호출 이벤트({time_label})를 삭제했어요!!"


async def _handle_r_call_event_all(args: list[str]) -> str:
    deleted = await delete_unposted_after(datetime.now(timezone.utc))
    if not deleted:
        return "삭제할 예정된 이벤트가 없어요!!"
    await log_command("r-call-event-all", "", f"{len(deleted)}개 예정", "전부 삭제됨")
    return f"네!! 아직 시작 안 한 호출 이벤트 {len(deleted)}개를 전부 삭제했어요!!"


async def _handle_s_version(args: list[str]) -> str:
    commit = get_commit_hash()
    updated_dt = datetime.fromisoformat(get_last_updated_iso()).astimezone(_KST)
    updated_label = updated_dt.strftime("%Y-%m-%d %H:%M")
    return f"지금 버전은 커밋 {commit}이에요!! 마지막 업데이트는 {updated_label}(KST)이에요!!"


async def _handle_s_hammie_runtime(args: list[str]) -> str:
    sleep_label = f"{SLEEP_START.hour:02d}:{SLEEP_START.minute:02d}"
    wake_label = f"{WAKE_TIME.hour:02d}:{WAKE_TIME.minute:02d}"
    call_start_label = f"{WINDOW_START.hour:02d}:{WINDOW_START.minute:02d}"
    call_end_label = f"{WINDOW_END.hour:02d}:{WINDOW_END.minute:02d}"
    return (
        f"햄미는 {wake_label}~{sleep_label}에 활동해요!! 그 시간 외엔 완전히 잠들어서 아무 반응도 안 해요!!\n"
        f"호출 이벤트는 {call_start_label}~{call_end_label} 사이에만 발생할 수 있어요!!"
    )


async def _handle_c(args: list[str]) -> str:
    return "\n".join(f"{spec.name} {spec.params}" for spec in _COMMAND_LIST)


async def _handle_c_help(args: list[str]) -> str:
    return "\n".join(f"{spec.name} {spec.params} - {spec.description}" for spec in _COMMAND_LIST)


async def _handle_c_no_parameter(args: list[str]) -> str:
    return "\n".join(spec.name for spec in _COMMAND_LIST)


_Handler = Callable[[list[str]], Awaitable[Union[str, tuple[str, discord.Embed]]]]


@dataclass(frozen=True)
class _CommandSpec:
    name: str
    arity: int  # boolean을 제외한 필수 인자 개수
    params: str
    description: str
    handler: _Handler


_COMMAND_LIST = (
    _CommandSpec("la-up", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도 +amount (일일 상한 미적용)", _handle_la_up),
    _CommandSpec("la-down", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도 -amount", _handle_la_down),
    _CommandSpec("la-set", 2, "{user_id} {amount} {boolean}", "해당 유저 호감도를 amount로 절대값 설정", _handle_la_set),
    _CommandSpec("la-reset", 1, "{user_id} {boolean}", "해당 유저 호감도를 초기값(10)으로 리셋", _handle_la_reset),
    _CommandSpec("tc-up", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수 +amount (0~당일 상한 클램프)", _handle_tc_up),
    _CommandSpec("tc-down", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수 -amount (0 미만 방지)", _handle_tc_down),
    _CommandSpec("tc-set", 2, "{user_id} {amount} {boolean}", "해당 유저 오늘 대화 횟수를 amount로 절대값 설정 (0~당일 상한 클램프)", _handle_tc_set),
    _CommandSpec("tc-reset", 1, "{user_id} {boolean}", "해당 유저 오늘 대화 횟수를 0으로 리셋", _handle_tc_reset),
    _CommandSpec("s-event-all", 0, "{boolean}", "오늘 부름 이벤트 전부와 결과 표시", _handle_s_event_all),
    _CommandSpec("s-event-next", 0, "{boolean}", "다음으로 남은 부름 이벤트 시각 표시", _handle_s_event_next),
    _CommandSpec("s-event-last", 0, "{boolean}", "가장 최근에 지난 부름 이벤트 시각 표시", _handle_s_event_last),
    _CommandSpec("s-user-stats", 1, "{user_id} {boolean}", "해당 유저의 일반 정보(=/내정보) 표시", _handle_s_user_stats),
    _CommandSpec("s-db-list", 0, "{boolean}", "등록된 테이블 이름 전부 표시", _handle_s_db_list),
    _CommandSpec("s-db", 2, "{name} {amount} {boolean}", "해당 테이블 최근 amount개 행 표시", _handle_s_db),
    _CommandSpec("g-call-event", 1, "{time} {boolean}", "time분 뒤에 호출 이벤트 1개를 수동 생성(최소 간격 30분 준수)", _handle_g_call_event),
    _CommandSpec("r-call-event", 0, "{boolean}", "가장 가까운(아직 시작 안 한) 호출 이벤트 삭제", _handle_r_call_event),
    _CommandSpec("r-call-event-all", 0, "{boolean}", "아직 시작 안 한 호출 이벤트 전부 삭제", _handle_r_call_event_all),
    _CommandSpec("s-version", 0, "{boolean}", "현재 버전(커밋)과 마지막 업데이트 일시 표시", _handle_s_version),
    _CommandSpec("s-hammie-runtime", 0, "{boolean}", "햄미 활동 시간 및 이벤트 발생 가능 시간 표시", _handle_s_hammie_runtime),
    _CommandSpec("c", 0, "{boolean}", "모든 명령어를 매개변수 포함해서 나열", _handle_c),
    _CommandSpec("c-help", 0, "{boolean}", "모든 명령어를 설명과 함께 나열", _handle_c_help),
    _CommandSpec("c-no-parameter", 0, "{boolean}", "모든 명령어를 이름만 나열", _handle_c_no_parameter),
)

_COMMANDS = {spec.name: spec for spec in _COMMAND_LIST}


_TRUE_TOKENS = ("true",)
_FALSE_TOKENS = ("false",)


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

    try:
        if dm:
            await message.author.send(**kwargs)
        else:
            await message.reply(**kwargs)
    except discord.HTTPException:
        logging.exception("Failed to send admin console response")


async def handle(message: discord.Message) -> None:
    """`주인님-가라사대 ...` 접두어로 시작한 메시지를 처리한다 (관리자 콘솔, §13-F).

    LLM/OpenAI API 호출 없이 순수 문자열 매칭 + DB 조작으로만 처리한다.
    관리자가 아니면 명령을 실행하지 않고 깨물기 + 호감도 -1로 응징한다.
    """
    if message.author.id != ADMIN_USER_ID:
        await ensure_user(message.author.id)
        result = await add_affection(message.author.id, -1)
        notice = format_affection_notice(result["applied_amount"], result["new_affection"])
        await _send(message, False, f"{_ABUSE_RESPONSE}{notice}")
        return

    tokens = message.content[len(_PREFIX) :].strip().split()
    if not tokens:
        await _send(message, False, "명령어를 같이 적어주세요!! 예: 주인님-가라사대 c true")
        return

    spec = _COMMANDS.get(tokens[0])
    if spec is None:
        await _send(message, False, f"모르는 명령어예요!! ({tokens[0]})")
        return

    dm, remaining_args = _extract_boolean(tokens[1:], spec.arity)
    try:
        response = await spec.handler(remaining_args)
    except _AdminError as e:
        response = str(e)

    await _send(message, dm, response)
