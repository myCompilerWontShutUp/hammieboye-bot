import asyncio
import random
from datetime import datetime, timedelta, timezone

import discord

from core.base import EMBED_COLOR
from core.korean import josa
from core.scheduler import KST, format_footer_time
from db.daily_stats import ensure_nl_cap
from db.history import get_recent
from db.ranking import compute_percentile, count_total, get_rank
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

# 다른 사람을 소개할 때(/니정보) 전용 고정 문구 풀. 햄미는 "님" 존칭을 쓰지 않는다(사용자
# 확정, 2026-08-27). "님"이 없으면 뒤따르는 조사(을/를, 이야/야)가 이름의 받침 유무에 따라
# 달라지므로, 해당 조사가 필요한 자리는 {을를}/{이야} 자리표시자로 남기고 포맷 시점에
# `core.korean.josa()`로 계산해 채운다 (나머지 줄은 조사가 안 붙는 자리라 손댈 필요 없음).
_INTRO_OTHER_LINES = (
    "내가 {name}{을를} 소개해주께!! _(으쓱)_",
    "짜잔!! {name} 정보 가져왔어!! _(자랑)_",
    "{name}에 대해 알려줄게!! _(신남)_",
    "이 사람이 바로 {name}{이야}!! _(소개)_",
    "{name} 소개 나갑니다!! _(당당)_",
    "궁금했지?? {name} 정보야!! _(장난)_",
    "{name}{을를} 데려왔어!! 구경해봐!! _(들뜸)_",
    "짜잔, {name}{이야}!! _(방긋)_",
    "{name} 정보 살짝 보여줄게!! _(찡긋)_",
    "이건 {name}의 이야기야!! _(진지)_",
    "{name}{을를} 자랑스럽게 소개할게!! _(뿌듯)_",
    "여기 {name} 정보 대령이요!! _(공손)_",
    "{name}, 잘 부탁해!! 소개해줄게!! _(설렘)_",
    "{name} 스포일러 나간다!! _(흥미)_",
    "이 친구가 {name}{이야}!! _(반가움)_",
    "{name} 정보 배달 완료!! _(뿌듯)_",
    "{name}{을를} 한번 살펴볼까?? _(호기심)_",
    "여기, {name}의 기록이야!! _(자랑)_",
    "{name} 소개할 시간이야!! _(기대)_",
    "{name} 정보 짠!! 놀랐지?? _(장난)_",
)


def _format_other_line(name: str) -> str:
    return random.choice(_INTRO_OTHER_LINES).format(
        name=name,
        을를=josa(name, "을", "를"),
        이야=josa(name, "이야", "야"),
    )


_SELF_DESCRIPTION = "지금까지 너랑 쌓아온 기록들이야!!"
_OTHER_DESCRIPTION_TEMPLATE = "햄미가 {name}에 대해 알고 있는 것들이야!!"


def _format_date(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).astimezone(KST).strftime("%Y. %m. %d")


async def handle(
    user_id: int,
    *,
    target_name: str | None = None,
    guild: discord.Guild | None = None,
) -> tuple[str, discord.Embed]:
    """target_name이 None이면 본인(/내정보) 조회, 아니면 그 이름의 다른 사람(/니정보) 조회.
    guild를 주면 그 서버 안에서의 호감도 순위도 같이 계산한다(등록된 서버원 기준). 업적은
    이제 별도 명령어(/내업적, /니업적, `command/achievements.py`)로 분리돼 여기서는
    안 보여준다(사용자 확정, 2026-08-27 — 업적이 늘어나며 정보량이 많아져서 분리)."""
    is_self = target_name is None

    user = await get_user(user_id)
    affection = user["affection"]
    member_ids = [m.id for m in guild.members if not m.bot] if guild is not None else None

    if member_ids is not None:
        stats, recent, global_rank, global_total, guild_rank, guild_total = await asyncio.gather(
            ensure_nl_cap(user_id, affection),
            get_recent(user_id, since=datetime.now(timezone.utc) - timedelta(minutes=30)),
            get_rank(user_id, affection),
            count_total(),
            get_rank(user_id, affection, member_ids),
            count_total(member_ids),
        )
    else:
        stats, recent, global_rank, global_total = await asyncio.gather(
            ensure_nl_cap(user_id, affection),
            get_recent(user_id, since=datetime.now(timezone.utc) - timedelta(minutes=30)),
            get_rank(user_id, affection),
            count_total(),
        )
        guild_rank = None
        guild_total = None

    # 햄미는 "님" 존칭을 쓰지 않는다(사용자 확정) — "의"는 받침 유무와 무관하게 불변이라
    # 별도 조사 계산 없이 안전하다.
    title = "나의 정보" if is_self else f"{target_name}의 정보"
    description = _SELF_DESCRIPTION if is_self else _OTHER_DESCRIPTION_TEMPLATE.format(name=target_name)
    heart_field_name = "💕 햄미와 나" if is_self else f"💕 햄미와 {target_name}"
    record_field_name = "📋 햄미와 나의 기록" if is_self else f"📋 햄미와 {target_name}의 기록"

    embed = discord.Embed(title=title, description=description, color=EMBED_COLOR)

    # 섹션 사이 여백은 각 필드 값 "끝"에 줄바꿈 + zero-width space를 붙여서 만든다 — 트레일링
    # 공백만 있는 줄은 렌더러가 트리밍할 수 있어서, ZWS로 "진짜 내용이 있는 빈 줄"을 만든다.
    heart_lines = [f"- 햄미의 호감도: **{affection}**"]
    if guild_rank is not None:
        guild_percentile = compute_percentile(guild_rank, guild_total)
        heart_lines.append(f"- 서버 호감도 순위: **{guild_rank}**위 (상위 {guild_percentile}%)")
    global_percentile = compute_percentile(global_rank, global_total)
    heart_lines.append(f"- 전체 호감도 순위: **{global_rank}**위 (상위 {global_percentile}%)")
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

    recent_texts = "\n".join(f"- {row['content']}" for row in recent[:3]) if recent else "- (최근 30분 내 대화 없음)"
    embed.add_field(name="🕐 최근 대화 (30분 이내)", value=recent_texts, inline=False)

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return _format_other_line(target_name), embed
