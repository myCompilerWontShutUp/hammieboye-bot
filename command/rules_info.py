import random
from datetime import datetime

import discord

import command.double_or_nothing as double_or_nothing
import command.horse_race as horse_race
import command.slot as slot
from command.bet import ODD_EVEN_RULE_TEXT, RPS_RULE_TEXT, UPDOWN_RULE_TEXT
from command.economy_common import GAMBLING_EMBED_COLOR, MAX_BET_BETTING, MAX_BET_GAMBLING
from core.base import clear_on_timeout
from events.scheduler import KST, format_footer_time

# `/봇정보-규칙`(舊 /내기-규칙·/도박-규칙 통합) — 내기·도박의 게임별 규칙을
# 한곳에서 안내하는 상시 조회용 명령어(`/봇정보-확률공개`와 동일한 원칙 —
# 가입/등록 무관, 메인/서브 채널 무관, core/client.py::_ONBOARDING_COMMAND_NAMES).
# 게임별 규칙 본문 자체는 각 게임 파일에 그대로 두고(command/bet.py·slot.py·
# horse_race.py·double_or_nothing.py) 여기서는 그걸 모아 RulesView 버튼으로
# 보여주기만 한다.


class _RuleButton(discord.ui.Button):
    """게임별 규칙 버튼 — 누르면 그 게임의 상세 규칙으로 임베드만 바꿔치기한다.
    지금 보는 게임은 초록(success), 나머지는 회색(secondary) — 다른 카테고리
    탭(/자판기·/암시장·/내정보·/랭킹)과 동일한 배색 원칙. row로 그룹(내기/도박)별
    줄바꿈을 배치한다."""

    def __init__(
        self, label: str, text: str, embed_title: str, color: int, *, active: bool, row: int | None = None
    ) -> None:
        style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=row)
        self._text = text
        self._embed_title = embed_title
        self._color = color

    async def callback(self, interaction: discord.Interaction) -> None:
        view: RulesView = self.view
        for child in view.children:
            if isinstance(child, _RuleButton):
                child.style = discord.ButtonStyle.success if child is self else discord.ButtonStyle.secondary
        embed = discord.Embed(title=self._embed_title, description=self._text, color=self._color)
        embed.set_footer(text=format_footer_time(datetime.now(KST)))
        await interaction.response.edit_message(embed=embed, view=view)


class RulesView(discord.ui.View):
    """`/봇정보-규칙` 전용 게임별 규칙 버튼 뷰(ephemeral, wrong-user 체크 불필요).
    game_rules는 {버튼 라벨: 규칙 본문}(삽입 순서 유지) — 3개씩 한 줄로 묶는다.
    다른 정보류 View와 동일하게 10분 무클릭 시 버튼만 지운다(메시지는 유지)."""

    _BUTTONS_PER_ROW = 3

    def __init__(self, embed_title: str, game_rules: dict[str, str], *, color: int) -> None:
        super().__init__(timeout=600)
        self.message: discord.Message | None = None
        for index, (label, text) in enumerate(game_rules.items()):
            row = index // self._BUTTONS_PER_ROW
            self.add_item(_RuleButton(label, text, embed_title, color, active=False, row=row))

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        await clear_on_timeout(lambda: self.message.edit(view=None), log_label="rules view buttons")

_TITLE = "🎲🎰 내기 · 도박 규칙"

_INTRO_LINES = (
    "내기랑 도박 규칙, 한 번에 알려줄게!! _(친절)_",
    "이거 하나면 규칙 다 끝!! _(자신감)_",
    "내기·도박 하는 법 여기 다 있어!! _(안내)_",
    "규칙부터 익히고 시작하자!! _(꼼꼼)_",
    "내기랑 도박, 이렇게 굴러가!! _(설명)_",
    "먼저 규칙 확인해볼래?? _(권유)_",
    "이거 알면 유리해!! 규칙이야!! _(웃음)_",
    "내기·도박 설명서 가져왔어!! _(뿌듯)_",
    "규칙 모르면 손해야!! 알려줄게!! _(진지)_",
    "짜잔, 게임 규칙 모음!! _(공개)_",
    "이거 읽고 도전해봐!! _(응원)_",
    "게임 하기 전에 이거부터!! _(추천)_",
    "규칙 요약해줄게!! _(친절)_",
    "룰 정리했어!! _(정리)_",
    "내기·도박, 알고 하면 더 재밌어!! _(웃음)_",
    "규칙 확인하고 배팅해봐!! _(권유)_",
    "게임 가이드 여기 있어!! _(안내)_",
)

# RulesView가 embed.description으로 그대로 보여주는 문구라 시스템 정중체로
# 고정한다(§22-4).
_OVERVIEW_TEXT = (
    "동전을 걸고 즐기는 미니게임 모음입니다.\n\n"
    f"- 내기: 홀짝 · 가위바위보 · 업다운 (비교적 안전, 배팅액 1~{MAX_BET_BETTING}동전)\n"
    f"- 도박: 슬롯머신 · 승부예측 (위험한 만큼 크게 벌 수 있음, 배팅액 "
    f"1~{MAX_BET_GAMBLING:,}동전) · 더블오어낫띵(올인 또는 하프 중 선택, 상한 없음)\n\n"
    "게임 진행 중 10분 동안 아무것도 고르지 않으면 포기한 것으로 간주해 배팅액을 "
    "모두 잃습니다.\n\n"
    "아래 버튼에서 원하는 게임을 골라주세요."
)


def build_view() -> tuple[str, discord.Embed, discord.ui.View]:
    """`/봇정보-규칙` 진입점 — 개요 임베드 + 게임별 버튼(RulesView)을 보여주고,
    버튼을 누르면 그 게임의 상세 규칙으로 임베드만 바꿔치기한다."""
    embed = discord.Embed(title=_TITLE, description=_OVERVIEW_TEXT, color=GAMBLING_EMBED_COLOR)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    view = RulesView(
        _TITLE,
        {
            "내기 - 홀짝": ODD_EVEN_RULE_TEXT,
            "내기 - 가위바위보": RPS_RULE_TEXT,
            "내기 - 업다운": UPDOWN_RULE_TEXT,
            "도박 - 슬롯머신": slot.SLOT_MACHINE_RULE_TEXT,
            "도박 - 승부예측": horse_race.HORSE_RACE_RULE_TEXT,
            "도박 - 더블오어낫띵": double_or_nothing.DOUBLE_OR_NOTHING_RULE_TEXT,
        },
        color=GAMBLING_EMBED_COLOR,
    )
    return random.choice(_INTRO_LINES), embed, view
