import logging
import random
from datetime import datetime
from typing import Awaitable, Callable

import discord

import achievements
import command.double_or_nothing as double_or_nothing
import command.horse_race as horse_race
from core.base import EphemeralAutoDeleteView
from command.economy_common import (
    GAMBLING_EMBED_COLOR,
    INSUFFICIENT_FUNDS_LINES,
    TIMEOUT_SECONDS,
    BetAmountModal,
    ReplayView,
    build_bet_receipt_embed,
    claim_active_or_reject,
    mark_active,
    mark_inactive,
    maybe_award_legendary_multiplier,
    reject_if_already_playing,
    reject_if_already_resolved,
    reject_if_wrong_user_with_cta,
)
from events import sleep_guard
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

_CHESTNUT, _STRAWBERRY, _PEANUT, _GRAPE, _STAR, _DIAMOND, _SEVEN, _HAMSTER = (
    "🌰", "🍓", "🥜", "🍇", "⭐", "💎", "7️⃣", "🐹",
)
# 표시 순서(2026-09-09 확정): 밤 → 딸기 → 땅콩 → 포도 → 별 → 다이아 → 세븐 → 햄스터.
# `/봇정보-확률공개`의 확률 목록도 이 SYMBOLS 순서를 그대로 따라간다(probability_summary()가
# 이 튜플을 순회해서 만들기 때문에 별도로 맞출 필요가 없다).
SYMBOLS: tuple[str, ...] = (
    _CHESTNUT, _STRAWBERRY, _PEANUT, _GRAPE, _STAR, _DIAMOND, _SEVEN, _HAMSTER,
)

# 햄스터(🐹)는 배율표에 없다 — 한 줄이라도 걸리면 배율 무관하게 전액 페널티로 분기.
# 심볼을 7종으로 줄였다가(밤/치즈 제거, 2026-09-04) 확률이 너무 높아졌다는 피드백으로
# 2026-09-07 9종으로 복원했었으나(밤/치즈 다시 추가), 2026-09-09 치즈만 다시
# 제거해 최종 8종으로 확정 — 칸당 적중 확률은 1/8. 밤은 포도/땅콩/딸기와 동일한 x2 등급.
_MULTIPLIERS: dict[str, int] = {
    _CHESTNUT: 2, _STRAWBERRY: 2, _PEANUT: 2, _GRAPE: 2,
    _STAR: 3, _DIAMOND: 10, _SEVEN: 77,
}
_SYMBOL_NAMES: dict[str, str] = {
    _CHESTNUT: "밤", _STRAWBERRY: "딸기", _PEANUT: "땅콩", _GRAPE: "포도",
    _STAR: "별", _DIAMOND: "다이아", _SEVEN: "세븐", _HAMSTER: "햄스터",
}

# 세븐 8라인 동시 완성(77^8) 등 배율이 지나치게 커지는 것을 막는 하드 상한
# (2026-09-07) — 이 이상은 절대 안 올라간다.
MAX_MULTIPLIER = 77
_MAX_MULTIPLIER_NOTICE = "🏆 최대 배수에 도달했어!! 이 이상은 더 안 올라가!! _(경악)_"

# 3x3 인덱스 0~8 기준 가로 3 + 세로 3 + 대각선 2 = 8라인.
_LINES: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)

