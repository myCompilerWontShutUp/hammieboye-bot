import logging
import random
from datetime import datetime

import discord

import achievements
from core.base import EphemeralAutoDeleteView
from command.economy_common import (
    GAMBLING_EMBED_COLOR,
    INSUFFICIENT_FUNDS_LINES,
    TIMEOUT_SECONDS,
    BetAmountModal,
    ReplayView,
    RulesView,
    format_coin_notice,
    reject_if_already_resolved,
    reject_if_wrong_user_with_cta,
)
from events.scheduler import KST, format_footer_time
from db.achievements import award as award_achievement
from db.affection import add_affection, format_affection_notice
from db.users import get_user
from db.wallet import add_coins, deduct_coins_clamped, spend_coins

# CTA 문구("너도 {own_command}로 직접 해볼 수 있어!!")와 ReplayView에 그대로 넘긴다 —
# economy_common.reject_if_wrong_user_with_cta/ReplayView가 /내기·/도박 공용이라 자기
# 커맨드 이름을 매번 받는다.
_OWN_COMMAND = "/도박"

# "에에?? 이런거 하면 안대는데... 다시 한 번 생각해바!!" — /도박 실행 직후, 게임 종류를
# 고르는 ephemeral 프롬프트에 붙는 인트로 문구. /내기의 신난 톤과 달리 걱정스러운
# 톤으로 시작하되, 결국은 선택 UI를 열어준다.
_GAMBLE_SELECT_INTRO_LINES = (
    "에에?? 이런거 하면 안대는데... 다시 한 번 생각해바!! _(걱정)_",
    "도박이라니, 위험한데?? 그래도 정 원하면... _(불안)_",
    "흐음... 이거 진짜 할 거야?? 조심해야 대!! _(걱정)_",
    "에엥, 도박은 위험하다구!! 그래도 보여줄게!! _(한숨)_",
    "잠깐, 진짜 괜찮겠어?? 신중하게 골라!! _(걱정)_",
    "도박은 무서운 건데... 알겠어, 보여줄게!! _(불안)_",
    "이거 정말 할 거야?? 후회 없이 골라봐!! _(걱정)_",
    "에구, 위험한 놀이네... 그래도 열어줄게!! _(한숨)_",
    "도박이라니 조마조마해!! 신중하게 해!! _(긴장)_",
    "정말 괜찮겠어?? 그럼 골라봐!! _(걱정)_",
    "흠... 위험할 수도 있는데, 알겠어!! _(불안)_",
    "도박은 늘 걱정돼!! 그래도 원하면 진행할게!! _(걱정)_",
    "에엥?? 다시 생각해볼 순 없어?? 뭐, 좋아!! _(불안)_",
    "위험 부담이 큰데... 그래도 보여줄게!! _(한숨)_",
    "정신 바짝 차리고 해야 대!! 알겠지?? _(걱정)_",
    "도박이라니, 조심 또 조심해야 대!! _(긴장)_",
    "에에, 진짜야?? 알겠어, 골라봐!! _(불안)_",
    "이런 위험한 걸... 그래도 도와줄게!! _(걱정)_",
    "흐음, 마음의 준비는 됐어?? 그럼 시작할게!! _(진지)_",
    "도박은 위험하지만... 좋아, 열어줄게!! _(체념)_",
)

_RULES_INTRO_LINES = (
    "도박 규칙 알려줄게!! _(진지)_",
    "이렇게 하면 대박날 수 있어!! _(자신감)_",
    "도박 하는 법 설명할게!! _(친절)_",
    "규칙부터 익히고 시작하자!! _(꼼꼼)_",
    "도박, 이렇게 굴러가!! _(설명)_",
    "먼저 규칙 확인해볼래?? _(권유)_",
    "도박 공략법이야!! _(자랑)_",
    "이거 알면 유리해!! 규칙이야!! _(웃음)_",
    "도박 설명서 가져왔어!! _(뿌듯)_",
    "규칙 모르면 손해야!! 알려줄게!! _(진지)_",
    "도박은 이렇게 하는 거야!! _(설명)_",
    "짜잔, 도박 규칙!! _(공개)_",
    "이거 읽고 도전해봐!! _(응원)_",
    "도박 하기 전에 이거부터!! _(추천)_",
    "규칙 요약해줄게!! _(친절)_",
    "도박 룰 정리했어!! _(정리)_",
    "이렇게 배율이 정해져!! _(설명)_",
    "도박, 알고 하면 더 재밌어!! _(웃음)_",
    "규칙 확인하고 배팅해봐!! _(권유)_",
    "도박 가이드 여기 있어!! _(안내)_",
)

