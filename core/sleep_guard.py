import discord

from core.scheduler import is_sleep_time

# 취침 시간대(00:00~06:30)에 "시스템형" 슬래시 커맨드(/내정보, /소개, /랭킹, /가입 등)에
# 답하는 고정 문구 — 자고 있는 컨셉이라 매번 다른 말을 지어낼 상황이 아니므로 단 하나만 쓴다
# (20개 만들지 않음, 사용자 확정). "괄호 행동"도 햄미 자신의 행동이 아니라 "근처에 메모가
# 놓여있다"는 컨셉으로 대체한다.
SLEEP_REPLY = "Zzzzz... _(쿨쿨)_ _(근처에 메모가 하나 놓여있다.)_"


async def guard(interaction: discord.Interaction, *, silent: bool) -> bool:
    """취침 시간대에 슬래시 커맨드를 가로챈다.

    silent=True(놀이형, 예: /페트병)면 완전히 무시한다(응답 없음 — 자연어와 동일하게
    "동작하지 않는다"). silent=False(정보/시스템형)면 고정 문구로 답하고 실행을 막는다.
    계속 진행해도 되면 True를 반환한다.
    """
    if not is_sleep_time():
        return True
    if not silent:
        await interaction.response.send_message(SLEEP_REPLY)
    return False
