import logging
import random
from datetime import datetime

import discord

import achievements
from core.base import EMBED_COLOR, LIST_EMBED_COLOR, reject_if_wrong_invoker
from events.scheduler import KST, format_footer_time
from db.achievements import get_earned

# 정책 고지 성격이라 고정 문구 1개 — 특정 업적을 짚어 물어보면 RAG 문서가 알려준다는
# "발견의 재미" 원칙과 이어지도록 자연어 질문을 유도한다.
_DESCRIPTION = "어떻게 얻는지 궁금하면 햄미에게 물어봐!! 내가 다 알려줄게!!"

_INTRO_LINES = (
    "내 업적 자랑해볼게!! _(으쓱)_",
    "짜잔!! 내 업적 목록이야!! _(뿌듯)_",
    "내가 모은 업적들 보여줄게!! _(신남)_",
    "햄미 업적판 열어본다!! _(기대)_",
    "이만큼 모아써!! 내 업적이야!! _(자랑)_",
    "내 업적 컬렉션 공개!! _(들뜸)_",
    "짠, 내 도전 기록이야!! _(당당)_",
    "업적 창고 열어볼게!! _(호기심)_",
    "내가 이만큼 해냈다구!! _(뿌듯)_",
    "업적 자랑 타임!! _(신남)_",
    "내 트로피들 구경할래?? _(반짝)_",
    "이게 다 내 업적이야!! _(으쓱)_",
    "업적판 공개합니다!! _(짠)_",
    "내가 모은 것들 보여줄게!! _(설렘)_",
    "짜잔, 업적 목록 나간다!! _(활짝)_",
    "내 도전 결과 확인해볼래?? _(궁금)_",
    "업적 자랑 좀 할게!! _(뿌듯)_",
    "내 업적들 한눈에 보여줄게!! _(자신감)_",
    "이만큼 모았어!! 놀랐지?? _(장난)_",
    "업적 리스트 대공개!! _(신남)_",
)

# {name} 바로 뒤에 조사가 붙는 자리는 전부 "의"(받침 무관 불변)로만 구성해서, 별도
# 조사 계산 없이도 어떤 이름이 와도 안전하다.
_INTRO_OTHER_LINES = (
    "{name}의 업적 보여줄게!! _(으쓱)_",
    "짜잔!! {name} 업적 목록이야!! _(자랑)_",
    "{name} 업적판 열어볼게!! _(신남)_",
    "{name}의 도전 기록이야!! _(기대)_",
    "{name} 업적 컬렉션 공개!! _(들뜸)_",
    "짠, {name}의 업적이야!! _(당당)_",
    "{name} 업적 창고 열어볼게!! _(호기심)_",
    "{name} 업적, 이만큼 모아써!! _(뿌듯)_",
    "{name} 업적 자랑 타임!! _(신남)_",
    "{name}의 트로피들 구경할래?? _(반짝)_",
    "이게 다 {name}의 업적이야!! _(으쓱)_",
    "{name} 업적판 공개합니다!! _(짠)_",
    "{name}의 업적, 짜잔!! _(설렘)_",
    "짜잔, {name} 업적 목록 나간다!! _(활짝)_",
    "{name}의 도전 결과 확인해볼래?? _(궁금)_",
    "{name} 업적 자랑 좀 할게!! _(뿌듯)_",
    "{name}의 업적들 한눈에 보여줄게!! _(자신감)_",
    "{name} 업적, 이만큼 모아써!! 놀랐지?? _(장난)_",
    "{name} 업적 리스트 대공개!! _(신남)_",
    "{name}의 업적을 소개할게!! _(설렘)_",
)

# /내업적·/니업적은 획득한 것만 보여준다 — 전체 목록은 /업적-리스트로 분리됐다(§1-7).
_NO_EARNED_LINE = "- 아직 획득한 업적이 없어"
_CATALOG_POINTER = "무슨 업적이 있는지 궁금하면 /업적-리스트를 확인해봐!!"

# /업적-리스트 전용 인트로 풀 — 완전히 비개인화된 정적 카탈로그라 대상자 이름이 안 들어간다.
_LIST_INTRO_LINES = (
    "여기 업적 전체 목록이야!! _(자랑)_",
    "이 세상 업적들, 싹 다 모아봤어!! _(뿌듯)_",
    "업적 도감 공개!! _(반짝)_",
    "이런 업적들이 있어!! _(설렘)_",
    "업적 목록 가져왔어!! _(신남)_",
    "짜잔, 업적 전체 목록이야!! _(당당)_",
    "궁금했지?? 업적 도감이야!! _(장난)_",
    "이만큼 업적이 있다구!! _(으쓱)_",
    "업적 리스트 열어볼게!! _(호기심)_",
    "업적 도감 펼쳐본다!! _(기대)_",
    "여기 다 있어!! 업적 목록이야!! _(자신감)_",
    "업적 전체 공개 타임!! _(들뜸)_",
    "이게 지금까지의 업적들이야!! _(진지)_",
    "업적 도감 보여줄게!! _(방긋)_",
    "이 목록 보면 도전하고 싶어질걸?? _(웃음)_",
    "업적들 쭉 나열해볼게!! _(정리)_",
    "짠!! 업적 카탈로그야!! _(공개)_",
    "이게 다 모을 수 있는 업적이야!! _(설명)_",
    "업적 목록, 참고해봐!! _(권유)_",
    "자, 업적 도감 여기 있어!! _(전달)_",
)


def _format_date(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).astimezone(KST).strftime("%Y. %m. %d")


