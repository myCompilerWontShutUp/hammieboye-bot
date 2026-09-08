from datetime import datetime, timedelta, timezone

from db.client import delete, insert, select

# db/history.py::RETENTION_DAYS와 동일한 이유(2026-09-08) — 관리자 콘솔의 "주인님
# 가라사대" 자연어 대화도 원문을 저장하고, users와 FK로 연결돼 있지 않아 /탈퇴로도
# 안 지워지므로 별도의 시간 기준 삭제가 반드시 필요했다.
RETENTION_DAYS = 30


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


async def purge_old() -> None:
    """`RETENTION_DAYS`보다 오래된 admin_chat_history 행을 삭제한다 — 매일 한 번
    스케줄러(core/dispatcher.py)가 호출한다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    await delete("admin_chat_history", {"created_at": f"lt.{cutoff.isoformat()}"})
