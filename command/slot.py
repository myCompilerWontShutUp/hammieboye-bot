import logging
import random
from datetime import datetime

import discord

import achievements
from command.economy_common import (
    INSUFFICIENT_FUNDS_LINES,
    TIMEOUT_SECONDS,
    VENDING_EMBED_COLOR,
    format_coin_notice,
    maybe_append_capacity_advice,
    reject_if_already_resolved,
    reject_if_wrong_user,
)
from events.scheduler import KST, format_footer_time
from db.achievements import award as award_achievement
from db.affection import add_affection, format_affection_notice
from db.wallet import add_coins, deduct_coins_clamped, spend_coins

_GRAPE, _PEANUT, _STRAWBERRY, _CHESTNUT, _HAMSTER, _CHEESE, _DIAMOND, _STAR, _SEVEN = (
    "🍇", "🥜", "🍓", "🌰", "🐹", "🧀", "💎", "⭐", "7️⃣",
)
SYMBOLS: tuple[str, ...] = (
    _GRAPE, _PEANUT, _STRAWBERRY, _CHESTNUT, _HAMSTER, _CHEESE, _DIAMOND, _STAR, _SEVEN,
)

# 햄스터(🐹)는 배율표에 없다 — 한 줄이라도 걸리면 배율 무관하게 전액 페널티로 분기.
_MULTIPLIERS: dict[str, int] = {
    _GRAPE: 2, _PEANUT: 2, _STRAWBERRY: 2, _CHESTNUT: 2,
    _CHEESE: 2, _STAR: 3, _DIAMOND: 10, _SEVEN: 77,
}
_SYMBOL_NAMES: dict[str, str] = {
    _GRAPE: "포도", _PEANUT: "땅콩", _STRAWBERRY: "딸기", _CHESTNUT: "밤",
    _CHEESE: "치즈", _DIAMOND: "다이아", _STAR: "별", _SEVEN: "세븐", _HAMSTER: "햄스터",
}

# 3x3 인덱스 0~8 기준 가로 3 + 세로 3 + 대각선 2 = 8라인.
_LINES: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)

# 전설 업적("제작자는 이 업적이 가능한지 테스트하지 않았습니다") 기준 — 세븐(77) 한
# 줄만 걸려도 이미 초과하지만, 세븐 한 줄이 뜰 확률 자체가 낮아 여전히 희귀하다.
# 정확히 16이면 미달(엄격한 초과 비교) — 다이아(10) 두 줄 동시 완성(10x10=100) 등도 해당.
_LEGENDARY_MULTIPLIER_THRESHOLD = 16

_INVALID_BET_RESPONSE = "배팅 금액은 동전 1개 이상이어야지!! _(갸웃)_"

_RULES_TEXT = (
    "3x3 칸을 채워서 가로 3줄 + 세로 3줄 + 대각선 2줄, 총 8줄을 확인해!! "
    "한 줄에 같은 그림이 3개 모이면 그 그림의 배율이 곱해지고, 여러 줄이 동시에 완성되면 "
    "배율끼리 전부 곱해져!! (배팅액 x 최종 배율을 돌려받아)\n\n"
    "그림별 배율은 이래:\n"
    "🍇 포도 x2 · 🥜 땅콩 x2 · 🍓 딸기 x2\n"
    "🌰 밤 x2 · 🧀 치즈 x2 · ⭐ 별 x3\n"
    "💎 다이아 x10 · 7️⃣ 세븐 x77\n\n"
    "그런데 🐹 햄스터가 한 줄이라도 걸리면 다른 배율은 몽땅 무시되고, 배팅액만큼 "
    "추가로 더 잃어버려!! 조심해!! _(경고)_"
)

