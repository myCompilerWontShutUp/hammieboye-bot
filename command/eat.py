import random

import discord
from discord import app_commands

import achievements
from command.vending_catalog import BY_ID, BY_NAME
from events.dessert_time import current_slot
from db.achievements import award as award_achievement
from db.affection import add_affection, format_affection_notice
from db.daily_stats import claim_dessert_slot, dessert_snack_id, ensure_daily_stats
from db.snacks import add_snack, consume_snack, get_inventory
from db.users import increment_snacks_given

_METHOD = "dessert_feed"
_MAX_AUTOCOMPLETE = 25

_NOT_DESSERT_TIME_LINES = (
    "지금은 디저트 타임이 아니야!! 하루 3번(아침/점심/저녁) 열려!! _(갸웃)_",
    "아직 간식 먹을 시간이 아니야!! 조금만 기다려줘!! _(아쉬움)_",
    "디저트 타임이 아니라서 지금은 못 먹어!! _(미안)_",
    "배는 안 고파!! 디저트 타임에 다시 와줄래?? _(갸웃)_",
    "지금은 간식 시간이 아니야!! _(고개 저음)_",
    "디저트 타임에만 먹을 수 이써!! 그때 다시 줘봐!! _(설명)_",
    "아직은 때가 아니야!! 디저트 타임을 기다려줘!! _(끄덕)_",
    "지금 먹으면 배탈 나!! 디저트 타임에 줘!! _(장난)_",
    "간식은 디저트 타임에만!! 지금은 안 돼!! _(단호)_",
    "아직 배가 안 고파!! 디저트 타임까지 기다려줄래?? _(웃음)_",
    "지금은 그냥 넘어가자!! 디저트 타임에 봐!! _(끄덕)_",
    "디저트 타임이 아니라서 사양할게!! _(미안)_",
    "아직 시간이 아니야!! 조금만 참아줘!! _(갸웃)_",
    "지금 먹기엔 일러!! 디저트 타임 때 줘!! _(설명)_",
    "간식은 정해진 시간에만!! 지금은 패스!! _(단호)_",
    "디저트 타임 아니면 안 먹어!! _(고집)_",
    "아직 그때가 아니야!! 기다려줄래?? _(부탁)_",
    "지금은 배 안 고파!! 나중에 줘봐!! _(웃음)_",
    "디저트 타임 시작하면 알려줄게!! 그때 줘!! _(약속)_",
    "때를 기다려야지!! 지금은 안 돼!! _(단호)_",
)
_ALREADY_FED_LINES = (
    "이번 디저트 타임엔 이미 먹었어!! 다음 시간에 또 줘!! _(만족)_",
    "벌써 배불러!! 이번 타임은 이미 먹었어!! _(뿌듯)_",
    "지금 타임엔 이미 먹었잖아!! _(웃음)_",
    "한 번 더는 무리야!! 이번 디저트 타임은 끝!! _(배부름)_",
    "이미 이번 시간엔 먹었어!! 다음 타임 기다려줘!! _(끄덕)_",
    "배가 이미 불러써!! 다음 디저트 타임에 또 줘!! _(만족)_",
    "이번 타임 몫은 벌써 먹었어!! _(웃음)_",
    "또 먹으면 배탈 나!! 이번엔 이미 먹었잖아!! _(장난)_",
    "이번 디저트 타임은 이미 끝났어!! _(뿌듯)_",
    "한 번 더 먹기엔 배불러!! 다음에 또 줘!! _(만족)_",
    "이번 시간엔 이미 챙겨 먹었어!! _(웃음)_",
    "벌써 이번 타임 몫 다 먹었는데?? _(갸웃)_",
    "다음 디저트 타임까지 기다려줄래?? 이미 먹었어!! _(부탁)_",
    "이번엔 이미 먹었으니 다음 기회에!! _(끄덕)_",
    "배부르다!! 이번 타임은 이걸로 충분해!! _(만족)_",
    "또 주는 거야?? 이번엔 이미 먹었어!! _(웃음)_",
    "이번 디저트 타임 몫은 이미 다 먹었어!! _(뿌듯)_",
    "한 타임에 한 번씩!! 이번엔 이미 먹었어!! _(설명)_",
    "다음 시간에 또 챙겨줘!! 지금은 배불러!! _(만족)_",
    "이미 먹었는데 또?? 배부르지만 고마워!! _(웃음)_",
)
_NO_SNACK_LINES = (
    "어라, 그 간식은 안 가지고 있는데?? _(갸웃)_",
    "그 간식은 없어!! 자판기에서 사 올래?? _(권유)_",
    "가방을 뒤져봤는데 그건 없어!! _(당황)_",
    "그 간식은 안 보여!! /내가방으로 확인해볼래?? _(안내)_",
    "어?? 그건 하나도 없는데?? _(놀람)_",
    "그 간식은 가지고 있지 않아!! _(미안)_",
    "가방에 그건 없어!! 자판기에서 사보자!! _(권유)_",
    "그 간식, 다 떨어졌나 봐!! _(아쉬움)_",
    "어라, 그건 재고가 없어!! _(갸웃)_",
    "그 간식은 못 찾겠어!! _(당황)_",
    "가방 확인해봤는데 그건 없어!! _(미안)_",
    "그건 안 가지고 있어!! 다른 간식은 어때?? _(제안)_",
    "그 간식은 자판기에서 사야 할 것 같아!! _(안내)_",
    "어?? 그거 하나도 없잖아!! _(놀람)_",
    "그 간식은 가방에 없어!! _(갸웃)_",
    "그건 다 떨어졌나 봐!! 자판기 들러봐!! _(권유)_",
    "가방을 열어봤는데 그건 안 보여!! _(당황)_",
    "그 간식은 없는 것 같아!! _(미안)_",
    "어라, 그건 재고 부족이야!! _(아쉬움)_",
    "그 간식은 못 가지고 있어!! /내가방 확인해봐!! _(안내)_",
)
_FEED_SUCCESS_LINES = (
    "냠냠!! {snack} 완전 맛있어!! _(행복)_",
    "우와, {snack}!! 진짜 맛있다!! _(황홀)_",
    "{snack} 냠냠 잘 먹었어!! _(만족)_",
    "오물오물, {snack} 최고야!! _(행복)_",
    "{snack}!! 이거 완전 좋아해!! _(신남)_",
    "냠냠냠, {snack} 맛있게 먹었어!! _(뿌듯)_",
    "{snack} 주다니, 최고의 선물이야!! _(감동)_",
    "오늘의 간식은 {snack}!! 냠냠!! _(행복)_",
    "{snack} 먹으니까 기분이 좋아져!! _(들뜸)_",
    "냠, {snack} 진짜 맛있다!! _(황홀)_",
    "{snack} 완전 맛있게 먹었어!! _(만족)_",
    "오물오물오물, {snack} 최고!! _(행복)_",
    "{snack} 줘서 고마워!! 냠냠!! _(감사)_",
    "이야, {snack}!! 오늘 최고의 간식이야!! _(신남)_",
    "{snack} 냠냠, 배가 든든해!! _(뿌듯)_",
    "우와아, {snack} 정말 맛있어!! _(황홀)_",
    "{snack} 먹으니까 행복해!! _(행복)_",
    "냠냠, {snack} 완전 취향저격!! _(들뜸)_",
    "{snack} 고마워!! 잘 먹었습니다!! _(감사)_",
    "오늘도 {snack} 덕분에 행복해!! _(만족)_",
)


