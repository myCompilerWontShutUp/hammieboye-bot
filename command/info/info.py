import random
from datetime import datetime, timedelta, timezone

import discord

import achievements
from command.base import EMBED_COLOR
from core.scheduler import KST
from db.achievements import get_earned
from db.daily_stats import ensure_nl_cap
from db.history import get_recent
from db.users import get_user

# "햄미가 알려주는 내 정보"라는 딱딱한 고정 문구 대신, 매번 다른 인트로 한 줄을
# 무작위로 고른다 (API로 생성 후 검수해서 고정, 사용자 요청).
_INTRO_LINES = (
    "햄미 정보 살짝 보여줄게!! _(찡긋)_",
    "햄미의 요모조모 알려줄게!! _(두근)_",
    "내 기록 구경할래?? _(신남)_",
    "햄미 상태판 열어볼게!! _(방긋)_",
    "내 정보가 여기 다 이써!! _(뿌듯)_",
    "햄미 데이터 살펴보자!! _(호기심)_",
    "지금 햄미는 이렇다구!! _(당당)_",
    "내 얘기 쪼금 보여줄게!! _(수줍)_",
    "햄미 비밀창 열어써!! _(살랑)_",
    "내 채팅 발자국도 보인다!! _(흥미)_",
    "햄미 현황 공개할게!! _(진지)_",
    "내가 얼마나 함께했는지 볼래?? _(설렘)_",
    "햄미 기록통을 열어볼게!! _(기대)_",
    "내 정보 한눈에 보여줄게!! _(자랑)_",
    "햄미의 작은 통계 나간다!! _(긴장)_",
    "내 호감도도 확인해봐!! _(부끄)_",
    "햄미가 모아둔 정보야!! _(애정)_",
    "지금까지의 햄미를 보여줄게!! _(뭉클)_",
    "내 상태 구경하고 가자!! _(활짝)_",
    "햄미 정보 출발한다구!! _(출발)_",
)


def _format_date(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).astimezone(KST).strftime("%Y. %m. %d")


async def handle(user_id: int) -> tuple[str, discord.Embed]:
    user = await get_user(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])
    recent = await get_recent(user_id, since=datetime.now(timezone.utc) - timedelta(minutes=30))
    earned = await get_earned(user_id)

    embed = discord.Embed(title="🐹 햄미와 나", color=EMBED_COLOR)

    embed.add_field(name="\n\n💕 햄미의 호감도", value=f"**{user['affection']}**", inline=False)

    embed.add_field(
        name="\n\n📋 햄미와 나의 기록",
        value=(
            f"- 도와준 횟수: **{user['help_count']}**\n"
            f"- 대화한 횟수: **{user['chat_count']}**\n"
            f"- 처음 만난 날: **{_format_date(user['first_seen_at'])}**"
        ),
        inline=False,
    )

    embed.add_field(
        name="\n\n📅 오늘의 기록",
        value=(
            f"- 대화 횟수: **{stats['nl_count']}/{stats['nl_cap']}**\n"
            f"- 획득 호감: **{stats['daily_gain_natural']}/20**"
        ),
        inline=False,
    )

    achievement_lines = [f"- 획득한 업적: **{len(earned)}/{achievements.TOTAL_COUNT}**"]
    for row in earned:
        module = achievements.REGISTRY.get(row["achievement_id"])
        if module is None:
            continue
        achievement_lines.append(f"- {module.NAME} ({_format_date(row['earned_at'])})")
    embed.add_field(name="\n\n🏆 우리들의 업적", value="\n".join(achievement_lines), inline=False)

    if recent:
        recent_texts = "\n".join(f"- {row['content']}" for row in recent[:3])
        embed.add_field(name="\n\n🕐 최근 대화 (30분 이내)", value=recent_texts, inline=False)

    return random.choice(_INTRO_LINES), embed