def _earned_lines(earned: list[dict]) -> list[str]:
    """전설 등급을 항상 최상단에, 그 안에서는 획득 순으로. get_earned()가 이미 획득
    시각 오름차순으로 반환하므로 필터링만 해도 각 그룹 내 순서가 보존된다."""
    legendary = [row for row in earned if achievements.REGISTRY[row["achievement_id"]].RARITY == achievements.LEGENDARY]
    normal = [row for row in earned if achievements.REGISTRY[row["achievement_id"]].RARITY != achievements.LEGENDARY]
    lines = []
    for row in legendary + normal:
        module = achievements.REGISTRY[row["achievement_id"]]
        lines.append(f"- {achievements.format_name(module)} ({_format_date(row['earned_at'])})")
    return lines or [_NO_EARNED_LINE]


async def handle(user_id: int, *, target_name: str | None = None) -> tuple[str, discord.Embed]:
    """target_name이 None이면 본인(/내업적) 조회, 아니면 그 이름의 다른 사람(/니업적) 조회."""
    is_self = target_name is None

    earned = await get_earned(user_id)

    title = "나의 업적" if is_self else f"{target_name}의 업적"
    embed = discord.Embed(title=title, description=_DESCRIPTION, color=EMBED_COLOR)

    embed.add_field(
        name=f"🏆 획득한 업적 ({len(earned)}/{achievements.TOTAL_COUNT})",
        value="\n".join(_earned_lines(earned)) + f"\n​\n{_CATALOG_POINTER}",
        inline=False,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return random.choice(_INTRO_OTHER_LINES).format(name=target_name), embed


# /업적-리스트 페이지네이션(2026-09-06 신규) — 한 임베드에 20개를 몰아넣으면
# 가독성이 떨어져서 6개씩 나눈다. REGISTRY(dict)는 achievements/__init__.py의 _MODULES
# 삽입 순서를 그대로 보존하므로("업적이 만들어진 순서") 전설/일반을 재정렬하지 않고
# 그 순서 그대로 페이지를 나눈다 — "발견 순서"라는 자연스러운 흐름과 맞춘 것.
_PAGE_SIZE = 6


def _page_count() -> int:
    return -(-achievements.TOTAL_COUNT // _PAGE_SIZE)  # 올림 나눗셈


def _entry_lines(module, earned_ids: set[str]) -> tuple[str, str]:
    """(이름 줄, 설명 줄) — 카드형으로 이름 위/설명 아래에 렌더링해 가독성을 높인다."""
    if module.RARITY == achievements.LEGENDARY and module.ID not in earned_ids:
        return achievements.format_hidden_legendary_name(module), f"({module.HINT})"
    return achievements.format_name(module), module.HOW_TO_EARN


def _build_list_embed(page: int, earned_ids: set[str]) -> discord.Embed:
    start = page * _PAGE_SIZE
    modules = list(achievements.REGISTRY.values())[start : start + _PAGE_SIZE]
    blocks = []
    for module in modules:
        name_line, desc_line = _entry_lines(module, earned_ids)
        # format_name()/format_hidden_legendary_name()이 전설 업적은 이미
        # "**__[👑]__** 이름" 형태로 굵게 스타일을 입혀서 반환한다 — 여기서 또
        # **로 감싸면 연속 4개의 *가 겹쳐 마크다운이 깨져 별표가 그대로 보인다
        # (일반 업적만 이름 자체에 스타일이 없어 여기서 감싸야 한다).
        if module.RARITY == achievements.LEGENDARY:
            blocks.append(f"{name_line}\n{desc_line}")
        else:
            blocks.append(f"**{name_line}**\n{desc_line}")

    embed = discord.Embed(
        title=f"📖 업적 목록 ({page + 1}/{_page_count()} 페이지)", color=LIST_EMBED_COLOR
    )
    embed.description = "\n\n".join(blocks)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


class _AchievementListView(discord.ui.View):
    """실행자 본인만 페이지를 넘길 수 있다(2026-09-06 신규 — 이전엔 이 명령어 자체가
    페이지네이션이 없어서 검증할 대상도 없었음). 60초 무클릭 시 버튼만 제거(다른
    쿨타임 명시 없음, 기본 규칙)."""

    def __init__(self, user_id: int, earned_ids: set[str]) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.earned_ids = earned_ids
        self.page = 0
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= _page_count() - 1

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logging.exception("Failed to clear achievement list buttons on timeout")

    async def _go(self, interaction: discord.Interaction, step: int) -> None:
        if not await reject_if_wrong_invoker(interaction, self.user_id):
            return
        self.page += step
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_build_list_embed(self.page, self.earned_ids), view=self
        )

    @discord.ui.button(label="◀ 이전", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._go(interaction, -1)

    @discord.ui.button(label="다음 ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._go(interaction, 1)


async def list_handle(user_id: int) -> tuple[str, discord.Embed, discord.ui.View]:
    """/업적-리스트 — 일반 업적은 항상 이름+획득 방법을 공개하는 정적 카탈로그지만,
    전설 업적은 호출한 본인의 획득 여부에 따라 개인화된다: 이미 획득했으면 실명+획득
    방법을 그대로 보여주고, 아직 못 얻었으면 "발견의 재미" 원칙대로 ???+힌트로 감춘다
    (모든 전설을 무조건 숨기는 것도, 미획득 전설의 실명/조건을 공개하는 것도 둘 다 틀림).
    6개씩 페이지네이션(◀ 이전/다음 ▶ 버튼)해서 보여준다."""
    earned = await get_earned(user_id)
    earned_ids = {row["achievement_id"] for row in earned}

    view = _AchievementListView(user_id, earned_ids)
    embed = _build_list_embed(0, earned_ids)
    return random.choice(_LIST_INTRO_LINES), embed, view
