"""/도박의 "더블오어낫띵" 서브게임(2026-09-09 신규) — 상자를 열어 50% 확률로 판돈이
2배가 되고, 50% 확률로 폭탄을 만나 전부 잃는다. 유일하게 판돈에 상한이 없는 도박
(사용자 확인) — "한 판 더"를 반복할수록 계속 두 배씩 불어나며, 실제 지급은 "여기까지"로
정산할 때 딱 한 번만 일어난다(그 전까지는 View의 파이썬 상태에만 존재하는 가상 판돈).

`command/slot.py`가 이미 커서(승부예측 신설로 600여 줄) 새 대형 서브게임을 또
그 안에 넣지 않고 `command/horse_race.py`와 동일한 원칙으로 별 파일에 전부 구현한
뒤 `slot.py`에서 얇게 연동한다."""

import logging
import random

import discord

from command.economy_common import (
    GAMBLING_EMBED_COLOR,
    INSUFFICIENT_FUNDS_LINES,
    ReplayView,
    claim_active_or_reject,
    format_bet_receipt,
    mark_active,
    mark_inactive,
    maybe_award_legendary_multiplier,
    reject_if_already_resolved,
    reject_if_wrong_user_with_cta,
)
from db.users import get_user
from db.wallet import add_coins, spend_coins

_OWN_COMMAND = "/도박"

# 상자 열기 / 한 판 더-여기까지 화면 공통 대기시간(2026-09-09) — 10분 안에 응답이
# 없으면 포기로 간주해 판돈을 몰수한다(bet.py의 새 몰수 정책과 동일한 원칙). "한 판
# 더/여기까지" 화면은 사용자 스펙에 타임아웃이 명시돼 있지 않지만, 이 배치 전체의
# "10분 무응답 = 몰수" 원칙과 일관되게 동일하게 적용한다 — 실제로 지급된 적 없는
# 가상 판돈이라 몰수해도 상태가 꼬이지 않는다.
_BOX_TIMEOUT_SECONDS = 600

_BOX_OPEN_INTRO_LINES = (
    "상자가 하나 있어!! 열어볼래?? _(두근)_",
    "수상한 상자야!! 뭐가 들었을까?? _(긴장)_",
    "상자 열기, 준비됐어?? _(설렘)_",
    "이 상자, 열어볼 용기 있어?? _(도발)_",
    "상자 안에 뭐가 있을지 궁금해!! _(호기심)_",
    "자, 상자를 열어보자!! _(기대)_",
    "이 상자... 돈일까 폭탄일까?? _(긴장)_",
    "상자가 기다리고 있어!! _(두근)_",
)
_BOX_MONEY_LINES = (
    "짜잔, 돈이었어!! 판돈이 2배가 됐어!! _(환호)_",
    "우와, 돈이야!! 두 배로 불어났어!! _(흥분)_",
    "안전했어!! 판돈이 2배로!! _(안도)_",
    "돈 상자였다구!! 두 배 획득!! _(신남)_",
    "휴, 폭탄이 아니었어!! 판돈 2배!! _(안심)_",
    "대박!! 판돈이 두 배가 됐어!! _(환호)_",
    "돈이다!! 계속 가볼까?? _(설렘)_",
    "성공!! 판돈이 2배로 불었어!! _(흥분)_",
)
_BOX_BOMB_LINES = (
    "펑!! 폭탄이었어... 전부 잃었어!! _(경악)_",
    "이런, 폭탄이야!! 판돈이 다 날아갔어!! _(허탈)_",
    "펑!! 아쉽게도 폭탄이었어!! _(좌절)_",
    "폭탄이 터졌어!! 판돈 전부 상실!! _(멘붕)_",
    "이럴 수가, 폭탄이었네!! 다 잃었어!! _(허탈)_",
    "펑!! 이번엔 운이 없었어!! _(아쉬움)_",
    "폭탄이었다구!! 판돈이 전부 사라졌어!! _(좌절)_",
    "아이고, 폭탄!! 전부 날렸어!! _(한숨)_",
)
# 2026-09-09 — 舊 _BET_TIMEOUT_LINES(환불)와 달리 몰수를 전제로 한다(bet.py의
# 새 정책과 동일한 원칙).
_BOX_FORFEIT_LINES = (
    "상자를 안 열길래 포기한 걸로 알고 판돈은 가져갈게!! _(단호)_",
    "10분이 지났어!! 결과 확인 없이 판돈을 몰수했어!! _(냉정)_",
    "응답이 없어서 이번 판은 포기 처리했어!! _(정리)_",
    "너무 오래 기다렸어!! 판돈은 이제 내 거야!! _(으쓱)_",
    "시간 초과!! 상자는 못 열어봤지만 판돈은 가져갈게!! _(단호)_",
    "아무 반응이 없어서 몰수 처리했어!! _(냉정)_",
)
_CASHOUT_LINES = (
    "여기까지!! 판돈을 안전하게 챙겼어!! _(안도)_",
    "현명한 선택이야!! 판돈을 확실히 받았어!! _(칭찬)_",
    "짜잔, 여기서 마무리!! 판돈 획득!! _(뿌듯)_",
    "안전하게 여기서 끝냈어!! _(안심)_",
    "여기까지 왔으면 충분해!! 판돈 정산!! _(만족)_",
    "욕심부리지 않고 딱 여기까지!! _(현명)_",
)

