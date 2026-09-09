"""/도박의 "승부예측" 서브게임 — 햄스터 10마리 중 1·2·3위를 예측하는 경마 게임
(2026-09-09 신규). `command/slot.py`가 이미 556줄이라(`/도박` 최상위 명령어 파일)
`vending.py`↔`vending_catalog.py`와 동일한 원칙으로 이 큰 서브게임을 별 파일로
분리했다 — `command/slot.py::_GambleSelectView`가 `start_round()`만 얇게 호출해서
연동한다.

전체 골격은 슬롯머신/내기와 동일한 부품(economy_common.py의 BetAmountModal/
ReplayView/format_bet_receipt/reject_if_wrong_user_with_cta)을 그대로 재사용한다.
관리자 개입은 전혀 없다 — 전 과정이 유저 혼자 `/도박` 안에서 시작·완결한다."""

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

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
from events.scheduler import KST, format_footer_time

# CTA 문구("너도 /도박으로 직접 해볼 수 있어!!")와 ReplayView에 그대로 넘긴다 —
# slot.py의 _OWN_COMMAND와 동일한 값이지만 파일이 분리돼 있어 여기서도 따로 둔다.
_OWN_COMMAND = "/도박"


@dataclass(frozen=True)
class Hamster:
    number: int  # 1~10, 레인 번호 겸 표시 순서
    species: str
    name: str
    blurb: str


HAMSTERS: tuple[Hamster, ...] = (
    Hamster(1, "정글리안 햄스터", "잠보", "평소엔 졸려 보이지만 출발 신호가 울리면 눈이 번쩍! 의외의 막판 스퍼트가 특기예요."),
    Hamster(2, "골든 햄스터", "가루", "커다란 몸집으로 우다다다! 묵직하지만 한 번 속도가 붙으면 쉽게 멈추지 않아요."),
    Hamster(3, "펄 햄스터", "반죽", "말랑말랑해 보여도 달릴 땐 누구보다 진지해요. 작은 발로 열심히 트랙을 누빕니다."),
    Hamster(4, "캠벨 햄스터", "호두", "작고 야무진 단거리 선수! 빠른 스타트로 초반부터 앞서나가는 걸 좋아해요."),
    Hamster(5, "차이니즈 햄스터", "밤톨", "길쭉한 몸을 살려 날렵하게 질주! 빈틈을 발견하면 순식간에 앞으로 파고들어요."),
    Hamster(6, "크림 햄스터", "모찌", "폭신한 외모와 달리 승부욕은 진심! 통통 튀듯 달리며 결승선을 노립니다."),
    Hamster(7, "밴디드 햄스터", "쿠키", "허리띠처럼 멋진 무늬를 두른 트랙의 멋쟁이. 안정적인 페이스로 끝까지 달려요."),
    Hamster(8, "블랙 햄스터", "콩이", "새까만 작은 탄환! 조그만 몸으로 트랙을 쏜살같이 질주합니다."),
    Hamster(9, "사파이어 햄스터", "소다", "시원한 색깔처럼 달리기도 경쾌하게! 가볍고 빠른 발놀림이 매력 포인트예요."),
    Hamster(10, "로보로브스키 햄스터", "찹쌀", "몸집은 제일 작아도 속도는 무시 못 해요. 정신없이 우다다 달리는 스피드광입니다."),
)
_HAMSTERS_BY_NUMBER: dict[int, Hamster] = {h.number: h for h in HAMSTERS}

# 트랙 디자인 변수(요청사항 — 바로바로 바꿀 수 있게 전부 상수로 뺌).
_TRACK_LENGTH = 12
_FRAME_COUNT = 5
_FRAME_INTERVAL_SECONDS = 3
_MAX_FORWARD_STEP = 5
_MAX_BACKWARD_STEP = 2
_TRACK_EMPTY = "⬜"
_TRACK_BOUNDARY = "⬛"
_LANE_NUMBER_EMOJI = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
_MEDAL_EMOJI = ("🥇", "🥈", "🥉")

# 예측 단계 전용 대기시간(2026-09-09 신규) — 슬롯머신과 동일한 10분/무초기화 원칙
# (command/slot.py::_SLOT_ROUND_TIMEOUT_SECONDS 참고). 이 뷰도 절대 bump()를
# 호출하지 않는다 — 그래야 상호작용해도 10분이 리셋되지 않는다.
_PREDICTION_TIMEOUT_SECONDS = 600

