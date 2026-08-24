from datetime import datetime, timezone

from db.client import insert, rpc, select, update


async def schedule(scheduled_at: datetime, prompt_text: str) -> dict:
    rows = await insert(
        "global_call_events",
        {"scheduled_at": scheduled_at.isoformat(), "prompt_text": prompt_text},
    )
    return rows[0]


async def get_due_unposted() -> list[dict]:
    now_iso = datetime.now(timezone.utc).isoformat()
    return await select(
        "global_call_events",
        {
            "posted_at": "is.null",
            "scheduled_at": f"lte.{now_iso}",
            "select": "*",
        },
    )


async def mark_posted(event_id: int, posted_at: datetime, expires_at: datetime, messages: dict) -> None:
    await update(
        "global_call_events",
        {"id": f"eq.{event_id}"},
        {
            "posted_at": posted_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "messages": messages,
        },
    )


async def get_active_events() -> list[dict]:
    """게시 완료됐고, 아직 클레임 안 됐고, 만료되지 않은 이벤트들."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return await select(
        "global_call_events",
        {
            "posted_at": "not.is.null",
            "claimed_by": "is.null",
            "expires_at": f"gt.{now_iso}",
            "select": "*",
        },
    )


async def get_expired_unpenalized() -> list[dict]:
    now_iso = datetime.now(timezone.utc).isoformat()
    return await select(
        "global_call_events",
        {
            "posted_at": "not.is.null",
            "claimed_by": "is.null",
            "expires_at": f"lte.{now_iso}",
            "penalty_applied": "eq.false",
            "select": "*",
        },
    )


async def mark_penalty_applied(event_id: int) -> None:
    await update("global_call_events", {"id": f"eq.{event_id}"}, {"penalty_applied": True})


async def claim(event_id: int, user_id: int, reward: int) -> bool:
    return await rpc(
        "claim_call_event",
        {"p_event_id": event_id, "p_user_id": user_id, "p_reward": reward},
    )
