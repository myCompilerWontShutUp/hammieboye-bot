import random

import achievements
from command.black_market_catalog import BlackMarketItem
from command.black_market_catalog import BY_ID as _BLACK_MARKET_BY_ID
from command.black_market_catalog import BY_NAME as _BLACK_MARKET_BY_NAME
from command.vending_catalog import BY_ID as _VENDING_BY_ID
from command.vending_catalog import BY_NAME as _VENDING_BY_NAME
from events.announcements import apply_xp_and_check_levelup
from events.dessert_time import current_slot
from db.achievements import award as award_achievement
from db.affection import add_affection, add_affection_uncapped, format_affection_notice
from db.daily_stats import claim_dessert_slot, dessert_snack_id, ensure_daily_stats
from db.snacks import add_snack, consume_snack
from db.users import get_user, increment_snacks_given

_METHOD = "dessert_feed"


def find_by_id(snack_id: str):
    """/자판기·/암시장 두 카탈로그를 합쳐서 조회한다 — user_snacks의 snack_id는 어느
    카탈로그 것이든 그냥 문자열이라 출처를 구분해서 저장하지 않는다. command/use.py도
    아이템 종류 판별에 그대로 재사용한다(밑줄 없는 공개 이름으로 통일)."""
    return _VENDING_BY_ID.get(snack_id) or _BLACK_MARKET_BY_ID.get(snack_id)


def find_by_name(snack_name: str):
    return _VENDING_BY_NAME.get(snack_name) or _BLACK_MARKET_BY_NAME.get(snack_name)

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
    "그 간식은 안 보여!! `/내정보`에서 가방을 확인해볼래?? _(안내)_",
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
    "그 간식은 못 가지고 있어!! `/내정보`의 가방을 확인해봐!! _(안내)_",
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




async def handle(user_id: int, snack_name: str, *, guild_id: int | None = None) -> str:
    slot = current_slot()
    if slot is None:
        return random.choice(_NOT_DESSERT_TIME_LINES)

    stats = await ensure_daily_stats(user_id)
    fed_today = dict(stats.get("dessert_fed_today") or {})
    if slot in fed_today:
        return random.choice(_ALREADY_FED_LINES)

    item = find_by_name(snack_name)
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
    # 레벨/XP 시스템(2026-09-10) — "디저트 타임 이벤트" +10xp(슬롯당 1회 제한이 이미
    # 있어 하루 최대 +30, 추가 상한 불필요).
    await apply_xp_and_check_levelup(user_id, 10, guild_id=guild_id)

    if isinstance(item, BlackMarketItem):
        # 암시장 확률적 간식 — item.good_chance로 결과를 굴린다(2026-09-09 신규,
        # 기본 50/50이지만 산딸기?는 25%로 예외). 악마의 씨앗(double_or_halve)만
        # 예외로, 고정 델타 대신 "지금 호감도의 2배" 또는 "지금 호감도의 절반으로
        # 감소"를 적용한다(최초 설계는 "0으로 리셋"이었으나 너무 가혹하다는 피드백으로
        # 완화). uncapped + 배율 미적용(add_affection의 일일 +100 상한/주말·생일
        # 배율은 이런 극단적인 도박성 결과와 안 어울려 건너뛴다) — 업적 달성 보너스와
        # 동일한 원칙.
        good = random.random() < item.good_chance
        if item.double_or_halve:
            current_user = await get_user(user_id)
            current_affection_before = current_user["affection"] if current_user is not None else 0
            # 호감도가 음수일 때 current_affection_before를 그대로 쓰면 "2배"(좋음)가
            # 오히려 더 나빠지고 "절반"(나쁨)이 오히려 좋아지는 부호 역전 버그가 있었다
            # (예: -30에서 좋음 결과가 -60, 나쁨 결과가 -15) — 2026-09-08 발견/수정.
            # abs()로 크기만 취해 방향은 항상 good=개선/bad=악화가 되도록 고정한다.
            # 호감도가 0 이상일 때는 abs(x) == x라 기존 동작(정확히 2배/절반)과 완전히
            # 동일하다.
            magnitude = abs(current_affection_before)
            delta = magnitude if good else -(magnitude // 2)
        else:
            delta = item.good_delta if good else item.bad_delta
        result = await add_affection_uncapped(
            user_id, delta, _METHOD, apply_day_multiplier=False, guild_id=guild_id
        )
        reaction = item.good_reaction if good else item.bad_reaction
        text = f"{item.name} 냠냠... {reaction}"
        multiplier_eligible = False
    else:
        result = await add_affection(user_id, item.effect, _METHOD, guild_id=guild_id)
        text = random.choice(_FEED_SUCCESS_LINES).format(snack=item.name)
        multiplier_eligible = True

    total_delta = result["applied_amount"]
    current_affection = result["new_affection"]

    # 2026-09-10부로 업적 달성 알림은 award() 내부에서 별도 글로벌 방송으로 처리된다
    # (호감도 보너스도 폐지) — 여기서는 조건이 맞을 때 부여만 시도하고 인라인 문구는
    # 더 이상 안 붙인다.
    if len({dessert_snack_id(v) for v in fed_today.values()}) == 3:
        await award_achievement(user_id, achievements.three_meals_a_day.ID, guild_id=guild_id)

    if item.id == "premium_mealworm":
        await award_achievement(user_id, achievements.strongest_snack_ever.ID, guild_id=guild_id)

    if total_delta != 0:
        text += format_affection_notice(total_delta, current_affection, multiplier_eligible=multiplier_eligible)
    return text
