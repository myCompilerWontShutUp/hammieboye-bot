from datetime import datetime, timezone

from db.client import select, upsert


async def record_withdrawal(user_id: int) -> None:
    """탈퇴(/탈퇴) 시각을 기록한다 (24시간 재가입 금지 판정용 최소 기록)."""
    await upsert(
        "withdrawn_users",
        {"user_id": user_id, "withdrawn_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="user_id",
    )


async def get_withdrawal(user_id: int) -> dict | None:
    rows = await select("withdrawn_users", {"user_id": f"eq.{user_id}", "select": "*"})
    return rows[0] if rows else None
