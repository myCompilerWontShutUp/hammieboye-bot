from db.client import select, upsert


async def set_last_channel(guild_id: int, channel_id: int) -> None:
    await upsert(
        "guild_channels",
        {"guild_id": guild_id, "last_channel_id": channel_id},
        on_conflict="guild_id",
    )


async def get_last_channel(guild_id: int) -> int | None:
    rows = await select(
        "guild_channels",
        {"guild_id": f"eq.{guild_id}", "select": "last_channel_id"},
    )
    return rows[0]["last_channel_id"] if rows else None


# guild_id -> 지정 채널 id. on_message마다(메시지가 지정 채널 안인지 판정) 조회하므로
# _authorized_ids/_emoji_tags(admin/console.py)와 동일한 이유로 DB 왕복 없이 캐시로 유지한다.
_designated_channels: dict[int, int] = {}


async def load_designated_channels_cache() -> None:
    rows = await select(
        "guild_channels",
        {"designated_channel_id": "not.is.null", "select": "guild_id,designated_channel_id"},
    )
    global _designated_channels
    _designated_channels = {row["guild_id"]: row["designated_channel_id"] for row in rows}


def get_designated_channel(guild_id: int) -> int | None:
    return _designated_channels.get(guild_id)


async def set_designated_channel(guild_id: int, channel_id: int) -> None:
    await upsert(
        "guild_channels",
        {"guild_id": guild_id, "designated_channel_id": channel_id},
        on_conflict="guild_id",
    )
    _designated_channels[guild_id] = channel_id


async def clear_designated_channel(guild_id: int) -> bool:
    had = guild_id in _designated_channels
    await upsert(
        "guild_channels",
        {"guild_id": guild_id, "designated_channel_id": None},
        on_conflict="guild_id",
    )
    _designated_channels.pop(guild_id, None)
    return had