_HAMSTER_PENALTY_LINES = (
    "으악, 햄스터가 나왔어!! 배팅액을 더 가져가버려써!! _(당황)_",
    "앗, 하필 햄스터 라인!! 완전 손해야!! _(억울)_",
    "햄스터가 훼방 놨어!! 추가로 더 잃었어!! _(허탈)_",
    "이런, 햄스터 라인이 떠버려써!! _(속상)_",
    "햄스터가 도망가면서 동전도 가져갔어!! _(황당)_",
    "최악이야!! 햄스터 라인, 추가 손실!! _(멘붕)_",
    "햄스터한테 배팅액을 더 뜯겨써!! _(울먹)_",
    "아이고, 햄스터가 훼방꾼이었네!! _(한숨)_",
    "햄스터 라인이라니!! 이건 진짜 아쉽다!! _(좌절)_",
    "동전이 햄스터한테 더 빨려갔어!! _(당황)_",
    "이런 정신 나간 햄스터!! 배팅액 더 날렸어!! _(황당)_",
    "햄스터가 슬롯머신을 씹어먹었나 봐!! 손해야!! _(허탈)_",
    "하필 이럴 때 햄스터가!! 추가 손실이야!! _(억울)_",
    "햄스터 라인 뜨면 다 소용없구나... _(깨달음)_",
    "동전이 와르르 햄스터한테 갔어!! _(속상)_",
    "이번 판은 완전 햄스터한테 진 거야!! _(패배)_",
    "햄스터가 배율을 몽땅 먹어버려써!! _(황당)_",
    "슬롯머신에 햄스터가 숨어 있었어!! 손해!! _(놀람)_",
    "햄스터 라인, 진짜 최악의 확률이야!! _(허탈)_",
    "이런... 햄스터한테 제대로 당했어!! _(울상)_",
)
_LOSE_LINES = (
    "꽝이야!! 아무 줄도 안 맞았어!! _(아쉬움)_",
    "이번엔 그냥 꽝!! 다음 판을 노려보자!! _(위로)_",
    "아쉽게 아무것도 안 맞았어!! _(안타까움)_",
    "완전 꽝!! 배팅액은 그대로 사라졌어!! _(속상)_",
    "이번 스핀은 허탕이었어!! _(아쉬움)_",
    "아무 줄도 안 걸렸네!! 다음엔 되겠지!! _(응원)_",
    "꽝!! 그림들이 다 따로 놀았어!! _(웃음)_",
    "이번엔 운이 안 따라줬어!! _(아쉬움)_",
    "아쉽다, 한 줄도 못 맞혔어!! _(안타까움)_",
    "허탕이야!! 다음 판에 기대해보자!! _(위로)_",
    "완전 꽝판이었어!! _(속상)_",
    "이번엔 그림들이 다 흩어져 있어!! _(웃음)_",
    "꽝!! 배팅액만 날아갔어!! _(아쉬움)_",
    "아무것도 안 맞아써!! 다음엔 잘될 거야!! _(응원)_",
    "이번 스핀은 실패!! _(안타까움)_",
    "아쉽게도 완전 꽝이야!! _(속상)_",
    "그림이 하나도 안 맞았어!! _(아쉬움)_",
    "이번엔 운이 없었나 봐!! _(위로)_",
    "꽝!! 다음 스핀을 노려보자!! _(응원)_",
    "허무하게 꽝이 나와버려써!! _(아쉬움)_",
)
_WIN_LINES = (
    "대박!! 배율 x{multiplier}!! _(환호)_",
    "우와아!! x{multiplier}배 터졌어!! _(흥분)_",
    "짜잔!! 배율 x{multiplier} 획득!! _(자랑)_",
    "성공!! x{multiplier}배로 불려써!! _(신남)_",
    "오오, x{multiplier}배 라인 완성!! _(놀람)_",
    "굿!! 배율 x{multiplier} 나왔다!! _(뿌듯)_",
    "완전 대박!! x{multiplier}배야!! _(환호)_",
    "라인 완성!! 배율 x{multiplier}!! _(신남)_",
    "이야, x{multiplier}배 라인이라니!! _(놀람)_",
    "슬롯머신 승리!! x{multiplier}배!! _(자랑)_",
    "짠!! 배율 x{multiplier}배로 정산!! _(뿌듯)_",
    "우와, 그림이 딱 맞았어!! x{multiplier}배!! _(흥분)_",
    "완벽해!! x{multiplier}배 라인!! _(환호)_",
    "성공적인 스핀!! 배율 x{multiplier}!! _(신남)_",
    "대단해!! x{multiplier}배나 됐어!! _(감탄)_",
    "이번 판 승리!! 배율 x{multiplier}!! _(자랑)_",
    "짜릿해!! x{multiplier}배 획득!! _(흥분)_",
    "슬롯머신이 터졌어!! x{multiplier}배!! _(환호)_",
    "라인이 딱딱 맞았어!! x{multiplier}배!! _(신남)_",
    "완전 좋았어!! 배율 x{multiplier}!! _(뿌듯)_",
)

