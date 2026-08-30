import logging
import random
from datetime import datetime, timedelta

import discord

import achievements
from config import ALLOWED_GUILD_IDS
from core.discord_names import resolve_real_name
from events.scheduler import KST, resolve_broadcast_channel_id
from db.achievements import award as award_achievement
from db.affection import add_affection
from db.client import select
from db.daily_stats import get_active_users_for, get_top_talkers_for
from db.guild_channels import get_last_channel

_client: discord.Client | None = None

_METHOD = "sleep_mention"

# {name}은 오늘의 최다 대화자(1명, 동점이면 타이브레이크로 단독 1위를 가린다)의 실제
# 이름으로 채워진다. 실제 보상(+1)은 이 1명이 아니라 그날 자연어로 한 번이라도 대화한
# 사용자 전원에게 별도로 지급되고, 그 안내는 이 문구 아래에 고정으로 덧붙인다(사용자 확정).
_ANNOUNCEMENT_LINES = (
    "오늘 하루 끝!! {name} 오늘 젤 많이 놀아줘서 조아써!! 이제 잘 시간이야... 쿨쿨 _(뿌듯)_",
    "오늘도 수고해써!! {name} 오늘 최고로 마니 놀아줬어!! 햄미 이제 잘게... _(행복)_",
    "하루가 다 갔네!! {name} 오늘 제일 마니 이야기해줘서 조아써!! 굿나잇!! _(포근)_",
    "오늘 대화 끝!! {name} 덕분에 넘 즐거운 하루였어!! 쿨쿨 잘게... _(만족)_",
    "밤이 왔어!! {name} 오늘 젤 마니 놀아준 칭구야, 고마워!! 잘 자!! _(감사)_",
    "오늘도 끝!! {name} 하루 종일 젤 마니 챙겨줘서 고마워!! 이제 잠들게... _(애정)_",
    "쿨쿨할 시간이야!! {name} 오늘 제일 마니 대화해줘서 행복해써!! _(행복)_",
    "하루 마무리!! {name} 오늘 최고 칭구였어!! 낼 또 놀자, 굿나잇!! _(따뜻)_",
    "잘 시간 다가와!! {name} 오늘 젤 마니 말 걸어줘서 조아써!! 쿨쿨... _(포근)_",
    "오늘도 즐거워써!! {name} 젤 마니 놀아준 사람 고마워!! 이제 잘게... _(뿌듯)_",
    "밤 인사할 시간!! {name} 오늘 최고로 마니 놀아줬어!! 잘 자!! _(설렘)_",
    "하루 끝났어!! {name} 오늘 제일 신경 써줘서 고마워!! 쿨쿨... _(감동)_",
    "오늘도 조은 하루였어!! {name} 젤 마니 대화해줘서 행복해!! 굿나잇!! _(행복)_",
    "잠들 시간이야!! {name} 오늘 최고 칭구로 뽑혔어!! 축하해!! _(자랑)_",
    "하루가 저물어써!! {name} 오늘 젤 마니 놀아줘서 고마워!! 쿨쿨... _(따뜻)_",
    "밤이 깊었어!! {name} 오늘 하루 젤 챙겨줘서 조아써!! 잘 자!! _(포근)_",
    "오늘 대화 마감!! {name} 최고로 마니 얘기해줘서 고마워!! 쿨쿨... _(감사)_",
    "이제 잘 시간!! {name} 오늘 젤 마니 놀아준 거 안 잊을게!! _(뭉클)_",
    "하루 끝!! {name} 오늘 제일 마니 챙겨줘서 넘 행복해써!! 굿나잇!! _(행복)_",
    "쿨쿨 잘 시간이야!! {name} 오늘 최고 칭구!! 낼도 놀아줘!! _(기대)_",
)

# 실제 보상 안내(고정, 20개 만들지 않음 — 순수 정책 고지성 문구라 매번 같은 의미를 유지하는
# 게 오히려 명확하다).
_REWARD_NOTICE = "💕 오늘 햄미와 대화한 전체 호감도 +1"


def init(client: discord.Client) -> None:
    global _client
    _client = client


def _target_date_str(now_kst: datetime) -> str:
    """이 함수는 자정(00:00) 시점에 실행되므로, "오늘"이 아니라 방금 끝난 "어제"(KST) 하루를
    대상으로 집계해야 한다."""
    return (now_kst - timedelta(days=1)).date().isoformat()


