import aiohttp

import achievements
from db.client import delete, insert, select


async def has_earned(user_id: int, achievement_id: str) -> bool:
    rows = await select(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "achievement_id": f"eq.{achievement_id}", "select": "user_id"},
    )
    return bool(rows)


async def award(user_id: int, achievement_id: str) -> bool:
    """이미 획득한 업적이면 아무 것도 안 하고 False. 처음 획득이면 기록하고 True.

    has_earned() 확인과 insert()는 원자적이지 않아서, 같은 유저가 짧은 시간에 메시지를
    여러 개 보내 두 요청이 거의 동시에 같은 업적을 확인하면 둘 다 has_earned()==False로
    통과한 뒤 insert()가 하나만 성공하고 나머지는 PK(user_id, achievement_id) 충돌로
    409를 반환할 수 있다. 이 경우를 못 잡으면 예외가 그대로 새서 메시지 처리 전체가
    죽는다(호감도는 이미 반영됐는데 답장이 안 가는, 이전에 겪은 것과 같은 부류의 사고) —
    409는 "누군가 먼저 기록함"과 동일하게 취급해 조용히 False를 반환한다.
    """
    if await has_earned(user_id, achievement_id):
        return False
    try:
        await insert("user_achievements", {"user_id": user_id, "achievement_id": achievement_id})
    except aiohttp.ClientResponseError as e:
        if e.status == 409:
            return False
        raise
    return True


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


# 호감도 관련 업적 마일스톤 (사용자 확정, 2026-08-27): 실제 상승분이 있으면 "햄미 러브 유"(최초
# 1회), 도달한 절대값 기준으로 "최고의 햄미 주인"(25 이상)/"아몬드 양보 가능"(250 이상, 전설).
# 정확히 그 값이 아니라 "이상"이라 한 번 넘으면 그 뒤로도 계속 조건을 만족하지만 award()가
# 전체 기간 기준으로 중복을 막아준다.
_GREAT_OWNER_THRESHOLD = 25
_ALMOND_WORTHY_THRESHOLD = 250


async def maybe_award_affection_milestones(
    user_id: int, applied_amount: int, new_affection: int
) -> str | None:
    """호감도가 바뀔 때마다 확인해야 하는 업적들을 한곳에서 처리한다. add_affection/
    add_affection_uncapped 안에서 호출되는 공용 훅 — 호감도가 바뀌는 경로가 여러 군데
    (페트병, 감정, 부름 이벤트, 관리자 등)라 개별 호출부마다 챙기지 않도록 중앙화했다.

    주의: la set/la reset(관리자, set_affection RPC)은 이 훅을 거치지 않는다 — daily_stats/
    affection_log를 아예 안 건드리는 별도 경로라는 기존 설계 원칙을 그대로 유지하기 위해
    의도적으로 손대지 않았다(사용자 확정). 그 경로로 마일스톤을 넘긴 경우는 ac grant로
    수동 부여하는 것이 의도된 우회로다.
    """
    notices: list[str] = []

    if applied_amount > 0:
        if await award(user_id, achievements.hammie_love_you.ID):
            notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.hammie_love_you)}!!")

    if new_affection >= _GREAT_OWNER_THRESHOLD:
        if await award(user_id, achievements.great_owner.ID):
            notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.great_owner)}!!")

    if new_affection >= _ALMOND_WORTHY_THRESHOLD:
        if await award(user_id, achievements.almond_worthy.ID):
            notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.almond_worthy)}!!")

    return "\n".join(notices) if notices else None
