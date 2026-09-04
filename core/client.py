import logging
import random

import discord
from discord import app_commands

from db.guild_channels import get_designated_channel
from events.scheduler import TEST_GUILD_ID


def create_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    # /소개(core/intro.py)가 서버 전체 멤버를 별명으로 검색해야 해서 필요하다. 이건
    # privileged intent라 코드에서 켜는 것만으로는 부족하고, Discord 개발자 포털의 봇 설정에서
    # "Server Members Intent"도 별도로 켜야 한다 — 안 켜면 로그인 자체가 실패한다.
    intents.members = True
    return discord.Client(intents=intents)


# "ds here"로 지정 채널이 설정된 서버에서도 신규 유저가 지정 채널을 몰라 온보딩 자체가
# 막히는 걸 방지하기 위해 예외로 항상 허용한다(core/slash_commands.py의 실제 등록 이름과
# 정확히 일치해야 함).
_ONBOARDING_COMMAND_NAMES = {"가입", "가입-수집항목", "탈퇴"}

# 슬래시 커맨드 실행 중 예상 못한 예외(DB 오류, 네트워크 오류 등)가 나면 인터랙션이
# 그냥 멈춘 것처럼 보이는 대신 이 문구로 답한다 — 트레이스백은 로그에만 남고 사용자에게는
# 절대 노출되지 않는다.
_UNEXPECTED_ERROR_LINES = (
    "어라, 방금 뭔가 문제가 생겼나 봐!! 잠시 후 다시 해볼래?? _(당황)_",
    "이런, 갑자기 삐끗했어!! 다시 시도해줄래?? _(허둥)_",
    "잠깐, 뭔가 꼬여버려써!! 조금 있다 다시 해줘!! _(당황)_",
    "앗, 이번엔 잘 안 됐나 봐!! 다시 한번 해볼래?? _(미안)_",
    "어어, 갑자기 멈칫했어!! 다시 시도해줘!! _(당황)_",
    "미안, 지금 뭔가 안 풀려써!! 잠시 후 다시!! _(허둥)_",
    "으엑, 처리하다 삐끗했어!! 다시 해볼래?? _(당황)_",
    "이런, 뭔가 걸렸나 봐!! 조금 이따 다시 해줘!! _(미안)_",
    "잠깐만, 뭔가 이상해!! 다시 시도해볼래?? _(당황)_",
    "어라라, 지금은 잘 안 되네!! 다시 해줘!! _(허둥)_",
    "미안해, 방금 문제가 생겼어!! 다시 한번!! _(미안)_",
    "이거 뭔가 꼬였나 봐!! 잠시 후 다시 해줄래?? _(당황)_",
    "어이쿠, 처리 중에 삐끗해써!! 다시 시도해줘!! _(허둥)_",
    "지금은 뭔가 안 풀리네!! 조금 있다 다시!! _(미안)_",
    "앗차, 갑자기 문제가!! 다시 해볼래?? _(당황)_",
    "이런, 지금 상태가 이상해!! 다시 시도해줘!! _(허둥)_",
    "잠깐 뭔가 걸렸어!! 조금 이따 다시 해볼래?? _(미안)_",
    "어라, 처리가 안 됐나 봐!! 다시 한번 해줘!! _(당황)_",
    "미안, 방금 좀 헤맸어!! 다시 시도해줄래?? _(허둥)_",
    "이거 지금은 안 되네!! 잠시 후 다시 해줘!! _(미안)_",
)


class _HammieCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None or guild.id == TEST_GUILD_ID:
            return True
        if interaction.command is not None and interaction.command.name in _ONBOARDING_COMMAND_NAMES:
            return True
        designated = get_designated_channel(guild.id)
        if designated is None or interaction.channel_id == designated:
            return True
        await interaction.response.send_message(
            f"이 명령어는 <#{designated}>에서만 사용할 수 있어요!!", ephemeral=True
        )
        return False

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """슬래시 커맨드 콜백 안에서 처리 못 한 예외가 새어 나오면 여기로 모인다(discord.py
        기본 동작은 로그만 남기고 사용자에게는 아무 응답도 안 해서, 상호작용이 그냥 멈춘
        것처럼 보인다) — 트레이스백 대신 햄미 말투 안내로 마무리한다."""
        command_name = interaction.command.name if interaction.command else "?"
        logging.exception("Slash command '%s' raised an error", command_name, exc_info=error)

        message = random.choice(_UNEXPECTED_ERROR_LINES)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logging.exception("Failed to send fallback error message for '%s'", command_name)


def create_tree(client: discord.Client) -> app_commands.CommandTree:
    return _HammieCommandTree(client)
