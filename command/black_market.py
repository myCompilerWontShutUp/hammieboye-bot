import logging
import random
from datetime import datetime

import discord

from core.base import reject_if_wrong_invoker
from core.korean import josa
from command.economy_common import (
    INSUFFICIENT_FUNDS_LINES,
    PurchaseConfirmModal,
    reject_if_wrong_user_with_cta,
)
from command.black_market_catalog import ITEMS
from events.scheduler import KST, format_footer_time
from db.snacks import add_snack
from db.users import get_user
from db.vending_log import get_purchase_counts, record_purchase
from db.wallet import spend_coins

# 취침 시간대(햄미 몰래 일어나 거래하는 컨셉)에만 열리는 상점 — /자판기와 정반대로
# 깨어있는 시간대에는 차단된다(events/sleep_guard.py::guard_sleep_only). 낮에 치면
# 이 문구로 거절한다. 기상 시각이 늦춰진 날도 is_sleep_time_for가 알아서 반영한다.
DAYTIME_BLOCK_MESSAGE = "에에?? 햄미는 암시장 그런거 안 하는데... _(저녁에 다시 와야 할 듯 싶다)_"

_INTRO_LINES = (
    "쉿... 아무한테도 말하면 안 돼!! _(소곤소곤)_",
    "이 시간에만 몰래 여는 가게야!! _(귓속말)_",
    "다들 자는 시간에 슬쩍 나왔어!! _(살금살금)_",
    "여기서 본 건 비밀이야!! _(눈짓)_",
    "쉿, 조용히!! 암시장이 열렸어!! _(소곤)_",
    "이 시간에만 만날 수 있는 상인이야!! _(속삭임)_",
    "아무도 모르게 준비했어!! _(찡긋)_",
    "밤에만 여는 특별한 가게라구!! _(은밀)_",
    "다들 잠든 사이에 몰래 열었어!! _(살짝)_",
    "이건 낮에는 못 보는 물건들이야!! _(비밀)_",
    "쉿... 들키면 안 돼!! _(긴장)_",
    "어둠 속에서만 여는 가게야!! _(소곤소곤)_",
    "이 시간이 딱 좋아!! 아무도 안 보거든!! _(장난)_",
    "몰래몰래 준비한 물건들이야!! _(은밀)_",
    "쉿, 조용히 골라봐!! _(귓속말)_",
    "이건 아무한테도 말하면 안 되는 거야!! _(비밀)_",
    "밤손님만 알아보는 가게라구!! _(찡긋)_",
    "다들 자니까 지금이 기회야!! _(살금살금)_",
    "이 시간에만 여는 수상한 가게야!! _(소곤)_",
    "쉿... 조용히 구경해봐!! _(속삭임)_",
)