# RulesView가 embed.description으로 그대로 보여주는 문구라 시스템 정중체로 고정한다
# (§22-4).
DOUBLE_OR_NOTHING_RULE_TEXT = (
    "📦 더블오어낫띵\n\n"
    "상자를 열면 50% 확률로 판돈이 2배가 되고, 50% 확률로 폭탄을 만나 판돈을 전부 "
    "잃습니다. 판돈이 2배가 되면 \"한 판 더\"로 계속 도전하거나 \"여기까지\"로 "
    "그 자리에서 정산받을 수 있습니다.\n\n"
    "판돈에는 상한이 없어 계속 반복할수록 배당이 커지지만, 그만큼 폭탄을 만날 "
    "위험도 매번 새로 50%씩 적용됩니다.\n\n"
    "상자 열기(또는 한 판 더/여기까지 선택)를 10분 안에 하지 않으면 포기한 것으로 "
    "간주해 결과 확인 없이 판돈을 모두 잃습니다."
)


def _build_replay_view(user_id: int) -> ReplayView:
    """다시하기를 누르면 완전히 새로운 배팅으로 새 세션을 시작한다 — bet.py/
    slot.py/horse_race.py의 동일한 헬퍼와 같은 원칙(항상 새 공개 메시지, old_message는
    버튼만 제거)."""

    async def _on_replay(
        interaction: discord.Interaction, amount: int, old_message: "discord.Message | None"
    ) -> None:
        await start_round(interaction, user_id, amount, is_replay=True)
        if old_message is not None:
            try:
                await old_message.edit(view=None)
            except discord.HTTPException:
                logging.exception("Failed to clear old double-or-nothing message buttons after replay")

    return ReplayView(user_id, _OWN_COMMAND, _on_replay)