_RANK_MULTIPLIERS: dict[int, int] = {1: 6, 2: 4, 3: 2}
_JACKPOT_MULTIPLIER = 100


# --- 순수 함수 (오프라인 테스트 가능) ---------------------------------------------


def compute_final_ranking(shuffle: Callable[[list[int]], None] = random.shuffle) -> list[int]:
    """햄스터 번호(1~10)를 무작위로 섞어 최종 1~10위 순서를 반환한다. shuffle을
    주입받는 순수 함수 형태라 테스트에서 결정적인 셔플 함수를 넣어 검증할 수 있다."""
    numbers = [h.number for h in HAMSTERS]
    shuffle(numbers)
    return numbers


def compute_final_positions(final_ranking: list[int]) -> dict[int, int]:
    """등수(0=1위)가 낮을수록 결승선(_TRACK_LENGTH)에 더 가깝게, 전부 서로 다른
    값에 배치한다. **1~3위(메달권)는 전부 결승선 이상**(render_track이 메달을
    붙이는 기준이 `pos >= _TRACK_LENGTH`이므로, 1위만 결승선에 정확히 닿게 하면
    2·3위는 메달을 영영 못 받는 버그가 된다 — 오프라인 테스트로 실제 발견) —
    1위는 결승선+2, 2위는 +1, 3위는 결승선 그 자리에 서로 다르게 도착시켜 셋 다
    "완주"로 인정되면서도 값 자체는 겹치지 않는다. 4위 이하는 결승선 뒤 트랙
    위에 한 칸씩 떨어져 남는다."""
    positions: dict[int, int] = {}
    for rank, number in enumerate(final_ranking):
        if rank < len(_MEDAL_EMOJI):
            positions[number] = _TRACK_LENGTH + (len(_MEDAL_EMOJI) - 1 - rank)
        else:
            positions[number] = max(_TRACK_LENGTH - (rank - len(_MEDAL_EMOJI) + 1), 0)
    return positions


def compute_frame_positions(
    final_ranking: list[int],
    *,
    rng: random.Random | None = None,
) -> list[dict[int, int]]:
    """말마다 완전히 독립적으로 프레임별 위치를 계산한다(리스트 인덱스 0 = 첫 번째
    프레임). 각 말의 최종 목표 칸(compute_final_positions)을 먼저 정해두고, 프레임
    1~(N-1)은 그 목표를 향한 선형 보간 기준값에 무작위 지터를 더해 계산하되 직전
    프레임 대비 이동폭을 [-_MAX_BACKWARD_STEP, +_MAX_FORWARD_STEP]로 클램프한다 —
    "최대 앞으로 5칸, 뒤로도 허용" 요청을 정확히 구현한다. 마지막 프레임은 지터 없이
    목표 칸 그대로 고정해 반드시 도착을 보장한다."""
    r = rng or random
    final_positions = compute_final_positions(final_ranking)
    frames: list[dict[int, int]] = []
    previous = {number: 0 for number in final_positions}
    for frame_index in range(1, _FRAME_COUNT + 1):
        current: dict[int, int] = {}
        # 이 프레임 "다음"(포함해서 마지막 강제 도착 프레임까지)에 남은 프레임 수 —
        # 이 수만큼의 최대 이동폭 예산 안에서 반드시 target에 도달할 수 있어야 한다.
        frames_left = _FRAME_COUNT - frame_index
        for number, target in final_positions.items():
            if frame_index == _FRAME_COUNT:
                current[number] = target
                continue
            baseline = round(target * frame_index / _FRAME_COUNT)
            jitter = r.randint(-_MAX_BACKWARD_STEP, _MAX_FORWARD_STEP)
            candidate = baseline + jitter
            prev = previous[number]
            # 세 제약(직전 프레임 대비 이동폭 / 남은 프레임 예산으로 target 도달 가능 /
            # 트랙 범위)의 교집합으로 **한 번에** 클램프한다 — 순차적으로 따로따로
            # 클램프하면 마지막 클램프가 앞선 클램프의 경계를 다시 깨버릴 수 있다
            # (실제로 이 버그가 재현돼 오프라인 테스트에서 잡혔다 — 목표 지점 강제
            # 스냅 직전 프레임이 이동폭 상한을 초과하는 사례). 각 프레임이 항상 이
            # 교집합 안에 있으면 다음 프레임에서도 다시 도달 가능함이 귀납적으로
            # 보장된다(직전 프레임 최대 이동폭이 다음 프레임의 도달 가능 범위 하한을
            # 정확히 커버하도록 설계됨).
            lo = max(prev - _MAX_BACKWARD_STEP, target - frames_left * _MAX_FORWARD_STEP, 0)
            hi = min(prev + _MAX_FORWARD_STEP, target + frames_left * _MAX_BACKWARD_STEP, _TRACK_LENGTH)
            candidate = max(lo, min(hi, candidate))
            current[number] = candidate
        frames.append(current)
        previous = current
    return frames