# 구매 완료 응답 전용 문구(2026-09-09 신규) — 이전엔 위 _INTRO_LINES(암시장에 들어올
# 때 쓰는 "쉿..." 류 문구)를 구매 완료 반응에도 그대로 재사용해서 방금 산 물건과
# 무관한 문구가 붙는 문제가 있었다. 확률형 간식/도구 각각 전용 반응 풀로 분리했다.
_SNACK_PURCHASE_LINES = (
    "쉿, 이거 궁금한 효과가 있대!! _(소곤)_",
    "몰래 산 보람이 있네!! _(뿌듯)_",
    "이거 먹으면 어떻게 될지 두근두근해!! _(긴장)_",
    "위험하지만 끌리는 물건이야!! _(호기심)_",
    "이런 걸 파는 데가 여기밖에 없을 거야!! _(자랑)_",
    "몰래 챙겨왔어!! _(살금)_",
    "이거 먹을 때 완전 떨릴 것 같아!! _(긴장)_",
    "수상하지만 놓칠 수 없었어!! _(단호)_",
    "이 정체불명의 물건, 기대돼!! _(설렘)_",
    "쉿, 아무한테도 말 안 할게!! _(비밀)_",
    "결과가 어떻게 나올지 아무도 몰라!! _(스릴)_",
    "이거 먹는 순간이 진짜 도박이야!! _(긴장)_",
    "밤에만 구할 수 있는 귀한 거야!! _(뿌듯)_",
    "이거 손에 넣다니 믿기지 않아!! _(흥분)_",
    "몰래 하나 더 챙겼어!! _(장난)_",
    "결과는 나중에 확인해볼게!! _(설렘)_",
    "이 수상한 물건, 완전 내 취향이야!! _(만족)_",
    "어둠 속 거래, 성공적이었어!! _(뿌듯)_",
    "이거 먹으면 무슨 일이 생길까?? _(궁금)_",
    "은밀하게 손에 넣은 전리품이야!! _(자랑)_",
)
_TOOL_PURCHASE_LINES = (
    "이거 쓸 데가 많을 것 같아!! _(기대)_",
    "쉿, 유용한 걸 챙겼어!! _(소곤)_",
    "이거 나중에 써먹어야지!! _(다짐)_",
    "몰래 좋은 물건을 건졌네!! _(뿌듯)_",
    "이 도구, 진짜 필요했던 거야!! _(만족)_",
    "언제 써볼까, 벌써 기대돼!! _(설렘)_",
    "이거 은근 쓸모 있어 보여!! _(호기심)_",
    "밤에만 구할 수 있는 특별한 물건이야!! _(자랑)_",
    "이걸로 뭘 할 수 있을지 궁금해!! _(궁금)_",
    "잘 챙겨뒀다가 나중에 쓸게!! _(단호)_",
    "이 도구, 딱 필요했던 참이야!! _(안도)_",
    "몰래 산 보람이 있는 물건이네!! _(뿌듯)_",
    "이거 하나로 하루가 달라질 것 같아!! _(기대)_",
    "쓰임새가 기대되는 아이템이야!! _(설렘)_",
    "이 도구, 소중히 다뤄야겠다!! _(다짐)_",
    "이런 물건은 흔치 않아!! _(자랑)_",
    "이거 바로 써보고 싶어!! _(들뜸)_",
    "어둠 속에서 건진 유용한 물건이야!! _(만족)_",
    "이 도구 덕분에 편해지겠다!! _(안심)_",
    "손에 넣자마자 쓰고 싶어져!! _(신남)_",
)

# 2026-09-09 — 카테고리 이름을 컨셉에 맞게 변경(간식→괴식, 도구→장비, 내부 kind
# 값은 그대로), "포션" 카테고리 신설(아직 재고 없음). 순서는 괴식-포션-장비.
_CATEGORY_LABELS: dict[str, str] = {"snack": "괴식", "potion": "포션", "tool": "장비"}
_CATEGORY_ORDER: tuple[str, ...] = ("snack", "potion", "tool")
_DEFAULT_CATEGORY = "snack"

# 임베드는 시스템 요소라 햄미의 반말/오타 페르소나를 쓰지 않고 정중체로 고정한다
# (2026-09-09 — 이 카테고리 안내와 아래 구매 완료 embed가 "~이야!!"/"~있어!!" 같은
# 페르소나 말투로 새 있던 걸 발견해 정정. 페르소나는 embed 밖 content= 플레이버
# 텍스트(_INTRO_LINES 등)에서만 쓴다).
_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "snack": (
        "위험한 확률 음식입니다. 운이 좋으면 크게 오르지만 나쁘면 오히려 깎일 수 있습니다. "
        "결과는 디저트 타임에 `/사용`으로 먹여야 알 수 있습니다."
    ),
    "tool": "산 물건은 `/사용`으로 직접 사용해보세요. 한 번에 하나씩만 사용할 수 있습니다.",
}

_EMPTY_CATEGORY_PLACEHOLDER = "재고 준비중"

# 관리자 권한 장난 품목 전용 — /자판기의 舊 op_permission과 동일한 컨셉(2026-09-08
# /암시장으로 이전). 구매 가능 여부·잔액과 무관하게 항상 이 문구로 대체하고 결제 자체를
# 건너뛴다.
_ADMIN_PERMISSION_JOKE_RESPONSE = (
    "뭐?? 햄미는 봇이 아니야!! 그리고 이건 장난으로 넣어둔 거야!! _(단호)_"
)


def _category_items(kind: str) -> list:
    return [item for item in ITEMS if item.kind == kind]


