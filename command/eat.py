import random
from datetime import datetime, timedelta, timezone

import achievements
from command.black_market_catalog import BlackMarketItem
from command.black_market_catalog import BY_ID as _BLACK_MARKET_BY_ID
from command.black_market_catalog import BY_NAME as _BLACK_MARKET_BY_NAME
from command.vending_catalog import BY_ID as _VENDING_BY_ID
from command.vending_catalog import BY_NAME as _VENDING_BY_NAME
from events.announcements import apply_xp_and_check_levelup, format_xp_notice
from events.dessert_time import current_slot, get_or_roll_slot_kind
from events.scheduler import KST, mark_late_sleep
from db.achievements import award as award_achievement
from db.affection import add_affection, add_affection_uncapped, format_affection_notice
from db.daily_stats import claim_dessert_slot, dessert_snack_id, ensure_daily_stats
from db.sleep_delay import claim_sleep_delay
from db.snacks import add_snack, consume_snack
from db.users import get_user, increment_snacks_given, set_affection_shield_until

_METHOD = "dessert_feed"

# "세계최강울트라킹왕짱간식"(전설) 트리거 품목 — 2026-09-12부로 프리미엄 건조 밀웜
# 또는 황금 피넛 버터 쉐이크(신규 음료, §24) 둘 중 하나만 먹여도 획득된다(OR 조건).
_STRONGEST_SNACK_ITEM_IDS = frozenset({"premium_mealworm", "golden_peanut_butter_shake"})


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
_DRINK_SUCCESS_LINES = (
    "꿀꺽!! {snack} 완전 시원해!! _(행복)_",
    "우와, {snack}!! 진짜 맛있다!! _(황홀)_",
    "{snack} 꿀꺽 잘 마셨어!! _(만족)_",
    "벌컥벌컥, {snack} 최고야!! _(행복)_",
    "{snack}!! 이거 완전 좋아해!! _(신남)_",
    "꿀꺽꿀꺽, {snack} 맛있게 마셨어!! _(뿌듯)_",
    "{snack} 주다니, 최고의 선물이야!! _(감동)_",
    "오늘의 음료는 {snack}!! 꿀꺽!! _(행복)_",
    "{snack} 마시니까 기분이 좋아져!! _(들뜸)_",
    "꿀꺽, {snack} 진짜 맛있다!! _(황홀)_",
    "{snack} 완전 시원하게 마셨어!! _(만족)_",
    "벌컥벌컥벌컥, {snack} 최고!! _(행복)_",
    "{snack} 줘서 고마워!! 꿀꺽!! _(감사)_",
    "이야, {snack}!! 오늘 최고의 음료야!! _(신남)_",
    "{snack} 꿀꺽, 속이 시원해!! _(뿌듯)_",
    "우와아, {snack} 정말 맛있어!! _(황홀)_",
    "{snack} 마시니까 행복해!! _(행복)_",
    "꿀꺽, {snack} 완전 취향저격!! _(들뜸)_",
    "{snack} 고마워!! 잘 마셨습니다!! _(감사)_",
    "오늘도 {snack} 덕분에 행복해!! _(만족)_",
)
# 슬롯이 디저트(간식 전용)/드링크(음료·포션 전용) 중 하나로 확정된 상태에서 그
# 카테고리가 아닌 품목을 급여하려 하면 이 문구로 거절한다(2026-09-12, §24 — 재고
# 자체는 있으니 _NO_SNACK_LINES와는 다른 상황).
_WRONG_SLOT_KIND_LINES = (
    "어라, 그건 지금 못 먹을 것 같아!! _(갸웃)_",
    "그건 지금 시간이랑 안 맞아!! _(고개 저음)_",
    "지금은 그런 거 먹을 때가 아닌 것 같아!! _(미안)_",
    "그건 다음 기회에!! 지금은 다른 게 필요해!! _(설명)_",
    "어?? 그건 지금 안 어울려!! _(갸웃)_",
    "지금 이 시간엔 그건 좀...!! _(난감)_",
    "그건 다른 타이밍에 줘야 할 것 같아!! _(단호)_",
    "지금은 그거 말고 다른 게 당겨!! _(고집)_",
    "어라, 지금 그건 못 먹겠어!! _(당황)_",
    "그건 지금 시간이랑 안 맞는 것 같아!! _(갸웃)_",
    "지금은 그거 받기엔 좀 그래!! _(미안)_",
    "그건 다음에 다시 줘볼래?? _(부탁)_",
    "지금 이 타이밍엔 안 맞아!! _(설명)_",
    "어?? 지금은 그거 아닌 것 같은데?? _(갸웃)_",
    "그건 지금 말고 나중에!! _(단호)_",
    "지금은 다른 게 필요한 시간이야!! _(고개 저음)_",
    "그거 말고 지금 맞는 걸로 줘볼래?? _(제안)_",
    "어라, 타이밍이 안 맞는 것 같아!! _(당황)_",
    "지금은 그걸 받을 때가 아니야!! _(미안)_",
    "그건 다음 시간에!! _(끄덕)_",
)

