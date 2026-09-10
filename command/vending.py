import logging
import random
from datetime import datetime

import discord

import achievements
from core.base import reject_if_wrong_invoker
from core.korean import josa
from command.economy_common import (
    INSUFFICIENT_FUNDS_LINES,
    VENDING_EMBED_COLOR,
    PurchaseConfirmModal,
    reject_if_wrong_user_with_cta,
)
from command.vending_catalog import ITEMS
from events.scheduler import KST, format_footer_time
from db.achievements import award as award_achievement
from db.snacks import add_snack
from db.users import get_user
from db.vending_log import get_purchase_counts, record_purchase
from db.wallet import increase_coin_grant_bonus, spend_coins

# "자판기에 머가 있을까??" 류 — 자판기 응답은 항상 이 한 줄로 먼저 시작한다.
_INTRO_LINES = (
    "자판기에 머가 있을까?? _(궁금)_",
    "짜잔, 자판기 도착!! 뭐가 나올까?? _(설렘)_",
    "자판기 앞에 왔어!! 구경해볼래?? _(신남)_",
    "여기 자판기야!! 뭐 사고 싶어?? _(호기심)_",
    "덜컹덜컹, 자판기가 움직여!! _(긴장)_",
    "자판기 버튼 누를 준비 됐어?? _(기대)_",
    "이 자판기 뭐가 들었을까?? _(궁금)_",
    "자판기한테 물어봤어!! 뭐가 나올까?? _(호기심)_",
    "짤랑짤랑, 동전 넣는 소리가 나!! _(설렘)_",
    "자판기 앞에 줄 서볼까?? _(장난)_",
    "이번엔 뭐가 나올지 궁금해!! _(두근)_",
    "자판기 문이 열려써!! _(신남)_",
    "여기 자판기 발견!! 같이 볼래?? _(들뜸)_",
    "동전 넣으면 뭐가 나올까?? _(궁금)_",
    "자판기가 반짝반짝해!! _(신기)_",
    "덜커덕!! 자판기가 움직이기 시작해써!! _(놀람)_",
    "이 자판기 인기 많대!! _(자랑)_",
    "자판기 구경하러 왔어!! _(신남)_",
    "짜잔!! 오늘의 자판기야!! _(방긋)_",
    "자판기 버튼 눌러볼까?? _(설렘)_",
)

# 구매 완료 응답 전용 문구(2026-09-09 신규) — 이전엔 위 _INTRO_LINES("자판기에 머가
# 있을까??" 류, 자판기를 열 때 쓰는 문구)를 구매 완료 반응에도 그대로 재사용해서
# "이 자판기 뭐가 들었을까??" 같은 문구가 방금 산 물건에 대한 반응인 것처럼 어색하게
# 붙는 문제가 있었다. 간식/투자 품목 각각 전용 반응 풀로 분리했다.
_SNACK_PURCHASE_LINES = (
    "냠냠, 이거 완전 좋아!! _(신남)_",
    "오예!! 간식 득템!! _(흥분)_",
    "이야, 맛있는 냄새가 나!! _(킁킁)_",
    "간식 상자 열어보고 시퍼!! _(설렘)_",
    "이거 냠냠 하기 딱 좋겠다!! _(기대)_",
    "오늘 간식 미리 골라놨어!! _(뿌듯)_",
    "짜잔, 간식 손에 넣었다!! _(자랑)_",
    "이거 디저트 타임에 냠냠 할 거야!! _(신남)_",
    "간식 냄새만 맡아도 행복해!! _(황홀)_",
    "오호, 좋은 걸 골랐네!! _(만족)_",
    "이제 배고플 일 없겠다!! _(안심)_",
    "간식 챙겼으니 든든해!! _(뿌듯)_",
    "이거 먹을 생각하니 벌써 신나!! _(들뜸)_",
    "냠냠 타임이 기다려져!! _(설렘)_",
    "간식 하나 더 모았다!! _(으쓱)_",
    "이거 진짜 잘 산 것 같아!! _(만족)_",
    "오늘도 간식 부자!! _(자랑)_",
    "이 간식, 기대되는데?? _(궁금)_",
    "간식 보따리가 두둑해졌어!! _(흐뭇)_",
    "이거 나중에 꼭 먹을 거야!! _(다짐)_",
)
_INVESTMENT_PURCHASE_LINES = (
    "오, 이제 동전을 더 많이 벌 수 있겠다!! _(신남)_",
    "짜잔, 투자 성공!! _(뿌듯)_",
    "이제 쳇바퀴 돌릴 맛이 나!! _(기대)_",
    "동전 벌이가 쏠쏠해지겠어!! _(흐뭇)_",
    "이거 진짜 알짜 투자였어!! _(만족)_",
    "다음 동전 받을 때가 기다려져!! _(설렘)_",
    "오늘 현명한 소비를 했다구!! _(으쓱)_",
    "이제 동전이 더 잘 모이겠지?? _(궁금)_",
    "투자 잘했다는 느낌이 들어!! _(자신감)_",
    "동전 벌이 업그레이드 완료!! _(신남)_",
    "이거 사길 잘했어!! _(뿌듯)_",
    "미래의 나한테 고마워할 거야!! _(뿌듯)_",
    "이제 부자 될 일만 남았어!! _(들뜸)_",
    "동전 벌이가 든든해졌다!! _(안심)_",
    "오늘의 투자, 성공적이야!! _(만족)_",
    "이거로 동전이 술술 모이겠다!! _(기대)_",
    "잘 굴렸다, 내 동전!! _(자랑)_",
    "이제 쳇바퀴가 더 즐거워질 것 같아!! _(신남)_",
    "동전 창고가 커진 기분이야!! _(뿌듯)_",
    "이 투자, 두고두고 도움 될 거야!! _(확신)_",
)

