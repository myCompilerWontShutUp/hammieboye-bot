from datetime import datetime, timedelta, timezone

from db.client import rpc, select, update, upsert

# Supabase의 kst_today() SQL 함수와 동일한 기준(KST = UTC+9, 서머타임 없음).
_KST_OFFSET = timedelta(hours=9)


def kst_today_str() -> str:
    return (datetime.now(timezone.utc) + _KST_OFFSET).date().isoformat()


async def ensure_daily_stats(user_id: int) -> dict:
    """오늘(KST) 구간 데이터 행이 없으면 만들고, 있으면 그대로 반환한다."""
    today = kst_today_str()
    rows = await select(
        "daily_stats",
        {"user_id": f"eq.{user_id}", "stat_date": f"eq.{today}", "select": "*"},
    )
    if rows:
        return rows[0]

    rows = await upsert(
        "daily_stats",
        {"user_id": user_id, "stat_date": today},
        on_conflict="user_id,stat_date",
    )
    return rows[0]


async def update_daily_stats(user_id: int, data: dict) -> dict:
    today = kst_today_str()
    rows = await update(
        "daily_stats",
        {"user_id": f"eq.{user_id}", "stat_date": f"eq.{today}"},
        data,
    )
    return rows[0]


async def increment_messages_today(user_id: int) -> int:
    return await rpc("increment_messages_today", {"p_user_id": user_id})


async def get_top_talkers_today() -> list[dict]:
    """오늘(KST) 당일 순증감이 음수가 아닌 사용자들을, 대화 횟수 내림차순으로 반환한다."""
    today = kst_today_str()
    return await select(
        "daily_stats",
        {
            "stat_date": f"eq.{today}",
            "daily_net": "gte.0",
            "select": "user_id,messages_today",
            "order": "messages_today.desc",
        },
    )