# /봇정보-규칙(command/rules_info.py)이 그대로 넘기는 규칙 본문(§22-4 정중체).
SLOT_MACHINE_RULE_TEXT = (
    "🎰 슬롯머신\n\n"
    "- 3x3 칸을 채워 가로 3줄·세로 3줄·대각선 2줄, 총 8줄을 확인합니다.\n"
    "- 한 줄에 같은 그림이 3개 모이면 그 그림의 배율이 곱해지고, 여러 줄이 동시에 "
    "완성되면 배율끼리 전부 곱해집니다(배팅액 x 최종 배율을 돌려받습니다).\n\n"
    "그림별 배율:\n"
    "- 🌰 밤 · 🍓 딸기 · 🥜 땅콩 · 🍇 포도: x2\n"
    "- ⭐ 별: x3\n"
    "- 💎 다이아: x10\n"
    "- 7️⃣ 세븐: x77\n\n"
    f"- 최종 배율은 최대 x{MAX_MULTIPLIER}를 넘지 않습니다.\n"
    "- 🐹 햄스터가 한 줄이라도 걸리면 다른 배율은 모두 무시되고, 배팅액만큼 "
    "추가로 잃습니다."
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

def probability_summary() -> str:
    """`/봇정보-확률공개` 전용 — 심볼별 등장 확률·배율을 사람이 읽을 문자열로 만든다.
    SYMBOLS/_MULTIPLIERS/_SYMBOL_NAMES를 그대로 참조해서 만들기 때문에, 나중에
    심볼 구성이 또 바뀌어도(예: 다른 심볼 추가/제거) 이 문구가 별도 수정 없이
    자동으로 맞게 갱신된다 — 하드코딩된 확률 문구를 따로 관리하지 않기 위함."""
    cell_count = len(SYMBOLS)
    chance = 100 / cell_count
    lines = []
    for symbol in SYMBOLS:
        name = _SYMBOL_NAMES[symbol]
        multiplier = _MULTIPLIERS.get(symbol)
        detail = f"배율 x{multiplier}" if multiplier is not None else "배율 없음(햄스터 페널티, 배팅액 추가 손실)"
        lines.append(f"{symbol} {name} — 칸당 {chance:.1f}% ({detail})")
    return "\n".join(lines)


def evaluate(grid: list[str]) -> tuple[int, bool, bool]:
    """고정 그리드를 받아 (최종 배율, 햄스터 발동 여부, 상한 클램프 여부)를 반환하는
    순수 함수 — 랜덤 추출과 분리해서 오프라인 테스트에서 특정 그리드를 그대로 넣어
    검증할 수 있게 한다. capped는 클램프 전 원래 배율이 MAX_MULTIPLIER를 넘었는지."""
    multiplier = 1
    hamster_hit = False
    for a, b, c in _LINES:
        if grid[a] == grid[b] == grid[c]:
            symbol = grid[a]
            if symbol == _HAMSTER:
                hamster_hit = True
            else:
                multiplier *= _MULTIPLIERS[symbol]
    capped = multiplier > MAX_MULTIPLIER
    if capped:
        multiplier = MAX_MULTIPLIER
    return multiplier, hamster_hit, capped


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


# 슬롯머신 판 자체(스핀 버튼 3개)의 대기시간(2026-09-09, 기존 60초 → 10분으로 연장) —
# economy_common.TIMEOUT_SECONDS(60초)는 게임 선택 프롬프트 등 다른 용도에 계속
# 쓰이므로 안 건드리고, 슬롯머신 판 완결 전용으로 파일 로컬 상수를 새로 둔다. 이
# 10분은 스핀 버튼을 눌러도 초기화되지 않는다 — discord.py View는 timeout을
# 재대입(bump())하지 않는 한 최초 생성 시각 기준으로만 만료되므로, 이 뷰 어디에서도
# bump()를 호출하지 않는 것만으로 이 요구사항이 그대로 충족된다.
_SLOT_ROUND_TIMEOUT_SECONDS = 600


def _render_grid(grid: list[str | None]) -> str:
    # 2026-09-10 — 舊 마크다운 헤딩(## ) 트릭을 버리고 그리드를 footer로 옮겼다
    # (모바일에서 헤딩 크기 이모지가 제대로 안 보인다는 신고로 이모지 크기를 대폭
    # 줄임) — footer는 마크다운 헤딩이 안 먹히고 항상 작은 글자로만 렌더링되므로
    # 이 문제가 자연히 해결된다. 줄 번호와 그림 사이 구분선은 얇은 "|"보다 눈에 잘
    # 띄는 굵은 세로선("┃")으로 확실하게 나눈다.
    rows = (" ".join(cell or _UNSPUN_PLACEHOLDER for cell in grid[i : i + 3]) for i in range(0, 9, 3))
    return "\n".join(f"{num} ┃ {row}" for num, row in zip(_ROW_NUMBER_EMOJI, rows))


def _build_embed(grid: list[str | None]) -> discord.Embed:
    embed = discord.Embed(title="🎰 개쩌는 슬롯머신!!", color=GAMBLING_EMBED_COLOR)
    embed.set_footer(text=f"{_render_grid(grid)}\n{format_footer_time(datetime.now(KST))}")
    return embed


async def _settle(
    user_id: int, bet: int, before_coins: int, challenger_name: str, grid: list[str]
) -> tuple[str, discord.Embed, discord.Embed]:
    """세 줄이 모두 채워진 뒤 정산. before_coins는 판 시작 시 역산해둔 배팅 전
    잔액 — 반환하는 두 번째 임베드(그리드)와 세 번째 임베드(영수증)는
    `embeds=[grid, receipt]`로 함께 붙인다. challenger_name은 공개 메시지 맨
    위에 보여줄 도전자 이름."""
    multiplier, hamster_hit, capped = evaluate(grid)
    embed = _build_embed(grid)
    # 2026-09-09 — 마크다운 헤딩(`## `)을 붙여 크게 표시(그리드에 이미 쓰인 트릭과
    # 동일 — Discord는 일반 메시지 content=에서도 헤딩을 렌더링한다).
    challenger_line = f"## 🎯 도전자: {challenger_name}\n"

    if hamster_hit:
        penalty = await deduct_coins_clamped(user_id, bet)
        # 햄스터 라인이 뜨면 동전은 잃지만, 그래도 놀아준 성의는 인정해 호감도 +1을
        # 정확히 한 번만 지급한다(햄스터 줄이 몇 개든 penalty처럼 한 번만).
        affection_result = await add_affection(user_id, 1, "slot_hamster_penalty")
        text = challenger_line + random.choice(_HAMSTER_PENALTY_LINES)
        receipt_embed = build_bet_receipt_embed(before_coins, bet, penalty["new_coins"])
        if affection_result["applied_amount"] != 0:
            text += format_affection_notice(
                affection_result["applied_amount"], affection_result["new_affection"]
            )
        return text, embed, receipt_embed

    if multiplier == 1:
        user = await get_user(user_id)
        text = challenger_line + random.choice(_LOSE_LINES)
        receipt_embed = build_bet_receipt_embed(before_coins, bet, user["coins"])
        return text, embed, receipt_embed

    result = await add_coins(user_id, bet * multiplier, method="slot_win")
    text = challenger_line + random.choice(_WIN_LINES).format(multiplier=multiplier)
    if capped:
        text += f"\n{_MAX_MULTIPLIER_NOTICE}"
    receipt_embed = build_bet_receipt_embed(before_coins, bet, result["new_coins"])

    # 2026-09-10부로 업적 달성 알림(호감도 보너스 포함)은 award() 내부에서 별도
    # 글로벌 방송으로 처리된다 — 여기서는 조건이 맞을 때 부여만 시도한다.
    await award_achievement(user_id, achievements.gambling_hotline_1336.ID)
    # 슬롯머신 전용 16배 초과 기준을 /도박 전체 공용 64배 이상 기준으로 대체
    # (economy_common.py::maybe_award_legendary_multiplier가 문턱값을 관리).
    await maybe_award_legendary_multiplier(user_id, multiplier)

    return text, embed, receipt_embed


def _build_replay_view(user_id: int) -> ReplayView:
    """다시하기를 누르면 새 배팅액으로 슬롯머신을 다시 연다 — game_kind가 하나뿐이라
    bet.py의 _build_replay_view와 달리 게임 종류를 안 받는다. 2026-09-07부터 새
    판은 old_message(이 판의 메시지)를 고쳐쓰지 않고 새 공개 메시지로 열리고,
    old_message는 버튼만 제거해 기록으로 남긴다."""

    async def _on_replay(
        interaction: discord.Interaction, amount: int, old_message: "discord.Message | None"
    ) -> None:
        await _start_round(interaction, user_id, amount, is_replay=True)
        if old_message is not None:
            try:
                await old_message.edit(view=None)
            except discord.HTTPException:
                logging.exception("Failed to clear old slot message buttons after replay")

    return ReplayView(user_id, _OWN_COMMAND, _on_replay)


class _SlotView(discord.ui.View):
    """가위바위보/홀짝과 동일한 결의 버튼 게임 — 다만 승부를 "고르는" 게 아니라 세 줄을
    각자 돌려서 "채우는" 방식이라 버튼이 3개 다 눌려야 결과가 나온다(순서는 자유)."""

    def __init__(self, user_id: int, bet: int, before_coins: int, challenger_name: str) -> None:
        super().__init__(timeout=_SLOT_ROUND_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.before_coins = before_coins
        self.challenger_name = challenger_name
        self.grid: list[str | None] = [None] * 9
        self._spun: set[int] = set()
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        """10분 동안 세 줄을 다 못 돌렸으면(2026-09-09, 기존 60초에서 연장 — 버튼을
        눌러도 이 10분은 초기화되지 않는다), 안 돌린 줄을 전부 자동으로 돌리고 그대로
        정산한다 — 내기(bet.py)와 달리 여기선 "선택"이 아니라 "공개"라서 환불이 아니라
        마저 진행하는 쪽이 자연스럽다. 정산 후에는 다른 완료 경로와 동일하게
        "다시하기" 버튼을 보여준다."""
        if self.message is None or self.is_finished():
            return
        for row in range(3):
            if row not in self._spun:
                self._spun.add(row)
                self.grid[row * 3 : row * 3 + 3] = random.choices(SYMBOLS, k=3)
        text, embed, receipt_embed = await _settle(
            self.user_id, self.bet, self.before_coins, self.challenger_name, self.grid
        )
        replay_view = _build_replay_view(self.user_id)
        try:
            await self.message.edit(content=text, embeds=[embed, receipt_embed], view=replay_view)
            replay_view.message = self.message
        except discord.HTTPException:
            # ReplayView가 메시지에 못 붙으면 그 on_timeout이 영영 안 불려
            # mark_inactive도 영영 안 불린다 — 여기서 직접 풀어준다(horse_race.py
            # 감사 중 발견된 동일 계열 버그, 2026-09-09 수정).
            logging.exception("Failed to edit slot prompt on timeout")
            mark_inactive(self.user_id)

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
            # embeds=는 배열을 통째로 교체하므로, 정산 전(current=None) 영수증
            # 임베드를 매번 다시 만들어 그리드와 함께 넘겨야 유지된다.
            receipt_embed = build_bet_receipt_embed(self.before_coins, self.bet, None)
            await interaction.response.edit_message(
                embeds=[_build_embed(self.grid), receipt_embed], view=self
            )
            return

        self.stop()
        text, embed, receipt_embed = await _settle(
            self.user_id, self.bet, self.before_coins, self.challenger_name, self.grid
        )
        replay_view = _build_replay_view(self.user_id)
        try:
            await interaction.response.edit_message(content=text, embeds=[embed, receipt_embed], view=replay_view)
            replay_view.message = await interaction.original_response()
        except discord.HTTPException:
            logging.exception("Failed to edit slot settlement message")
            mark_inactive(self.user_id)

    @discord.ui.button(emoji=_ROW_NUMBER_EMOJI[0], label=_SPIN_LABEL, style=discord.ButtonStyle.primary)
    async def spin_row_1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 0, button)

    @discord.ui.button(emoji=_ROW_NUMBER_EMOJI[1], label=_SPIN_LABEL, style=discord.ButtonStyle.primary)
    async def spin_row_2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 1, button)

    @discord.ui.button(emoji=_ROW_NUMBER_EMOJI[2], label=_SPIN_LABEL, style=discord.ButtonStyle.primary)
    async def spin_row_3(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._spin_row(interaction, 2, button)


async def _start_round(
    interaction: discord.Interaction, user_id: int, bet: int, *, is_replay: bool = False
) -> None:
    """모달에서 유효한 금액을 받은 뒤 실제 슬롯머신 판을 새 공개 메시지로 연다 —
    첫 판이든 "다시하기"든 항상 새 메시지다(2026-09-07, 이전엔 다시하기가 같은
    메시지를 고쳐써서 이전 판 기록이 사라졌다). 금액 검증(1~MAX_BET)은 모달이 이미
    끝냈으니 여기서는 잔액만 확인한다.

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

    # 배팅이 성립한 시점부터 "진행 중"으로 표시한다(2026-09-09) — 정산 후
    # "다시하기" 버튼이 사라지기 전까지 /내기·/도박(승부예측 포함) 재진입을 막는다.
    if is_replay:
        mark_active(user_id, _OWN_COMMAND)

    # vending.py::_execute_purchase와 동일한 역산 — spend_coins가 차감 전 잔액을
    # 반환하지 않아서, 차감 후 조회한 잔액에 배팅액을 다시 더해 "기존 금액"을 구한다.
    user = await get_user(user_id)
    before_coins = user["coins"] + bet
    # 공개 메시지라 누구의 판인지 한눈에 보이게 도전자 이름을 맨 위에 적는다
    # (2026-09-07 신규) — interaction.user는 항상 이 판을 시작한 본인.
    challenger_name = interaction.user.display_name

    view = _SlotView(user_id, bet, before_coins, challenger_name)
    content = f"## 🎯 도전자: {challenger_name}\n" + random.choice(_SPIN_PROMPT_LINES)
    embed = _build_embed(view.grid)
    receipt_embed = build_bet_receipt_embed(before_coins, bet, None)

    await interaction.response.send_message(content=content, embeds=[embed, receipt_embed], view=view)
    view.message = await interaction.original_response()


class _GambleSelectView(EphemeralAutoDeleteView):
    """/도박 실행 직후 뜨는 ephemeral 프롬프트 — 본인에게만 보이므로 "다른 사람이
    눌렀을 때" 처리는 애초에 불필요하다(디스코드가 다른 사람에게 아예 안 보여준다).
    /내기의 _GameSelectView와 동일한 골격이라 게임이 늘어도 버튼만 추가하면 된다
    (2026-09-09 "승부예측" 추가로 처음 늘어남)."""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id

    async def _open_bet_modal(
        self, interaction: discord.Interaction, on_valid: Callable[[discord.Interaction, int], Awaitable[None]]
    ) -> None:
        self.bump()
        user = await get_user(self.user_id)
        balance = user["coins"] if user is not None else 0
        await interaction.response.send_modal(BetAmountModal(balance=balance, on_valid=on_valid))

    # 2026-09-10 — 셋 다 danger(빨강)로 통일했다(舊 슬롯머신만 danger/승부예측 primary/
    # 더블오어낫띵 secondary로 제각각이었음) — /내기의 세 버튼이 전부 primary로
    # 일관된 것과 동일한 원칙, "위험한 게임" 도메인이라는 걸 색으로도 통일해서
    # 드러낸다(舊 슬롯머신 단독일 때의 danger 의도를 세 게임 전체로 확장).
    @discord.ui.button(label="슬롯머신", style=discord.ButtonStyle.danger)
    async def slot_machine(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def _on_valid(modal_interaction: discord.Interaction, amount: int) -> None:
            await _start_round(modal_interaction, self.user_id, amount)
            # 게임이 실제로 시작됐으니(= 공개 메시지가 새로 생겼으니) 애초의 ephemeral
            # 선택 프롬프트는 이제 볼일이 없다 — 지운다.
            try:
                await self.interaction.delete_original_response()
            except discord.HTTPException:
                logging.exception("Failed to delete gamble-select prompt after game start")

        await self._open_bet_modal(interaction, _on_valid)

    @discord.ui.button(label="승부예측", style=discord.ButtonStyle.danger)
    async def horse_race_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def _on_valid(modal_interaction: discord.Interaction, amount: int) -> None:
            await horse_race.start_round(modal_interaction, self.user_id, amount)
            try:
                await self.interaction.delete_original_response()
            except discord.HTTPException:
                logging.exception("Failed to delete gamble-select prompt after horse race start")

        await self._open_bet_modal(interaction, _on_valid)

    @discord.ui.button(label="더블오어낫띵", style=discord.ButtonStyle.danger)
    async def double_or_nothing_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async def _on_valid(modal_interaction: discord.Interaction, amount: int) -> None:
            await double_or_nothing.start_round(modal_interaction, self.user_id, amount)
            try:
                await self.interaction.delete_original_response()
            except discord.HTTPException:
                logging.exception("Failed to delete gamble-select prompt after double-or-nothing start")

        await self._open_bet_modal(interaction, _on_valid)


async def handle_gamble(interaction: discord.Interaction) -> None:
    """/도박 진입점 — 이미 ephemeral로 defer된 상태라고 가정하고 edit_original_response로
    게임 선택 프롬프트(인트로 문구 + 임베드 + 슬롯머신 버튼)를 보여준다.

    /내기와 달리 취침 시간대에도 완전히 차단하지 않고 그대로 진행된다(2026-09-06) —
    대신 인트로 문구만 SLEEP_REPLY_GAMBLE로 바뀐다("몰래 하는" 컨셉).

    2026-09-09부터 이미 진행 중인 /내기·/도박 판이 있으면(슬롯머신·승부예측 크로스
    포함) 여기서 막힌다 — reject_if_already_playing이 이미 defer된 응답을 대신
    채운다."""
    if not await reject_if_already_playing(interaction, interaction.user.id):
        return
    user = await get_user(interaction.user.id)
    balance = user["coins"] if user is not None else 0

    embed = discord.Embed(title="🎰 도박", color=GAMBLING_EMBED_COLOR)
    embed.description = (
        f"현재 보유 동전 : {balance}개\n"
        "위험한 게임을 진행하여 한 번에 매우 많은 돈을 얻을 수 있지만, "
        "패배 시 배팅 금액을 모두 잃습니다.\n"
        "자세한 규칙은 `/봇정보-규칙`을 통해 확인할 수 있습니다."
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    view = _GambleSelectView(interaction.user.id)
    content = sleep_guard.wrap_text_if_asleep(
        interaction.channel_id,
        random.choice(_GAMBLE_SELECT_INTRO_LINES),
        override=sleep_guard.SLEEP_REPLY_GAMBLE,
    )
    await interaction.edit_original_response(content=content, embed=embed, view=view)
    view.interaction = interaction
