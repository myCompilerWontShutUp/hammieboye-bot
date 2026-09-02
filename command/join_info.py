import discord

from core.base import touch_channel

_COLLECTION_NOTICE = (
    "가입하면 햄미가 저장하는 정보는 이래!!\n"
    "- 채팅 횟수\n"
    "- 도와준 횟수\n"
    "- 호감도\n"
    "- 가입(동의) 여부와 날짜\n\n"
    "서버가 달라도 같은 사람이면 하나로 합쳐서 관리해. 가입하고 싶으면 `/가입`이라고 쳐줘!!"
)


async def handle(interaction: discord.Interaction) -> None:
    if interaction.user.bot:
        return
    # ephemeral 응답이라 취침 게이트 없이 24시간 동작한다.
    await interaction.response.defer(ephemeral=True)
    await touch_channel(interaction)
    await interaction.edit_original_response(content=_COLLECTION_NOTICE)
