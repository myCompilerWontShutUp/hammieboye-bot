import logging
import random

import discord

from config import ALLOWED_GUILD_IDS
from db.affection import add_affection
from db.client import select
from db.daily_stats import get_top_talkers_today
from db.guild_channels import get_last_channel

_client: discord.Client | None = None

_METHOD = "sleep_mention"


def init(client: discord.Client) -> None:
    global _client
    _client = client


async def announce_and_reward() -> None:
    """매일 23:59(KST)에 그날 가장 많이 대화한 사용자(들)를 언급하고 호감도를 지급한다."""
    winners = await _pick_winners()
    if not winners:
        return

    for user_id in winners:
        await add_affection(user_id, 1, _METHOD)

    await _post_announcement(winners)


async def _pick_winners() -> list[int]:
    rows = await get_top_talkers_today()
    if rows and rows[0]["messages_today"] > 0:
        top = rows[0]["messages_today"]
        return [row["user_id"] for row in rows if row["messages_today"] == top]

    # 오늘 대화한 사람이 없거나(0건), 전원이 당일 순증감 음수라 후보에서 제외된 경우
    # -> "아무도 대화 안 한 경우"와 동일하게 취급해 등록된 사용자 중 무작위로 지급한다.
    all_users = await select("users", {"select": "user_id"})
    if not all_users:
        return []
    return [random.choice(all_users)["user_id"]]


async def _post_announcement(winners: list[int]) -> None:
    if _client is None:
        return
    mentions = " ".join(f"<@{user_id}>" for user_id in winners)
    text = f"오늘 하루 끝!! {mentions} 오늘 젤 많이 놀아줘서 조아써!! 이제 잘 시간이야... 쿨쿨"

    for guild in _client.guilds:
        if guild.id not in ALLOWED_GUILD_IDS:
            continue
        channel_id = await get_last_channel(guild.id)
        if channel_id is None:
            continue
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue
        try:
            await channel.send(text)
        except discord.HTTPException:
            logging.exception("Failed to post sleep announcement in guild %s", guild.id)