def evaluate_payout(predictions: dict[int, int], final_ranking: list[int]) -> int:
    """예측(등수 → 햄스터 번호)과 실제 최종 순위(인덱스 0 = 1위)를 비교해 최종 배율을
    반환하는 순수 함수. 3개 등수를 모두 정확히 맞히면 다른 계산을 무시하고 고정
    100배(잭팟). 하나도 못 맞히면 0을 반환한다(호출부가 이 값이면 add_coins를 아예
    안 부르고 배팅액 전액 손실로 처리 — 슬롯머신의 multiplier==1과 동일한 원칙).
    그 외엔 맞힌 등수의 배율만 전부 곱한다(3위 x2 · 2위 x4 · 1위 x6, 서로 중첩)."""
    correctness = {rank: predictions.get(rank) == final_ranking[rank - 1] for rank in (1, 2, 3)}
    if all(correctness.values()):
        return _JACKPOT_MULTIPLIER
    multiplier = 1
    hit_any = False
    for rank, correct in correctness.items():
        if correct:
            multiplier *= _RANK_MULTIPLIERS[rank]
            hit_any = True
    return multiplier if hit_any else 0


def render_track(positions: dict[int, int], finished_order: list[int]) -> str:
    """레인 10개를 `{번호이모지} {트랙}` 형식으로 렌더링한다. 2026-09-10 — 舊 마크다운
    헤딩(## ) 트릭을 버리고 embed footer로 옮겼다(모바일에서 헤딩 크기 이모지가
    제대로 안 보인다는 신고로 이모지 크기를 대폭 줄임, command/slot.py
    ::_render_grid와 동일한 원칙). finished_order는 이미 결승(_TRACK_LENGTH)에
    도달한 순서대로 담긴 번호 리스트 — 그 레인 끝에 도착 순서대로 메달 이모지를
    붙인다."""
    medal_by_number = {
        number: _MEDAL_EMOJI[i] for i, number in enumerate(finished_order) if i < len(_MEDAL_EMOJI)
    }
    lines = []
    for hamster in HAMSTERS:
        pos = positions.get(hamster.number, 0)
        marker = _LANE_NUMBER_EMOJI[hamster.number - 1]
        cells = [marker if i == pos else _TRACK_EMPTY for i in range(_TRACK_LENGTH)]
        row = _TRACK_BOUNDARY + "".join(cells) + _TRACK_BOUNDARY
        if pos >= _TRACK_LENGTH:
            row += marker
            medal = medal_by_number.get(hamster.number)
            if medal:
                row += medal
        lines.append(row)
    return "\n".join(lines)


# --- 문구 풀 -----------------------------------------------------------------------

_PREDICTION_INTRO_LINES = (
    "이번엔 승부예측이야!! 3마리를 정확히 골라봐!! _(기대)_",
    "햄스터 경마 시작이야!! 1등부터 3등까지 골라줘!! _(신남)_",
    "누가 우승할지 골라봐!! 재밌겠다!! _(설렘)_",
    "10마리 중에 셋만 딱 골라내면 대박이야!! _(흥분)_",
    "자, 경마 예측 타임!! 신중하게 골라봐!! _(진지)_",
    "1등, 2등, 3등을 예측해봐!! 다 맞히면 100배야!! _(놀람)_",
    "햄스터들이 준비하고 있어!! 예측부터 해줘!! _(기대)_",
    "누가 빠를지 아무도 몰라!! 그래도 골라봐!! _(웃음)_",
    "경마 승부예측!! 감으로 골라도 괜찮아!! _(장난)_",
    "이 게임은 순서가 중요해!! 잘 골라봐!! _(진지)_",
    "3마리 순서를 정확히 맞혀야 돼!! 파이팅!! _(응원)_",
    "누가 결승선을 먼저 통과할까?? 예측해봐!! _(궁금)_",
    "햄스터 경마는 처음이지?? 재밌게 골라봐!! _(웃음)_",
    "이번 판은 완전 새로운 게임이야!! _(신남)_",
    "1등부터 차근차근 골라볼래?? _(권유)_",
    "예측이 딱 맞으면 짜릿할 거야!! _(기대)_",
    "경주마 대신 경주 햄스터야!! 골라봐!! _(웃음)_",
    "신중하게, 근데 재밌게 골라봐!! _(설렘)_",
    "누가 1등 할지 감이 와?? 골라봐!! _(궁금)_",
    "자, 승부예측 시작한다!! 준비됐지?? _(흥분)_",
)

