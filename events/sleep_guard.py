import discord

from events.scheduler import is_sleep_time_for

# 취침 시간대(00:00~06:30)에 계정 관리형 커맨드(/가입 등)를 막는 고정 문구.
SLEEP_REPLY = "Zzzzz... _(쿨쿨)_ _(근처에 메모가 하나 놓여있다.)_"

# /내정보·/니정보(2026-09-06 통합) 전용 — 수첩을 펼쳐 읽어본다는 능동적인 컨셉.
SLEEP_REPLY_NOTEBOOK = "Zzzzz... _(쿨쿨)_ _(햄미 옆에 놓인 수첩을 슬쩍 펼쳐 읽어본다.)_"

# /페트병 전용 — 놀이형 커맨드라 메모/수첩 컨셉과 안 어울려서 분리했다.
SLEEP_REPLY_PLASTIC = "(자고 있어서 반응을 보이지 않는다)"


async def guard(interaction: discord.Interaction, *, silent: bool, message: str = SLEEP_REPLY) -> bool:
    """취침 시간대에 슬래시 커맨드를 가로챈다.

    silent=True면 완전히 무시한다(응답 없음 — 자연어와 동일하게 "동작하지 않는다").
    silent=False면 고정 문구(기본값 `SLEEP_REPLY`, 커맨드별로 `message`를 따로 줄 수
    있다)로 답하고 실행을 막는다. 계속 진행해도 되면 True를 반환한다.
    """
    if not is_sleep_time_for(interaction.channel_id):
        return True
    if not silent:
        await interaction.response.send_message(message)
    return False


def wrap_text_if_asleep(channel_id: int | None, text: str, *, notebook: bool = False) -> str:
    """임베드가 있는 시스템 커맨드는 취침 중에도 실제로 실행해서 임베드는 그대로 붙이고
    응답 텍스트만 이 고정 문구로 바꾼다("메모/수첩에 적힌 내용이 바로 그 임베드").
    notebook=True면(/내정보·/니정보) `SLEEP_REPLY_NOTEBOOK`을, 그 외엔 `SLEEP_REPLY`를 쓴다."""
    if is_sleep_time_for(channel_id):
        return SLEEP_REPLY_NOTEBOOK if notebook else SLEEP_REPLY
    return text
