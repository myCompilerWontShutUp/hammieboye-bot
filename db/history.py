from datetime import datetime, timezone

from db.client import insert, select


async def log(user_id: int, guild_id: int, content: str) -> None:
    await insert(
        "chat_history",
        {"user_id": user_id, "guild_id": guild_id, "content": content},
    )


async def get_recent(user_id: int, since: datetime) -> list[dict]:
    """최근 히스토리를 최신순으로 반환한다 (CLAUDE.md 1-2: 30분/최대 50개 범위 내에서 호출할 것)."""
    rows = await select(
        "chat_history",
        {
            "user_id": f"eq.{user_id}",
            "created_at": f"gte.{since.astimezone(timezone.utc).isoformat()}",
            "select": "content,created_at",
            "order": "created_at.desc",
            "limit": "50",
        },
    )
    return rows