# 출발선 프레임 전용(2026-09-10 신규) — 말들이 트랙 맨 앞에 나란히 서있는 모습을
# 실제로 한 번 보여준 뒤에 달리기 시작한다(舊에는 이 장면 없이 바로 진행 중인
# 첫 프레임부터 보였다).
_RACE_START_LINES = (
    "다들 출발선에 섰어!! 준비 완료!! _(긴장)_",
    "자, 다들 준비됐지?? 출발선이야!! _(두근)_",
    "출발 신호만 기다리고 있어!! _(긴장)_",
    "다들 자리 잡았다!! 곧 출발이야!! _(설렘)_",
    "긴장되는 출발선!! 누가 먼저 튀어나갈까?? _(흥분)_",
    "레디... 이제 곧 달려나갈 거야!! _(집중)_",
    "다들 결의에 찬 눈빛이야!! 출발 직전!! _(진지)_",
    "출발선에 정렬 완료!! 곧 시작한다!! _(기대)_",
)

_RACE_RUNNING_LINES = (
    "출발!! 다들 열심히 달리고 있어!! _(응원)_",
    "우와, 접전이야!! 누가 앞서나갈까?? _(긴장)_",
    "달려, 달려!! 결승선이 얼마 안 남았어!! _(응원)_",
    "이야, 순위가 계속 바뀌고 있어!! _(놀람)_",
    "다들 최선을 다해 달리고 있어!! _(감동)_",
    "누가 1등으로 들어올지 아직 몰라!! _(긴장)_",
    "우다다다!! 트랙이 뜨거워!! _(흥분)_",
    "결승선이 코앞이야!! 조금만 더!! _(응원)_",
    "예측대로 될까?? 두근두근해!! _(설렘)_",
    "막판 스퍼트!! 순위가 요동치고 있어!! _(놀람)_",
)

_LOSE_LINES = (
    "아쉽다, 예측이 하나도 안 맞았어!! _(안타까움)_",
    "이번엔 완전 빗나갔어!! 다음에 도전해봐!! _(위로)_",
    "아쉽게도 하나도 못 맞혔네!! _(속상)_",
    "예측이 다 틀렸어!! 다음 판을 노려보자!! _(응원)_",
    "이번 경주는 완전 예상 밖이었네!! _(허탈)_",
    "아까비!! 다음엔 더 잘 맞힐 거야!! _(위로)_",
    "순위가 예측이랑 완전 달랐어!! _(아쉬움)_",
    "이번엔 운이 안 따라줬나 봐!! _(안타까움)_",
    "예측 실패!! 그래도 재밌었지?? _(웃음)_",
    "다 틀렸지만 경주는 짜릿했지?? _(위로)_",
)

_WIN_LINES = (
    "우와, 예측 적중!! x{multiplier}배야!! _(환호)_",
    "대박!! 배율 x{multiplier}배 획득!! _(흥분)_",
    "짜잔, 정확히 맞혔어!! x{multiplier}배!! _(자랑)_",
    "예측 성공!! x{multiplier}배로 불려써!! _(신남)_",
    "오오, x{multiplier}배 적중!! _(놀람)_",
    "굿!! 배율 x{multiplier} 나왔다!! _(뿌듯)_",
    "완전 대박!! x{multiplier}배야!! _(환호)_",
    "예측 성공!! 배율 x{multiplier}!! _(신남)_",
    "이야, x{multiplier}배 적중이라니!! _(놀람)_",
    "승부예측 승리!! x{multiplier}배!! _(자랑)_",
)

