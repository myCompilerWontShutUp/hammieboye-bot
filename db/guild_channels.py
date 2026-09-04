from db.client import delete, insert, select, upsert


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


# guild_id -> 메인 채널 id. on_message/interaction_check마다(메시지가 메인/서브 채널
# 안인지 판정) 조회하므로 _authorized_ids/_emoji_tags(admin/console.py)와 동일한 이유로
# DB 왕복 없이 캐시로 유지한다.
_main_channels: dict[int, int] = {}
# guild_id -> 서브 채널 id 집합.
_sub_channels: dict[int, set[int]] = {}


async def load_channel_caches() -> None:
    global _main_channels, _sub_channels
    main_rows = await select(
        "guild_channels",
        {"main_channel_id": "not.is.null", "select": "guild_id,main_channel_id"},
    )
    _main_channels = {row["guild_id"]: row["main_channel_id"] for row in main_rows}

    sub_rows = await select("guild_sub_channels", {"select": "guild_id,channel_id"})
    sub_map: dict[int, set[int]] = {}
    for row in sub_rows:
        sub_map.setdefault(row["guild_id"], set()).add(row["channel_id"])
    _sub_channels = sub_map


def get_main_channel(guild_id: int) -> int | None:
    return _main_channels.get(guild_id)


def get_sub_channel_ids(guild_id: int) -> set[int]:
    return set(_sub_channels.get(guild_id, ()))


def is_allowed_channel(guild_id: int, channel_id: int) -> bool:
    """메인 채널이 하나도 없으면(그 서버의 첫 상호작용 전) 전부 허용한다. 메인이 있으면
    메인 또는 서브 채널만 허용 — 그 외 채널은 명령어/자연어가 전부 막힌다."""
    main = _main_channels.get(guild_id)
    if main is None:
        return True
    if channel_id == main:
        return True
    return channel_id in _sub_channels.get(guild_id, ())


async def set_main_channel(guild_id: int, channel_id: int) -> None:
    """기존 메인을 교체한다. 새 메인이 기존에 서브 채널이었다면 서브 목록에서도 자동으로
    뺀다 — 한 채널이 메인이면서 동시에 서브인 상태를 만들지 않기 위함."""
    await upsert(
        "guild_channels",
        {"guild_id": guild_id, "main_channel_id": channel_id},
        on_conflict="guild_id",
    )
    _main_channels[guild_id] = channel_id
    if channel_id in _sub_channels.get(guild_id, ()):
        await remove_sub_channel(guild_id, channel_id)


async def clear_main_channel(guild_id: int) -> bool:
    had = guild_id in _main_channels
    await upsert(
        "guild_channels",
        {"guild_id": guild_id, "main_channel_id": None},
        on_conflict="guild_id",
    )
    _main_channels.pop(guild_id, None)
    return had


async def add_sub_channel(guild_id: int, channel_id: int) -> bool:
    """이미 서브였으면 아무것도 안 하고 False, 새로 추가됐으면 True."""
    if channel_id in _sub_channels.get(guild_id, ()):
        return False
    await insert("guild_sub_channels", {"guild_id": guild_id, "channel_id": channel_id})
    _sub_channels.setdefault(guild_id, set()).add(channel_id)
    return True


async def remove_sub_channel(guild_id: int, channel_id: int) -> bool:
    if channel_id not in _sub_channels.get(guild_id, ()):
        return False
    await delete(
        "guild_sub_channels",
        {"guild_id": f"eq.{guild_id}", "channel_id": f"eq.{channel_id}"},
    )
    _sub_channels[guild_id].discard(channel_id)
    return True


async def touch(guild_id: int, channel_id: int) -> None:
    """유저가 실제로 햄미를 부른 채널을 "마지막 사용 채널"로 기록한다 — 그 서버에 메인
    채널이 아직 없으면(캐시 조회라 추가 DB 왕복 없음) 이 채널을 자동으로 메인으로
    지정한다(조용히 처리, 별도 안내 문구 없음) — 여러 채널에 난잡하게 방송되던 문제를
    없애기 위함. core/base.py::touch_channel(슬래시 커맨드)과 core/dispatcher.py
    ::on_message(자연어) 둘 다 이 함수 하나를 공유한다."""
    if guild_id not in _main_channels:
        await set_main_channel(guild_id, channel_id)
    await set_last_channel(guild_id, channel_id)
