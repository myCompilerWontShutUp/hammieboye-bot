import discord

from events.scheduler import is_sleep_time_for

# `wrap_text_if_asleep()`의 기본값 — 임베드가 있는 읽기 전용 "확인한다"류 명령어
# (/랭킹·/업적-리스트) 전용. 이 명령어들은 취침
# 중에도 실제로 실행돼 embed는 그대로 붙고 텍스트만 이 문구로 바뀐다 — "이미 적어둔
# 메모(=그 embed)를 보여준다"는 컨셉이라 별도 행동이 필요 없는 조회성 명령어에만 맞는다.
# 2026-09-10 舊 /내기-규칙·/도박-규칙이 폐지되고 `/봇정보-규칙`으로 통합되면서
# 이 기본값 대상에서 빠졌다 — `/봇정보-` 계열은 §5 원칙대로 취침 게이트 자체가
# 없다(항상 24시간 동작).
# 2026-09-08 舊 /자판기-리스트가 폐지되고 /자판기 하나로 합쳐지면서(간식/투자
# 목록 보기 + 구매를 한 명령어가 겸함) 이 기본값 대상에서 완전히 빠졌다 — /자판기는
# 원래도 `guard()`(완전 차단, SLEEP_REPLY_VENDING) 대상이었다.
SLEEP_REPLY = "Zzzzz... _(쿨쿨)_ _(근처에 메모가 하나 놓여있다.)_"

# /내정보·/니정보(2026-09-06 통합) 전용 — 수첩을 펼쳐 읽어본다는 능동적인 컨셉.
SLEEP_REPLY_NOTEBOOK = "Zzzzz... _(쿨쿨)_ _(햄미 옆에 놓인 수첩을 슬쩍 펼쳐 읽어본다.)_"

# /페트병 전용 — 놀이형 커맨드라 메모/수첩 컨셉과 안 어울려서 분리했다.
SLEEP_REPLY_PLASTIC = "(자고 있어서 반응을 보이지 않는다)"

# /도박 전용(2026-09-06) — /내기와 달리 취침 중에도 완전히 차단하지 않고 그대로 진행되게
# 바뀌었다("도박"이라는 컨셉상 몰래 하는 쪽이 더 자연스럽다는 판단). 명령어 자체는 막지
# 않고, 게임 선택 프롬프트의 안내 문구만 이 문구로 바꿔치기한다(wrap_text_if_asleep의
# override로 사용).
SLEEP_REPLY_GAMBLE = "Zzzzzz... _(햄미 몰래 해보자!!)_"

# 아래 3개는 `guard()`로 명령어 실행 자체를 완전히 막는 "행동형" 명령어 전용
# (2026-09-06) — 舊에는 이 셋 다 위 SLEEP_REPLY(메모 컨셉)를 그대로 썼는데, 아무
# embed도 안 뜨고 그냥 거절되는 상황이라 "메모가 놓여있다"는 설명이 안 맞았다(뭘 보라는
# 메모인지 알 수 없음). 각자 실제로 못 하는 이유에 맞춰 따로 뺐다.
SLEEP_REPLY_COIN = "(자고 있어서 못 가져오는 듯 하다)"
SLEEP_REPLY_VENDING = "(자고 있어서 자판기를 쓸 수 없는 듯 하다)"
SLEEP_REPLY_BET = "(자고 있어서 내기에 응할 수 없는 듯 하다)"


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


async def guard_sleep_only(interaction: discord.Interaction, *, message: str) -> bool:
    """`guard()`와 정반대 게이트 — 오직 취침 시간대(`is_sleep_time_for`)에만 실행을
    허용한다. `/암시장` 전용이다(2026-09-08 신규) — "햄미가 몰래 일어나 거래한다"는
    컨셉이라, 다른 모든 명령어와 반대로 **깨어있는 시간대에 차단**된다. 방해금지로
    기상이 늦춰진 날도 `is_sleep_time_for`가 이미 그 지연(`DELAYED_WAKE_TIME`)을
    반영하므로 이 함수는 별도 처리 없이 그대로 넘겨받아 정확하게 동작한다."""
    if is_sleep_time_for(interaction.channel_id):
        return True
    await interaction.response.send_message(message)
    return False


def wrap_text_if_asleep(
    channel_id: int | None, text: str, *, notebook: bool = False, override: str | None = None
) -> str:
    """임베드가 있는 시스템 커맨드는 취침 중에도 실제로 실행해서 임베드는 그대로 붙이고
    응답 텍스트만 이 고정 문구로 바꾼다("메모/수첩에 적힌 내용이 바로 그 임베드").
    override가 있으면 그 문구를 그대로 쓰고(/도박의 SLEEP_REPLY_GAMBLE 전용), 없으면
    notebook=True일 때(/내정보·/니정보) `SLEEP_REPLY_NOTEBOOK`을, 그 외엔 `SLEEP_REPLY`를
    쓴다."""
    if is_sleep_time_for(channel_id):
        if override is not None:
            return override
        return SLEEP_REPLY_NOTEBOOK if notebook else SLEEP_REPLY
    return text
