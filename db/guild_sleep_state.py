from db.client import rpc


async def register_mention(guild_id: int) -> bool:
    """맨션 1회를 원자적으로 기록한다.

    밤이 바뀌었으면 서버 임계치를 새로 뽑아 리셋하고, 이번 호출로 막 임계치에
    도달해 깨움 이벤트가 발동했으면 True를 반환한다(그 서버는 이후 같은 밤
    동안 다시 발동하지 않는다). DB 함수(register_sleep_mention)가 행 잠금으로
    직렬화하므로, 같은 서버에서 서로 다른 유저가 동시에 맨션해도 안전하다.
    """
    rows = await rpc("register_sleep_mention", {"p_guild_id": guild_id})
    return rows[0]["just_triggered"]
