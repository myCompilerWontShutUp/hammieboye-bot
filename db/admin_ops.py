import aiohttp

from db.client import delete, insert, select, upsert


async def seed_prime(user_id: int) -> None:
    """부팅 시 최초 명령어 제공자(prime) 행이 항상 있도록 보장한다 (idempotent)."""
    await upsert("admin_ops", {"user_id": user_id, "prime": True}, on_conflict="user_id")


async def has_op(user_id: int) -> bool:
    rows = await select("admin_ops", {"user_id": f"eq.{user_id}", "select": "user_id"})
    return bool(rows)


async def grant(user_id: int) -> bool:
    """이미 권한이 있으면 아무 것도 안 하고 False. 신규면 기록하고 True.

    db/achievements.py::award()와 동일한 이유로 has_op() 확인과 insert() 사이의 레이스를
    409로 방어한다(op grant는 사용 빈도가 낮아 실제로 겹칠 일은 거의 없지만, 같은 부류의
    사고를 원천적으로 막기 위해 동일한 방어 패턴을 그대로 재사용했다).
    """
    if await has_op(user_id):
        return False
    try:
        await insert("admin_ops", {"user_id": user_id, "prime": False})
    except aiohttp.ClientResponseError as e:
        if e.status == 409:
            return False
        raise
    return True


async def revoke(user_id: int) -> bool:
    """권한이 있었으면 지우고 True, 애초에 없었으면 아무 것도 안 하고 False."""
    if not await has_op(user_id):
        return False
    await delete("admin_ops", {"user_id": f"eq.{user_id}"})
    return True


async def list_all() -> list[dict]:
    """prime이 먼저, 그다음 부여된 순으로 반환한다."""
    return await select(
        "admin_ops",
        {"select": "*", "order": "prime.desc,granted_at.asc"},
    )
