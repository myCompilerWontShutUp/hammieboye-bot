import discord
from discord import app_commands

from command.info.info import handle as info_handle
from command.plastic.plastic import handle as plastic_handle
from core import membership, onboarding, ranking
from db.daily_stats import increment_messages_today
from db.guild_channels import set_last_channel
from db.users import ensure_user, increment_chat_count


async def _prepare(interaction: discord.Interaction) -> bool:
    """동의 게이트 + 채팅 횟수 집계. 명령어 실행을 진행해도 되면 True."""
    if interaction.guild is not None and interaction.channel_id is not None:
        await set_last_channel(interaction.guild.id, interaction.channel_id)

    user = await ensure_user(interaction.user.id)
    if not user["consent_given"]:
        # 자연어 경로(개인화 불가)와 경험을 통일하기 위해 공개로 응답한다 (사용자 확정).
        await interaction.response.send_message(onboarding.random_guide())
        return False

    await increment_chat_count(interaction.user.id)
    await increment_messages_today(interaction.user.id)
    return True


def register(tree: app_commands.CommandTree) -> None:
    @tree.command(name="가입", description="햄미와 친해지기 위해 가입한다")
    async def join_command(interaction: discord.Interaction) -> None:
        await membership.handle_join(interaction)

    @tree.command(name="가입-수집항목", description="가입 시 수집되는 정보를 자세히 안내한다")
    async def join_info_command(interaction: discord.Interaction) -> None:
        await membership.handle_join_info(interaction)

    @tree.command(name="탈퇴", description="햄미와의 관계를 정리하고 탈퇴한다")
    async def leave_command(interaction: discord.Interaction) -> None:
        await membership.handle_leave(interaction)

    @tree.command(name="페트병", description="페트병 던지기 놀이를 한다")
    async def plastic_command(interaction: discord.Interaction) -> None:
        if not await _prepare(interaction):
            return
        text = await plastic_handle(interaction.user.id)
        await interaction.response.send_message(text)

    @tree.command(name="내정보", description="내 호감도, 채팅 횟수, 도와준 횟수 등 정보를 확인한다")
    async def info_command(interaction: discord.Interaction) -> None:
        if not await _prepare(interaction):
            return
        text, embed = await info_handle(interaction.user.id)
        await interaction.response.send_message(content=text, embed=embed)

    @tree.command(name="랭킹", description="호감도 기준 상위 5명 순위를 확인한다")
    async def ranking_command(interaction: discord.Interaction) -> None:
        if not await _prepare(interaction):
            return
        text, embed = await ranking.build_embed()
        await interaction.response.send_message(content=text, embed=embed)