_LIST_RULES_INTRO_LINES = (
    "슬롯머신 규칙 알려줄게!! _(진지)_",
    "이렇게 하면 대박날 수 있어!! _(자신감)_",
    "슬롯머신 하는 법 설명할게!! _(친절)_",
    "규칙부터 익히고 시작하자!! _(꼼꼼)_",
    "슬롯머신, 이렇게 굴러가!! _(설명)_",
    "먼저 규칙 확인해볼래?? _(권유)_",
    "슬롯머신 공략법이야!! _(자랑)_",
    "이거 알면 유리해!! 규칙이야!! _(웃음)_",
    "슬롯머신 설명서 가져왔어!! _(뿌듯)_",
    "규칙 모르면 손해야!! 알려줄게!! _(진지)_",
    "슬롯머신은 이렇게 하는 거야!! _(설명)_",
    "짜잔, 슬롯머신 규칙!! _(공개)_",
    "이거 읽고 도전해봐!! _(응원)_",
    "슬롯머신 하기 전에 이거부터!! _(추천)_",
    "규칙 요약해줄게!! _(친절)_",
    "슬롯머신 룰 정리했어!! _(정리)_",
    "이렇게 배율이 정해져!! _(설명)_",
    "슬롯머신, 알고 하면 더 재밌어!! _(웃음)_",
    "규칙 확인하고 배팅해봐!! _(권유)_",
    "슬롯머신 가이드 여기 있어!! _(안내)_",
)


def evaluate(grid: list[str]) -> tuple[int, bool]:
    """고정 그리드를 받아 (최종 배율, 햄스터 발동 여부)를 반환하는 순수 함수 — 랜덤 추출과
    분리해서 오프라인 테스트에서 특정 그리드를 그대로 넣어 검증할 수 있게 한다."""
    multiplier = 1
    hamster_hit = False
    for a, b, c in _LINES:
        if grid[a] == grid[b] == grid[c]:
            symbol = grid[a]
            if symbol == _HAMSTER:
                hamster_hit = True
            else:
                multiplier *= _MULTIPLIERS[symbol]
    return multiplier, hamster_hit


_ROW_LABELS = ("1번째 줄", "2번째 줄", "3번째 줄")
_SPIN_BUTTON_LABELS = ("1번째 줄 돌리기", "2번째 줄 돌리기", "3번째 줄 돌리기")
_SPIN_DONE_LABEL = "완료!!"
_UNSPUN_PLACEHOLDER = "❔"

