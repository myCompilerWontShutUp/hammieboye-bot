import logging
import random

import discord

from core.base import touch_channel
from db.users import delete_user, get_user
from db.withdrawals import record_withdrawal

_LEAVE_COMPLETE_LINES = (
    "그동안 즐거웠어... 잘 가!! 데이터는 다 지워졌어. _(서운)_",
    "안녕... 언젠가 다시 만나자!! 기록은 전부 삭제됐어. _(슬픔)_",
    "탈퇴 완료됐어!! 그동안 고마웠어... _(아쉬움)_",
    "이제 안녕이네... 잘 지내!! 데이터는 깨끗이 지웠어. _(눈물)_",
    "그동안 함께해줘서 고마워... 잘 가!! _(먹먹)_",
    "탈퇴됐어... 언제든 다시 와도 조아!! _(서운)_",
    "안녕히... 기록은 전부 사라졌어. 잘 지내!! _(슬픔)_",
    "잘 가!! 우리 추억은 여기까지네... _(아쉬움)_",
    "탈퇴 완료!! 그동안 즐거운 시간이었어. _(먹먹)_",
    "이별이네... 데이터는 다 지웠어. 잘 지내!! _(서운)_",
    "안녕... 다음에 또 만날 수 있으면 좋겠어. _(그리움)_",
    "잘 가!! 모든 기록이 삭제됐어. 고마웠어!! _(슬픔)_",
    "탈퇴됐어... 그동안 놀아줘서 고마웠어!! _(뭉클)_",
    "안녕히 가!! 언제든 다시 가입할 수 이써!! _(위로)_",
    "이제 헤어지는구나... 잘 지내!! _(아쉬움)_",
    "탈퇴 완료. 데이터는 전부 지워졌어... 잘 가!! _(먹먹)_",
    "그동안 고마웠어!! 안녕... _(서운)_",
    "잘 가!! 다시 오고 시프면 언제든 `/가입`해줘!! _(위로)_",
    "안녕... 우리 인연은 여기까지네. _(슬픔)_",
    "탈퇴됐어. 그동안 함께해서 행복했어!! 잘 가!! _(먹먹)_",
)

_LEAVE_CONFIRM_PROMPT = (
    "정말 탈퇴할 거야?? 탈퇴하면 호감도·채팅 기록 등 모든 데이터가 즉시 삭제되고, "
    "24시간 동안은 다시 가입할 수 없어. 아래 버튼을 누르면 탈퇴가 진행돼... _(훌쩍)_"
)
_LEAVE_TIMEOUT_MESSAGE = "시간 초과됐어!! 탈퇴가 취소됐어. _(안도)_"
_NOT_JOINED_LEAVE_MESSAGE = "어라, 아직 가입도 안 했잖아!! 탈퇴할 게 없어~ _(갸웃)_"
_WRONG_USER_MESSAGE = "이건 다른 사람의 탈퇴 확인이에요!! _(단호)_"


class _WithdrawView(discord.ui.View):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=30)
        self.user_id = user_id
        self.interaction: discord.Interaction | None = None

    async def on_timeout(self) -> None:
        if self.interaction is None:
            return
        try:
            await self.interaction.edit_original_response(content=_LEAVE_TIMEOUT_MESSAGE, view=None)
        except discord.HTTPException:
            logging.exception("Failed to edit withdraw prompt on timeout")

    @discord.ui.button(label="탈퇴하기", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(_WRONG_USER_MESSAGE, ephemeral=True)
            return

        self.stop()
        await delete_user(self.user_id)
        await record_withdrawal(self.user_id)
        await interaction.response.edit_message(
            content=random.choice(_LEAVE_COMPLETE_LINES), view=None
        )


async def handle(interaction: discord.Interaction) -> None:
    if interaction.user.bot:
        return
    # ephemeral 응답이라 취침 게이트 없이 24시간 동작한다.
    await interaction.response.defer(ephemeral=True)
    await touch_channel(interaction)

    user = await get_user(interaction.user.id)
    if user is None or not user["consent_given"]:
        await interaction.edit_original_response(content=_NOT_JOINED_LEAVE_MESSAGE)
        return

    view = _WithdrawView(interaction.user.id)
    await interaction.edit_original_response(content=_LEAVE_CONFIRM_PROMPT, view=view)
    view.interaction = interaction
