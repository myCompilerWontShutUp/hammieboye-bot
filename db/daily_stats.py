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


async def claim_dessert_slot(user_id: int, slot: str, snack_id: str) -> bool:
    """그 슬롯 키가 아직 없을 때만 원자적으로 기록한다(/사용의 슬롯당 1회 제한을
    조건부 UPDATE로 보장 — 오늘 daily_stats 행이 이미 있어야 하므로 ensure_daily_stats를
    먼저 호출해야 한다). 실패(False)면 그 슬롯은 이미 다른 요청이 먼저 채간 것.

    저장 형태는 {"id": snack_id, "at": 먹인 시각(timestamptz)} — 디저트 타임 종료 방송의
    상위 5명 랭킹(events/dessert_time.py)이 "같은 간식이면 먼저 먹인 사람 우선"을
    가리는 데 이 시각을 그대로 쓴다."""
    return await rpc(
        "claim_dessert_slot",
        {"p_user_id": user_id, "p_slot": slot, "p_snack_id": snack_id},
    )


def dessert_snack_id(entry) -> str:
    """dessert_fed_today[slot] 값에서 간식 id를 뽑는다 — 정상 형태는
    {"id": ..., "at": ...}이지만, 이 형태로 바뀌기 전(문자열만 저장하던 시절)에 이미
    기록된 당일 데이터도 그대로 호환한다."""
    return entry["id"] if isinstance(entry, dict) else entry


async def get_dessert_feeders_for(date_str: str, slot: str) -> list[dict]:
    """그 날짜(KST)에 해당 슬롯에서 간식을 먹인 사용자 전원을 {user_id, snack_id, fed_at}
    형태로 반환한다(디저트 타임 종료 방송 상위 5명 랭킹용, events/dessert_time.py 전용).
    fed_at은 마이그레이션 이전 데이터(문자열만 저장)면 None일 수 있다."""
    rows = await select(
        "daily_stats",
        {"stat_date": f"eq.{date_str}", "select": "user_id,dessert_fed_today"},
    )
    feeders = []
    for row in rows:
        entry = (row.get("dessert_fed_today") or {}).get(slot)
        if entry is None:
            continue
        feeders.append(
            {
                "user_id": row["user_id"],
                "snack_id": dessert_snack_id(entry),
                "fed_at": entry["at"] if isinstance(entry, dict) else None,
            }
        )
    return feeders


async def claim_coin_daily_use(user_id: int) -> bool:
    """/동전의 하루 사용 횟수를 원자적으로 제한한다(하루 최대 3회) — "오늘 아직 3회
    미만일 때만" 원자적으로 +1 한다(claim_dessert_slot과 동일한 idiom). 오늘
    daily_stats 행이 이미 있어야 하므로(ensure_daily_stats로 미리 보장) 실패(False)면
    오늘은 이미 3번 다 썼다는 뜻."""
    return await rpc("claim_coin_daily_use", {"p_user_id": user_id})


async def increment_messages_today(user_id: int) -> int:
    return await rpc("increment_messages_today", {"p_user_id": user_id})


async def increment_slash_count(user_id: int) -> int:
    """슬래시 명령어 사용 횟수(레벨/XP 시스템, 2026-09-10 신규) — messages_today와
    달리 자연어/슬래시를 합치지 않고 슬래시만 따로 센다."""
    return await rpc("increment_slash_count", {"p_user_id": user_id})


async def refresh_conversation_caps() -> None:
    """매일 06:30(기상 시각)에 등록된 모든 유저의 nl_count/over_cap_attempts를
    리셋한다. 2026-09-10부로 자연어 일일 횟수 상한(舊 nl_cap, 호감도 기반 공식)은
    레벨 시스템으로 교체됐다 — 더 이상 여기서 동결하지 않고, 호출부가 매 메시지마다
    levels.get_level_for_xp(user["total_xp"]).daily_nl_limit을 실시간 조회한다
    (레벨업 즉시 혜택 체감)."""
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
    (daily_net 필터 없음 — 당일 순증감과 무관하게 활동 자체만 기준). 취침 전 대화왕
    전원 보상에 쓴다. `messages_today`는 자연어와 공개 슬래시 커맨드 양쪽에서 증가하므로
    이 기준으로 자동 포함되고, ephemeral 전용 커맨드(/탈퇴·/봇정보-수집항목 등)는 이걸 안
    건드려 자연히 제외된다.
    """
    rows = await select(
        "daily_stats",
        {"stat_date": f"eq.{date_str}", "messages_today": "gt.0", "select": "user_id"},
    )
    return [row["user_id"] for row in rows]
