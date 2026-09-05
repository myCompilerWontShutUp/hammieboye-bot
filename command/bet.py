import logging
import random
from datetime import datetime

import discord

import achievements
from core.base import EphemeralAutoDeleteView
from command.economy_common import (
    GAMBLING_EMBED_COLOR,
    INSUFFICIENT_FUNDS_LINES,
    MAX_BET,
    TIMEOUT_SECONDS,
    BetAmountModal,
    ReplayView,
    RulesView,
    format_coin_notice,
    reject_if_already_resolved,
    reject_if_wrong_user_with_cta,
)
from db.achievements import award as award_achievement
from db.affection import format_affection_notice
from db.users import get_user
from db.wallet import add_coins, spend_coins
from events.scheduler import KST, format_footer_time

# CTA 문구("너도 {own_command}로 직접 해볼 수 있어!!")와 ReplayView에 그대로 넘긴다 —
# economy_common.reject_if_wrong_user_with_cta/ReplayView가 /내기·/도박 공용이라 자기
# 커맨드 이름을 매번 받는다.
_OWN_COMMAND = "/내기"

_ODD_EVEN = "odd_even"
_RPS = "rps"

# "으흠! 나에게 내기를 걸다니..." — /내기 실행 직후, 게임 종류를 고르는 ephemeral
# 프롬프트에 붙는 인트로 문구.
_GAME_SELECT_INTRO_LINES = (
    "으흠! 나에게 내기를 걸다니... 받아주게써!! _(자신감)_",
    "오호, 내기라니!! 좋아, 받아줄게!! _(흥미)_",
    "흐음, 도전하는 거야?? 좋아!! _(도전)_",
    "내기?? 재밌겠다!! 뭘로 해볼까?? _(신남)_",
    "오, 승부욕 발동!! 받아주겠어!! _(투지)_",
    "좋아, 내기 한판 해보자!! _(자신감)_",
    "으흐흐, 내기라니 흥미로운데?? _(웃음)_",
    "내기 신청 접수!! 뭐부터 해볼래?? _(기대)_",
    "오호라, 한판 붙어보자는 거지?? _(흥미)_",
    "내기라면 자신 있지!! 골라봐!! _(자신)_",
    "흠, 좋은 승부가 되겠는걸?? _(기대)_",
    "내기 좋아!! 어떤 걸로 할래?? _(설렘)_",
    "으흠, 받아주지!! 종류를 골라봐!! _(자신감)_",
    "오오, 내기 타임!! 신난다!! _(신남)_",
    "좋아, 승부다!! 뭐로 할까?? _(투지)_",
    "내기라니, 피가 끓는걸?? _(흥분)_",
    "으흠! 재밌겠어, 받아주게써!! _(자신감)_",
    "오호, 좋은 생각이야!! 골라봐!! _(흥미)_",
    "내기 접수 완료!! 종류 골라줘!! _(정리)_",
    "좋아, 이번엔 내가 이길 거야!! _(자신감)_",
)

_RULES_INTRO_LINES = (
    "내기 규칙 알려줄게!! _(진지)_",
    "이렇게 하면 이길 수 있어!! _(자신감)_",
    "내기 하는 법 설명할게!! _(친절)_",
    "규칙부터 익히고 시작하자!! _(꼼꼼)_",
    "내기, 이렇게 굴러가!! _(설명)_",
    "먼저 규칙 확인해볼래?? _(권유)_",
    "내기 공략법이야!! _(자랑)_",
    "이거 알면 유리해!! 규칙이야!! _(웃음)_",
    "내기 설명서 가져왔어!! _(뿌듯)_",
    "규칙 모르면 손해야!! 알려줄게!! _(진지)_",
    "내기는 이렇게 하는 거야!! _(설명)_",
    "짜잔, 내기 규칙!! _(공개)_",
    "이거 읽고 도전해봐!! _(응원)_",
    "내기 하기 전에 이거부터!! _(추천)_",
    "규칙 요약해줄게!! _(친절)_",
    "내기 룰 정리했어!! _(정리)_",
    "이렇게 승부가 갈려!! _(설명)_",
    "내기, 알고 하면 더 재밌어!! _(웃음)_",
    "규칙 확인하고 배팅해봐!! _(권유)_",
    "내기 가이드 여기 있어!! _(안내)_",
)

_RULES_OVERVIEW_TEXT = (
    "동전을 걸고 하는 미니게임이야!! 지금은 두 가지가 있어(앞으로 더 늘어날 수도 "
    "있어!!) — 아래 버튼에서 궁금한 게임을 골라봐!!\n\n"
    f"배팅액은 1~{MAX_BET}동전까지 걸 수 있고, 게임 진행 중 60초 동안 아무것도 안 "
    "고르면 배팅액을 그대로 환불해줘!!"
)

