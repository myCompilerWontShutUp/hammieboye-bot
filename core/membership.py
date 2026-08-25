import logging
import random
from datetime import datetime, timedelta, timezone

import discord

from core.scheduler import KST
from db.guild_channels import set_last_channel
from db.users import delete_user, ensure_user, get_user, set_consent
from db.withdrawals import get_withdrawal, record_withdrawal

# 탈퇴 후 재가입 금지 기간 (사용자 확정: 24시간)
_COOLDOWN = timedelta(hours=24)

_ALREADY_JOINED_LINES = (
    "어라?? 너는 이미 나랑 친구잖아!! 또 가입 안 해도 대!! _(웃음)_",
    "이미 가입했는데?? 우리 벌써 친하잖아!! _(뿌듯)_",
    "엥, 넌 이미 등록됐어!! `/내정보`로 확인해봐!! _(갸웃)_",
    "또 가입하려구?? 이미 친구인데!! _(장난)_",
    "이미 나랑 친해졌잖아!! 걱정 마!! _(안심)_",
    "가입은 벌써 했는데?? 잊어버린 거야?? _(웃김)_",
    "너랑은 이미 친구야!! 중복 가입은 안 대!! _(단호)_",
    "어어, 넌 이미 등록된 칭구야!! _(반가움)_",
    "이미 가입 완료된 상태야!! 걱정하지 마!! _(안심)_",
    "또 가입? 우리 이미 친하다니깐!! _(웃음)_",
    "가입은 한 번이면 충분해!! 이미 했잖아!! _(단호)_",
    "너는 이미 명단에 이써!! _(자랑)_",
    "우리 벌써 친구인데 까먹었어?? _(놀림)_",
    "이미 가입돼 이써서 또 할 필요 없어!! _(설명)_",
    "엥?? 넌 이미 햄미 친구 목록에 있어!! _(뿌듯)_",
    "가입 완료 상태야!! 다시 안 해도 대!! _(단호)_",
    "벌써 친해졌잖아!! 새삼스럽게 왜 그래!! _(웃김)_",
    "너랑 나는 이미 등록된 사이야!! _(따뜻)_",
    "이미 가입했으니까 걱정 말고 놀자!! _(신남)_",
    "다시 가입할 필요 없어!! 이미 친구니까!! _(든든)_",
)

# {withdrawn}/{eligible}은 각각 탈퇴 시각/재가입 가능 시각(KST)으로 채워진다.
_COOLDOWN_TEMPLATE_LINES = (
    "너 {withdrawn}에 탈퇴했었잖아!! {eligible}부터 다시 가입할 수 이써!! _(안내)_",
    "음... {withdrawn}에 떠났었네?? {eligible} 이후에 다시 와줘!! _(아쉬움)_",
    "잠깐, {withdrawn}에 탈퇴 기록이 이써!! {eligible}부터 재가입 가능해!! _(설명)_",
    "탈퇴한 지 얼마 안 됐어!! ({withdrawn} 탈퇴, {eligible}부터 다시 가입 가능) _(진지)_",
    "{withdrawn}에 헤어졌었지... {eligible}에 다시 만나자!! _(그리움)_",
    "아직은 안 대!! {withdrawn}에 탈퇴했으니까 {eligible}부터 다시 가입해줘!! _(단호)_",
    "너 {withdrawn}에 나갔었어!! {eligible} 지나면 다시 가입할 수 이써!! _(기다림)_",
    "조금만 기다려줘!! {withdrawn} 탈퇴 → {eligible}부터 재가입 가능이야!! _(부탁)_",
    "탈퇴 기록이 남아 이써!! ({withdrawn}) {eligible}부터 다시 와줘!! _(안내)_",
    "아직 24시간이 안 지났어!! {withdrawn}에 탈퇴, {eligible}부터 가능!! _(설명)_",
    "{withdrawn}에 떠났던 거 기억나!! {eligible}에 다시 가입해줘!! _(서운)_",
    "지금은 안 대!! {withdrawn} 탈퇴니까 {eligible}부터 다시 시도해줘!! _(진지)_",
    "잠깐 헤어진 사이잖아!! {withdrawn} 탈퇴, {eligible}에 재회하자!! _(기대)_",
    "쿨타임 중이야!! {withdrawn}에 탈퇴했고 {eligible}부터 풀려!! _(안내)_",
    "{withdrawn}에 나갔었지?? {eligible}까지 조금만 기다려줘!! _(부탁)_",
    "재가입은 {eligible}부터야!! ({withdrawn}에 탈퇴했었어) _(단호)_",
    "아직 시간이 덜 지났어!! {withdrawn} 탈퇴 → {eligible} 재가입 가능!! _(설명)_",
    "{withdrawn}에 헤어졌으니 {eligible}까지 기다려줘!! _(아쉬움)_",
    "우리 다시 만나려면 {eligible}까지 기다려야 해!! ({withdrawn} 탈퇴) _(그리움)_",
    "탈퇴한 지 하루가 안 지났어!! {withdrawn} → {eligible}부터 가능!! _(안내)_",
)

