import logging
import random
from datetime import datetime

import discord

import achievements
from core.base import reject_if_wrong_invoker
from command.economy_common import INSUFFICIENT_FUNDS_LINES, VENDING_EMBED_COLOR
from command.vending_catalog import BY_NAME, ITEMS
from events.scheduler import KST, format_footer_time
from db.achievements import award as award_achievement
from db.affection import format_affection_notice
from db.snacks import add_snack
from db.users import get_user
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

_JOKE_RESPONSE = "...어?? 이건 사실 파는 거 아니야!! 장난으로 넣어둔 거야!! _(웃음)_"
_INVALID_COUNT_RESPONSE = "1개 이상 사야지!! _(갸웃)_"


async def handle_purchase(
    user_id: int, item_name: str, count: int
) -> str | tuple[str, discord.Embed]:
    item = BY_NAME[item_name]  # 슬래시 커맨드 Literal 제약으로 항상 존재.
    if count < 1:
        return _INVALID_COUNT_RESPONSE

    if item.kind == "joke":
        return _JOKE_RESPONSE

    # 2026-09-05부로 "원" 단위 개념 자체를 없앴다 — price는 이제 코인 그대로다(구
    # //100 환산 불필요).
    total_cost = item.price * count
    if not await spend_coins(user_id, total_cost):
        return random.choice(INSUFFICIENT_FUNDS_LINES)

    if item.kind == "snack":
        new_qty = await add_snack(user_id, item.id, count)
        effect_summary = f"{item.name} x{count}개를 받았어!! (보유: {new_qty}개)"
    else:  # "coin" — /동전 그랜트 보너스 증가
        gained = item.effect * count
        new_bonus = await increase_coin_grant_bonus(user_id, gained)
        effect_summary = (
            f"/동전 획득량이 {gained}만큼 늘어서 이제 한 번에 {1 + new_bonus}개씩 벌 수 있어!!"
        )

    user = await get_user(user_id)
    total_delta = 0
    current_affection = user["affection"]
    achievement_notices: list[str] = []

    first_purchase = await award_achievement(user_id, achievements.vending_first_purchase.ID)
    if first_purchase["earned"]:
        total_delta += first_purchase["applied_amount"]
        current_affection = first_purchase["new_affection"]
        achievement_notices.append(
            f"🏆 업적 달성: {achievements.format_name(achievements.vending_first_purchase)}!!"
        )
    if item.id == "piggy_bank":
        savings = await award_achievement(user_id, achievements.savings_start.ID)
        if savings["earned"]:
            total_delta += savings["applied_amount"]
            current_affection = savings["new_affection"]
            achievement_notices.append(
                f"🏆 업적 달성: {achievements.format_name(achievements.savings_start)}!!"
            )

    embed = discord.Embed(title="🛒 구매 완료!!", color=VENDING_EMBED_COLOR)
    embed.description = (
        f"- 품목: {item.name} x {count}\n"
        f"- 차감: {total_cost:,}코인\n"
        f"- {effect_summary}"
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    text = random.choice(_INTRO_LINES)
    for notice in achievement_notices:
        text += f"\n{notice}"
    if total_delta != 0:
        # total_delta는 여기서 항상 업적 보너스(vending_first_purchase/savings_start,
        # apply_day_multiplier=False)로만 구성된다 — 배율 분해 대상이 아니다.
        text += format_affection_notice(total_delta, current_affection, multiplier_eligible=False)
    return text, embed


# /자판기-리스트 카테고리(2026-09-05 신규) — 모바일에서 표(코드블록) 형태가 깨져
# 보인다는 피드백으로 표를 없애고 카테고리 버튼으로 나눴다. 기본값은 "간식".
_CATEGORY_LABELS: dict[str, str] = {"snack": "간식", "coin": "동전", "joke": "기타"}
_CATEGORY_ORDER: tuple[str, ...] = ("snack", "coin", "joke")
_DEFAULT_CATEGORY = "snack"


def _category_items(kind: str) -> list:
    return [item for item in ITEMS if item.kind == kind]


def _item_block(item) -> str:
    """업적 리스트(command/achievements.py)와 동일한 카드형 — 이름 줄 + 설명 줄로
    가독성을 높인다(2026-09-06, 舊 "- **이름** — 가격 (설명)" 한 줄 형식에서 변경)."""
    note = f" ({item.note})" if item.note else ""
    if item.kind == "snack":
        detail = f"먹일 시 호감도 +{item.effect}{note}"
    elif item.kind == "coin":
        detail = f"/동전 획득량 +{item.effect}"
    else:
        detail = "???"
    return f"**{item.name}**\n{item.price:,}코인 — {detail}"


# 카테고리를 바꿔도 임베드 세로 길이가 들쭉날쭉하지 않게, 가장 긴 카테고리(간식) 품목
# 수에 맞춰 짧은 카테고리는 빈 칸으로 채운다.
_MAX_CATEGORY_ITEMS = max(len(_category_items(kind)) for kind in _CATEGORY_ORDER)


def _build_list_embed(kind: str) -> discord.Embed:
    blocks = [_item_block(item) for item in _category_items(kind)]
    blocks += [""] * (_MAX_CATEGORY_ITEMS - len(blocks))
    embed = discord.Embed(title="🛒 자판기 판매 목록", color=VENDING_EMBED_COLOR)
    embed.description = f"**[{_CATEGORY_LABELS[kind]}]**\n\n" + "\n\n".join(blocks)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


class _CategoryButton(discord.ui.Button):
    def __init__(self, kind: str, *, active: bool) -> None:
        # 활성=초록(success), 비활성=회색(secondary) — 2026-09-06 舊 파란색(primary)에서
        # 변경, /랭킹의 카테고리 버튼(command/ranking.py)도 동일한 배색을 쓴다.
        style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary
        super().__init__(label=_CATEGORY_LABELS[kind], style=style)
        self._kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _VendingListView = self.view
        if not await reject_if_wrong_invoker(interaction, view.user_id):
            return
        for child in view.children:
            if isinstance(child, _CategoryButton):
                child.style = (
                    discord.ButtonStyle.success if child is self else discord.ButtonStyle.secondary
                )
        await interaction.response.edit_message(embed=_build_list_embed(self._kind), view=view)


class _VendingListView(discord.ui.View):
    """1분간 상호작용이 없으면 버튼만 지운다(내용은 그대로 둠) — 명령어 실행자
    (user_id) 외에는 카테고리 버튼을 못 누른다(2026-09-06, 이전엔 검증이 없었음)."""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.message: discord.Message | None = None
        for kind in _CATEGORY_ORDER:
            self.add_item(_CategoryButton(kind, active=(kind == _DEFAULT_CATEGORY)))

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logging.exception("Failed to clear vending list buttons on timeout")


async def handle_list(user_id: int) -> tuple[str, discord.Embed, discord.ui.View]:
    return random.choice(_INTRO_LINES), _build_list_embed(_DEFAULT_CATEGORY), _VendingListView(user_id)
