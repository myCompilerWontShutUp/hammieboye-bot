import discord

from db.guild_channels import set_last_channel

# 시스템 형태 메시지(호감도/대화 횟수 등)를 embed로 보일 때 쓰는 시그니처 컬러 (연주황색).
EMBED_COLOR = 0xFFCC99


def normalize(text: str) -> str:
    return text.replace(" ", "").strip().lower()


async def touch_channel(interaction: discord.Interaction) -> None:
    """부름/취침/아침 인사 이벤트가 어느 채널에 올릴지는 유저가 봇을 실제로 부른 채널
    기준으로 정하므로, 여러 슬래시 커맨드 핸들러가 동일하게 이 갱신을 필요로 한다."""
    if interaction.guild is not None and interaction.channel_id is not None:
        await set_last_channel(interaction.guild.id, interaction.channel_id)
