import logging
import random

import discord

import achievements
from command.economy_common import (
    INSUFFICIENT_FUNDS_LINES,
    TIMEOUT_SECONDS,
    format_coin_notice,
    maybe_append_capacity_advice,
    reject_if_already_resolved,
    reject_if_wrong_user,
)
from db.achievements import award as award_achievement
from db.affection import format_affection_notice
from db.users import get_user
from db.wallet import add_coins, spend_coins

_INVALID_BET_RESPONSE = "배팅 금액은 동전 1개 이상이어야지!! _(갸웃)_"

_BET_TIMEOUT_LINES = (
    "너무 오래 기다려서 그냥 취소했어!! 배팅금은 돌려줄게!! _(안도)_",
    "시간 초과됐어!! 건 동전은 그대로 돌려줄게!! _(휴)_",
    "아무도 안 골라서 내기를 접었어!! 동전은 무사해!! _(정리)_",
    "이번 내기는 여기까지!! 배팅금은 돌려줬어!! _(끄덕)_",
    "시간이 다 됐어... 동전은 다시 넣어줄게!! _(아쉬움)_",
    "너무 뜸 들여써!! 배팅은 취소, 동전은 환불!! _(단호)_",
    "기다리다 지쳐써!! 그래도 동전은 돌려줄게!! _(피곤)_",
    "결정을 못 내려서 내기를 마감했어!! 동전은 그대로야!! _(정리)_",
    "시간 초과!! 손해는 없게 동전 돌려줬어!! _(안심)_",
    "너무 늦어서 그냥 없던 일로 했어!! 동전은 환불!! _(정리)_",
    "아무 반응이 없길래 취소했어!! 동전 걱정은 하지 마!! _(위로)_",
    "제한 시간이 끝나써!! 배팅금은 안전하게 돌려줬어!! _(끄덕)_",
    "이번엔 흐지부지됐네!! 그래도 동전은 그대로 남아있어!! _(안도)_",
    "기다림도 한계가 있지!! 동전은 돌려줬어!! _(단호)_",
    "시간 다 됐다구!! 배팅은 무효, 동전은 환불!! _(정리)_",
    "결국 아무도 안 눌러써!! 동전만 조용히 돌려줄게!! _(체념)_",
    "너무 늦게 왔나 봐!! 동전은 도로 챙겨줬어!! _(아쉬움)_",
    "내기는 취소됐지만 동전은 안 사라져!! _(안심)_",
    "시간 초과로 판이 접혔어!! 동전은 무사히 돌아왔어!! _(끄덕)_",
    "너무 오래 걸려써!! 그래도 손해는 없게 해줬어!! _(정리)_",
)

