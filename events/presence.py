import logging

import discord

_client: discord.Client | None = None

_SLEEP_STATUS_TEXT = "쿨쿨... 잠자는 중 🌙"
_AWAKE_STATUS_TEXT = "페트병 흔드는 중 🐹"
_DND_STATUS_TEXT = "누가 깨웠어... 방해금지 🚫"
# 2026-09-09 신규 — 헬프 미 이벤트/디저트 타임이 활성 상태인 동안 상태 메시지를
# 바꾼다(events/help_me_event.py::_post_one/_announce_timeout·handle_potential_response,
# events/dessert_time.py::broadcast_open/broadcast_close가 각각 진입/해제 시점에
# 호출). 두 이벤트는 스케줄링 단계에서 서로 겹치지 않게 보장되므로(§3-2 참고) 항상
# 최대 하나만 활성 상태라, 별도 카운터 없이 단순 진입/복귀(wake_up())만으로 충분하다.
_HELP_REQUEST_STATUS_TEXT = "도움 요청 중 🆘"
_SNACK_REQUEST_STATUS_TEXT = "간식 요청 중 🍪"


def init(client: discord.Client) -> None:
    global _client
    _client = client


async def enter_sleep() -> None:
    if _client is None:
        return
    try:
        await _client.change_presence(
            status=discord.Status.idle,
            activity=discord.CustomActivity(name=_SLEEP_STATUS_TEXT),
        )
    except discord.HTTPException:
        logging.exception("Failed to set sleep presence")


async def enter_dnd() -> None:
    """취침 중 맨션 깨움 이벤트가 발생했을 때 커스텀 상태를 방해금지로 바꾼다.

    Discord 상태(presence)는 봇 계정 전체에 하나뿐이라 서버별로는 못 나눈다.
    이벤트 자체(맨션 카운트/1회 제한)는 서버마다 독립이지만, 이 상태 표시는
    다음 기상(06:30)까지 전역으로 유지된다.
    """
    if _client is None:
        return
    try:
        await _client.change_presence(
            status=discord.Status.dnd,
            activity=discord.CustomActivity(name=_DND_STATUS_TEXT),
        )
    except discord.HTTPException:
        logging.exception("Failed to set DND presence")


async def wake_up() -> None:
    if _client is None:
        return
    try:
        await _client.change_presence(
            status=discord.Status.online,
            activity=discord.CustomActivity(name=_AWAKE_STATUS_TEXT),
        )
    except discord.HTTPException:
        logging.exception("Failed to set awake presence")


async def enter_help_request() -> None:
    """헬프 미 이벤트가 방송된 순간부터(클레임 또는 10분 무응답 만료로 끝날 때까지)."""
    if _client is None:
        return
    try:
        await _client.change_presence(
            status=discord.Status.online,
            activity=discord.CustomActivity(name=_HELP_REQUEST_STATUS_TEXT),
        )
    except discord.HTTPException:
        logging.exception("Failed to set help-request presence")


async def enter_snack_request() -> None:
    """디저트 타임 슬롯이 열려있는 동안(broadcast_open ~ broadcast_close)."""
    if _client is None:
        return
    try:
        await _client.change_presence(
            status=discord.Status.online,
            activity=discord.CustomActivity(name=_SNACK_REQUEST_STATUS_TEXT),
        )
    except discord.HTTPException:
        logging.exception("Failed to set snack-request presence")
