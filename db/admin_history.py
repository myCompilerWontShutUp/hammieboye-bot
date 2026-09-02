from datetime import datetime, timezone

from db.client import insert, select


async def log(user_id: int, content: str, role: str = "user") -> dict:
    rows = await insert(
        "admin_chat_history",
        {"user_id": user_id, "content": content, "role": role},
    )
    return rows[0]


async def get_recent_turns(user_id: int, since: datetime, limit: int = 5) -> list[dict]:
    """"주인님 가라사대" 자연어 전용 히스토리 — 일반 자연어(chat_history)와는 별개 저장소라
    섞이지 않는다. 형식/조회 방식은 db/history.py::get_recent_turns()와 동일(오래된 순)."""
    rows = await select(
        "admin_chat_history",
        {
            "user_id": f"eq.{user_id}",
            "created_at": f"gte.{since.astimezone(timezone.utc).isoformat()}",
            "select": "role,content,created_at",
            "order": "created_at.desc",
            "limit": str(limit),
        },
    )
    return list(reversed(rows))