_ODD_EVEN_WIN_LINES = (
    "정답은 {actual}이었어!! 완전 딱 맞혔다!! _(환호)_",
    "우와, {actual}!! 정확히 맞혔어!! _(놀람)_",
    "짜잔, {actual}이었어!! 실력이야 운이야?? _(웃음)_",
    "{actual}!! 완벽하게 맞혔네!! _(감탄)_",
    "정답 {actual}!! 눈치가 좋은데?? _(칭찬)_",
    "오, {actual} 맞혔다!! 대단해!! _(박수)_",
    "{actual}이었어!! 딱 걸렸다, 정답!! _(신남)_",
    "역시!! {actual} 정확히 맞혔어!! _(뿌듯)_",
    "{actual}!! 완전 촉 좋은데?? _(감탄)_",
    "정답은 {actual}, 완벽하게 맞혔어!! _(환호)_",
    "우와아, {actual}!! 소름 돋았어!! _(놀람)_",
    "{actual} 정답!! 다음에도 잘할 듯?? _(웃음)_",
    "빙고!! {actual} 맞혔다!! _(신남)_",
    "{actual}이었네!! 운이 좋았나 봐!! _(감탄)_",
    "정답 {actual}!! 이건 실력인 듯!! _(칭찬)_",
    "오옷, {actual} 정확히!! 대박!! _(놀람)_",
    "{actual}!! 완전 잘 맞혔어!! _(박수)_",
    "정답은 {actual}이었다구!! 축하해!! _(환호)_",
    "{actual} 맞혔네!! 다음 판도 기대돼!! _(웃음)_",
    "짠, {actual}!! 완벽한 정답이야!! _(감탄)_",
)
_ODD_EVEN_LOSE_LINES = (
    "정답은 {actual}이었는데... 아쉽다!! _(안타까움)_",
    "어이쿠, {actual}이었어!! 다음엔 맞혀봐!! _(아쉬움)_",
    "땡!! 정답은 {actual}이었어!! _(장난)_",
    "{actual}이었는데 틀렸네!! 다음 기회에!! _(위로)_",
    "아깝다, 정답은 {actual}!! _(안타까움)_",
    "정답은 {actual}!! 이번엔 햄미가 이겼다!! _(으쓱)_",
    "땡땡!! {actual}이었어!! 다음엔 잘될 거야!! _(웃음)_",
    "{actual}이었네!! 살짝 빗나갔어!! _(아쉬움)_",
    "정답 {actual}, 아쉽게 틀렸어!! _(안타까움)_",
    "이런, {actual}이었다구!! 다음엔 맞힐 수 이써!! _(응원)_",
    "{actual}이 정답이었어!! 다음 판 노려봐!! _(웃음)_",
    "아쉽지만 {actual}이었어!! _(안타까움)_",
    "땡!! {actual}!! 햄미가 이겼네?? _(장난)_",
    "정답은 {actual}이었는데 놓쳤어!! _(아쉬움)_",
    "{actual}이었다구!! 다음엔 꼭 맞혀봐!! _(응원)_",
    "이번엔 {actual}!! 아깝게 틀렸네!! _(위로)_",
    "정답 {actual}, 다음엔 더 잘할 거야!! _(격려)_",
    "{actual}이 나왔어!! 다음번엔 이길지도?? _(웃음)_",
    "아깝게 빗나갔어!! 정답은 {actual}!! _(안타까움)_",
    "정답은 {actual}이었다구!! 재도전 해볼래?? _(권유)_",
)