# 포션 3종의 급여 반응 문구(2026-09-13) — 원래 각각 하드코딩된 문장 1개씩이었는데,
# 다른 모든 반응 문구(_FEED_SUCCESS_LINES 등)와 달리 유일하게 고정 메시지였다.
# 다른 풀들과 동일하게 20개씩으로 맞췄다 — 효과 설명(호감도 변화 없음/즉시 효과
# 없음 등 부가 시스템 설명)은 넣지 않고 그 순간의 반응만 담는다.
_TREADMILL_ENERGY_DRINK_LINES = (
    "{item} 원샷!! 오늘 밤은 잠이 안 올 것 같아!! _(신남)_",
    "우와, {item} 마시니까 완전 힘이 나!! _(들뜸)_",
    "{item} 짜릿해!! 밤새 쳇바퀴 돌릴 기세야!! _(흥분)_",
    "{item} 마시니까 잠이 확 깨!! _(신남)_",
    "{item} 최고!! 왠지 오늘 밤은 길어질 것 같아!! _(들뜸)_",
    "짜릿짜릿, {item} 덕분에 기운이 넘쳐!! _(흥분)_",
    "{item} 마시고 나니까 눈이 말똥말똥해!! _(신남)_",
    "{item} 덕분에 오늘 밤 왠지 쌩쌩할 것 같아!! _(들뜸)_",
    "{item} 한 모금에 잠이 싹 달아났어!! _(놀람)_",
    "{item}의 에너지, 오늘 밤새 갈 수 있을 것 같아!! _(자신감)_",
    "{item} 마시니까 완전 쌩쌩해졌어!! _(신남)_",
    "{item} 덕분에 오늘은 평소보다 늦게까지 놀아야겠다!! _(들뜸)_",
    "{item} 정말 시원하고 짜릿해!! _(흥분)_",
    "{item} 마시니까 졸릴 틈이 없어!! _(신남)_",
    "{item} 최고!! 밤이 더 길게 느껴져!! _(들뜸)_",
    "짜릿한 {item}, 오늘 밤은 특별할 것 같아!! _(신남)_",
    "{item} 마시니까 눈이 번쩍 뜨여!! _(놀람)_",
    "{item}이 준 이 힘, 오늘 밤 늦게까지 갈 수 있을 것 같아!! _(자신감)_",
    "{item} 원기 충전 완료!! _(들뜸)_",
    "{item} 짜릿해!! 오늘 밤은 잠이 안 오겠다!! _(흥분)_",
)
_MEMORY_ADE_LINES = (
    "{item} 한 모금... 왠지 아련한 기분이 들어!! _(몽글)_",
    "{item} 마시니까 옛날 생각이 스쳐가!! _(아련)_",
    "이상하게 {item} 마시니 마음이 몽글몽글해져!! _(몽글)_",
    "{item}... 뭔가 그리운 느낌이야!! _(아련)_",
    "{item} 한 모금에 추억이 스쳐지나가!! _(몽글)_",
    "왠지 {item} 마시니까 코끝이 찡해!! _(아련)_",
    "{item}... 알 수 없는 그리움이 몰려와!! _(몽글)_",
    "{item} 마시니까 마음 한켠이 따뜻해져!! _(몽글)_",
    "{item} 마시니 뭔가 떠오를 듯 말 듯해!! _(아련)_",
    "{item}... 옛 기억이 아른거리는 것 같아!! _(몽글)_",
    "왠지 모르게 {item} 마시니 눈시울이 뜨거워져!! _(아련)_",
    "{item} 한 모금, 마음이 살짝 몽글해졌어!! _(몽글)_",
    "이상하게 {item} 마시니까 옛 생각이 나!! _(아련)_",
    "{item}... 뭔가 소중한 걸 떠올린 기분이야!! _(몽글)_",
    "{item} 마시니까 마음이 몽글몽글, 따뜻해져!! _(몽글)_",
    "왠지 {item} 마시고 나니 그리운 마음이 들어!! _(아련)_",
    "{item}... 알 수 없이 아련한 기분이 스쳐가!! _(몽글)_",
    "{item} 마시니까 마음이 몽글몽글해진 것 같아!! _(몽글)_",
    "{item} 한 모금에 뭔가 뭉클해져!! _(아련)_",
    "{item}... 왠지 마음이 몽글몽글, 아련해져!! _(몽글)_",
)
_H_POTION_LINES = (
    "{item}... 왠지 모르게 마음이 몽글몽글해져!! _(수줍)_",
    "{item} 마시니까 기분이 몽글몽글, 포근해져!! _(수줍)_",
    "이상하게 {item} 마시고 나니 마음이 따뜻해져!! _(수줍)_",
    "{item}... 왠지 모든 게 다 좋게 느껴져!! _(몽글)_",
    "{item} 한 모금에 마음이 사르르 녹아!! _(수줍)_",
    "왠지 {item} 마시니까 세상이 다정하게 느껴져!! _(몽글)_",
    "{item}... 마음이 몽글몽글, 사랑스러워져!! _(수줍)_",
    "{item} 마시니까 왠지 다 이해가 되는 기분이야!! _(몽글)_",
    "{item} 마시고 나니 마음이 몽글몽글 편안해져!! _(수줍)_",
    "{item}... 왠지 모르게 포근한 기분이 들어!! _(몽글)_",
    "이상하게 {item} 마시니까 마음이 몽글해졌어!! _(수줍)_",
    "{item} 한 모금에 왠지 다 사랑스러워 보여!! _(몽글)_",
    "{item}... 마음 한켠이 몽글몽글 따뜻해져!! _(수줍)_",
    "왠지 {item} 마시니까 기분이 몽글몽글해!! _(몽글)_",
    "{item} 마시고 나니 세상이 다 예뻐 보여!! _(수줍)_",
    "{item}... 왠지 모르게 마음이 사르르 녹아내려!! _(몽글)_",
    "{item} 마시니까 마음이 몽글몽글, 몽실몽실해져!! _(수줍)_",
    "{item} 한 모금에 왠지 다 괜찮게 느껴져!! _(몽글)_",
    "{item}... 마음이 몽글몽글, 왠지 다정해져!! _(수줍)_",
    "{item} 마시니까 왠지 모두가 좋아 보여!! _(몽글)_",
)


