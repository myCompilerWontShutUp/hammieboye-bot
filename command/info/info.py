from datetime import datetime, timedelta, timezone

import discord

from command.base import EMBED_COLOR
from db.daily_stats import ensure_daily_stats
from db.history import get_recent
from db.users import get_user


async def handle(user_id: int) -> tuple[str, discord.Embed]:
    user = await get_user(user_id)
    stats = await ensure_daily_stats(user_id)
    recent = await get_recent(user_id, since=datetime.now(timezone.utc) - timedelta(minutes=30))

    embed = discord.Embed(color=EMBED_COLOR)
    embed.add_field(name="호감도", value=str(user["affection"]), inline=True)
    embed.add_field(name="채팅 횟수", value=str(user["chat_count"]), inline=True)
    embed.add_field(name="도와준 횟수", value=str(user["help_count"]), inline=True)
    embed.add_field(name="오늘 대화 횟수", value=str(stats["messages_today"]), inline=True)

    if recent:
        recent_texts = "\n".join(f"- {row['content']}" for row in recent[:3])
        embed.add_field(name="최근 대화(30분 이내)", value=recent_texts, inline=False)

    return "햄미가 알려주는 내 정보 뾱", embed
