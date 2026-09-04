from datetime import datetime, timedelta, timezone

from db.client import insert, rpc, select

_KST_OFFSET = timedelta(hours=9)

KNOWN_TABLES = (
    "users",
    "daily_stats",
    "chat_history",
    "affection_log",
    "admin_command_log",
    "global_call_events",
    "guild_channels",
    "guild_sleep_state",
    "withdrawn_users",
    "user_achievements",
    "admin_ops",
    "admin_sessions",
    "admin_chat_history",
    "user_emoji_tags",
)

# 최근 등록순 정렬 기준 컬럼 (테이블마다 created_at이 없는 경우가 있어 따로 정의).
_ORDER_COLUMN = {
    "users": "created_at",
    "daily_stats": "created_at",
    "chat_history": "created_at",
    "affection_log": "created_at",
    "admin_command_log": "created_at",
    "global_call_events": "created_at",
    "guild_channels": "updated_at",
    "guild_sleep_state": "updated_at",
    "withdrawn_users": "withdrawn_at",
    "user_achievements": "earned_at",
    "admin_ops": "granted_at",
    "admin_sessions": "updated_at",
    "admin_chat_history": "created_at",
    "user_emoji_tags": "updated_at",
}


async def set_affection(user_id: int, value: int) -> int:
    """la set/la reset 전용: daily_stats/affection_log를 건드리지 않고 절대값으로 SET."""
    return await rpc("set_affection", {"p_user_id": user_id, "p_value": value})


async def log_command(command: str, args: str, before: str | None, after: str | None) -> None:
    """관리자 콘솔(주인님-가라사대)에서 상태를 바꾸는 명령어 실행 이력을 남긴다.
    조회 전용 명령어(s-*/c*)는 변경이 없으므로 호출하지 않는다."""
    await insert(
        "admin_command_log",
        {"command": command, "args": args, "before_value": before, "after_value": after},
    )


def _kst_day_bounds_utc() -> tuple[str, str]:
    now_kst = datetime.now(timezone.utc) + _KST_OFFSET
    start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    end_kst = start_kst + timedelta(days=1)
    start_utc = (start_kst - _KST_OFFSET).isoformat()
    end_utc = (end_kst - _KST_OFFSET).isoformat()
    return start_utc, end_utc


async def get_today_events() -> list[dict]:
    """오늘(KST) 등록된(또는 등록될) 헬프 미 이벤트를 예정 시각 순으로 반환한다."""
    start, end = _kst_day_bounds_utc()
    return await select(
        "global_call_events",
        {
            "and": f"(scheduled_at.gte.{start},scheduled_at.lt.{end})",
            "select": "*",
            "order": "scheduled_at.asc",
        },
    )


async def get_next_event() -> dict | None:
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = await select(
        "global_call_events",
        {
            "scheduled_at": f"gt.{now_iso}",
            "select": "*",
            "order": "scheduled_at.asc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def get_last_event() -> dict | None:
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = await select(
        "global_call_events",
        {
            "scheduled_at": f"lte.{now_iso}",
            "select": "*",
            "order": "scheduled_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def dump_table(name: str, amount: int | None) -> list[dict]:
    """amount가 None이면(sh db의 "*") limit 파라미터 자체를 생략해서 PostgREST 기본
    최대치까지 반환한다 — 응답이 길어지는 건 admin/console.py의 파일 첨부 폴백이 처리한다."""
    if name not in KNOWN_TABLES:
        raise ValueError(f"unknown table: {name}")
    order_column = _ORDER_COLUMN[name]
    params = {"select": "*", "order": f"{order_column}.desc"}
    if amount is not None:
        params["limit"] = str(amount)
    return await select(name, params)
