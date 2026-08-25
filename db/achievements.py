from db.client import insert, select

_HAMMIE_LOVE_YOU_ID = "hammie_love_you"


async def has_earned(user_id: int, achievement_id: str) -> bool:
    rows = await select(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "achievement_id": f"eq.{achievement_id}", "select": "user_id"},
    )
    return bool(rows)


async def award(user_id: int, achievement_id: str) -> bool:
    """이미 획득한 업적이면 아무 것도 안 하고 False. 처음 획득이면 기록하고 True."""
    if await has_earned(user_id, achievement_id):
        return False
    await insert("user_achievements", {"user_id": user_id, "achievement_id": achievement_id})
    return True


async def get_earned(user_id: int) -> list[dict]:
    """획득 순(오래된 순)으로 반환한다."""
    return await select(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "select": "achievement_id,earned_at", "order": "earned_at.asc"},
    )


async def maybe_award_first_affection_rise(user_id: int, applied_amount: int) -> str | None:
    """호감도가 실제로 오른 경우(applied_amount > 0)에만 "햄미 러브 유" 업적을 확인한다.
    add_affection/add_affection_uncapped 안에서 호출되는 공용 훅 — 호감도가 오르는 경로가
    여러 군데(페트병, 감정, 부름 이벤트, 관리자 등)라 개별 호출부마다 챙기지 않도록 중앙화했다.
    """
    if applied_amount <= 0:
        return None
    newly_earned = await award(user_id, _HAMMIE_LOVE_YOU_ID)
    if not newly_earned:
        return None
    from achievements import hammie_love_you  # 지연 import로 순환 참조 방지

    return f"🏆 업적 달성: {hammie_love_you.NAME}!!"