async def _handle_potion(
    user_id: int, item: BlackMarketItem, *, guild_id: int | None = None
) -> tuple[str, int, int, bool]:
    """암시장 "포션"(§24, 2026-09-12 신규) 전용 — 기존 확률형 괴식(good_delta/
    bad_delta/double_or_halve)과 완전히 다른 형태로, 확률 없이 품목별로 고정된 특수
    효과를 부여한다. 3종뿐이라 item.id로 직접 분기한다(악마의 씨앗을 double_or_halve
    플래그로 특수 처리하는 것과 동일한 원칙 — 품목 수가 적을 때는 개별 분기가
    카탈로그에 필드를 늘리는 것보다 명확하다). (텍스트, 호감도 델타, 갱신 후 호감도,
    multiplier_eligible) 4-튜플을 반환해 handle()의 공용 후처리(알림 문구 등)와
    형태를 맞춘다."""
    if item.id == "treadmill_energy_drink":
        result = await add_affection(user_id, 2, _METHOD, guild_id=guild_id)
        # "그날 취침 30분 지연"의 그날은 이 슬롯이 있는 날의 자정(=다음 날 00:00)을
        # 가리킨다 — events/scheduler.py::is_sleep_time()이 00:00~00:30 구간을 체크할
        # 시점엔 current_dt.date()가 이미 다음 날이기 때문(mark_late_sleep 독스트링 참고).
        tomorrow = datetime.now(timezone.utc).astimezone(KST).date() + timedelta(days=1)
        await claim_sleep_delay(tomorrow.isoformat())
        mark_late_sleep(for_date=tomorrow)
        text = random.choice(_TREADMILL_ENERGY_DRINK_LINES).format(item=item.name)
        return text, result["applied_amount"], result["new_affection"], True

    if item.id == "memory_ade":
        # 호감도 변화 없음, 대신 경험치 1~100 랜덤(우캡드, 하루 상한 없음 — 업적/이벤트
        # xp와 동일한 원칙). 이 품목만 예외적으로 획득 경험치를 알린다(format_xp_notice
        # 독스트링 참고 — 다른 곳에서는 XP 획득을 절대 안 보여준다).
        xp_gain = random.randint(1, 100)
        _, _, new_total_xp = await apply_xp_and_check_levelup(
            user_id, xp_gain, guild_id=guild_id, return_totals=True
        )
        current_user = await get_user(user_id)
        current_affection = current_user["affection"] if current_user is not None else 0
        text = random.choice(_MEMORY_ADE_LINES).format(item=item.name) + format_xp_notice(xp_gain, new_total_xp)
        return text, 0, current_affection, False

    # h_potion — 즉시 호감도 변화 없음, 대신 24시간짜리 보호막(획득 x2 + 하락 차단)을
    # 건다. 실제 배율/차단 로직은 add_affection/add_affection_uncapped RPC 내부에서
    # affection_shield_until을 직접 읽어 처리한다(db/affection.py 참고) — 여기서는
    # 만료 시각만 세팅하면 된다.
    await set_affection_shield_until(user_id, datetime.now(timezone.utc) + timedelta(hours=24))
    current_user = await get_user(user_id)
    current_affection = current_user["affection"] if current_user is not None else 0
    text = random.choice(_H_POTION_LINES).format(item=item.name)
    return text, 0, current_affection, False


