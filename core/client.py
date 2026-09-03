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


def create_tree(client: discord.Client) -> app_commands.CommandTree:
    return _HammieCommandTree(client)