async def autocomplete_간식(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """호출한 본인의 간식 인벤토리 기준으로만 제안한다(/먹어는 항상 자기 자신에게만
    먹이므로 /니정보류와 달리 대상자 조회가 필요 없다)."""
    inventory = await get_inventory(interaction.user.id)
    query = current.strip().lower()
    choices: list[app_commands.Choice[str]] = []
    for row in inventory:
        item = BY_ID.get(row["snack_id"])
        if item is None or query and query not in item.name.lower():
            continue
        choices.append(app_commands.Choice(name=f"{item.name} ({row['quantity']}개)", value=item.name))
        if len(choices) >= _MAX_AUTOCOMPLETE:
            break
    return choices


async def handle(user_id: int, snack_name: str) -> str:
    slot = current_slot()
    if slot is None:
        return random.choice(_NOT_DESSERT_TIME_LINES)

    stats = await ensure_daily_stats(user_id)
    fed_today = dict(stats.get("dessert_fed_today") or {})
    if slot in fed_today:
        return random.choice(_ALREADY_FED_LINES)

    item = BY_NAME.get(snack_name)
    if item is None or item.kind != "snack":
        return random.choice(_NO_SNACK_LINES)
    if not await consume_snack(user_id, item.id):
        return random.choice(_NO_SNACK_LINES)

    # 위의 fed_today 확인은 "이미 먹은 게 뻔한" 경우를 빠르게 걸러내는 사전 확인일 뿐,
    # 실제 슬롯 선점은 이 원자적 클레임이 최종 권한자다 — 두 요청이 같은 슬롯에 거의
    # 동시에 도착해도(연타 등) 딱 하나만 성공한다(TOCTOU 방지). 방금 소비한 간식은
    # 이 클레임이 실패하면(아주 드문 경합) 그대로 돌려준다.
    if not await claim_dessert_slot(user_id, slot, item.id):
        await add_snack(user_id, item.id, 1)
        return random.choice(_ALREADY_FED_LINES)

    fed_today[slot] = item.id
    await increment_snacks_given(user_id)

    result = await add_affection(user_id, item.effect, _METHOD)
    text = random.choice(_FEED_SUCCESS_LINES).format(snack=item.name)

    total_delta = result["applied_amount"]
    current_affection = result["new_affection"]
    achievement_notices: list[str] = []
    if result["achievement_notice"]:
        achievement_notices.append(result["achievement_notice"])

    if len({dessert_snack_id(v) for v in fed_today.values()}) == 3:
        three_meals = await award_achievement(user_id, achievements.three_meals_a_day.ID)
        if three_meals["earned"]:
            total_delta += three_meals["applied_amount"]
            current_affection = three_meals["new_affection"]
            achievement_notices.append(
                f"🏆 업적 달성: {achievements.format_name(achievements.three_meals_a_day)}!!"
            )

    if item.id == "premium_mealworm":
        strongest = await award_achievement(user_id, achievements.strongest_snack_ever.ID)
        if strongest["earned"]:
            total_delta += strongest["applied_amount"]
            current_affection = strongest["new_affection"]
            achievement_notices.append(
                f"🏆 업적 달성: {achievements.format_name(achievements.strongest_snack_ever)}!!"
            )

    for notice in achievement_notices:
        text += f"\n{notice}"
    if total_delta != 0:
        text += format_affection_notice(total_delta, current_affection)
    return text
