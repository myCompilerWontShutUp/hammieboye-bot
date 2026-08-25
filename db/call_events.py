from datetime import datetime, timezone

from db.client import delete, insert, rpc, select, update


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


async def get_nearest_before(scheduled_at: datetime) -> dict | None:
    """주어진 시각보다 앞서 예약된 이벤트 중 가장 가까운 것 (g-call-event 간격 검사용)."""
    rows = await select(
        "global_call_events",
        {
            "scheduled_at": f"lt.{scheduled_at.isoformat()}",
            "select": "*",
            "order": "scheduled_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def get_nearest_after(scheduled_at: datetime) -> dict | None:
    """주어진 시각보다 뒤에 예약된 이벤트 중 가장 가까운 것 (g-call-event 간격 검사용)."""
    rows = await select(
        "global_call_events",
        {
            "scheduled_at": f"gt.{scheduled_at.isoformat()}",
            "select": "*",
            "order": "scheduled_at.asc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def delete_event(event_id: int) -> None:
    await delete("global_call_events", {"id": f"eq.{event_id}"})


async def delete_unposted_after(now: datetime) -> list[dict]:
    """아직 게시되지 않은(진행 중이지 않은) 미래 예약 이벤트를 전부 삭제하고 삭제된 행을 반환한다."""
    return await delete(
        "global_call_events",
        {"scheduled_at": f"gt.{now.isoformat()}", "posted_at": "is.null"},
    )