_RPS_WIN_LINES = (
    "햄미는 {actual}!! 네가 이겼어!! _(감탄)_",
    "우와, 햄미가 {actual} 냈는데 졌다!! _(놀람)_",
    "햄미 {actual}!! 완패했어!! _(박수)_",
    "{actual} 냈는데도 졌어!! 잘했다!! _(칭찬)_",
    "햄미는 {actual}이었어!! 네가 한 수 위야!! _(감탄)_",
    "이런, 햄미 {actual}!! 완전 졌네!! _(웃음)_",
    "햄미가 {actual} 냈는데 발렸어!! _(놀람)_",
    "{actual}!! 햄미 패배 인정!! _(박수)_",
    "햄미는 {actual}로 도전했지만 졌어!! _(칭찬)_",
    "우와아, {actual} 냈는데도 졌다!! _(놀람)_",
    "햄미 {actual}!! 이번엔 완전 밀렸어!! _(웃음)_",
    "{actual} 냈는데 상대가 안 됐어!! _(감탄)_",
    "햄미는 {actual}이었는데... 졌다!! _(박수)_",
    "짜잔, 햄미 {actual}!! 그래도 졌어!! _(웃음)_",
    "햄미가 {actual} 내고 완패!! _(칭찬)_",
    "{actual}!! 햄미 실력이 안 됐어!! _(감탄)_",
    "햄미는 {actual}!! 완전히 읽혔나 봐!! _(놀람)_",
    "이번 햄미는 {actual}, 패배!! _(박수)_",
    "햄미가 {actual} 냈는데 밀렸어!! _(웃음)_",
    "{actual} 냈지만 졌다!! 축하해!! _(감탄)_",
)
_RPS_LOSE_LINES = (
    "햄미는 {actual}!! 이번엔 햄미가 이겼다!! _(으쓱)_",
    "우와, 햄미 {actual}로 승리!! _(환호)_",
    "햄미 {actual}!! 완전 이겼어!! _(신남)_",
    "{actual} 낸 햄미가 이겼네?? _(으쓱)_",
    "햄미는 {actual}이었어!! 이번엔 이겼다!! _(환호)_",
    "짜잔, 햄미 {actual}!! 승리!! _(신남)_",
    "햄미가 {actual} 내고 이겼어!! _(자랑)_",
    "{actual}!! 햄미 승리 축하!! _(환호)_",
    "햄미는 {actual}로 이겼다구!! _(으쓱)_",
    "우와아, {actual} 내고 햄미 승리!! _(신남)_",
    "햄미 {actual}!! 이번엔 완전 이겼어!! _(자랑)_",
    "{actual} 낸 게 신의 한 수였나 봐!! 햄미 승!! _(환호)_",
    "햄미는 {actual}이었는데... 이겼다!! _(신남)_",
    "짠, 햄미 {actual}!! 그리고 승리!! _(으쓱)_",
    "햄미가 {actual} 내고 완승!! _(자랑)_",
    "{actual}!! 햄미 실력 인정?? _(환호)_",
    "햄미는 {actual}!! 완전히 읽었나 봐!! _(신남)_",
    "이번 햄미는 {actual}, 승리!! _(으쓱)_",
    "햄미가 {actual} 내고 이겼어!! 다음엔 조심해!! _(자랑)_",
    "{actual} 냈고 햄미 승!! 아쉽게 됐네!! _(환호)_",
)
_RPS_DRAW_LINES = (
    "우와, 둘 다 {actual}!! 비겼어!! 배팅금은 돌려줄게!! _(놀람)_",
    "어라, 똑같이 {actual}!! 무승부야!! _(웃음)_",
    "{actual} 대 {actual}!! 비겼네, 동전은 그대로 돌려줄게!! _(정리)_",
    "동시에 {actual}!! 무승부, 배팅금 환불할게!! _(끄덕)_",
    "둘 다 {actual}이라니!! 이번엔 비긴 걸로!! _(웃음)_",
    "우연히 같은 {actual}!! 무승부, 동전은 안전해!! _(안심)_",
    "{actual}이 겹쳤어!! 비겼으니까 동전 돌려줄게!! _(정리)_",
    "완전 똑같이 {actual}!! 무승부야!! _(놀람)_",
    "둘 다 {actual} 냈네?? 비겼다!! _(웃음)_",
    "무승부!! {actual}가 겹쳤어, 배팅금 그대로!! _(끄덕)_",
    "이야, {actual} 대 {actual}!! 승부는 다음에!! _(웃음)_",
    "동전은 그대로!! 둘 다 {actual}로 비겼어!! _(안심)_",
    "{actual}이 똑같아!! 이번엔 무승부로!! _(정리)_",
    "우와, 텔레파시 통했나?? 둘 다 {actual}!! _(놀람)_",
    "비겼어!! {actual}가 겹쳐서 동전은 돌려줄게!! _(끄덕)_",
    "{actual} 대 {actual}, 승부를 못 가렸어!! _(웃음)_",
    "무승부다!! 배팅금은 안전하게 돌아가!! _(안심)_",
    "둘이 똑같이 {actual}!! 다음 판을 노려보자!! _(웃음)_",
    "{actual}이 겹쳐서 이번엔 비겼어!! _(정리)_",
    "완전 똑같은 선택!! {actual} 무승부야!! _(놀람)_",
)


async def _refund_timeout(view: discord.ui.View, user_id: int, bet: int) -> None:
    """60초 동안 아무도 안 누르면 이미 선차감된 배팅액을 그대로 돌려준다 — 원금
    반환일 뿐이라 count_as_earned=False(무승부 환불과 동일한 이유)."""
    if view.interaction is None:
        return
    await add_coins(user_id, bet, method="bet_timeout_refund", count_as_earned=False)
    try:
        await view.interaction.edit_original_response(content=random.choice(_BET_TIMEOUT_LINES), view=None)
    except discord.HTTPException:
        logging.exception("Failed to edit bet prompt on timeout")


async def _maybe_award_win_achievement(user_id: int) -> str:
    """업적 보너스 호감도(§15)는 add_coins와 별개 경로라, 여기서 벌어도 델타를 놓치지
    않도록 항상 format_affection_notice까지 같이 붙여서 반환한다(plastic.py/vending.py와
    동일한 원칙 — 코인 알림과는 완전히 별개로 호감도 알림도 빠짐없이 보여준다)."""
    result = await award_achievement(user_id, achievements.hammie_ez_noob.ID)
    if not result["earned"]:
        return ""
    notice = f"\n🏆 업적 달성: {achievements.format_name(achievements.hammie_ez_noob)}!!"
    notice += format_affection_notice(result["applied_amount"], result["new_affection"])
    return notice


