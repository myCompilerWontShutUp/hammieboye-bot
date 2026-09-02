from datetime import datetime, timezone

from db.client import delete, upsert


async def save_session(user_id: int, channel_id: int, expires_at: datetime) -> None:
    """관리자 콘솔 세션 상태를 DB에 거울(mirror)로 남긴다. 실제 만료 판정은
    admin/console.py의 인메모리 타이머(asyncio.Task)가 하고, 이 테이블은 조회/감사용이다."""
    await upsert(
        "admin_sessions",
        {
            "user_id": user_id,
            "channel_id": channel_id,
            "expires_at": expires_at.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="user_id",
    )


async def clear_session(user_id: int) -> None:
    await delete("admin_sessions", {"user_id": f"eq.{user_id}"})