def _item_block(item, purchase_count: int) -> str:
    """/자판기와 동일한 카드형(이름+횟수 줄 / 가격 줄 / 효과 줄로 3줄 — 2026-09-08
    가격과 효과를 한 줄에 "—"로 붙여 쓰다가 모바일에서 줄바꿈이 애매하게 꺾여
    가독성이 떨어진다는 지적으로 줄을 분리했다). 확률적 간식은 "N 오르거나 M 감소"
    형태로 두 결과를 함께 보여주고, 장비는 카탈로그의 description을 그대로 쓴다.
    장난 품목(is_joke)도 2026-09-09부터 다른 품목과 동일하게 "(N회 구매)"를
    표시한다(실제 의미는 없는 숫자지만 — 결제/로그가 안 남아 항상 0 — 일관성을
    위해 예외 없이 보여준다)."""
    count_suffix = f" ({purchase_count}회 구매)"
    if item.kind == "snack":
        if item.double_or_halve:
            detail = "먹일 시 호감도가 현재의 2배가 되거나 절반으로 줄어듦"
        else:
            detail = f"먹일 시 호감도 +{item.good_delta} 또는 {item.bad_delta}"
    else:
        detail = item.description or "???"
    return f"**{item.name}**{count_suffix}\n{item.price:,}코인\n{detail}"


_MAX_CATEGORY_ITEMS = max(len(_category_items(kind)) for kind in _CATEGORY_ORDER) or 1


async def _build_shop_embed(kind: str, counts: dict[str, int]) -> discord.Embed:
    items = _category_items(kind)
    blocks = [_item_block(item, counts.get(item.id, 0)) for item in items]
    if not blocks:
        blocks = [_EMPTY_CATEGORY_PLACEHOLDER]
    blocks += [""] * (_MAX_CATEGORY_ITEMS - len(blocks))
    embed = discord.Embed(title="🌙 암시장", color=discord.Color.dark_purple())
    description = f"**[{_CATEGORY_LABELS[kind]}]**\n\n" + "\n\n".join(blocks)
    category_note = _CATEGORY_DESCRIPTIONS.get(kind)
    if category_note:
        description += f"\n\n{category_note}"
    embed.description = description
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


