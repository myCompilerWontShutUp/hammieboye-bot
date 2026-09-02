from db.client import delete, select, upsert


async def get_all() -> dict[int, list[str]]:
    rows = await select("user_emoji_tags", {"select": "user_id,emojis"})
    return {row["user_id"]: row["emojis"] for row in rows}


async def set_tags(user_id: int, emojis: list[str]) -> None:
    """완전 리셋 — 기존 목록을 유지하지 않고 emojis로 통째로 대체한다."""
    await upsert(
        "user_emoji_tags",
        {"user_id": user_id, "emojis": emojis},
        on_conflict="user_id",
    )


async def clear_tags(user_id: int) -> bool:
    rows = await delete("user_emoji_tags", {"user_id": f"eq.{user_id}"})
    return bool(rows)
