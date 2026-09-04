import random
from datetime import datetime

import discord

import achievements
from command.economy_common import INSUFFICIENT_FUNDS_LINES, VENDING_EMBED_COLOR, format_table
from command.vending_catalog import BY_NAME, ITEMS
from events.scheduler import KST, format_footer_time
from db.achievements import award as award_achievement
from db.affection import format_affection_notice
from db.snacks import add_snack
from db.users import get_user
from db.wallet import increase_max_coins, spend_coins

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

    total_cost = (item.price // 100) * count
    if not await spend_coins(user_id, total_cost):
        return random.choice(INSUFFICIENT_FUNDS_LINES)

    if item.kind == "snack":
        new_qty = await add_snack(user_id, item.id, count)
        effect_summary = f"{item.name} x{count}개를 받았어!! (보유: {new_qty}개)"
    else:  # "capacity"
        gained = item.effect * count
        new_max = await increase_max_coins(user_id, gained)
        effect_summary = f"동전 최대 보유량이 {gained}만큼 늘어서 이제 {new_max}개까지 담을 수 있어!!"

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
        f"- 차감: {total_cost * 100:,}원\n"
        f"- {effect_summary}"
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    text = random.choice(_INTRO_LINES)
    for notice in achievement_notices:
        text += f"\n{notice}"
    if total_delta != 0:
        text += format_affection_notice(total_delta, current_affection)
    return text, embed


async def handle_list() -> tuple[str, discord.Embed]:
    embed = discord.Embed(title="🛒 자판기 판매 목록", color=VENDING_EMBED_COLOR)
    rows = []
    for item in ITEMS:
        note = f" ({item.note})" if item.note else ""
        if item.kind == "snack":
            detail = f"먹일 시 호감도 +{item.effect}{note}"
        elif item.kind == "capacity":
            detail = f"최대 동전 개수 +{item.effect}"
        else:
            detail = "???"
        rows.append((item.name, f"{item.price:,}원", detail))
    embed.description = format_table(("품목", "가격", "효과"), rows, right_align=(1,))
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return random.choice(_INTRO_LINES), embed
