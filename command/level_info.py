import logging
from datetime import datetime

import discord

import levels
from core.base import SYSTEM_EMBED_COLOR, reject_if_wrong_invoker
from events.scheduler import KST, format_footer_time

# `/봇정보-레벨`(2026-09-10 신규) — 레벨 시스템(CLAUDE.md §23)의 단계별 혜택을
# 안내하는 상시 조회용 명령어. `/봇정보-확률공개`와 동일한 원칙으로 완전히
# 독립적인 정보 명령어라 어떤 가입/등록 절차와도 무관하고, 메인/서브 채널
# 지정과 무관하게 항상 어느 채널에서나 동작한다(core/client.py
# ::_ONBOARDING_COMMAND_NAMES). 개인화하지 않는다(누가 조회하든 항상 같은
# 내용) — 유저 본인의 현재 레벨/경험치는 `/내정보`가 이미 보여준다.

_TITLE = "🎖️ 레벨 시스템"
_OVERVIEW_TEXT = (
    "경험치(XP)를 모아 레벨을 올리면 하루 자연어 대화 횟수, 입출력 글자 상한, "
    "`/자판기`·`/암시장`·`/도박` 이용 가능 여부, `/동전` 2배 획득 확률 등 다양한 "
    "혜택이 늘어납니다.\n\n"
    "아래 목록에서 레벨을 선택하면 그 레벨의 상세 정보를 확인할 수 있습니다."
)


def _overview_embed() -> discord.Embed:
    embed = discord.Embed(title=_TITLE, description=_OVERVIEW_TEXT, color=SYSTEM_EMBED_COLOR)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


def _level_embed(level: levels.Level) -> discord.Embed:
    input_limit = "무제한" if level.input_char_limit is None else f"{level.input_char_limit}자"
    output_limit = "무제한" if level.output_char_limit is None else f"{level.output_char_limit}자"
    lines = [
        f"- 필요 경험치: {level.xp_required}",
        f"- 하루 자연어 대화 횟수: {level.daily_nl_limit}회",
        f"- 입력 글자 상한: {input_limit}",
        f"- 출력 글자 상한: {output_limit}",
        f"- `/자판기` 이용: {'가능' if level.vending_allowed else '불가능'}",
        f"- `/암시장` 이용: {'가능' if level.black_market_allowed else '불가능'}",
        f"- `/도박` 이용: {'가능' if level.gambling_allowed else '불가능'}",
        f"- `/동전` 2배 획득 확률: {int(level.double_drop_chance * 100)}%",
    ]
    embed = discord.Embed(
        title=f"🎖️ {level.number}레벨 - {level.name}",
        description="\n".join(lines),
        color=SYSTEM_EMBED_COLOR,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


class _LevelSelect(discord.ui.Select):
    """레벨 0~7을 고르는 드롭다운. 고른 레벨은 `default=True`로 표시해 드롭다운을
    다시 열어도 지금 보고 있는 레벨이 그대로 선택 상태로 보이게 한다."""

    def __init__(self, selected: int | None) -> None:
        options = [
            discord.SelectOption(
                label=f"{lvl.number}레벨 - {lvl.name}",
                value=str(lvl.number),
                default=(lvl.number == selected),
            )
            for lvl in levels.LEVELS
        ]
        super().__init__(placeholder="레벨을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _LevelInfoView = self.view
        if not await reject_if_wrong_invoker(interaction, view.user_id):
            return
        number = int(self.values[0])
        view.selected = number
        view._rebuild_select()
        await interaction.response.edit_message(embed=_level_embed(levels.LEVELS[number]), view=view)


class _LevelInfoView(discord.ui.View):
    """`/봇정보-레벨` 전용 뷰 — 실행자 본인만 드롭다운을 조작할 수 있다
    (command/probability_info.py::_ProbabilityInfoView와 동일한 원칙). 10분간
    무클릭 시 드롭다운만 제거한다(내용은 유지, §23-11 참고)."""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.selected: int | None = None
        self.message: discord.Message | None = None
        self._select = _LevelSelect(self.selected)
        self.add_item(self._select)

    def _rebuild_select(self) -> None:
        self.remove_item(self._select)
        self._select = _LevelSelect(self.selected)
        self.add_item(self._select)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logging.exception("Failed to clear level info dropdown on timeout")


def build_view(user_id: int) -> tuple[discord.Embed, discord.ui.View]:
    """`/봇정보-레벨` 진입점 — 개요 embed와 레벨 선택 드롭다운 뷰를 반환한다."""
    return _overview_embed(), _LevelInfoView(user_id)