_SPIN_PROMPT_LINES = (
    "슬롯머신 준비됐어!! 줄을 하나씩 돌려봐!! _(두근)_",
    "자, 버튼을 눌러서 한 줄씩 돌려줘!! _(기대)_",
    "슬롯머신 스탠바이!! 순서대로 돌려봐!! _(설렘)_",
    "줄마다 버튼이 있어!! 하나씩 눌러줘!! _(신남)_",
    "준비 완료!! 이제 돌려볼까?? _(두근)_",
    "슬롯머신이 기다리고 있어!! 줄을 돌려줘!! _(기대)_",
    "버튼 눌러서 한 줄씩 확인해볼래?? _(설렘)_",
    "자, 슬롯머신 시작이야!! 줄부터 돌려봐!! _(신남)_",
    "세 줄 다 돌려야 결과가 나와!! 시작해볼까?? _(긴장)_",
    "슬롯머신 가동 준비 끝!! 돌려줘!! _(두근)_",
    "한 줄씩 천천히 돌려보자!! _(설렘)_",
    "버튼이 세 개야!! 순서는 자유, 다 눌러줘!! _(안내)_",
    "슬롯머신 워밍업 완료!! 이제 돌려봐!! _(기대)_",
    "줄을 다 돌리면 결과를 알려줄게!! _(신남)_",
    "자, 어떤 줄부터 돌려볼래?? _(궁금)_",
    "슬롯머신 대기 중!! 버튼을 눌러줘!! _(두근)_",
    "이번엔 어떤 그림이 나올까?? 돌려봐!! _(설렘)_",
    "세 줄 다 돌리면 정산할게!! 시작!! _(기대)_",
    "슬롯머신 준비 끝!! 어서 돌려줘!! _(신남)_",
    "자, 각 줄을 눌러서 돌려볼래?? _(안내)_",
)


def _render_grid(grid: list[str | None]) -> str:
    rows = (" ".join(cell or _UNSPUN_PLACEHOLDER for cell in grid[i : i + 3]) for i in range(0, 9, 3))
    return "\n".join(f"{label} : {row}" for label, row in zip(_ROW_LABELS, rows))