_JOIN_SUCCESS_LINES = (
    "조아!! 이제부터 우리 친구야!! 잘 부탁해!! _(신남)_",
    "야호!! 가입 완료!! 앞으로 친하게 지내자!! _(환희)_",
    "반가워!! 이제 너랑 진짜 친구다!! _(방긋)_",
    "좋았어!! 이제부터 잘 지내보자!! _(설렘)_",
    "가입 성공!! 햄미랑 즐거운 시간 보내자!! _(들뜸)_",
    "야호, 새 친구 생겼다!! 잘 부탁해!! _(기쁨)_",
    "환영해!! 이제 진짜 친구 사이야!! _(따뜻)_",
    "됐다!! 이제 우리 친구니까 자주 놀자!! _(신남)_",
    "가입 완료!! 앞으로 잘 지내보자구!! _(뿌듯)_",
    "좋아좋아!! 이제 너도 햄미 친구야!! _(행복)_",
    "야호!! 드디어 친구가 됐어!! _(환호)_",
    "환영해!! 앞으로 자주 얘기하자!! _(반가움)_",
    "가입해줘서 고마워!! 잘 부탁해!! _(감사)_",
    "됐다됐다!! 이제부터 친구 사이야!! _(들뜸)_",
    "좋았어!! 우리 이제 친해진 거야!! _(설렘)_",
    "환영합니다!! 앞으로 잘 지내봐요!! _(공손)_",
    "야호!! 가입 완료됐어!! 놀러 와줘!! _(신남)_",
    "이제 진짜 친구다!! 반가워!! _(행복)_",
    "가입 성공!! 우리 사이 시작이야!! _(기대)_",
    "잘 왔어!! 이제부터 친하게 지내자!! _(따뜻)_",
)

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

_COLLECTION_NOTICE = (
    "가입하면 햄미가 저장하는 정보는 이래!!\n"
    "- 채팅 횟수\n"
    "- 도와준 횟수\n"
    "- 호감도\n"
    "- 가입(동의) 여부와 날짜\n\n"
    "서버가 달라도 같은 사람이면 하나로 합쳐서 관리해. 가입하고 싶으면 `/가입`이라고 쳐줘!!"
)

_LEAVE_CONFIRM_PROMPT = (
    "정말 탈퇴할 거야?? 탈퇴하면 호감도·채팅 기록 등 모든 데이터가 즉시 삭제되고, "
    "24시간 동안은 다시 가입할 수 없어. 아래 버튼을 누르면 탈퇴가 진행돼... _(훌쩍)_"
)
_LEAVE_TIMEOUT_MESSAGE = "시간 초과됐어!! 탈퇴가 취소됐어. _(안도)_"
_NOT_JOINED_LEAVE_MESSAGE = "어라, 아직 가입도 안 했잖아!! 탈퇴할 게 없어~ _(갸웃)_"
_WRONG_USER_MESSAGE = "이건 다른 사람의 탈퇴 확인이에요!! _(단호)_"


def _format_kst(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")


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


async def _touch_channel(interaction: discord.Interaction) -> None:
    if interaction.guild is not None and interaction.channel_id is not None:
        await set_last_channel(interaction.guild.id, interaction.channel_id)


async def handle_join(interaction: discord.Interaction) -> None:
    if interaction.user.bot:  # 실제 사용자만 응답 대상 (확인사항 2)
        return
    await _touch_channel(interaction)
    user_id = interaction.user.id

    existing = await get_user(user_id)
    if existing is not None and existing["consent_given"]:
        await interaction.response.send_message(
            random.choice(_ALREADY_JOINED_LINES), ephemeral=True
        )
        return

    withdrawal = await get_withdrawal(user_id)
    if withdrawal is not None:
        withdrawn_at = datetime.fromisoformat(withdrawal["withdrawn_at"])
        eligible_at = withdrawn_at + _COOLDOWN
        if datetime.now(timezone.utc) < eligible_at:
            message = random.choice(_COOLDOWN_TEMPLATE_LINES).format(
                withdrawn=_format_kst(withdrawn_at), eligible=_format_kst(eligible_at)
            )
            await interaction.response.send_message(message, ephemeral=True)
            return

    await ensure_user(user_id)
    await set_consent(user_id)
    await interaction.response.send_message(random.choice(_JOIN_SUCCESS_LINES), ephemeral=True)


async def handle_join_info(interaction: discord.Interaction) -> None:
    if interaction.user.bot:  # 실제 사용자만 응답 대상 (확인사항 2)
        return
    await _touch_channel(interaction)
    await interaction.response.send_message(_COLLECTION_NOTICE, ephemeral=True)


async def handle_leave(interaction: discord.Interaction) -> None:
    if interaction.user.bot:  # 실제 사용자만 응답 대상 (확인사항 2)
        return
    await _touch_channel(interaction)

    user = await get_user(interaction.user.id)
    if user is None or not user["consent_given"]:
        await interaction.response.send_message(_NOT_JOINED_LEAVE_MESSAGE, ephemeral=True)
        return

    view = _WithdrawView(interaction.user.id)
    await interaction.response.send_message(_LEAVE_CONFIRM_PROMPT, view=view, ephemeral=True)
    view.interaction = interaction
