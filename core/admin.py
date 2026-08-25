import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Union

import discord

from command.info.info import handle as info_handle
from config import ADMIN_USER_ID
from db.admin import (
    KNOWN_TABLES,
    dump_table,
    get_last_event,
    get_next_event,
    get_today_events,
    set_affection,
)
from db.affection import add_affection, add_affection_uncapped, format_affection_notice
from db.users import ensure_user, get_user

_KST = timezone(timedelta(hours=9))
_PREFIX = "주인님-가라사대 "
_INITIAL_AFFECTION = 10
_ABUSE_RESPONSE = "넌 주인이 아니자나!! (콱)"


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


async def _require_registered(user_id: int) -> None:
    if await get_user(user_id) is None:
        raise _AdminError(f"등록 안 된 유저야: {user_id}")


async def _handle_la_up(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la-up {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    await _require_registered(user_id)
    new_affection = await add_affection_uncapped(user_id, amount, "admin_la_up")
    return f"{user_id} 호감도 +{amount} 적용 완료 (현재 {new_affection})"


async def _handle_la_down(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la-down {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    await _require_registered(user_id)
    new_affection = await add_affection_uncapped(user_id, -amount, "admin_la_down")
    return f"{user_id} 호감도 -{amount} 적용 완료 (현재 {new_affection})"


async def _handle_la_set(args: list[str]) -> str:
    if len(args) != 2:
        raise _AdminError("사용법: la-set {user_id} {amount} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    amount = _parse_int(args[1], "amount")
    await _require_registered(user_id)
    new_affection = await set_affection(user_id, amount)
    return f"{user_id} 호감도를 {amount}로 설정 완료 (현재 {new_affection})"


async def _handle_la_reset(args: list[str]) -> str:
    if len(args) != 1:
        raise _AdminError("사용법: la-reset {user_id} {boolean}")
    user_id = _parse_int(args[0], "user_id")
    await _require_registered(user_id)
    await set_affection(user_id, _INITIAL_AFFECTION)
    return f"{user_id} 호감도를 초기값({_INITIAL_AFFECTION})으로 리셋 완료"


async def _handle_s_event_all(args: list[str]) -> str:
    events = await get_today_events()
    if not events:
        return "오늘 등록된 이벤트가 없어."
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
    return _format_event_time(event["scheduled_at"]) if event else "오늘 남은 이벤트가 없어."


async def _handle_s_event_last(args: list[str]) -> str:
    event = await get_last_event()
    return _format_event_time(event["scheduled_at"]) if event else "아직 지난 이벤트가 없어."


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
        raise _AdminError(f"모르는 테이블이야: {name} (가능: {', '.join(KNOWN_TABLES)})")
    rows = await dump_table(name, amount)
    return "\n".join(str(row) for row in rows) if rows else f"{name}: 데이터 없음"


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
    params: str
    description: str
    handler: _Handler


_COMMAND_LIST = (
    _CommandSpec("la-up", "{user_id} {amount} {boolean}", "해당 유저 호감도 +amount (일일 상한 미적용)", _handle_la_up),
    _CommandSpec("la-down", "{user_id} {amount} {boolean}", "해당 유저 호감도 -amount", _handle_la_down),
    _CommandSpec("la-set", "{user_id} {amount} {boolean}", "해당 유저 호감도를 amount로 절대값 설정", _handle_la_set),
    _CommandSpec("la-reset", "{user_id} {boolean}", "해당 유저 호감도를 초기값(10)으로 리셋", _handle_la_reset),
    _CommandSpec("s-event-all", "{boolean}", "오늘 부름 이벤트 전부와 결과 표시", _handle_s_event_all),
    _CommandSpec("s-event-next", "{boolean}", "다음으로 남은 부름 이벤트 시각 표시", _handle_s_event_next),
    _CommandSpec("s-event-last", "{boolean}", "가장 최근에 지난 부름 이벤트 시각 표시", _handle_s_event_last),
    _CommandSpec("s-user-stats", "{user_id} {boolean}", "해당 유저의 일반 정보(=/내정보) 표시", _handle_s_user_stats),
    _CommandSpec("s-db-list", "{boolean}", "등록된 테이블 이름 전부 표시", _handle_s_db_list),
    _CommandSpec("s-db", "{name} {amount} {boolean}", "해당 테이블 최근 amount개 행 표시", _handle_s_db),
    _CommandSpec("c", "{boolean}", "모든 명령어를 매개변수 포함해서 나열", _handle_c),
    _CommandSpec("c-help", "{boolean}", "모든 명령어를 설명과 함께 나열", _handle_c_help),
    _CommandSpec("c-no-parameter", "{boolean}", "모든 명령어를 이름만 나열", _handle_c_no_parameter),
)

_COMMANDS = {spec.name: spec for spec in _COMMAND_LIST}


def _extract_boolean(tokens: list[str]) -> tuple[bool, list[str]]:
    if not tokens:
        return False, []
    *rest, last = tokens
    return last.strip().lower() in ("true", "1"), rest


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
        await _send(message, False, "명령어를 같이 적어줘. 예: 주인님-가라사대 c true")
        return

    spec = _COMMANDS.get(tokens[0])
    if spec is None:
        await _send(message, False, f"모르는 명령어야: {tokens[0]}")
        return

    dm, remaining_args = _extract_boolean(tokens[1:])
    try:
        response = await spec.handler(remaining_args)
    except _AdminError as e:
        response = str(e)

    await _send(message, dm, response)