_RULES_OVERVIEW_TEXT = (
    "위험한 게임들을 모아둔 곳이야!! 지금은 슬롯머신 하나가 있어(앞으로 더 늘어날 "
    "수도 있어!!) — 아래 버튼에서 궁금한 게임을 골라봐!!\n\n"
    "한 번에 아주 크게 벌 수도 있지만, 패배하면 배팅액을 몽땅 잃을 수도 있으니 "
    "조심해!!"
)

_GRAPE, _PEANUT, _STRAWBERRY, _HAMSTER, _DIAMOND, _STAR, _SEVEN = (
    "🍇", "🥜", "🍓", "🐹", "💎", "⭐", "7️⃣",
)
SYMBOLS: tuple[str, ...] = (
    _GRAPE, _PEANUT, _STRAWBERRY, _HAMSTER, _DIAMOND, _STAR, _SEVEN,
)

# 햄스터(🐹)는 배율표에 없다 — 한 줄이라도 걸리면 배율 무관하게 전액 페널티로 분기.
# 심볼이 9종에서 7종으로 줄어(밤/치즈 제거, 2026-09-04) 칸당 적중 확률이 1/9 -> 1/7로
# 올라간다 — 확률이 너무 낮다는 피드백으로 당첨 라인이 나올 확률 자체를 높인 것.
_MULTIPLIERS: dict[str, int] = {
    _GRAPE: 2, _PEANUT: 2, _STRAWBERRY: 2, _STAR: 3, _DIAMOND: 10, _SEVEN: 77,
}
_SYMBOL_NAMES: dict[str, str] = {
    _GRAPE: "포도", _PEANUT: "땅콩", _STRAWBERRY: "딸기",
    _DIAMOND: "다이아", _STAR: "별", _SEVEN: "세븐", _HAMSTER: "햄스터",
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

_SLOT_MACHINE_RULE_TEXT = (
    "3x3 칸을 채워서 가로 3줄 + 세로 3줄 + 대각선 2줄, 총 8줄을 확인해!! "
    "한 줄에 같은 그림이 3개 모이면 그 그림의 배율이 곱해지고, 여러 줄이 동시에 완성되면 "
    "배율끼리 전부 곱해져!! (배팅액 x 최종 배율을 돌려받아)\n\n"
    "그림별 배율은 이래:\n"
    "🍇 포도 x2 · 🥜 땅콩 x2 · 🍓 딸기 x2\n"
    "⭐ 별 x3 · 💎 다이아 x10 · 7️⃣ 세븐 x77\n\n"
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


_ROW_NUMBER_EMOJI = ("1️⃣", "2️⃣", "3️⃣")
# 버튼 label에 emoji를 같이 안 섞고 discord.ui.button의 전용 emoji 슬롯을 쓴다 — 그래야
# 숫자가 아이콘 크기로 크게 나온다(label 문자열 안에 넣으면 다른 글자와 똑같이 작게
# 렌더링됨). "돌리기"/"완료됨"은 둘 다 3글자라 눌러도 버튼 너비가 거의 안 바뀐다.
_SPIN_LABEL = "돌림"
_SPIN_DONE_LABEL = "완료"
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
    # 마크다운 헤딩(#/##/###)은 title이 아니라 description 안에서만 실제로 크기가
    # 커진다 — 그래서 그리드를 title이 아니라 description에 두고, 줄마다 "## "를
    # 붙여 이모지가 일반 텍스트보다 크게 보이게 한다. 줄 번호와 그림 사이 구분선은
    # 얇은 "|"보다 눈에 잘 띄는 굵은 세로선("┃")으로 확실하게 나눈다.
    rows = (" ".join(cell or _UNSPUN_PLACEHOLDER for cell in grid[i : i + 3]) for i in range(0, 9, 3))
    return "\n".join(f"## {num} ┃ {row}" for num, row in zip(_ROW_NUMBER_EMOJI, rows))


def _build_embed(grid: list[str | None]) -> discord.Embed:
    embed = discord.Embed(
        title="🎰 개쩌는 슬롯머신!!", description=_render_grid(grid), color=GAMBLING_EMBED_COLOR
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


async def _settle(user_id: int, bet: int, grid: list[str]) -> tuple[str, discord.Embed]:
    """세 줄이 모두 채워진 뒤 정산 — 배율/보상 로직 자체는 커맨드 개편과 무관하게
    그대로 유지된다."""
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
        # total_affection_delta는 항상 업적 보너스(apply_day_multiplier=False)로만
        # 구성돼 있어 배율 적용 대상이 아니다 — "N x 배율"로 잘못 분해되지 않도록
        # 명시적으로 알린다.
        text += format_affection_notice(
            total_affection_delta, current_affection, multiplier_eligible=False
        )
    return text, embed


def _build_replay_view(user_id: int) -> ReplayView:
    """다시하기를 누르면 새 배팅액으로 슬롯머신을 다시 연다 — game_kind가 하나뿐이라
    bet.py의 _build_replay_view와 달리 게임 종류를 안 받는다."""

    async def _on_replay(interaction: discord.Interaction, amount: int) -> None:
        await _start_round(interaction, user_id, amount, edit=True)

    return ReplayView(user_id, _OWN_COMMAND, _on_replay)


class _SlotView(discord.ui.View):
    """가위바위보/홀짝과 동일한 결의 버튼 게임 — 다만 승부를 "고르는" 게 아니라 세 줄을
    각자 돌려서 "채우는" 방식이라 버튼이 3개 다 눌려야 결과가 나온다(순서는 자유)."""

    def __init__(self, user_id: int, bet: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.grid: list[str | None] = [None] * 9
        self._spun: set[int] = set()
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        """60초 동안 세 줄을 다 못 돌렸으면, 안 돌린 줄을 전부 자동으로 돌리고 그대로
        정산한다 — 내기(bet.py)와 달리 여기선 "선택"이 아니라 "공개"라서 환불이 아니라
        마저 진행하는 쪽이 자연스럽다. 정산 후에는 다른 완료 경로와 동일하게
        "다시하기" 버튼을 보여준다."""
        if self.message is None or self.is_finished():
            return
        for row in range(3):
            if row not in self._spun:
                self._spun.add(row)
                self.grid[row * 3 : row * 3 + 3] = random.choices(SYMBOLS, k=3)
        text, embed = await _settle(self.user_id, self.bet, self.grid)
        replay_view = _build_replay_view(self.user_id)
        try:
            await self.message.edit(content=text, embed=embed, view=replay_view)
            replay_view.message = self.message
        except discord.HTTPException:
            logging.exception("Failed to edit slot prompt on timeout")

    async def _spin_row(
        self, interaction: discord.Interaction, row: int, button: discord.ui.Button
    ) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
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
        replay_view = _build_replay_view(self.user_id)
        await interaction.response.edit_message(content=text, embed=embed, view=replay_view)
        replay_view.message = await interaction.original_response()

    @discord.ui.button(emoji=_ROW_NUMBER_EMOJI[0], label=_SPIN_LABEL, style=discord.ButtonStyle.primary)
    async def spin_row_1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 0, button)

    @discord.ui.button(emoji=_ROW_NUMBER_EMOJI[1], label=_SPIN_LABEL, style=discord.ButtonStyle.primary)
    async def spin_row_2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 1, button)

    @discord.ui.button(emoji=_ROW_NUMBER_EMOJI[2], label=_SPIN_LABEL, style=discord.ButtonStyle.primary)
    async def spin_row_3(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 2, button)


async def _start_round(interaction: discord.Interaction, user_id: int, bet: int, *, edit: bool) -> None:
    """모달에서 유효한 금액을 받은 뒤 실제 슬롯머신 판을 연다 — edit=False면 새 공개
    메시지로(게임 선택 직후 첫 판), edit=True면 지금 이 메시지를 고쳐 쓴다("다시하기").
    금액 검증(1~MAX_BET)은 모달이 이미 끝냈으니 여기서는 잔액만 확인한다."""
    if not await spend_coins(user_id, bet):
        await interaction.response.send_message(random.choice(INSUFFICIENT_FUNDS_LINES), ephemeral=True)
        return

    view = _SlotView(user_id, bet)
    content = random.choice(_SPIN_PROMPT_LINES)
    embed = _build_embed(view.grid)

    if edit:
        await interaction.response.edit_message(content=content, embed=embed, view=view)
    else:
        await interaction.response.send_message(content=content, embed=embed, view=view)
    view.message = await interaction.original_response()


class _GambleSelectView(EphemeralAutoDeleteView):
    """/도박 실행 직후 뜨는 ephemeral 프롬프트 — 본인에게만 보이므로 "다른 사람이
    눌렀을 때" 처리는 애초에 불필요하다(디스코드가 다른 사람에게 아예 안 보여준다).
    지금은 슬롯머신 하나뿐이지만, /내기의 _GameSelectView와 동일한 골격이라 게임이
    늘어도 버튼만 추가하면 된다."""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id

    @discord.ui.button(label="슬롯머신", style=discord.ButtonStyle.danger)
    async def slot_machine(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.bump()
        user = await get_user(self.user_id)
        balance = user["coins"] if user is not None else 0

        async def _on_valid(modal_interaction: discord.Interaction, amount: int) -> None:
            await _start_round(modal_interaction, self.user_id, amount, edit=False)
            # 게임이 실제로 시작됐으니(= 공개 메시지가 새로 생겼으니) 애초의 ephemeral
            # 선택 프롬프트는 이제 볼일이 없다 — 지운다.
            try:
                await self.interaction.delete_original_response()
            except discord.HTTPException:
                logging.exception("Failed to delete gamble-select prompt after game start")

        await interaction.response.send_modal(BetAmountModal(balance=balance, on_valid=_on_valid))


async def handle_gamble(interaction: discord.Interaction) -> None:
    """/도박 진입점 — 이미 ephemeral로 defer된 상태라고 가정하고 edit_original_response로
    게임 선택 프롬프트(인트로 문구 + 임베드 + 슬롯머신 버튼)를 보여준다."""
    user = await get_user(interaction.user.id)
    balance = user["coins"] if user is not None else 0

    embed = discord.Embed(title="🎰 도박", color=GAMBLING_EMBED_COLOR)
    embed.description = (
        f"현재 보유 동전 : {balance}개\n"
        "위험한 게임을 진행하여 한 번에 매우 많은 돈을 얻을 수 있지만, "
        "패배 시 배팅 금액을 모두 잃습니다.\n"
        "자세한 규칙은 /도박-규칙 을 통해 확인할 수 있습니다."
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    view = _GambleSelectView(interaction.user.id)
    await interaction.edit_original_response(
        content=random.choice(_GAMBLE_SELECT_INTRO_LINES), embed=embed, view=view
    )
    view.interaction = interaction


async def handle_rules() -> tuple[str, discord.Embed, discord.ui.View]:
    """/도박-규칙 진입점 — ephemeral. 개요 임베드 + 게임별 버튼(RulesView)을 보여주고,
    버튼을 누르면 그 게임의 상세 규칙으로 임베드만 바꿔치기한다."""
    embed = discord.Embed(title="🎰 도박 규칙", description=_RULES_OVERVIEW_TEXT, color=GAMBLING_EMBED_COLOR)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    view = RulesView(
        "🎰 도박 규칙", {"슬롯머신": _SLOT_MACHINE_RULE_TEXT}, color=GAMBLING_EMBED_COLOR
    )
    return random.choice(_RULES_INTRO_LINES), embed, view
