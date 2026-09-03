import aiohttp

import achievements
from db.client import delete, insert, select


async def has_earned(user_id: int, achievement_id: str) -> bool:
    rows = await select(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "achievement_id": f"eq.{achievement_id}", "select": "user_id"},
    )
    return bool(rows)


_LEGENDARY_BONUS = 10
_NORMAL_BONUS = 3


async def award(user_id: int, achievement_id: str) -> dict:
    """업적을 부여하고, 새로 획득한 경우 보너스 호감도(일반 +3/전설 +10)까지 함께 지급한다.

    반환값은 {earned, applied_amount, new_affection} — earned=False면 이미 가지고
    있던 상태(다른 필드는 0/None). has_earned() 확인과 insert()는 원자적이지 않다 —
    거의 동시 요청 두 개가 둘 다 has_earned()==False로 통과하면 insert() 하나만
    성공하고 나머지는 PK 충돌로 409를 받을 수 있다. 이걸 못 잡으면 예외가 새서 메시지
    처리 전체가 죽으므로, 409는 "누군가 먼저 기록함"과 동일하게 취급해 조용히 처리한다.

    보너스 호감도는 상한도, 주말/기념일/생일 배율도 적용받지 않고 항상 전액 지급된다
    (add_affection_uncapped의 apply_day_multiplier=False). check_achievements=False라
    이 보너스 자체가 호감도 마일스톤 업적(햄미 러브 유 등)을 다시 트리거하지는 않는다 —
    아래 순환 임포트 참고.
    """
    if await has_earned(user_id, achievement_id):
        return {"earned": False, "applied_amount": 0, "new_affection": None}
    try:
        await insert("user_achievements", {"user_id": user_id, "achievement_id": achievement_id})
    except aiohttp.ClientResponseError as e:
        if e.status == 409:
            return {"earned": False, "applied_amount": 0, "new_affection": None}
        raise

    # db/affection.py가 이미 이 모듈의 maybe_award_affection_milestones()를 최상단에서
    # import하므로, 여기서 db.affection을 최상단에서 import하면 순환된다 — 지역 import로 끊는다.
    from db.affection import add_affection_uncapped

    module = achievements.REGISTRY[achievement_id]
    bonus = _LEGENDARY_BONUS if module.RARITY == achievements.LEGENDARY else _NORMAL_BONUS
    result = await add_affection_uncapped(
        user_id, bonus, "achievement_bonus", check_achievements=False, apply_day_multiplier=False
    )
    return {"earned": True, "applied_amount": bonus, "new_affection": result["new_affection"]}


async def revoke(user_id: int, achievement_id: str) -> bool:
    """관리자 콘솔의 ac revoke 전용. 가지고 있던 업적이면 지우고 True, 애초에 없었으면
    아무 것도 안 하고 False(중복 처리 없이 조용히 성공으로 취급하지 않고, 호출부가
    "원래 안 가지고 있었다"는 걸 구분할 수 있게 한다)."""
    if not await has_earned(user_id, achievement_id):
        return False
    await delete(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "achievement_id": f"eq.{achievement_id}"},
    )
    return True


async def get_earned(user_id: int) -> list[dict]:
    """획득 순(오래된 순)으로 반환한다."""
    return await select(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "select": "achievement_id,earned_at", "order": "earned_at.asc"},
    )


# 실제 상승분이 있으면 "햄미 러브 유"(최초 1회), 절대값 기준으로 "최고의 햄미 주인"(25
# 이상)/"아몬드 양보 가능"(250 이상, 전설). "이상"이라 계속 조건을 만족해도 award()가
# 중복을 막아준다.
_GREAT_OWNER_THRESHOLD = 25
_ALMOND_WORTHY_THRESHOLD = 250


async def maybe_award_affection_milestones(
    user_id: int, applied_amount: int, new_affection: int
) -> str | None:
    """호감도가 바뀔 때마다 확인해야 하는 업적들을 한곳에서 처리한다. add_affection/
    add_affection_uncapped 안에서 호출되는 공용 훅 — 호감도가 바뀌는 경로가 여러 군데라
    개별 호출부마다 챙기지 않도록 중앙화했다.

    la set/la reset(set_affection RPC)은 daily_stats/affection_log를 안 건드리는 별도
    경로라 이 훅을 거치지 않는다 — 그 경로로 마일스톤을 넘긴 경우는 ac grant가 우회로다.
    """
    notices: list[str] = []

    if applied_amount > 0:
        if (await award(user_id, achievements.hammie_love_you.ID))["earned"]:
            notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.hammie_love_you)}!!")

    if new_affection >= _GREAT_OWNER_THRESHOLD:
        if (await award(user_id, achievements.great_owner.ID))["earned"]:
            notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.great_owner)}!!")

    if new_affection >= _ALMOND_WORTHY_THRESHOLD:
        if (await award(user_id, achievements.almond_worthy.ID))["earned"]:
            notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.almond_worthy)}!!")

    return "\n".join(notices) if notices else None
