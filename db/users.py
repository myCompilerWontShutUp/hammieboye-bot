from datetime import datetime, timezone

from db.client import delete, rpc, select, update, upsert


async def ensure_user(user_id: int) -> dict:
    """유저 레코드가 없으면 만들고, 있으면 그대로 반환한다.

    동의 여부와 무관하게 최소 식별 레코드는 항상 있어야 최초 호출 여부를
    판별할 수 있다 (CLAUDE.md 1-1 참고, 별도 고지 불필요로 결정됨).
    """
    rows = await upsert("users", {"user_id": user_id}, on_conflict="user_id")
    return rows[0]


async def get_user(user_id: int) -> dict | None:
    rows = await select("users", {"user_id": f"eq.{user_id}", "select": "*"})
    return rows[0] if rows else None


async def set_consent(user_id: int) -> dict:
    rows = await update(
        "users",
        {"user_id": f"eq.{user_id}"},
        {"consent_given": True, "consent_at": datetime.now(timezone.utc).isoformat()},
    )
    return rows[0]


async def increment_chat_count(user_id: int) -> int:
    return await rpc("increment_chat_count", {"p_user_id": user_id})


async def increment_help_count(user_id: int) -> int:
    return await rpc("increment_help_count", {"p_user_id": user_id})


async def set_plastic_cooldown(user_id: int, until: datetime) -> dict:
    rows = await update(
        "users",
        {"user_id": f"eq.{user_id}"},
        {"plastic_cooldown_until": until.isoformat()},
    )
    return rows[0]


async def delete_user(user_id: int) -> None:
    """탈퇴(/탈퇴) 시 유저 행을 완전히 삭제한다. daily_stats/chat_history/affection_log는
    CASCADE로 같이 삭제된다."""
    await delete("users", {"user_id": f"eq.{user_id}"})
