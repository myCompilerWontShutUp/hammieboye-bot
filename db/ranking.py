from db.client import select

_CANDIDATE_POOL = 20


async def get_top_candidates() -> list[dict]:
    """호감도 상위 후보(여유 있게 넉넉히)를 가져온다. 동점 처리는 애플리케이션에서 한다."""
    return await select(
        "users",
        {
            "select": "user_id,affection,created_at",
            "order": "affection.desc",
            "limit": str(_CANDIDATE_POOL),
        },
    )


async def get_last_increase_time(user_id: int, current_affection: int) -> str | None:
    """현재 호감도 값에 '증가로' 도달한 가장 최근 시각. 감소로 도달했다면 None."""
    rows = await select(
        "affection_log",
        {
            "user_id": f"eq.{user_id}",
            "new_value": f"eq.{current_affection}",
            "delta": "gt.0",
            "select": "created_at",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    return rows[0]["created_at"] if rows else None
