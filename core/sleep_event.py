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

# {mentions}는 오늘의 최다 대화자(들) 멘션으로 채워진다 (동률이면 여러 명).
_ANNOUNCEMENT_LINES = (
    "오늘 하루 끝!! {mentions} 오늘 젤 많이 놀아줘서 조아써!! 이제 잘 시간이야... 쿨쿨 _(뿌듯)_",
    "오늘도 수고해써!! {mentions} 오늘 최고로 마니 놀아줬어!! 햄미 이제 잘게... _(행복)_",
    "하루가 다 갔네!! {mentions} 오늘 제일 마니 이야기해줘서 조아써!! 굿나잇!! _(포근)_",
    "오늘 대화 끝!! {mentions} 덕분에 넘 즐거운 하루였어!! 쿨쿨 잘게... _(만족)_",
    "밤이 왔어!! {mentions} 오늘 젤 마니 놀아준 칭구야, 고마워!! 잘 자!! _(감사)_",
    "오늘도 끝!! {mentions} 하루 종일 젤 마니 챙겨줘서 고마워!! 이제 잠들게... _(애정)_",
    "쿨쿨할 시간이야!! {mentions} 오늘 제일 마니 대화해줘서 행복해써!! _(행복)_",
    "하루 마무리!! {mentions} 오늘 최고 칭구였어!! 낼 또 놀자, 굿나잇!! _(따뜻)_",
    "잘 시간 다가와!! {mentions} 오늘 젤 마니 말 걸어줘서 조아써!! 쿨쿨... _(포근)_",
    "오늘도 즐거워써!! {mentions} 젤 마니 놀아준 사람 고마워!! 이제 잘게... _(뿌듯)_",
    "밤 인사할 시간!! {mentions} 오늘 최고로 마니 놀아줬어!! 잘 자!! _(설렘)_",
    "하루 끝났어!! {mentions} 오늘 제일 신경 써줘서 고마워!! 쿨쿨... _(감동)_",
    "오늘도 조은 하루였어!! {mentions} 젤 마니 대화해줘서 행복해!! 굿나잇!! _(행복)_",
    "잠들 시간이야!! {mentions} 오늘 최고 칭구로 뽑혔어!! 축하해!! _(자랑)_",
    "하루가 저물어써!! {mentions} 오늘 젤 마니 놀아줘서 고마워!! 쿨쿨... _(따뜻)_",
    "밤이 깊었어!! {mentions} 오늘 하루 젤 챙겨줘서 조아써!! 잘 자!! _(포근)_",
    "오늘 대화 마감!! {mentions} 최고로 마니 얘기해줘서 고마워!! 쿨쿨... _(감사)_",
    "이제 잘 시간!! {mentions} 오늘 젤 마니 놀아준 거 안 잊을게!! _(뭉클)_",
    "하루 끝!! {mentions} 오늘 제일 마니 챙겨줘서 넘 행복해써!! 굿나잇!! _(행복)_",
    "쿨쿨 잘 시간이야!! {mentions} 오늘 최고 칭구!! 낼도 놀아줘!! _(기대)_",
)


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
    text = random.choice(_ANNOUNCEMENT_LINES).format(mentions=mentions)

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
