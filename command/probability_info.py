import logging
from dataclasses import dataclass
from datetime import datetime

import discord

import command.horse_race as horse_race
import command.slot as slot
from command.black_market_catalog import SNACK_ITEMS as _BLACK_MARKET_SNACKS
from core.base import SYSTEM_EMBED_COLOR, reject_if_wrong_invoker
from events.scheduler import KST, format_footer_time

# `/봇정보-확률공개` — 확률형 콘텐츠(내기·도박·암시장)의 확률을 전부 공개하는 상시
# 조회용 명령어. `/봇정보-수집항목`과 동일한 원칙으로 완전히 독립적인 정보 명령어라
# 어떤 가입/등록 절차와도 무관하고, 메인/서브 채널 지정과 무관하게 항상 어느
# 채널에서나 동작한다(core/client.py::_ONBOARDING_COMMAND_NAMES). 업적(특히 전설
# 등급)은 "발견의 재미" 원칙(§15)이 적용되는 별개 보상 체계라 여기 공개 대상에서
# 제외한다 — 순수하게 결제·확률에 관한 정보만 다룬다.
#
# 2026-09-09 — 업적 리스트(command/achievements.py::_AchievementListView)와 동일한
# 원칙으로 페이지네이션을 도입했다. 카테고리(내기/도박/암시장)마다 분량이 크게
# 달라서 "N개씩 고정"이 아니라 **섹션을 원자 단위로 그리디 패킹**한다 — 한 섹션이
# 두 페이지에 걸쳐 잘리는 일 없이, 글자 수 예산 안에서 최대한 채우고 넘치면 다음
# 페이지로 넘어간다. 순서는 항상 내기 → 도박 → 암시장 고정.

_TITLE = "📊 확률 공개"
_INTRO = "햄미가 제공하는 확률형 콘텐츠의 확률을 모두 공개합니다."

# 한 페이지에 담을 카테고리의 대략적인 글자 수 예산 — 임베드 description 상한
# (4,096자) 대비 충분히 여유 있게, 가독성 기준으로 잡은 값. 이 값을 넘기지 않는
# 선에서 카테고리를 최대한 채우고, 넘치면 다음 페이지로 넘어간다(카테고리 자체는
# 절대 분할하지 않음). 2026-09-09 — 도박 카테고리(슬롯머신 심볼 목록 때문에 압도적
# 으로 김)가 내기와 한 페이지에 묶여 업적 리스트(페이지당 6항목×약 2줄)보다 실질적
# 으로 더 빽빽해지는 문제로, 예산을 750→400으로 낮췄다 — 내기/도박/암시장 세
# 카테고리가 서로 합쳐지지 않고 각자 페이지를 차지해 3페이지가 된다.
_PAGE_CHAR_BUDGET = 400

_ODD_EVEN_PROBABILITY_TEXT = "승리 확률 50%, 패배 확률 50%."
_RPS_PROBABILITY_TEXT = "승리 확률 약 33.3%, 무승부 확률 약 33.3%, 패배 확률 약 33.3%."
_UPDOWN_PROBABILITY_TEXT = (
    "최적의 전략으로 플레이할 경우 승리 확률은 약 35%이며, 승리 시 배팅액의 3배를 "
    "받습니다."
)
_DOUBLE_OR_NOTHING_PROBABILITY_TEXT = (
    "매 상자마다 50% 확률로 판돈이 2배가 되고, 50% 확률로 판돈을 전부 잃습니다. "
    "반복할 때마다 독립적으로 다시 계산되며, 판돈에는 상한이 없습니다."
)


def _slot_field_value() -> str:
    cell_count = len(slot.SYMBOLS)
    return (
        slot.probability_summary()
        + f"\n\n한 줄(가로·세로·대각선)이 완성될 확률: 약 {100 / (cell_count ** 2):.2f}%\n"
        f"최종 배율은 최대 x{slot.MAX_MULTIPLIER}로 제한됩니다."
    )


def _horse_race_field_value() -> str:
    # horse_race.HAMSTERS의 실제 마릿수에서 그대로 계산한다 — 슬롯머신
    # probability_summary()가 SYMBOLS 구성 변경에 자동 대응하는 것과 동일한 원칙으로,
    # 나중에 마릿수가 바뀌어도 이 문구가 자동으로 맞게 갱신된다.
    count = len(horse_race.HAMSTERS)
    single_rank_chance = 100 / count
    exact_combos = count * (count - 1) * (count - 2)
    jackpot_chance = 100 / exact_combos
    return (
        f"1등·2등·3등 중 하나를 정확히 맞힐 확률은 각각 약 {single_rank_chance:.1f}%"
        f"입니다. 세 등수를 모두 정확히 맞힐 확률은 약 {jackpot_chance:.3f}%"
        f"(1/{exact_combos})입니다. 적중한 등수의 배율은 서로 곱해지며(3위 x2 · "
        "2위 x4 · 1위 x6, 중첩), 세 등수를 전부 맞히면 고정 x100을 받습니다."
    )


def _format_percent(value: float) -> str:
    return f"{value:.0f}%" if value == int(value) else f"{value:.1f}%"


