import logging

import discord

_client: discord.Client | None = None

_SLEEP_STATUS_TEXT = "쿨쿨... 잠자는 중 🌙"
_AWAKE_STATUS_TEXT = "페트병 흔드는 중 🐹"
_DND_STATUS_TEXT = "누가 깨웠어... 방해금지 🚫"


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
