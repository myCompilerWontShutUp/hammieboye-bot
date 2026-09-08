from datetime import datetime, timedelta, timezone

from db.client import delete, insert, rpc, select, update

# 디버깅 목적으로 헬프 미 이벤트(global_call_events) 기록을 30일치만 보관한다
# (2026-09-08 신규 — 이전엔 이 테이블에서 실제로 행을 지우는 코드가 전혀 없어
# 무기한 쌓였다, db/history.py::RETENTION_DAYS와 동일한 패턴).
RETENTION_DAYS = 30


async def schedule(scheduled_at: datetime, prompt_text: str) -> dict:
    rows = await insert(
        "global_call_events",
        {"scheduled_at": scheduled_at.isoformat(), "prompt_text": prompt_text},
    )
    return rows[0]


async def get_scheduled_between(start: datetime, end: datetime) -> list[dict]:
    """`[start, end)` 구간에 예약된 이벤트 전부(게시 여부 무관), 시각 순 — /사용
    햄미 일정표(2026-09-08 신규) 전용. PostgREST에서 같은 컬럼에 두 조건(gte+lt)을
    걸려면 쿼리 파라미터를 두 번 보내야 하는데 db/client.py::select()가 단순 dict라
    이를 못 표현해서, 하한만 서버에 걸고 상한은 여기서 파이썬으로 자른다(하루치라
    많아야 몇십 행 — 성능 문제 없음)."""
    rows = await select(
        "global_call_events",
        {"scheduled_at": f"gte.{start.isoformat()}", "select": "*", "order": "scheduled_at.asc"},
    )
    return [row for row in rows if datetime.fromisoformat(row["scheduled_at"]) < end]


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


async def get_recently_claimed(since: datetime) -> list[dict]:
    """`since` 이후에 클레임된 이벤트들(만료 여부 무관) — 클레임 후 1분 유예 기간 판정에 쓴다."""
    return await select(
        "global_call_events",
        {
            "claimed_by": "not.is.null",
            "claimed_at": f"gt.{since.isoformat()}",
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


async def get_claimed_by(event_id: int) -> int | None:
    """이 이벤트를 실제로 클레임한 user_id(없으면 None). claim()이 실패했을 때, 캐시가
    낡아서 실패한 건지 자기 자신이 이미 클레임한 상태인지 구분하려고 캐시를 우회해서
    직접 조회한다."""
    rows = await select("global_call_events", {"id": f"eq.{event_id}", "select": "claimed_by"})
    return rows[0]["claimed_by"] if rows else None


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


async def purge_old() -> None:
    """`RETENTION_DAYS`(30일)보다 오래된 `global_call_events` 행을 실제로 삭제한다 —
    매일 00:00(취침 시작, core/dispatcher.py)에 스케줄러가 호출한다. `created_at`
    기준이라 "30일치만 항상 남는" FIFO처럼 동작한다 — 매일 3~5개씩 꾸준히 쌓이는
    구조라, 이 컷오프가 지난 행은 곧 그만큼씩 밀려나며 지워진다.

    **현재 진행 중인 이벤트와 절대 안 겹친다**: 이벤트는 매일 기상 시각(06:30~07:00)에
    당일 07:30~22:30 사이로만 예약되고(§3-2), 게시 후 10분 안에 만료되거나 클레임돼
    끝난다 — 30일 전 컷오프에 걸릴 수 있는 행은 이미 오래전에 결판난 것뿐이라
    `get_active_events()`/`get_recently_claimed()`(클레임 유예 1분) 등 어떤 조회
    로직과도 충돌하지 않는다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    await delete("global_call_events", {"created_at": f"lt.{cutoff.isoformat()}"})