_ODD_EVEN_RULE_TEXT = (
    "🪙 홀짝\n\n홀 또는 짝을 골라서 맞히면 배팅액의 2배를 받고, 틀리면 배팅액을 전부 "
    "잃어!! 승리하면 HAMMIE EZ NOOB 업적도 노려볼 수 있어!!"
)
_RPS_RULE_TEXT = (
    "✂️ 가위바위보\n\n가위/바위/보 중 하나를 내서 햄미를 이기면 배팅액의 2배, 비기면 "
    "배팅액을 그대로 돌려받고(번 게 아니라서 순수 반환이야), 지면 배팅액을 전부 "
    "잃어!! 승리하면 HAMMIE EZ NOOB 업적도 노려볼 수 있어!!"
)

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


async def _refund_timeout(message: discord.Message, user_id: int, bet: int) -> None:
    """60초 동안 아무도 안 누르면 이미 선차감된 배팅액을 그대로 돌려준다 — 원금
    반환일 뿐이라 count_as_earned=False(무승부 환불과 동일한 이유). 인터랙션 토큰이
    아니라 메시지 객체를 직접 들고 있다가 edit한다 — 이 메시지가 최초 슬래시 응답으로
    생겼는지(첫 판) 모달 제출로 edit된 건지(다시하기)와 무관하게 항상 동작한다."""
    await add_coins(user_id, bet, method="bet_timeout_refund", count_as_earned=False)
    try:
        await message.edit(content=random.choice(_BET_TIMEOUT_LINES), embed=None, view=None)
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
    # 업적 보너스는 항상 배율 미적용(apply_day_multiplier=False)으로 지급되므로,
    # 우연히 그날 배율로 나누어떨어져도 "N x 배율"로 잘못 분해해 보여주면 안 된다.
    notice += format_affection_notice(
        result["applied_amount"], result["new_affection"], multiplier_eligible=False
    )
    return notice


def _build_replay_view(user_id: int, game_kind: str) -> ReplayView:
    """다시하기를 누르면 같은 게임 종류로 새 판을 연다 — game_kind를 클로저로 감싸서
    ReplayView(범용, economy_common.py)에 넘긴다."""

    async def _on_replay(interaction: discord.Interaction, amount: int) -> None:
        await _start_round(interaction, user_id, game_kind, amount, edit=True)

    return ReplayView(user_id, _OWN_COMMAND, _on_replay)


async def _start_round(
    interaction: discord.Interaction, user_id: int, game_kind: str, bet: int, *, edit: bool
) -> None:
    """모달에서 유효한 금액을 받은 뒤 실제 판을 연다 — edit=False면 새 공개 메시지로
    (게임 선택 직후 첫 판), edit=True면 지금 이 메시지를 고쳐 쓴다("다시하기").
    금액 검증(1~MAX_BET)은 모달이 이미 끝냈으니 여기서는 잔액만 확인한다."""
    if not await spend_coins(user_id, bet):
        await interaction.response.send_message(random.choice(INSUFFICIENT_FUNDS_LINES), ephemeral=True)
        return

    if game_kind == _ODD_EVEN:
        view: discord.ui.View = _OddEvenView(user_id, bet)
        content = f"홀?? 짝?? 골라봐!! (배팅: {bet}동전) _(두근)_"
    else:
        view = _RPSView(user_id, bet)
        content = f"가위?? 바위?? 보?? 골라봐!! (배팅: {bet}동전) _(긴장)_"

    if edit:
        await interaction.response.edit_message(content=content, embed=None, view=view)
    else:
        await interaction.response.send_message(content=content, view=view)
    view.message = await interaction.original_response()