_JACKPOT_LINES = (
    "말도 안 돼!! 세 마리 전부 정확히 맞혔어!! x100배!! _(경악)_",
    "완벽 적중!! 1등부터 3등까지 전부 맞혔어!! _(환호)_",
    "이건 실화야?? 100배 잭팟이야!! _(경악)_",
    "믿을 수가 없어!! 완벽한 예측, x100배!! _(감동)_",
    "우와아아!! 셋 다 정확히!! 잭팟이야!! _(흥분)_",
)

# RulesView가 embed.description으로 그대로 보여주는 문구라 시스템 정중체로 고정한다
# (§22-4).
HORSE_RACE_RULE_TEXT = (
    "🐹 승부예측\n\n"
    "햄스터 10마리 중 1등·2등·3등을 예측하는 경마 게임입니다. \"1등 예측하기\"·"
    "\"2등 예측하기\"·\"3등 예측하기\" 버튼으로 각 등수에 들어올 햄스터를 하나씩 "
    "고르면 됩니다(같은 햄스터를 두 등수에 중복으로 고를 수 없습니다).\n\n"
    "적중한 등수의 배율은 서로 곱해집니다 — 3위 적중 x2, 2위 적중 x4, 1위 적중 x6"
    "(예: 1위·3위를 모두 맞히면 x12). 세 등수를 전부 정확히 맞히면 다른 계산과 "
    "무관하게 항상 x100을 받습니다. 하나도 맞히지 못하면 배팅액을 전부 잃습니다.\n\n"
    "예측은 10분 안에 완료해야 하며, 완료하지 않으면 남은 등수가 무작위로 채워진 "
    "뒤 자동으로 경주가 시작됩니다."
)


# --- 임베드/렌더링 헬퍼 -------------------------------------------------------------


def _roster_field_value() -> str:
    return "\n".join(f"{h.number}번 {h.species} : {h.name} — {h.blurb}" for h in HAMSTERS)


# 기본(예측 단계) 임베드에 들어가는 간단한 규칙+배수 요약(2026-09-10 신규) — 舊에는
# "선수 정보 보기" 버튼을 안 눌러도 참가 선수 소개가 통째로 나와 있었는데, 그 정보는
# 그 버튼을 눌러야만 보이는 게 맞다는 지적으로 여기서는 규칙/배수만 짧게 안내한다.
_QUICK_RULES_FIELD_VALUE = (
    "적중한 등수의 배율은 서로 곱해집니다 — 3위 x2, 2위 x4, 1위 x6. 세 등수를 전부 "
    "맞히면 x100(잭팟)입니다. 하나도 못 맞히면 배팅액을 전부 잃습니다."
)


def _prediction_embed(predictions: dict[int, int]) -> discord.Embed:
    embed = discord.Embed(title="🐹 햄스터 경마 승부예측", color=GAMBLING_EMBED_COLOR)
    lines = []
    for rank in (1, 2, 3):
        number = predictions.get(rank)
        label = f"{number}번 {_HAMSTERS_BY_NUMBER[number].name}" if number else "???"
        lines.append(f"{rank}등 예측: {label}")
    embed.description = "\n".join(lines)
    embed.add_field(name="🔢 배율 안내", value=_QUICK_RULES_FIELD_VALUE, inline=False)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


def _prediction_content(challenger_name: str, before_coins: int, bet: int) -> str:
    return (
        f"## 🎯 도전자: {challenger_name}\n"
        + random.choice(_PREDICTION_INTRO_LINES)
        + "\n\n"
        + format_bet_receipt(before_coins, bet, None)
    )


def _build_race_embed(positions: dict[int, int], finished_order: list[int]) -> discord.Embed:
    embed = discord.Embed(title="🐹 햄스터 경마", color=GAMBLING_EMBED_COLOR)
    track_text = render_track(positions, finished_order)
    embed.set_footer(text=f"{track_text}\n{format_footer_time(datetime.now(KST))}")
    return embed


# --- 모달 --------------------------------------------------------------------------


