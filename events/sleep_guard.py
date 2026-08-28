import discord

from events.scheduler import is_sleep_time_for

# 취침 시간대(00:00~06:30)에 임베드가 없는 "놀이형" 슬래시 커맨드(/페트병)나 정보 조회를
# 아예 막는 계정 관리형 커맨드(/가입 등)에 답하는 고정 문구 — 자고 있는 컨셉이라 매번
# 다른 말을 지어낼 상황이 아니므로 단 하나만 쓴다(20개 만들지 않음, 사용자 확정). "괄호
# 행동"도 햄미 자신의 행동이 아니라 "근처에 메모가 놓여있다"는 컨셉으로 대체한다.
SLEEP_REPLY = "Zzzzz... _(쿨쿨)_ _(근처에 메모가 하나 놓여있다.)_"

# 2026-08-27: /소개(다른 사람 조회)는 /내정보·/랭킹과 컨셉을 분리한다 — "메모가 놓여있다"는
# 수동적 표현 대신, "햄미 옆에 있는 수첩을 슬쩍 펼쳐 읽어본다"는 능동적인 느낌으로(사용자 확정).
SLEEP_REPLY_NOTEBOOK = "Zzzzz... _(쿨쿨)_ _(햄미 옆에 놓인 수첩을 슬쩍 펼쳐 읽어본다.)_"

# /페트병 전용(사용자 확정, 2026-08-29) — 놀이형 커맨드라 메모/수첩 컨셉과 안 어울려서,
# 그냥 자느라 반응이 없다는 걸 그대로 알려주는 문구로 분리했다.
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
    """임베드가 있는 시스템 커맨드(/내정보, /니정보, /랭킹)는 취침 중에도 실제로 실행해서
    임베드는 그대로 붙이고, 응답 텍스트만 이 고정 문구로 바꾼다 — "메모/수첩에 적힌 내용이
    바로 그 임베드"라는 컨셉(사용자 확정). 임베드 자체는 건드리지 않는다.

    notebook=True면(/니정보·/니업적 전용) `SLEEP_REPLY_NOTEBOOK`을, 그 외(/내정보·/내업적·
    /랭킹)엔 기존 `SLEEP_REPLY`(메모)를 쓴다.
    """
    if is_sleep_time_for(channel_id):
        return SLEEP_REPLY_NOTEBOOK if notebook else SLEEP_REPLY
    return text
