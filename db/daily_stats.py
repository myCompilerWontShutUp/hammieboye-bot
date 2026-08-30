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


# 자연어 대화 일일 상한(신규): 호감도x2, 최솟값 20(음수 호감도 사용자도 동일 적용), 최대 500
_NL_CAP_MULTIPLIER = 2
_NL_CAP_MIN = 20
_NL_CAP_MAX = 500


async def ensure_nl_cap(user_id: int, affection: int) -> dict:
    """오늘의 nl_cap을 확보한다. 정규적으로는 매일 06:30에 refresh_conversation_caps()가
    전체 유저 일괄로 동결하지만, 그 시점 이후 새로 등록된 유저 등 아직 값이 없는 경우엔
    지금 시점 호감도로 즉석 계산해서 그 값을 그대로 저장(동결)한다. 한 번 저장되면 그 값을
    다시 재계산하지 않고 그대로 쓴다 (당일 06:30 값 고정 원칙)."""
    stats = await ensure_daily_stats(user_id)
    if stats["nl_cap"] is not None:
        return stats
    cap = min(max(affection * _NL_CAP_MULTIPLIER, _NL_CAP_MIN), _NL_CAP_MAX)
    return await update_daily_stats(user_id, {"nl_cap": cap})


async def refresh_conversation_caps() -> None:
    """매일 06:30(기상 시각)에 등록된 모든 유저의 nl_cap을 그 순간 호감도로 동결하고
    nl_count/over_cap_attempts를 리셋한다."""
    await rpc("refresh_daily_conversation_caps", {})


async def get_top_talkers_for(date_str: str) -> list[dict]:
    """지정한 날짜(KST)에 당일 순증감이 음수가 아닌 사용자들을, 대화 횟수 내림차순으로
    반환한다. messages_today_reached_at은 그날 마지막으로 messages_today가 갱신된
    시각(=오늘의 최종 횟수에 도달한 시각)이라, 동점자 중 "먼저 그 횟수를 채운 사람"을
    가리는 타이브레이크에 그대로 쓸 수 있다."""
    return await select(
        "daily_stats",
        {
            "stat_date": f"eq.{date_str}",
            "daily_net": "gte.0",
            "select": "user_id,messages_today,messages_today_reached_at",
            "order": "messages_today.desc",
        },
    )


async def get_top_talkers_today() -> list[dict]:
    return await get_top_talkers_for(kst_today_str())


async def get_active_users_for(date_str: str) -> list[int]:
    """그날(KST) 자연어 또는 공개 슬래시 커맨드로 최소 한 번이라도 활동한 사용자 id 목록
    (daily_net 필터 없음 — 당일 순증감과 무관하게 활동 자체만 기준). 취침 전 대화왕 보상
    (3-5, "그날 대화한 사용자 전원 +1")에 쓴다.

    기존엔 `chat_history`(자연어만)를 기준으로 삼아서, 자연어 없이 공개 슬래시 커맨드
    (`/페트병`·`/내정보`·`/내업적`·`/랭킹`·`/니정보`·`/니업적`)만 쓴 유저가 보상에서
    누락되는 버그가 있었다(사용자 발견·확정, 2026-08-30). `daily_stats.messages_today`는
    자연어(dispatcher.py)와 공개 슬래시 커맨드(core/slash_commands.py의 `_prepare()`)
    양쪽에서 함께 증가하므로 이걸 기준으로 삼으면 자동으로 포함된다 — ephemeral 전용
    커맨드(`/가입`·`/가입-수집항목`·`/탈퇴`)는 `_prepare()`를 거치지 않아 애초에
    `messages_today`를 안 건드리므로 자연히 제외된다(사용자 확정 사항과 일치).
    """
    rows = await select(
        "daily_stats",
        {"stat_date": f"eq.{date_str}", "messages_today": "gt.0", "select": "user_id"},
    )
    return [row["user_id"] for row in rows]