async def _execute_purchase(user_id: int, item) -> str | tuple[str, discord.Embed]:
    """결제+지급 실행 — 결과(호감도 증감)는 이 자리에서 정해지지 않는다. 암시장은
    간식을 인벤토리에 넣어줄 뿐이고, 실제 50/50 굴림은 나중에 /사용(디저트 타임)으로
    먹였을 때 일어난다(command/eat.py 참고)."""
    if not await spend_coins(user_id, item.price):
        return random.choice(INSUFFICIENT_FUNDS_LINES)

    new_qty = await add_snack(user_id, item.id, 1)
    await record_purchase(user_id, item.id, item.price)

    user = await get_user(user_id)
    current_coins = user["coins"]
    before_coins = current_coins + item.price

    embed = discord.Embed(title="🌙 은밀한 거래 완료!!", color=discord.Color.dark_purple())
    embed.description = (
        f"- 품목: {item.name}\n"
        f"- 기존 금액: {before_coins:,}코인\n"
        f"- 사용 금액: {item.price:,}코인\n"
        f"- 현재 금액: {current_coins:,}코인\n"
        f"- {item.name}{josa(item.name, '을', '를')} 받았습니다. (보유: {new_qty}개)"
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    purchase_lines = _SNACK_PURCHASE_LINES if item.kind == "snack" else _TOOL_PURCHASE_LINES
    return random.choice(purchase_lines), embed


class _ItemSelect(discord.ui.Select):
    def __init__(self, items: list, counts: dict[str, int], selected_id: str | None) -> None:
        options = [
            discord.SelectOption(
                label=item.name,
                value=item.id,
                description=f"{item.price:,}코인",
                default=(item.id == selected_id),
            )
            for item in items
        ] or [discord.SelectOption(label=_EMPTY_CATEGORY_PLACEHOLDER, value="__none__")]
        super().__init__(placeholder="살 물건을 선택해줘!!", options=options, row=0, disabled=not items)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _BlackMarketView = self.view
        if not await reject_if_wrong_invoker(interaction, view.user_id):
            return
        view.selected_item_id = self.values[0]
        await interaction.response.defer()


class _CategoryButton(discord.ui.Button):
    def __init__(self, kind: str, *, active: bool) -> None:
        style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary
        super().__init__(label=_CATEGORY_LABELS[kind], style=style, row=1)
        self._kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _BlackMarketView = self.view
        if not await reject_if_wrong_invoker(interaction, view.user_id):
            return
        await view.switch_category(interaction, self._kind)


class _BuyButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="구매", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _BlackMarketView = self.view
        if not await reject_if_wrong_user_with_cta(interaction, view.user_id, "/암시장"):
            return

        item = view.selected_item()
        if item is None:
            await interaction.response.send_message("먼저 살 품목을 골라줘!!", ephemeral=True)
            return

        if item.is_joke:
            # 장난 품목은 결제 자체를 안 하므로 모달(금액 확인)을 띄울 이유가 없다 —
            # /자판기의 op_permission과 동일하게 즉시 이 문구로 답한다.
            await interaction.response.send_message(_ADMIN_PERMISSION_JOKE_RESPONSE, ephemeral=True)
            return

        user = await get_user(view.user_id)
        before = user["coins"] if user is not None else 0

        # 잔액이 모자라면 모달 자체를 열지 않는다(2026-09-09 — 이전엔 모달을 일단
        # 띄워 "구매 후 잔액"이 음수로 보이다가 실제 결제 시점(_execute_purchase의
        # spend_coins)에야 실패했다).
        if before < item.price:
            await interaction.response.send_message(random.choice(INSUFFICIENT_FUNDS_LINES), ephemeral=True)
            return

        async def _on_confirm(modal_interaction: discord.Interaction) -> None:
            result = await _execute_purchase(view.user_id, item)
            if isinstance(result, tuple):
                text, embed = result
                await modal_interaction.response.send_message(content=text, embed=embed, ephemeral=True)
            else:
                await modal_interaction.response.send_message(result, ephemeral=True)

        await interaction.response.send_modal(
            PurchaseConfirmModal(item_name=item.name, before=before, price=item.price, on_confirm=_on_confirm)
        )


class _BlackMarketView(discord.ui.View):
    def __init__(self, user_id: int, counts: dict[str, int]) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.message: discord.Message | None = None
        self.active_category = _DEFAULT_CATEGORY
        items = _category_items(_DEFAULT_CATEGORY)
        self.selected_item_id: str | None = items[0].id if items else None
        self._select: _ItemSelect | None = None
        self._rebuild_select(counts)
        for kind in _CATEGORY_ORDER:
            self.add_item(_CategoryButton(kind, active=(kind == _DEFAULT_CATEGORY)))
        self.add_item(_BuyButton())

    def _rebuild_select(self, counts: dict[str, int]) -> None:
        if self._select is not None:
            self.remove_item(self._select)
        items = _category_items(self.active_category)
        self._select = _ItemSelect(items, counts, self.selected_item_id)
        self.add_item(self._select)

    def selected_item(self):
        for item in _category_items(self.active_category):
            if item.id == self.selected_item_id:
                return item
        return None

    async def switch_category(self, interaction: discord.Interaction, kind: str) -> None:
        self.active_category = kind
        items = _category_items(kind)
        self.selected_item_id = items[0].id if items else None
        counts = await get_purchase_counts(self.user_id)
        self._rebuild_select(counts)
        for child in self.children:
            if isinstance(child, _CategoryButton):
                child.style = (
                    discord.ButtonStyle.success if child._kind == kind else discord.ButtonStyle.secondary
                )
        embed = await _build_shop_embed(kind, counts)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logging.exception("Failed to clear black market buttons on timeout")


async def handle(user_id: int) -> tuple[str, discord.Embed, discord.ui.View]:
    counts = await get_purchase_counts(user_id)
    embed = await _build_shop_embed(_DEFAULT_CATEGORY, counts)
    return random.choice(_INTRO_LINES), embed, _BlackMarketView(user_id, counts)