class _BoxView(discord.ui.View):
    """상자 열기 화면 — "한 판 더"로 이어지는 매 라운드마다 새로 만들어진다(같은
    메시지를 계속 고쳐쓴다, 슬롯머신이 스핀마다 메시지를 고쳐쓰는 것과 동일한 결).
    original_bet은 영수증 표시용(항상 최초 배팅액), pot은 지금까지 불어난 가상
    판돈이다."""

    def __init__(
        self, user_id: int, challenger_name: str, original_bet: int, pot: int, before_coins: int
    ) -> None:
        super().__init__(timeout=_BOX_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.challenger_name = challenger_name
        self.original_bet = original_bet
        self.pot = pot
        self.before_coins = before_coins
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        """상자를 안 열면 결과 공개 없이 곧바로 몰수한다 — 환불도, 다시하기 버튼도
        없다(사용자 명시: "결과 없이 돈을 모두 잃습니다")."""
        if self.message is None:
            return
        mark_inactive(self.user_id)
        try:
            content = f"## 🎯 도전자: {self.challenger_name}\n{random.choice(_BOX_FORFEIT_LINES)}"
            await self.message.edit(content=content, embed=None, view=None)
        except discord.HTTPException:
            logging.exception("Failed to edit double-or-nothing box on timeout")

    @discord.ui.button(label="상자 열기", style=discord.ButtonStyle.primary)
    async def open_box(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
            return
        self.stop()

        if random.random() < 0.5:
            new_pot = self.pot * 2
            content = (
                f"## 🎯 도전자: {self.challenger_name}\n{random.choice(_BOX_MONEY_LINES)}\n\n"
                f"현재 판돈: {new_pot:,}코인"
            )
            choice_view = _DoubleOrNothingChoiceView(
                self.user_id, self.challenger_name, self.original_bet, new_pot, self.before_coins
            )
            try:
                await interaction.response.edit_message(content=content, view=choice_view)
                choice_view.message = await interaction.original_response()
            except discord.HTTPException:
                # self.stop()은 이미 호출된 뒤라 새 view가 못 붙으면 게임이
                # 통째로 멈춘다 — 잠금을 풀어줘야 새 판을 다시 시작할 수 있다.
                logging.exception("Failed to edit double-or-nothing money reveal")
                mark_inactive(self.user_id)
        else:
            content = f"## 🎯 도전자: {self.challenger_name}\n{random.choice(_BOX_BOMB_LINES)}\n\n"
            current = self.before_coins - self.original_bet
            content += format_bet_receipt(self.before_coins, self.original_bet, current)
            replay_view = _build_replay_view(self.user_id)
            try:
                await interaction.response.edit_message(content=content, view=replay_view)
                replay_view.message = await interaction.original_response()
            except discord.HTTPException:
                logging.exception("Failed to edit double-or-nothing bomb settlement")
                mark_inactive(self.user_id)


class _DoubleOrNothingChoiceView(discord.ui.View):
    """상자에서 돈이 나온 직후 — "한 판 더"(같은 판돈으로 새 상자, 추가 배팅 없음)
    또는 "여기까지"(그 자리에서 실제 정산, 이 시점에 딱 한 번 add_coins)."""

    def __init__(
        self, user_id: int, challenger_name: str, original_bet: int, pot: int, before_coins: int
    ) -> None:
        super().__init__(timeout=_BOX_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.challenger_name = challenger_name
        self.original_bet = original_bet
        self.pot = pot
        self.before_coins = before_coins
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        mark_inactive(self.user_id)
        try:
            content = f"## 🎯 도전자: {self.challenger_name}\n{random.choice(_BOX_FORFEIT_LINES)}"
            await self.message.edit(content=content, embed=None, view=None)
        except discord.HTTPException:
            logging.exception("Failed to edit double-or-nothing choice screen on timeout")

    @discord.ui.button(label="한 판 더", style=discord.ButtonStyle.danger)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
            return
        self.stop()

        content = (
            f"## 🎯 도전자: {self.challenger_name}\n{random.choice(_BOX_OPEN_INTRO_LINES)}\n\n"
            f"현재 판돈: {self.pot:,}코인"
        )
        box_view = _BoxView(self.user_id, self.challenger_name, self.original_bet, self.pot, self.before_coins)
        try:
            await interaction.response.edit_message(content=content, view=box_view)
            box_view.message = await interaction.original_response()
        except discord.HTTPException:
            logging.exception("Failed to edit double-or-nothing again screen")
            mark_inactive(self.user_id)

    @discord.ui.button(label="여기까지", style=discord.ButtonStyle.success)
    async def cash_out(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
            return
        self.stop()

        result = await add_coins(self.user_id, self.pot, method="double_or_nothing_cashout")
        text = random.choice(_CASHOUT_LINES)

        # "제작자는 이 업적이..." 전설 업적이 /도박 전체 공용(배율 64 이상)으로 확장됨에
        # 따라 더블오어낫띵도 대상에 포함 — "여기까지"로 실제 지급이 확정되는 이
        # 시점에만 체크한다(판돈이 상한 없이 두 배씩 불어나므로 6회 연속 성공(64배)
        # 부터 해당). 2026-09-10부로 업적 달성 알림(호감도 보너스 포함)은 award()
        # 내부에서 별도 글로벌 방송으로 처리되므로 여기서는 부여만 시도한다.
        multiplier = self.pot // self.original_bet
        await maybe_award_legendary_multiplier(self.user_id, multiplier)

        content = f"## 🎯 도전자: {self.challenger_name}\n{text}\n\n"
        content += format_bet_receipt(self.before_coins, self.original_bet, result["new_coins"])
        replay_view = _build_replay_view(self.user_id)
        try:
            await interaction.response.edit_message(content=content, view=replay_view)
            replay_view.message = await interaction.original_response()
        except discord.HTTPException:
            logging.exception("Failed to edit double-or-nothing cash-out settlement")
            mark_inactive(self.user_id)


async def start_round(
    interaction: discord.Interaction, user_id: int, bet: int, *, is_replay: bool = False
) -> None:
    """모달에서 유효한 배팅액을 받은 뒤 첫 상자를 새 공개 메시지로 연다 —
    command/slot.py::_GambleSelectView의 "더블오어낫띵" 버튼과 다시하기가 공유하는
    진입점.

    is_replay 처리는 bet.py::_start_round와 동일한 원칙(economy_common
    .claim_active_or_reject docstring 참고) — 신규 진입일 때만 spend_coins 이전에
    원자적 크로스블록 체크를 한다."""
    if not is_replay and not await claim_active_or_reject(interaction, user_id, _OWN_COMMAND):
        return

    if not await spend_coins(user_id, bet):
        if not is_replay:
            mark_inactive(user_id)
        await interaction.response.send_message(random.choice(INSUFFICIENT_FUNDS_LINES), ephemeral=True)
        return

    # 배팅이 성립한 시점부터 "진행 중"으로 표시한다 — 정산 후 "다시하기" 버튼이
    # 사라지기 전까지 /내기·/도박(슬롯머신·승부예측 포함) 재진입을 막는다.
    if is_replay:
        mark_active(user_id, _OWN_COMMAND)

    # vending.py::_execute_purchase와 동일한 역산 — spend_coins가 차감 전 잔액을
    # 반환하지 않아서, 차감 후 조회한 잔액에 배팅액을 다시 더해 "기존 금액"을 구한다.
    user = await get_user(user_id)
    before_coins = user["coins"] + bet
    challenger_name = interaction.user.display_name

    content = (
        f"## 🎯 도전자: {challenger_name}\n{random.choice(_BOX_OPEN_INTRO_LINES)}\n\n"
        f"현재 판돈: {bet:,}코인"
    )
    view = _BoxView(user_id, challenger_name, bet, bet, before_coins)
    await interaction.response.send_message(content=content, view=view)
    view.message = await interaction.original_response()