async def handle(user_id: int, snack_name: str, *, guild_id: int | None = None) -> str:
    slot = current_slot()
    if slot is None:
        return random.choice(_NOT_DESSERT_TIME_LINES)
    # 이 슬롯이 오늘 "dessert"(간식)/"drink"(음료·포션) 중 무엇으로 확정됐는지 —
    # 확정 안 됐으면 지금 50/50으로 굴려서 확정한다(2026-09-12, §24). 오픈 방송이
    # 이미 먼저 확정해뒀을 확률이 높지만(cron이 슬롯 시작 시각에 발동), 멱등이라
    # 순서가 어떻든 항상 같은 결과로 수렴한다.
    slot_kind = await get_or_roll_slot_kind(slot)

    stats = await ensure_daily_stats(user_id)
    fed_today = dict(stats.get("dessert_fed_today") or {})
    if slot in fed_today:
        return random.choice(_ALREADY_FED_LINES)

    item = find_by_name(snack_name)
    if item is None:
        return random.choice(_NO_SNACK_LINES)
    # 슬롯 배타적 급여(2026-09-12 사용자 확정) — 디저트 타임엔 간식만, 드링킹
    # 타임엔 음료/포션만 급여 가능(교차 급여 차단).
    if slot_kind == "dessert" and item.kind != "snack":
        return random.choice(_WRONG_SLOT_KIND_LINES)
    if slot_kind == "drink" and item.kind not in ("beverage", "potion"):
        return random.choice(_WRONG_SLOT_KIND_LINES)
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
    # 있어 하루 최대 +30, 추가 상한 불필요). 드링킹 타임 급여도 동일하게 적용된다.
    await apply_xp_and_check_levelup(user_id, 10, guild_id=guild_id)

    if isinstance(item, BlackMarketItem) and item.kind == "potion":
        text, total_delta, current_affection, multiplier_eligible = await _handle_potion(
            user_id, item, guild_id=guild_id
        )
    elif isinstance(item, BlackMarketItem):
        # 암시장 확률적 간식(괴식) — item.good_chance로 결과를 굴린다(2026-09-09 신규,
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
        total_delta = result["applied_amount"]
        current_affection = result["new_affection"]
    else:
        result = await add_affection(user_id, item.effect, _METHOD, guild_id=guild_id)
        success_lines = _FEED_SUCCESS_LINES if item.kind == "snack" else _DRINK_SUCCESS_LINES
        text = random.choice(success_lines).format(snack=item.name)
        multiplier_eligible = True
        total_delta = result["applied_amount"]
        current_affection = result["new_affection"]

    # 2026-09-10부로 업적 달성 알림은 award() 내부에서 별도 글로벌 방송으로 처리된다
    # (호감도 보너스도 폐지) — 여기서는 조건이 맞을 때 부여만 시도하고 인라인 문구는
    # 더 이상 안 붙인다.
    if len({dessert_snack_id(v) for v in fed_today.values()}) == 3:
        await award_achievement(user_id, achievements.three_meals_a_day.ID, guild_id=guild_id)

    if item.id in _STRONGEST_SNACK_ITEM_IDS:
        await award_achievement(user_id, achievements.strongest_snack_ever.ID, guild_id=guild_id)

    if total_delta != 0:
        text += format_affection_notice(total_delta, current_affection, multiplier_eligible=multiplier_eligible)
    return text
