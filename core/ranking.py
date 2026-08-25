import discord

from command.base import EMBED_COLOR
from db.ranking import get_last_increase_time, get_top_candidates

_TOP_N = 5


async def _sort_key(candidate: dict) -> tuple:
    timestamp = await get_last_increase_time(candidate["user_id"], candidate["affection"])
    # 증가로 도달한 시각이 없으면(감소로만 도달했으면) 동점자 중 가장 뒤로 밀린다.
    return (
        -candidate["affection"],
        0 if timestamp is not None else 1,
        timestamp or "",
        candidate["created_at"],
        candidate["user_id"],
    )


async def build_embed() -> discord.Embed:
    candidates = await get_top_candidates()
    keyed = [((await _sort_key(c)), c) for c in candidates]
    keyed.sort(key=lambda pair: pair[0])
    top = [c for _, c in keyed[:_TOP_N]]

    embed = discord.Embed(title="햄미의 호감도 랭킹", color=EMBED_COLOR)
    if not top:
        embed.description = "아직 등록된 사용자가 없어."
        return embed

    lines = [f"{i + 1}. <@{c['user_id']}> — {c['affection']}" for i, c in enumerate(top)]
    embed.description = "\n".join(lines)
    return embed