def _black_market_field_value() -> str:
    # 슬롯머신 필드(_slot_field_value)와 동일하게 항목마다 줄바꿈 한 번만(2026-09-09
    # 이전엔 "\n\n"으로 이어붙여 슬롯머신 목록보다 줄 간격이 유독 넓어 보였다).
    # 2026-09-09 — item.good_chance를 그대로 반영해 산딸기?의 25/75 편향도
    # 하드코딩 없이 자동으로 맞게 표시된다.
    lines = []
    for item in _BLACK_MARKET_SNACKS:
        if item.double_or_halve:
            good, bad = "호감도가 현재의 2배로 증가", "호감도가 현재의 절반으로 감소"
        else:
            good, bad = f"호감도 +{item.good_delta}", f"호감도 {item.bad_delta}"
        good_pct = item.good_chance * 100
        bad_pct = 100 - good_pct
        lines.append(
            f"**{item.name}** — 좋은 결과 {_format_percent(good_pct)}({good}) / "
            f"나쁜 결과 {_format_percent(bad_pct)}({bad})"
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class _Section:
    title: str
    body: str


def _category_groups() -> tuple[tuple[_Section, ...], ...]:
    """카테고리(내기/도박/암시장) 순서 고정 — **카테고리 자체가 원자 단위**다(요구사항:
    "같은 내용이 다른 페이지에 가지 않도록" — 개별 게임이 아니라 내기/도박/암시장이라는
    묶음 단위로 안 갈린다는 뜻으로 해석했다). 카테고리 안의 게임별 세부 항목은 각자
    별도 embed 필드로 남아 가독성은 그대로 유지된다."""
    return (
        (
            _Section("🪙 내기 — 홀짝", _ODD_EVEN_PROBABILITY_TEXT),
            _Section("✂️ 내기 — 가위바위보", _RPS_PROBABILITY_TEXT),
            _Section("🔢 내기 — 업다운", _UPDOWN_PROBABILITY_TEXT),
        ),
        (
            _Section("🎰 도박 — 슬롯머신", _slot_field_value()),
            _Section("🐹 도박 — 승부예측", _horse_race_field_value()),
            _Section("📦 도박 — 더블오어낫띵", _DOUBLE_OR_NOTHING_PROBABILITY_TEXT),
        ),
        (
            _Section("🌙 암시장 — 확률형 간식", _black_market_field_value()),
        ),
    )


def _pack_pages(groups: tuple[tuple[_Section, ...], ...]) -> list[list[_Section]]:
    """카테고리를 순서대로 순회하며 글자 수 예산 안에서 그리디하게 페이지에 채운다 —
    한 카테고리에 속한 섹션들은 항상 통째로 같은 페이지에 들어가고 절대 다른
    페이지로 갈리지 않는다(요구사항). 순수 함수라 오프라인 테스트에서 카테고리
    개수/순서/미분할을 검증할 수 있다."""
    pages: list[list[_Section]] = []
    current: list[_Section] = []
    current_len = 0
    for group in groups:
        group_len = sum(len(s.title) + len(s.body) for s in group)
        if current and current_len + group_len > _PAGE_CHAR_BUDGET:
            pages.append(current)
            current = []
            current_len = 0
        current.extend(group)
        current_len += group_len
    if current:
        pages.append(current)
    return pages


def _build_pages() -> list[discord.Embed]:
    pages = _pack_pages(_category_groups())
    total = len(pages)
    embeds: list[discord.Embed] = []
    for index, page_sections in enumerate(pages):
        embed = discord.Embed(title=f"{_TITLE} ({index + 1}/{total} 페이지)", color=SYSTEM_EMBED_COLOR)
        if index == 0:
            embed.description = _INTRO
        for section in page_sections:
            embed.add_field(name=section.title, value=section.body, inline=False)
        embed.set_footer(text=format_footer_time(datetime.now(KST)))
        embeds.append(embed)
    return embeds


class _ProbabilityInfoView(discord.ui.View):
    """실행자 본인만 페이지를 넘길 수 있다(command/achievements.py
    ::_AchievementListView와 동일한 원칙). ephemeral 응답이라 굳이
    EphemeralAutoDeleteView를 쓰지 않고(achievements 리스트와 동일하게) 60초
    무클릭 시 버튼만 제거한다 — 내용 자체는 다시 읽을 수 있게 남겨둔다."""

    def __init__(self, user_id: int, pages: list[discord.Embed]) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.pages = pages
        self.page = 0
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= len(self.pages) - 1

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logging.exception("Failed to clear probability info buttons on timeout")

    async def _go(self, interaction: discord.Interaction, step: int) -> None:
        if not await reject_if_wrong_invoker(interaction, self.user_id):
            return
        self.page += step
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    @discord.ui.button(label="◀ 이전", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._go(interaction, -1)

    @discord.ui.button(label="다음 ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._go(interaction, 1)


def build_view(user_id: int) -> tuple[discord.Embed, discord.ui.View]:
    """`/봇정보-확률공개` 진입점 — 첫 페이지 embed와 페이지네이션 뷰를 반환한다."""
    pages = _build_pages()
    view = _ProbabilityInfoView(user_id, pages)
    return pages[0], view