# 동전 카테고리 품목은 살 때마다 가격이 정확히 2배씩 오른다(효과량은 그대로,
# 2026-09-08 너프) — 이미 산 횟수만큼 이 배수를 곱해서 "다음 구매 가격"을 낸다.
_COIN_PRICE_MULTIPLIER = 2

# "저축의 시작" 업적 — 가장 싼 동전 지갑(coin_wallet)은 제외하고, 쪼꼬미 금고
# 이상의 저금통/계좌류를 사면 얻는다(2026-09-08 — 애초에 tiny_safe 하나만
# 트리거였는데, 그 위 상위 티어를 먼저 사도 인정되도록 확장).
_SAVINGS_START_ELIGIBLE_ITEM_IDS = frozenset(
    {"tiny_safe", "honey_piggy_bank", "hm_bank_account", "cheek_pouch"}
)

# 2026-09-08 개편: /자판기-리스트를 없애고 /자판기 하나에 카테고리 탭(간식/투자) +
# 품목 선택 + 구매 버튼을 합쳤다. "동전"이라는 표시 이름을 "투자"로 바꿨을 뿐 내부
# kind("coin")·가격 인상 로직·효과(=`/동전` 획득량 증가)는 전부 그대로다. 舊 "기타"
# (장난 품목, op_permission)는 이 화면에서 아예 안 보이게 뺐다 — 카탈로그에서 지우진
# 않았지만 이 UI로는 더 이상 도달할 방법이 없다. 2026-09-09 "음료" 카테고리 신설
# (아직 재고 없음, command/black_market.py의 빈 카테고리 처리를 그대로 이식) —
# 순서는 간식-음료-투자.
_CATEGORY_LABELS: dict[str, str] = {"snack": "간식", "beverage": "음료", "coin": "투자"}
_CATEGORY_ORDER: tuple[str, ...] = ("snack", "beverage", "coin")
_DEFAULT_CATEGORY = "snack"

_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "snack": (
        "**디저트 타임** 이벤트 진행 중 `/사용`을 통해 햄미에게 먹이고 호감도를 얻으세요!"
    ),
    "coin": "`/동전` 획득량을 늘리세요! 단, 구매할 때 마다 가격이 2배 상승해요!",
}

_JOKE_RESPONSE = "...어?? 이건 사실 파는 거 아니야!! 장난으로 넣어둔 거야!! _(웃음)_"

# 아직 재고가 없는 카테고리(음료) 전용 — command/black_market.py::_EMPTY_CATEGORY_PLACEHOLDER
# 와 동일한 원칙.
_EMPTY_CATEGORY_PLACEHOLDER = "재고 준비중"


def _category_items(kind: str) -> list:
    return [item for item in ITEMS if item.kind == kind]


def _price_from_counts(item, counts: dict[str, int]) -> int:
    if item.kind != "coin":
        return item.price
    return item.price * (_COIN_PRICE_MULTIPLIER ** counts.get(item.id, 0))