async def announce_and_reward() -> None:
    """정확히 자정(KST 00:00)에 실행되어, 방금 끝난 하루를 기준으로:
    1) 가장 많이 대화한 사용자 1명을 "1위"로 발표만 하고 (동점이면 타이브레이크로 단독 선정)
    2) 그날 활동(자연어 또는 공개 슬래시 커맨드)한 사용자 전원에게 호감도 +1을 지급한다
       (사용자 확정 — 기존엔 1위에게만 지급했으나, 표시(1위)와 보상(전원)을 분리했다).
    """
    target_date_str = _target_date_str(datetime.now(KST))

    winner_id = await _pick_winner(target_date_str)
    chatters = await get_active_users_for(target_date_str)

    for user_id in chatters:
        await add_affection(user_id, 1, _METHOD)

    if winner_id is not None:
        await _post_announcement(winner_id, bool(chatters))


async def _pick_winner(target_date_str: str) -> int | None:
    """그날 최다 대화자 1명을 고른다. 동점이면 그 횟수를 "먼저" 채운 사람(=오늘의 최종
    메시지 횟수에 먼저 도달한 사람) 우선, 그마저 같으면 user_id가 낮은 사람 순으로
    /랭킹과 동일한 방식의 단독 1위를 가린다(사용자 확정 — 기존엔 동점자 전원 발표)."""
    rows = await get_top_talkers_for(target_date_str)
    if rows and rows[0]["messages_today"] > 0:
        top = rows[0]["messages_today"]
        tied = [row for row in rows if row["messages_today"] == top]
        tied.sort(key=lambda row: (row["messages_today_reached_at"] or "", row["user_id"]))
        return tied[0]["user_id"]

    # 오늘 대화한 사람이 없거나(0건), 전원이 당일 순증감 음수라 후보에서 제외된 경우
    # -> "아무도 대화 안 한 경우"와 동일하게 취급해 등록된 사용자 중 무작위로 1명을 "1위"로
    # 발표한다 (단, 이 경우는 실제 자연어 대화자가 없다는 뜻이므로 보상 대상은 없다).
    all_users = await select("users", {"select": "user_id"})
    if not all_users:
        return None
    return random.choice(all_users)["user_id"]


async def _resolve_display_name(guild: discord.Guild, winner_id: int) -> str:
    """이 서버에 winner가 실제로 있으면 그 서버의 별명을, 없으면(다른 서버 전용 유저)
    실제(글로벌) 이름을 반환한다(사용자 확정, 2026-08-27) — 절대 멘션(핑)하지 않는다."""
    member = guild.get_member(winner_id)
    if member is not None:
        return member.display_name
    return await resolve_real_name(_client, winner_id)


async def _post_announcement(winner_id: int, reward_given: bool) -> None:
    if _client is None:
        return
    line_template = random.choice(_ANNOUNCEMENT_LINES)
    suffix = f"\n{_REWARD_NOTICE}" if reward_given else ""

    # "너가 짱!!" — 사용자 인터랙션이 아니라 이 스케줄 태스크가 직접 메시지를 조립하는
    # 구조라, achievement_notices 반환값 체계를 안 거치고 여기서 바로 이어붙인다.
    # award()가 멱등이라 이미 가지고 있으면 이 줄 자체가 안 붙는다.
    # 이 업적 알림은 winner가 실제로 그 서버에 있을 때만 보여준다(사용자 발견·확정,
    # 2026-08-30) — 다른 업적은 전부 유저 본인의 인터랙션(자기 서버) 안에서만 트리거되니
    # 이 문제 자체가 없지만, 이 업적만 유일하게 모든 서버에 팬아웃되는 전역 태스크에서
    # 트리거돼서 이름 표기(별명/실제 이름)처럼 서버별 분기가 필요했다.
    achievement_suffix = ""
    if await award_achievement(winner_id, achievements.daily_top_talker.ID):
        achievement_suffix = f"\n🏆 업적 달성: {achievements.format_name(achievements.daily_top_talker)}!!"

    for guild in _client.guilds:
        if guild.id not in ALLOWED_GUILD_IDS:
            continue
        channel_id = resolve_broadcast_channel_id(guild.id, await get_last_channel(guild.id))
        if channel_id is None:
            continue
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue
        # 서버마다 이름 표기가 다르다 — winner가 그 서버에 있으면 별명, 없으면 실제 이름
        # (사용자 확정, 2026-08-27). 멘션은 절대 안 한다.
        name = await _resolve_display_name(guild, winner_id)
        text = line_template.format(name=name) + suffix
        if achievement_suffix and guild.get_member(winner_id) is not None:
            text += achievement_suffix
        try:
            await channel.send(text)
        except discord.HTTPException:
            logging.exception("Failed to post sleep announcement in guild %s", guild.id)