class _OddEvenView(discord.ui.View):
    def __init__(self, user_id: int, bet: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.interaction: discord.Interaction | None = None

    async def on_timeout(self) -> None:
        await _refund_timeout(self, self.user_id, self.bet)

    async def _resolve(self, interaction: discord.Interaction, guess: str) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user(interaction, self.user_id):
            return
        self.stop()
        for child in self.children:
            child.disabled = True

        actual = random.choice(("홀", "짝"))
        if guess == actual:
            result = await add_coins(self.user_id, self.bet * 2, method="bet_odd_even_win")
            text = random.choice(_ODD_EVEN_WIN_LINES).format(actual=actual)
            text += format_coin_notice(result["applied_amount"], result["new_coins"])
            text = maybe_append_capacity_advice(text, self.bet * 2, result)
            if result["achievement_notice"]:
                text += f"\n{result['achievement_notice']}"
            text += await _maybe_award_win_achievement(self.user_id)
        else:
            user = await get_user(self.user_id)
            text = random.choice(_ODD_EVEN_LOSE_LINES).format(actual=actual)
            text += format_coin_notice(-self.bet, user["coins"])

        await interaction.response.edit_message(content=text, view=self)

    @discord.ui.button(label="홀", style=discord.ButtonStyle.primary)
    async def odd(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "홀")

    @discord.ui.button(label="짝", style=discord.ButtonStyle.primary)
    async def even(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "짝")


class _RPSView(discord.ui.View):
    # key가 value를 이긴다 (가위는 보를 이기고, 바위는 가위를 이기고, 보는 바위를 이긴다).
    _BEATS = {"가위": "보", "바위": "가위", "보": "바위"}

    def __init__(self, user_id: int, bet: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.interaction: discord.Interaction | None = None

    async def on_timeout(self) -> None:
        await _refund_timeout(self, self.user_id, self.bet)

    async def _resolve(self, interaction: discord.Interaction, choice: str) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user(interaction, self.user_id):
            return
        self.stop()
        for child in self.children:
            child.disabled = True

        actual = random.choice(("가위", "바위", "보"))
        actual_bold = f"**{actual}**"
        if choice == actual:
            result = await add_coins(self.user_id, self.bet, method="bet_rps_draw", count_as_earned=False)
            text = random.choice(_RPS_DRAW_LINES).format(actual=actual_bold)
            text += format_coin_notice(result["applied_amount"], result["new_coins"])
        elif self._BEATS[choice] == actual:
            result = await add_coins(self.user_id, self.bet * 2, method="bet_rps_win")
            text = random.choice(_RPS_WIN_LINES).format(actual=actual_bold)
            text += format_coin_notice(result["applied_amount"], result["new_coins"])
            text = maybe_append_capacity_advice(text, self.bet * 2, result)
            if result["achievement_notice"]:
                text += f"\n{result['achievement_notice']}"
            text += await _maybe_award_win_achievement(self.user_id)
        else:
            user = await get_user(self.user_id)
            text = random.choice(_RPS_LOSE_LINES).format(actual=actual_bold)
            text += format_coin_notice(-self.bet, user["coins"])

        await interaction.response.edit_message(content=text, view=self)

    @discord.ui.button(label="가위", style=discord.ButtonStyle.primary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "가위")

    @discord.ui.button(label="바위", style=discord.ButtonStyle.primary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "바위")

    @discord.ui.button(label="보", style=discord.ButtonStyle.primary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "보")


async def handle_odd_even(interaction: discord.Interaction, bet: int) -> None:
    if bet < 1:
        await interaction.edit_original_response(content=_INVALID_BET_RESPONSE)
        return
    if not await spend_coins(interaction.user.id, bet):
        await interaction.edit_original_response(content=random.choice(INSUFFICIENT_FUNDS_LINES))
        return
    view = _OddEvenView(interaction.user.id, bet)
    await interaction.edit_original_response(
        content=f"홀?? 짝?? 골라봐!! (배팅: {bet}동전) _(두근)_", view=view
    )
    view.interaction = interaction


async def handle_rps(interaction: discord.Interaction, bet: int) -> None:
    if bet < 1:
        await interaction.edit_original_response(content=_INVALID_BET_RESPONSE)
        return
    if not await spend_coins(interaction.user.id, bet):
        await interaction.edit_original_response(content=random.choice(INSUFFICIENT_FUNDS_LINES))
        return
    view = _RPSView(interaction.user.id, bet)
    await interaction.edit_original_response(
        content=f"가위?? 바위?? 보?? 골라봐!! (배팅: {bet}동전) _(긴장)_", view=view
    )
    view.interaction = interaction