def _item_block(item, price: int, purchase_count: int) -> str:
    """업적 리스트(command/achievements.py)와 동일한 카드형 — 이름 줄 + 가격 줄 + 효과
    줄로 3줄 분리한다(2026-09-08 — 가격과 효과를 "—"로 한 줄에 붙여 쓰다가 모바일에서
    줄바꿈이 애매하게 꺾여 가독성이 떨어진다는 지적으로 줄을 나눴다, /암시장
    ::_item_block과 동일한 원칙). price는 그 유저 기준 "다음 구매 가격"(투자 품목은
    이미 산 횟수만큼 2배씩 올라 카탈로그 기본값과 다를 수 있다)."""
    note = f" ({item.note})" if item.note else ""
    if item.kind == "snack":
        detail = f"먹일 시 호감도 +{item.effect}{note}"
    else:  # "coin"("투자") — /동전 획득량 증가
        detail = f"`/동전` 획득량 +{item.effect}"
    return f"**{item.name}** ({purchase_count}회 구매)\n{price:,}코인\n{detail}"


_MAX_CATEGORY_ITEMS = max(len(_category_items(kind)) for kind in _CATEGORY_ORDER) or 1


async def _build_shop_embed(kind: str, counts: dict[str, int]) -> discord.Embed:
    items = _category_items(kind)
    blocks = [_item_block(item, _price_from_counts(item, counts), counts.get(item.id, 0)) for item in items]
    if not blocks:
        blocks = [_EMPTY_CATEGORY_PLACEHOLDER]
    blocks += [""] * (_MAX_CATEGORY_ITEMS - len(blocks))
    embed = discord.Embed(title="🛒 자판기", color=VENDING_EMBED_COLOR)
    description = f"**[{_CATEGORY_LABELS[kind]}]**\n\n" + "\n\n".join(blocks)
    category_note = _CATEGORY_DESCRIPTIONS.get(kind)
    if category_note:
        description += f"\n\n{category_note}"
    embed.description = description
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


