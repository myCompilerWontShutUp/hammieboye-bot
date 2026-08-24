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