class _RankPickModal(discord.ui.Modal):
    """등수 하나를 예측할 햄스터를 고르는 모달 — discord.py 2.7의 모달 전용
    `RadioGroup`(2~10개 옵션, `.value`가 선택된 값 하나만 반환)을 씀. 이미 다른
    등수에 예측된 햄스터는 옵션에서 아예 제외해 중복 선택 자체가 불가능하다."""

    def __init__(
        self,
        rank: int,
        remaining: list[Hamster],
        on_pick: Callable[[discord.Interaction, int], Awaitable[None]],
    ) -> None:
        super().__init__(title=f"{rank}등 예측")
        self._on_pick = on_pick
        self._radio = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(label=f"{h.number}번 {h.species} : {h.name}", value=str(h.number))
                for h in remaining
            ]
        )
        self.add_item(
            discord.ui.Label(text=f"{rank}등을 예측할 햄스터를 골라주세요", component=self._radio)
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_pick(interaction, int(self._radio.value))


# --- 예측 단계 뷰 --------------------------------------------------------------------


class _PredictionView(discord.ui.View):
    """예측 단계 뷰 — 10분 동안 1~3등 예측을 전부 안 채우면 남은 등수를 무작위로
    채운 뒤 자동으로 경주를 시작한다. **절대 bump()를 호출하지 않는다** — 그래야
    상호작용해도 이 10분이 초기화되지 않는다(core/base.py::EphemeralAutoDeleteView의
    bump() 패턴과 의도적으로 다른 부분, command/slot.py::_SlotView와 동일한 원칙)."""

    def __init__(self, challenger_id: int, challenger_name: str, before_coins: int, bet: int) -> None:
        super().__init__(timeout=_PREDICTION_TIMEOUT_SECONDS)
        self.challenger_id = challenger_id
        self.challenger_name = challenger_name
        self.before_coins = before_coins
        self.bet = bet
        self.predictions: dict[int, int] = {}
        self.message: discord.Message | None = None

        for rank in (1, 2, 3):
            button = discord.ui.Button(
                label=f"{rank}등 예측하기", style=discord.ButtonStyle.primary, row=0
            )
            button.callback = self._make_rank_callback(rank, button)
            self.add_item(button)

        info_button = discord.ui.Button(label="선수 정보 보기", style=discord.ButtonStyle.secondary, row=1)
        info_button.callback = self._show_info
        self.add_item(info_button)

    def _make_rank_callback(
        self, rank: int, button: discord.ui.Button
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def _callback(interaction: discord.Interaction) -> None:
            if not await reject_if_already_resolved(self, interaction):
                return
            if not await reject_if_wrong_user_with_cta(interaction, self.challenger_id, _OWN_COMMAND):
                return
            chosen_numbers = set(self.predictions.values())
            remaining = [h for h in HAMSTERS if h.number not in chosen_numbers]

            async def _on_pick(modal_interaction: discord.Interaction, number: int) -> None:
                self.predictions[rank] = number
                self.remove_item(button)
                if len(self.predictions) == 3:
                    self._add_start_button()
                await modal_interaction.response.edit_message(embed=_prediction_embed(self.predictions), view=self)

            await interaction.response.send_modal(_RankPickModal(rank, remaining, _on_pick))

        return _callback

    async def _show_info(self, interaction: discord.Interaction) -> None:
        """선수 정보 보기는 challenger 제한이 없다 — 이 뷰의 유일한 "누구나 클릭
        가능" 버튼(사용자 요청)."""
        embed = discord.Embed(title="🐹 참가 선수 정보", description=_roster_field_value(), color=GAMBLING_EMBED_COLOR)
        embed.set_footer(text=format_footer_time(datetime.now(KST)))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _add_start_button(self) -> None:
        button = discord.ui.Button(label="경기 시작", style=discord.ButtonStyle.success, row=0)

        async def _callback(interaction: discord.Interaction) -> None:
            if not await reject_if_already_resolved(self, interaction):
                return
            if not await reject_if_wrong_user_with_cta(interaction, self.challenger_id, _OWN_COMMAND):
                return
            self.stop()
            await _run_race(self, interaction)

        button.callback = _callback
        self.add_item(button)

    async def on_timeout(self) -> None:
        """10분 동안 못 채운 등수는 무작위(중복 없이)로 채운 뒤 곧바로 경주를
        시작한다 — "경기 시작" 수동 클릭과 동일한 _run_race()를 그대로 공유해
        로직이 갈리지 않는다. 이미 3개 등수가 다 채워진 채로(= "경기 시작"만 안
        누른 채로) 시간이 지난 경우도 채울 게 없으니 그대로 경주를 시작한다."""
        if self.message is None or self.is_finished():
            return
        chosen_numbers = set(self.predictions.values())
        leftover = [h.number for h in HAMSTERS if h.number not in chosen_numbers]
        random.shuffle(leftover)
        for rank in (1, 2, 3):
            if rank not in self.predictions:
                self.predictions[rank] = leftover.pop()
        await _run_race(self, None)


def _build_replay_view(user_id: int) -> ReplayView:
    """다시하기를 누르면 새 배팅액으로 예측 단계를 다시 연다 — bet.py/slot.py의
    동일한 헬퍼와 같은 원칙(항상 새 공개 메시지, old_message는 버튼만 제거)."""

    async def _on_replay(
        interaction: discord.Interaction, amount: int, old_message: "discord.Message | None"
    ) -> None:
        await start_round(interaction, user_id, amount, is_replay=True)
        if old_message is not None:
            try:
                await old_message.edit(view=None)
            except discord.HTTPException:
                logging.exception("Failed to clear old horse race message buttons after replay")

    return ReplayView(user_id, _OWN_COMMAND, _on_replay)


async def _run_race(view: _PredictionView, trigger_interaction: discord.Interaction | None) -> None:
    """"경기 시작" 수동 클릭과 10분 타임아웃 자동 시작이 공유하는 경주 진행 함수 —
    최종 순위와 5개 프레임의 위치를 미리 전부 계산한 뒤, 출발선 프레임(전부 트랙
    맨 앞)을 먼저 한 번 보여주고, 그다음 3초 간격으로 메시지를 고쳐써서 애니메이션
    처럼 보여주고 마지막에 정산한다. trigger_interaction이 있으면(수동 클릭) 출발선
    프레임만 그 인터랙션으로 응답해 3초 제한 내에 확인시키고, 이후 프레임은 전부
    view.message.edit()으로 진행한다(타임아웃 경로는 애초에 살아있는 인터랙션이
    없어 처음부터 message.edit()만 쓴다)."""
    final_ranking = compute_final_ranking()
    frames = compute_frame_positions(final_ranking)
    empty_view = discord.ui.View()  # 애니메이션 중엔 상호작용 불가(중복 시작 방지)
    finished_order: list[int] = []

    # 출발선 프레임(2026-09-10 신규) — compute_frame_positions()는 이미 진행된
    # 위치부터 시작해서, 이걸 그대로 첫 프레임으로 쓰면 말들이 나란히 서있는 출발
    # 장면 없이 바로 달리는 중인 모습부터 보이는 문제가 있었다. 여기서 전부
    # 0(트랙 맨 앞)인 프레임을 애니메이션 맨 앞에 명시적으로 하나 더 보여준다.
    start_positions = {h.number: 0 for h in HAMSTERS}
    start_embed = _build_race_embed(start_positions, finished_order)
    start_content = f"## 🎯 도전자: {view.challenger_name}\n{random.choice(_RACE_START_LINES)}"

    if trigger_interaction is not None:
        try:
            await trigger_interaction.response.edit_message(content=start_content, embed=start_embed, view=empty_view)
            view.message = await trigger_interaction.original_response()
        except discord.HTTPException:
            logging.exception("Failed to edit horse race start frame")
            # 이 실패로 _settle_race까지 절대 못 가서(경주 자체가 시작도
            # 못 함) mark_inactive를 대신할 곳이 없다 — 여기서 직접 풀어준다
            # (2026-09-09, 크로스블록 잠금 영구 미해제 버그 수정).
            mark_inactive(view.challenger_id)
            return
    else:
        if view.message is None:
            mark_inactive(view.challenger_id)
            return
        try:
            await view.message.edit(content=start_content, embed=start_embed, view=empty_view)
        except discord.HTTPException:
            logging.exception("Failed to edit horse race start frame")
            mark_inactive(view.challenger_id)
            return

    await asyncio.sleep(_FRAME_INTERVAL_SECONDS)

    for frame_index, positions in enumerate(frames):
        for number in final_ranking:
            if positions[number] >= _TRACK_LENGTH and number not in finished_order:
                finished_order.append(number)
        embed = _build_race_embed(positions, finished_order)
        content = f"## 🎯 도전자: {view.challenger_name}\n{random.choice(_RACE_RUNNING_LINES)}"

        if view.message is None:
            mark_inactive(view.challenger_id)
            return
        try:
            await view.message.edit(content=content, embed=embed, view=empty_view)
        except discord.HTTPException:
            logging.exception("Failed to edit horse race animation frame")
            mark_inactive(view.challenger_id)
            return

        if frame_index < _FRAME_COUNT - 1:
            await asyncio.sleep(_FRAME_INTERVAL_SECONDS)

    await _settle_race(view, final_ranking)


async def _settle_race(view: _PredictionView, final_ranking: list[int]) -> None:
    multiplier = evaluate_payout(view.predictions, final_ranking)

    result_lines = []
    for rank in (1, 2, 3):
        predicted_number = view.predictions.get(rank)
        actual_number = final_ranking[rank - 1]
        mark = "✅" if predicted_number == actual_number else "❌"
        predicted_name = _HAMSTERS_BY_NUMBER[predicted_number].name if predicted_number else "???"
        result_lines.append(f"{rank}등 예측: {predicted_name} {mark}")

    if multiplier == 0:
        user = await get_user(view.challenger_id)
        current_coins = user["coins"] if user is not None else view.before_coins - view.bet
        text = random.choice(_LOSE_LINES)
        receipt = format_bet_receipt(view.before_coins, view.bet, current_coins)
    else:
        result = await add_coins(view.challenger_id, view.bet * multiplier, method="horse_race_win")
        if multiplier >= _JACKPOT_MULTIPLIER:
            text = random.choice(_JACKPOT_LINES)
        else:
            text = random.choice(_WIN_LINES).format(multiplier=multiplier)
        receipt = format_bet_receipt(view.before_coins, view.bet, result["new_coins"])

        # "제작자는 이 업적이..." 전설 업적이 /도박 전체 공용(배율 64 이상)으로 확장됨에
        # 따라 승부예측도 대상에 포함(사실상 잭팟(x100)만 해당). 2026-09-10부로 업적
        # 달성 알림(호감도 보너스 포함)은 award() 내부에서 별도 글로벌 방송으로
        # 처리되므로 여기서는 부여만 시도한다.
        await maybe_award_legendary_multiplier(view.challenger_id, multiplier)

    content = (
        f"## 🎯 도전자: {view.challenger_name}\n{text}\n\n"
        + "\n".join(result_lines)
        + "\n\n"
        + receipt
    )

    if view.message is None:
        mark_inactive(view.challenger_id)
        return
    replay_view = _build_replay_view(view.challenger_id)
    try:
        # embed는 일부러 안 넘긴다 — 마지막 애니메이션 프레임(결승선 트랙)이 그대로
        # 남아있는 게 자연스럽다(vending.py 등이 정산 후에도 영수증 embed를 그대로
        # 남겨두는 것과 동일한 원칙).
        await view.message.edit(content=content, view=replay_view)
        replay_view.message = view.message
    except discord.HTTPException:
        # ReplayView가 메시지에 못 붙으면 그 on_timeout이 영영 안 불려
        # mark_inactive도 영영 안 불린다 — 여기서 직접 풀어준다(2026-09-09, 감사
        # 중 발견된 크로스블록 잠금 영구 미해제 버그 수정).
        logging.exception("Failed to edit horse race settlement message")
        mark_inactive(view.challenger_id)


async def start_round(
    interaction: discord.Interaction, user_id: int, bet: int, *, is_replay: bool = False
) -> None:
    """모달에서 유효한 배팅액을 받은 뒤 예측 단계를 새 공개 메시지로 연다 —
    command/slot.py::_GambleSelectView의 "승부예측" 버튼과 다시하기가 공유하는
    진입점(bet.py/slot.py::_start_round와 동일한 역할).

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
    # "다시하기" 버튼이 사라지기 전까지 /내기·/도박(슬롯머신 포함) 재진입을 막는다.
    if is_replay:
        mark_active(user_id, _OWN_COMMAND)

    # vending.py::_execute_purchase와 동일한 역산 — spend_coins가 차감 전 잔액을
    # 반환하지 않아서, 차감 후 조회한 잔액에 배팅액을 다시 더해 "기존 금액"을 구한다.
    user = await get_user(user_id)
    before_coins = user["coins"] + bet
    challenger_name = interaction.user.display_name

    view = _PredictionView(user_id, challenger_name, before_coins, bet)
    content = _prediction_content(challenger_name, before_coins, bet)
    embed = _prediction_embed(view.predictions)

    await interaction.response.send_message(content=content, embed=embed, view=view)
    view.message = await interaction.original_response()