async def _execute_purchase(user_id: int, item) -> str | tuple[str, discord.Embed]:
    """실제 결제+지급을 실행하고 결과 텍스트(+영수증 embed)를 만든다 — 모달 제출
    직후에만 호출되며, 이 시점에 가격을 다시 계산해 그대로 차감한다."""
    counts = await get_purchase_counts(user_id)
    total_cost = _price_from_counts(item, counts)
    if not await spend_coins(user_id, total_cost):
        return random.choice(INSUFFICIENT_FUNDS_LINES)

    # embed.description에 들어가는 문구라 시스템 정중체로 고정한다(2026-09-09 —
    # "받았어!!"/"벌 수 있어!!" 같은 페르소나 말투가 섞여 있던 걸 발견해 정정,
    # command/black_market.py와 동일한 원칙).
    if item.kind == "snack":
        new_qty = await add_snack(user_id, item.id, 1)
        effect_summary = f"{item.name}{josa(item.name, '을', '를')} 받았습니다. (보유: {new_qty}개)"
    else:  # "coin"("투자") — /동전 그랜트 보너스 증가
        new_bonus = await increase_coin_grant_bonus(user_id, item.effect)
        effect_summary = (
            f"`/동전` 획득량이 {item.effect}만큼 늘어나 이제 한 번에 {1 + new_bonus}개씩 "
            "받을 수 있습니다."
        )

    await record_purchase(user_id, item.id, total_cost)

    user = await get_user(user_id)
    current_coins = user["coins"]
    before_coins = current_coins + total_cost  # spend_coins 이후 조회라 역산으로 구한다

    # 2026-09-10부로 업적 달성 알림은 award() 내부에서 별도 글로벌 방송으로 처리된다
    # (호감도 보너스도 폐지) — 여기서는 조건이 맞을 때 부여만 시도한다.
    await award_achievement(user_id, achievements.vending_first_purchase.ID)
    if item.id in _SAVINGS_START_ELIGIBLE_ITEM_IDS:
        await award_achievement(user_id, achievements.savings_start.ID)

    embed = discord.Embed(title="🛒 구매 완료!!", color=VENDING_EMBED_COLOR)
    embed.description = (
        f"- 품목: {item.name}\n"
        f"- 기존 금액: {before_coins:,}코인\n"
        f"- 사용 금액: {total_cost:,}코인\n"
        f"- 현재 금액: {current_coins:,}코인\n"
        f"- {effect_summary}"
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    purchase_lines = _SNACK_PURCHASE_LINES if item.kind == "snack" else _INVESTMENT_PURCHASE_LINES
    text = random.choice(purchase_lines)
    return text, embed


class _ItemSelect(discord.ui.Select):
    """현재 카테고리의 품목 목록 — 고른 값이 view.selected_item_id로 반영된다.
    카테고리를 바꾸면 _VendingView가 이 컴포넌트를 통째로 새로 만들어 갈아끼운다
    (discord.py Select는 옵션 목록을 나중에 바꿔치기하기보다 새로 만드는 쪽이 안전)."""

    def __init__(self, kind: str, items: list, counts: dict[str, int], selected_id: str | None) -> None:
        options = [
            discord.SelectOption(
                label=item.name,
                value=item.id,
                description=f"{_price_from_counts(item, counts):,}코인",
                default=(item.id == selected_id),
            )
            for item in items
        ] or [discord.SelectOption(label=_EMPTY_CATEGORY_PLACEHOLDER, value="__none__")]
        super().__init__(placeholder="구매할 품목을 선택해줘!!", options=options, row=0, disabled=not items)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _VendingView = self.view
        if not await reject_if_wrong_invoker(interaction, view.user_id):
            return
        view.selected_item_id = self.values[0]
        await interaction.response.defer()


class _CategoryButton(discord.ui.Button):
    def __init__(self, kind: str, *, active: bool) -> None:
        # 활성=초록(success), 비활성=회색(secondary) — /랭킹·舊 /자판기-리스트와 동일한 배색.
        style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary
        super().__init__(label=_CATEGORY_LABELS[kind], style=style, row=1)
        self._kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _VendingView = self.view
        if not await reject_if_wrong_invoker(interaction, view.user_id):
            return
        await view.switch_category(interaction, self._kind)


class _BuyButton(discord.ui.Button):
    """카테고리 버튼과 시각적으로 구분되는 별도 색(파랑/primary) — 여기만 누르면 실제
    결제 흐름(모달)으로 들어간다. 타인이 누르면 "너도 /자판기로 직접 해볼 수 있어!!"
    CTA 포함 거절을 받는다(2026-09-08, 공개 메시지에서 벌어지는 구매 액션이라
    /내기·/도박의 버튼들과 동일한 원칙)."""

    def __init__(self) -> None:
        super().__init__(label="구매", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _VendingView = self.view
        if not await reject_if_wrong_user_with_cta(interaction, view.user_id, "/자판기"):
            return

        item = view.selected_item()
        if item is None:
            await interaction.response.send_message("먼저 살 품목을 골라줘!!", ephemeral=True)
            return

        if item.kind == "joke":
            await interaction.response.send_message(_JOKE_RESPONSE, ephemeral=True)
            return

        counts = await get_purchase_counts(view.user_id)
        price = _price_from_counts(item, counts)
        user = await get_user(view.user_id)
        before = user["coins"] if user is not None else 0

        # 잔액이 모자라면 모달 자체를 열지 않는다(2026-09-09 — 이전엔 모달을 일단
        # 띄워 "구매 후 잔액"이 음수로 보이다가 실제 결제 시점(_execute_purchase의
        # spend_coins)에야 실패했다).
        if before < price:
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
            PurchaseConfirmModal(item_name=item.name, before=before, price=price, on_confirm=_on_confirm)
        )


class _VendingView(discord.ui.View):
    """10분간 상호작용이 없으면 버튼만 지운다(내용은 그대로 둠, §23-11) — 명령어
    실행자(user_id) 외에는 카테고리/선택 버튼을 못 누르고, 구매 버튼도 CTA 포함
    거절을 받는다."""

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
        self._select = _ItemSelect(self.active_category, items, counts, self.selected_item_id)
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
            logging.exception("Failed to clear vending buttons on timeout")


async def handle(user_id: int) -> tuple[str, discord.Embed, discord.ui.View]:
    counts = await get_purchase_counts(user_id)
    embed = await _build_shop_embed(_DEFAULT_CATEGORY, counts)
    return random.choice(_INTRO_LINES), embed, _VendingView(user_id, counts)
