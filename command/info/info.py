import asyncio
import random
from datetime import datetime, timedelta, timezone

import discord

import achievements
from command.base import EMBED_COLOR
from core.scheduler import KST, format_footer_time
from db.achievements import get_earned
from db.daily_stats import ensure_nl_cap
from db.history import get_recent
from db.ranking import get_rank
from db.users import get_user

# "햄미가 알려주는 내 정보"라는 딱딱한 고정 문구 대신, 매번 다른 인트로 한 줄을
# 무작위로 고른다 (API로 생성 후 검수해서 고정, 사용자 요청). 내가 나를 볼 때(/내정보) 전용.
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

# 다른 사람을 소개할 때(/소개) 전용 고정 문구 풀. "{name}"에는 항상 "님"을 붙여서 끼워 넣으므로
# ("님"은 항상 받침이 있는 글자라 을/는/이 조사가 이름 원문의 받침 유무와 무관하게 고정된다),
# 어떤 이름이 와도 조사가 안 어색하다.
_INTRO_OTHER_LINES = (
    "내가 {name}님을 소개해주께!! _(으쓱)_",
    "짜잔!! {name}님 정보 가져왔어!! _(자랑)_",
    "{name}님에 대해 알려줄게!! _(신남)_",
    "이 사람이 바로 {name}님이야!! _(소개)_",
    "{name}님 소개 나갑니다!! _(당당)_",
    "궁금했지?? {name}님 정보야!! _(장난)_",
    "{name}님을 데려왔어!! 구경해봐!! _(들뜸)_",
    "짜잔, {name}님이야!! _(방긋)_",
    "{name}님 정보 살짝 보여줄게!! _(찡긋)_",
    "이건 {name}님의 이야기야!! _(진지)_",
    "{name}님을 자랑스럽게 소개할게!! _(뿌듯)_",
    "여기 {name}님 정보 대령이요!! _(공손)_",
    "{name}님, 잘 부탁해!! 소개해줄게!! _(설렘)_",
    "{name}님 스포일러 나간다!! _(흥미)_",
    "이 친구가 {name}님이야!! _(반가움)_",
    "{name}님 정보 배달 완료!! _(뿌듯)_",
    "{name}님을 한번 살펴볼까?? _(호기심)_",
    "여기, {name}님의 기록이야!! _(자랑)_",
    "{name}님 소개할 시간이야!! _(기대)_",
    "{name}님 정보 짠!! 놀랐지?? _(장난)_",
)

_SELF_DESCRIPTION = "지금까지 너랑 쌓아온 기록들이야!!"
_OTHER_DESCRIPTION_TEMPLATE = "햄미가 {name}님에 대해 알고 있는 것들이야!!"


def _format_date(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).astimezone(KST).strftime("%Y. %m. %d")


async def handle(
    user_id: int,
    *,
    target_name: str | None = None,
    guild: discord.Guild | None = None,
) -> tuple[str, discord.Embed]:
    """target_name이 None이면 본인(/내정보) 조회, 아니면 그 이름의 다른 사람(/소개) 조회.
    guild를 주면 그 서버 안에서의 호감도 순위도 같이 계산한다(등록된 서버원 기준)."""
    is_self = target_name is None

    user = await get_user(user_id)
    affection = user["affection"]
    member_ids = [m.id for m in guild.members if not m.bot] if guild is not None else None

    if member_ids is not None:
        stats, recent, earned, global_rank, guild_rank = await asyncio.gather(
            ensure_nl_cap(user_id, affection),
            get_recent(user_id, since=datetime.now(timezone.utc) - timedelta(minutes=30)),
            get_earned(user_id),
            get_rank(user_id, affection),
            get_rank(user_id, affection, member_ids),
        )
    else:
        stats, recent, earned, global_rank = await asyncio.gather(
            ensure_nl_cap(user_id, affection),
            get_recent(user_id, since=datetime.now(timezone.utc) - timedelta(minutes=30)),
            get_earned(user_id),
            get_rank(user_id, affection),
        )
        guild_rank = None

    title = "나의 정보" if is_self else f"{target_name}님의 정보"
    description = _SELF_DESCRIPTION if is_self else _OTHER_DESCRIPTION_TEMPLATE.format(name=target_name)
    heart_field_name = "💕 햄미와 나" if is_self else f"💕 햄미와 {target_name}님"
    record_field_name = "📋 햄미와 나의 기록" if is_self else f"📋 햄미와 {target_name}님의 기록"
    achievement_field_name = "🏆 우리들의 업적" if is_self else f"🏆 {target_name}님의 업적"

    embed = discord.Embed(title=title, description=description, color=EMBED_COLOR)

    # 섹션 사이 여백은 각 필드 값 "끝"에 줄바꿈 + zero-width space를 붙여서 만든다 — 트레일링
    # 공백만 있는 줄은 렌더러가 트리밍할 수 있어서, ZWS로 "진짜 내용이 있는 빈 줄"을 만든다.
    heart_lines = [f"- 햄미의 호감도: **{affection}**"]
    if guild_rank is not None:
        heart_lines.append(f"- 서버 호감도 순위: **{guild_rank}**위")
    heart_lines.append(f"- 전체 호감도 순위: **{global_rank}**위")
    embed.add_field(name=heart_field_name, value="\n".join(heart_lines) + "\n​", inline=False)

    embed.add_field(
        name=record_field_name,
        value=(
            f"- 도와준 횟수: **{user['help_count']}**\n"
            f"- 대화한 횟수: **{user['chat_count']}**\n"
            f"- 처음 만난 날: {_format_date(user['first_seen_at'])}\n​"
        ),
        inline=False,
    )

    embed.add_field(
        name="📅 오늘의 기록",
        value=(
            f"- 대화 횟수: **{stats['nl_count']}**/{stats['nl_cap']}\n"
            f"- 획득 호감: **{stats['daily_gain_natural']}**/20\n​"
        ),
        inline=False,
    )

    achievement_lines = [f"- 획득한 업적: **{len(earned)}**/{achievements.TOTAL_COUNT}"]
    for row in earned:
        module = achievements.REGISTRY.get(row["achievement_id"])
        if module is None:
            continue
        achievement_lines.append(f"- {module.NAME} ({_format_date(row['earned_at'])})")
    embed.add_field(name=achievement_field_name, value="\n".join(achievement_lines) + "\n​", inline=False)

    recent_texts = "\n".join(f"- {row['content']}" for row in recent[:3]) if recent else "- (최근 30분 내 대화 없음)"
    embed.add_field(name="🕐 최근 대화 (30분 이내)", value=recent_texts, inline=False)

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return random.choice(_INTRO_OTHER_LINES).format(name=target_name), embed
