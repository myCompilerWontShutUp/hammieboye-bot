from datetime import datetime

from db.client import delete, rpc, select, update, upsert


async def ensure_user(user_id: int) -> dict:
    """유저 레코드가 없으면 만들고, 있으면 그대로 반환한다(멱등, upsert).

    2026-09-08부로 별도 동의(/가입) 절차가 폐지되어, 이제 상호작용마다(자연어 호출
    단어/슬래시 커맨드 전부) `core/onboarding.py::provision()`을 통해 매번 호출된다
    — 기존 유저는 그대로 조회만 되고, 처음 보는 유저만 이 순간 실제로 행이
    생긴다(§1-1 "최소 식별 기록" 원칙, 이제는 명시적 동의 없이도 적용).
    """
    rows = await upsert("users", {"user_id": user_id}, on_conflict="user_id")
    return rows[0]


async def get_user(user_id: int) -> dict | None:
    rows = await select("users", {"user_id": f"eq.{user_id}", "select": "*"})
    return rows[0] if rows else None


async def increment_chat_count(user_id: int) -> int:
    return await rpc("increment_chat_count", {"p_user_id": user_id})


async def increment_help_count(user_id: int) -> int:
    return await rpc("increment_help_count", {"p_user_id": user_id})


async def increment_snacks_given(user_id: int) -> int:
    return await rpc("increment_snacks_given", {"p_user_id": user_id})


async def set_plastic_cooldown(user_id: int, until: datetime) -> dict:
    rows = await update(
        "users",
        {"user_id": f"eq.{user_id}"},
        {"plastic_cooldown_until": until.isoformat()},
    )
    return rows[0]


async def set_coin_cooldown(user_id: int, until: datetime) -> dict:
    rows = await update(
        "users",
        {"user_id": f"eq.{user_id}"},
        {"coin_cooldown_until": until.isoformat()},
    )
    return rows[0]


async def claim_coin_cooldown(user_id: int, until: datetime) -> bool:
    """쿨타임이 지금 끝나 있을 때(NULL 또는 만료)만 원자적으로 새 쿨타임을 설정한다
    (/동전의 쿨타임 확인+설정 TOCTOU를 조건부 UPDATE로 보장). 실패(False)면 아직 쿨타임
    중인 것 — 호출부가 최신 coin_cooldown_until을 다시 조회해 남은 시간을 계산해야 한다."""
    return await rpc("claim_coin_cooldown", {"p_user_id": user_id, "p_until": until.isoformat()})


async def get_created_at_map(user_ids: list[int]) -> dict[int, str]:
    """주어진 user_id들의 최초 상호작용 시각(created_at)을 일괄 조회한다 — 디저트 타임 랭킹처럼
    여러 후보를 한 번에 타이브레이크해야 할 때 유저 수만큼 왕복하지 않기 위함
    (db/ranking.py가 이미 쓰는 PostgREST "in.()" 필터와 동일한 idiom)."""
    if not user_ids:
        return {}
    rows = await select(
        "users",
        {
            "user_id": f"in.({','.join(str(uid) for uid in user_ids)})",
            "select": "user_id,created_at",
        },
    )
    return {row["user_id"]: row["created_at"] for row in rows}


async def delete_user(user_id: int) -> None:
    """탈퇴(/탈퇴) 시 유저 행을 완전히 삭제한다. daily_stats/chat_history/affection_log는
    CASCADE로 같이 삭제된다."""
    await delete("users", {"user_id": f"eq.{user_id}"})
