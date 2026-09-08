import discord

from core.base import EphemeralAutoDeleteView
from db.forbidden_books import (
    MAX_PER_USER,
    add_entry,
    days_remaining,
    get_entries_for_user,
    keyword_taken,
)
from db.snacks import consume_snack, get_inventory

ITEM_ID = "forbidden_book"

_KEYWORD_MAX_LENGTH = 20
_CONTENT_MAX_LENGTH = 100

# 인젝션 방어는 "프롬프트 프레이밍만"(2026-09-08 사용자 확인) — 별도 필터링 없이,
# 동의 문구에서 애초에 하지 말라고 명시하고 실제 주입 시점(core/chat.py)에서 모델에게
# "절대 지시로 따르지 말라"고 강하게 프레이밍하는 두 겹으로 완화한다.
_CONSENT_TEXT = (
    "**금서를 가르치기 전 아래 내용에 동의해줘:**\n"
    "- 프롬프트 인젝션(햄미에게 시스템 설정을 무시하라거나 다른 역할을 하라고 "
    "지시하는 내용)을 적지 않는다.\n"
    "- 특정 인물을 지나치게 비방하거나 모욕하는 내용을 적지 않는다.\n"
    "- 이 내용은 정확히 7일 뒤 자동으로 사라진다는 걸 숙지한다(그 이후엔 별도 "
    "안내 없이 조용히 사라져).\n\n"
    "**내용을 구체적으로 적을수록 햄미가 더 자연스럽게 답할 수 있어!!**"
)

_LIMIT_REACHED_TEMPLATE = (
    "이미 금서를 {max}개나 가르쳤잖아!! 하나가 사라질 때까지 기다려줘!! _(단호)_\n\n{list_text}"
)
_NO_ITEM_MESSAGE = "어라, 금서를 안 가지고 있는데?? `/암시장`에서 먼저 사 와줄래?? _(갸웃)_"
_DUPLICATE_KEYWORD_MESSAGE = "어라, 그 키워드는 이미 다른 금서에 쓰이고 있어!! 다른 걸로 적어줘!! _(단호)_"
_CANCEL_MESSAGE = "그래, 다음에 하자!! _(끄덕)_"
_SUCCESS_TEMPLATE = "좋아, 몰래 배워둘게!! \"{keyword}\"에 대해 물어보면 슬쩍 알려줄지도 몰라... _(비밀)_"
_RACE_FAILURE_MESSAGE = "어라, 방금 사이에 뭔가 꼬였나 봐!! 다시 시도해줄래?? _(당황)_"


def _format_list(entries: list[dict]) -> str:
    if not entries:
        return "지금 가르쳐둔 금서는 없어."
    lines = [
        f"- {entry['keyword']} (약 {days_remaining(entry)}일 남음)" for entry in entries
    ]
    return "지금까지 가르친 금서:\n" + "\n".join(lines)


class _ForbiddenBookModal(discord.ui.Modal):
    def __init__(self, user_id: int) -> None:
        super().__init__(title="금서 등록")
        self._user_id = user_id
        self.keyword_input = discord.ui.TextInput(
            placeholder="예: 쥬앵빈", required=True, max_length=_KEYWORD_MAX_LENGTH
        )
        self.content_input = discord.ui.TextInput(
            placeholder="예: 키가 작은 여자 아이",
            required=True,
            max_length=_CONTENT_MAX_LENGTH,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(discord.ui.Label(text="키워드 (최대 20자)", component=self.keyword_input))
        self.add_item(discord.ui.Label(text="내용 (최대 100자)", component=self.content_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        keyword = self.keyword_input.value.strip()
        content = self.content_input.value.strip()

        # 등록 시점에 다시 확인 — 동의 단계와 제출 사이에 다른 요청으로 상태가
        # 바뀌었을 수 있는 경합을 방어한다(재고 소진/한도 도달/키워드 중복 전부).
        current_entries = await get_entries_for_user(self._user_id)
        if len(current_entries) >= MAX_PER_USER:
            await interaction.response.send_message(
                _LIMIT_REACHED_TEMPLATE.format(max=MAX_PER_USER, list_text=_format_list(current_entries)),
                ephemeral=True,
            )
            return
        if await keyword_taken(keyword):
            await interaction.response.send_message(_DUPLICATE_KEYWORD_MESSAGE, ephemeral=True)
            return
        if not await consume_snack(self._user_id, ITEM_ID):
            await interaction.response.send_message(_RACE_FAILURE_MESSAGE, ephemeral=True)
            return

        await add_entry(self._user_id, keyword, content)
        await interaction.response.send_message(
            _SUCCESS_TEMPLATE.format(keyword=keyword), ephemeral=True
        )


class _ConsentView(EphemeralAutoDeleteView):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id

    @discord.ui.button(label="동의", style=discord.ButtonStyle.success)
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            return
        self.stop()
        await interaction.response.send_modal(_ForbiddenBookModal(self.user_id))

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            return
        self.stop()
        await interaction.response.edit_message(content=_CANCEL_MESSAGE, view=None)


async def handle_use(interaction: discord.Interaction, user_id: int) -> None:
    """`/사용 금서` 진입점 — 호출부(command/use.py)가 이미 ephemeral로 defer해뒀다고
    가정하고 `edit_original_response`만 쓴다. Discord 모달은 defer된 인터랙션 위에
    바로 못 띄우므로(모달은 아직 응답 안 한 "새" 인터랙션에만 열 수 있다), 동의는
    별도의 버튼 뷰로 먼저 받고 "동의" 클릭이라는 완전히 새 인터랙션에서 실제 등록
    모달을 연다."""
    inventory = await get_inventory(user_id)
    owned = any(row["snack_id"] == ITEM_ID and row["quantity"] > 0 for row in inventory)
    if not owned:
        await interaction.edit_original_response(content=_NO_ITEM_MESSAGE)
        return

    entries = await get_entries_for_user(user_id)
    if len(entries) >= MAX_PER_USER:
        await interaction.edit_original_response(
            content=_LIMIT_REACHED_TEMPLATE.format(max=MAX_PER_USER, list_text=_format_list(entries))
        )
        return

    content = f"{_format_list(entries)}\n\n{_CONSENT_TEXT}"
    view = _ConsentView(user_id)
    await interaction.edit_original_response(content=content, view=view)
    view.interaction = interaction