def _build_embed(grid: list[str | None]) -> discord.Embed:
    embed = discord.Embed(
        title="🎰 개쩌는 슬롯머신!!", description=_render_grid(grid), color=VENDING_EMBED_COLOR
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


async def _settle(user_id: int, bet: int, grid: list[str]) -> tuple[str, discord.Embed]:
    """세 줄이 모두 채워진 뒤 정산 — 기존 handle_spin이 즉시 스핀 직후 하던 일과 동일
    (버튼으로 한 줄씩 돌리는 방식으로 바뀌었을 뿐 배율/보상 로직 자체는 그대로)."""
    multiplier, hamster_hit = evaluate(grid)
    embed = _build_embed(grid)

    if hamster_hit:
        penalty = await deduct_coins_clamped(user_id, bet)
        # 햄스터 라인이 뜨면 동전은 잃지만, 그래도 놀아준 성의는 인정해 호감도 +1을
        # 정확히 한 번만 지급한다(햄스터 줄이 몇 개든 penalty처럼 한 번만).
        affection_result = await add_affection(user_id, 1, "slot_hamster_penalty")
        text = random.choice(_HAMSTER_PENALTY_LINES)
        text += format_coin_notice(-penalty["deducted"], penalty["new_coins"])
        if affection_result["achievement_notice"]:
            text += f"\n{affection_result['achievement_notice']}"
        if affection_result["applied_amount"] != 0:
            text += format_affection_notice(
                affection_result["applied_amount"], affection_result["new_affection"]
            )
        return text, embed

    if multiplier == 1:
        return random.choice(_LOSE_LINES), embed

    result = await add_coins(user_id, bet * multiplier, method="slot_win")
    text = random.choice(_WIN_LINES).format(multiplier=multiplier)
    text += format_coin_notice(result["applied_amount"], result["new_coins"])
    text = maybe_append_capacity_advice(text, bet * multiplier, result)

    total_affection_delta = 0
    current_affection: int | None = None
    achievement_notices: list[str] = []
    if result["achievement_notice"]:
        achievement_notices.append(result["achievement_notice"])

    first_win = await award_achievement(user_id, achievements.gambling_hotline_1336.ID)
    if first_win["earned"]:
        total_affection_delta += first_win["applied_amount"]
        current_affection = first_win["new_affection"]
        achievement_notices.append(
            f"🏆 업적 달성: {achievements.format_name(achievements.gambling_hotline_1336)}!!"
        )
    if multiplier > _LEGENDARY_MULTIPLIER_THRESHOLD:
        legendary = await award_achievement(user_id, achievements.dev_never_tested_this.ID)
        if legendary["earned"]:
            total_affection_delta += legendary["applied_amount"]
            current_affection = legendary["new_affection"]
            achievement_notices.append(
                f"🏆 업적 달성: {achievements.format_name(achievements.dev_never_tested_this)}!!"
            )

    for notice in achievement_notices:
        text += f"\n{notice}"
    if total_affection_delta != 0:
        text += format_affection_notice(total_affection_delta, current_affection)
    return text, embed


class _SlotView(discord.ui.View):
    """가위바위보/홀짝과 동일한 결의 버튼 게임 — 다만 승부를 "고르는" 게 아니라 세 줄을
    각자 돌려서 "채우는" 방식이라 버튼이 3개 다 눌려야 결과가 나온다(순서는 자유)."""

    def __init__(self, user_id: int, bet: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.grid: list[str | None] = [None] * 9
        self._spun: set[int] = set()
        self.interaction: discord.Interaction | None = None

    async def on_timeout(self) -> None:
        """60초 동안 세 줄을 다 못 돌렸으면, 안 돌린 줄을 전부 자동으로 돌리고 그대로
        정산한다 — 내기(bet.py)와 달리 여기선 "선택"이 아니라 "공개"라서 환불이 아니라
        마저 진행하는 쪽이 자연스럽다."""
        if self.interaction is None or self.is_finished():
            return
        for row in range(3):
            if row not in self._spun:
                self._spun.add(row)
                self.grid[row * 3 : row * 3 + 3] = random.choices(SYMBOLS, k=3)
        text, embed = await _settle(self.user_id, self.bet, self.grid)
        try:
            await self.interaction.edit_original_response(content=text, embed=embed, view=None)
        except discord.HTTPException:
            logging.exception("Failed to edit slot prompt on timeout")

    async def _spin_row(
        self, interaction: discord.Interaction, row: int, button: discord.ui.Button
    ) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user(interaction, self.user_id):
            return
        if row in self._spun:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return

        self._spun.add(row)
        self.grid[row * 3 : row * 3 + 3] = random.choices(SYMBOLS, k=3)
        button.disabled = True
        button.label = _SPIN_DONE_LABEL

        if len(self._spun) < 3:
            await interaction.response.edit_message(embed=_build_embed(self.grid), view=self)
            return

        self.stop()
        text, embed = await _settle(self.user_id, self.bet, self.grid)
        await interaction.response.edit_message(content=text, embed=embed, view=self)

    @discord.ui.button(label=_SPIN_BUTTON_LABELS[0], style=discord.ButtonStyle.primary)
    async def spin_row_1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 0, button)

    @discord.ui.button(label=_SPIN_BUTTON_LABELS[1], style=discord.ButtonStyle.primary)
    async def spin_row_2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 1, button)

    @discord.ui.button(label=_SPIN_BUTTON_LABELS[2], style=discord.ButtonStyle.primary)
    async def spin_row_3(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 2, button)


async def handle_spin(interaction: discord.Interaction, bet: int) -> None:
    if bet < 1:
        await interaction.edit_original_response(content=_INVALID_BET_RESPONSE)
        return
    if not await spend_coins(interaction.user.id, bet):
        await interaction.edit_original_response(content=random.choice(INSUFFICIENT_FUNDS_LINES))
        return
    view = _SlotView(interaction.user.id, bet)
    await interaction.edit_original_response(
        content=random.choice(_SPIN_PROMPT_LINES), embed=_build_embed(view.grid), view=view
    )
    view.interaction = interaction


async def handle_rules() -> tuple[str, discord.Embed]:
    embed = discord.Embed(title="🎰 슬롯머신 규칙", description=_RULES_TEXT, color=VENDING_EMBED_COLOR)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return random.choice(_LIST_RULES_INTRO_LINES), embed