class _OddEvenView(discord.ui.View):
    def __init__(self, user_id: int, bet: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        await _refund_timeout(self.message, self.user_id, self.bet)

    async def _resolve(self, interaction: discord.Interaction, guess: str) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
            return
        self.stop()

        actual = random.choice(("홀", "짝"))
        if guess == actual:
            result = await add_coins(self.user_id, self.bet * 2, method="bet_odd_even_win")
            text = random.choice(_ODD_EVEN_WIN_LINES).format(actual=actual)
            text += format_coin_notice(result["applied_amount"], result["new_coins"])
            if result["achievement_notice"]:
                text += f"\n{result['achievement_notice']}"
            text += await _maybe_award_win_achievement(self.user_id)
        else:
            user = await get_user(self.user_id)
            text = random.choice(_ODD_EVEN_LOSE_LINES).format(actual=actual)
            text += format_coin_notice(-self.bet, user["coins"])

        replay_view = _build_replay_view(self.user_id, _ODD_EVEN)
        await interaction.response.edit_message(content=text, view=replay_view)
        replay_view.message = await interaction.original_response()

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
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        await _refund_timeout(self.message, self.user_id, self.bet)

    async def _resolve(self, interaction: discord.Interaction, choice: str) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
            return
        self.stop()

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
            if result["achievement_notice"]:
                text += f"\n{result['achievement_notice']}"
            text += await _maybe_award_win_achievement(self.user_id)
        else:
            user = await get_user(self.user_id)
            text = random.choice(_RPS_LOSE_LINES).format(actual=actual_bold)
            text += format_coin_notice(-self.bet, user["coins"])

        replay_view = _build_replay_view(self.user_id, _RPS)
        await interaction.response.edit_message(content=text, view=replay_view)
        replay_view.message = await interaction.original_response()

    @discord.ui.button(label="가위", style=discord.ButtonStyle.primary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "가위")

    @discord.ui.button(label="바위", style=discord.ButtonStyle.primary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "바위")

    @discord.ui.button(label="보", style=discord.ButtonStyle.primary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "보")


class _GameSelectView(EphemeralAutoDeleteView):
    """/내기 실행 직후 뜨는 ephemeral 프롬프트 — 본인에게만 보이므로 "다른 사람이
    눌렀을 때" 처리는 애초에 불필요하다(디스코드가 다른 사람에게 아예 안 보여준다)."""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id

    async def _select(self, interaction: discord.Interaction, game_kind: str) -> None:
        self.bump()
        user = await get_user(self.user_id)
        balance = user["coins"] if user is not None else 0

        async def _on_valid(modal_interaction: discord.Interaction, amount: int) -> None:
            await _start_round(modal_interaction, self.user_id, game_kind, amount, edit=False)
            # 게임이 실제로 시작됐으니(= 공개 메시지가 새로 생겼으니) 애초의 ephemeral
            # 선택 프롬프트는 이제 볼일이 없다 — 지운다.
            try:
                await self.interaction.delete_original_response()
            except discord.HTTPException:
                logging.exception("Failed to delete game-select prompt after game start")

        await interaction.response.send_modal(BetAmountModal(balance=balance, on_valid=_on_valid))

    @discord.ui.button(label="홀짝", style=discord.ButtonStyle.primary)
    async def odd_even(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._select(interaction, _ODD_EVEN)

    @discord.ui.button(label="가위바위보", style=discord.ButtonStyle.primary)
    async def rps(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._select(interaction, _RPS)


async def handle_bet(interaction: discord.Interaction) -> None:
    """/내기 진입점 — 이미 ephemeral로 defer된 상태라고 가정하고 edit_original_response로
    게임 선택 프롬프트(인트로 문구 + 임베드 + 홀짝/가위바위보 버튼)를 보여준다."""
    user = await get_user(interaction.user.id)
    balance = user["coins"] if user is not None else 0

    embed = discord.Embed(title="🎲 내기", color=GAMBLING_EMBED_COLOR)
    embed.description = (
        f"현재 보유 동전 : {balance}개\n"
        "햄미와 내기를 하여 승리 시 배팅 금액의 2배, 패배 시 모두 잃습니다.\n"
        "자세한 규칙은 /내기-규칙 을 통해 확인할 수 있습니다."
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    view = _GameSelectView(interaction.user.id)
    await interaction.edit_original_response(
        content=random.choice(_GAME_SELECT_INTRO_LINES), embed=embed, view=view
    )
    view.interaction = interaction


async def handle_rules() -> tuple[str, discord.Embed, discord.ui.View]:
    """/내기-규칙 진입점 — ephemeral. 개요 임베드 + 게임별 버튼(RulesView)을 보여주고,
    버튼을 누르면 그 게임의 상세 규칙으로 임베드만 바꿔치기한다."""
    embed = discord.Embed(title="🎲 내기 규칙", description=_RULES_OVERVIEW_TEXT, color=GAMBLING_EMBED_COLOR)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    view = RulesView(
        "🎲 내기 규칙",
        {"홀짝": _ODD_EVEN_RULE_TEXT, "가위바위보": _RPS_RULE_TEXT},
        color=GAMBLING_EMBED_COLOR,
    )
    return random.choice(_RULES_INTRO_LINES), embed, view
