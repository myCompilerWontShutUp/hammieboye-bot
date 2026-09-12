import discord
from discord import app_commands

import command.eat as eat
import command.forbidden_book as forbidden_book
import command.hammie_schedule as hammie_schedule
from db.snacks import get_inventory

_MAX_AUTOCOMPLETE = 25

_UNUSABLE_ITEM_MESSAGE = "어라, 그건 이렇게 쓸 수 있는 물건이 아닌 것 같아!! _(갸웃)_"


async def autocomplete_아이템(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """호출한 본인의 "사용 가능" 인벤토리 기준으로만 제안한다 — 舊 /먹어의
    autocomplete_간식과 동일한 원칙, 다만 간식(자판기/암시장)뿐 아니라 도구(금서/
    햄미 일정표)까지 함께 보여준다. 둘 다 결국 db/snacks.py::user_snacks 하나로
    관리되는 재고라 조회 자체는 동일하다 — 투자 품목·장난 품목은 애초에 이 인벤토리에
    안 들어오므로 자연히 제외된다."""
    inventory = await get_inventory(interaction.user.id)
    query = current.strip().lower()
    choices: list[app_commands.Choice[str]] = []
    for row in inventory:
        item = eat.find_by_id(row["snack_id"])
        if item is None or query and query not in item.name.lower():
            continue
        choices.append(app_commands.Choice(name=f"{item.name} ({row['quantity']}개)", value=item.name))
        if len(choices) >= _MAX_AUTOCOMPLETE:
            break
    return choices


async def handle(interaction: discord.Interaction, user_id: int, item_name: str) -> None:
    """`/사용` 진입점 — 아이템에 따라 응답 공개 범위가 갈린다(간식은 舊 /먹어처럼
    공개, 금서·햄미 일정표는 본인에게만 보이는 ephemeral). `/니정보`와 동일한 이유로
    core/slash_commands.py::_prepare(deferred=False)를 거쳐 defer 전 상태로 넘어오고,
    이 함수가 아이템을 먼저 찾아본 뒤 defer 여부를 스스로 결정한다."""
    item = eat.find_by_name(item_name)

    if item is not None and item.id == forbidden_book.ITEM_ID:
        await interaction.response.defer(ephemeral=True)
        await forbidden_book.handle_use(interaction, user_id)
        return

    if item is not None and item.id == hammie_schedule.ITEM_ID:
        await interaction.response.defer(ephemeral=True)
        result = await hammie_schedule.handle_use(user_id)
        if isinstance(result, discord.Embed):
            await interaction.edit_original_response(content=None, embed=result)
        else:
            await interaction.edit_original_response(content=result)
        return

    # 그 외(간식/음료/포션)는 舊 /먹어와 동일하게 공개 응답 — 자판기/암시장 카탈로그
    # 어느 쪽이든 command/eat.py::handle()이 이미 둘 다 조회한다. 실제 디저트/드링킹
    # 타임 슬롯 유효성 검사는 전부 eat.py::handle()에 위임한다(2026-09-12 "beverage"/
    # "potion" kind 추가, §24).
    await interaction.response.defer()
    if item is None or item.kind not in ("snack", "beverage", "potion"):
        await interaction.edit_original_response(content=_UNUSABLE_ITEM_MESSAGE)
        return
    guild_id = interaction.guild.id if interaction.guild else None
    text = await eat.handle(user_id, item_name, guild_id=guild_id)
    await interaction.edit_original_response(content=text)
