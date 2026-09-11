import asyncio
import random
from datetime import datetime

import discord

import command.coin as coin_command
from core.base import EMBED_COLOR
from core.korean import josa
from events.scheduler import KST, format_footer_time
from db.ranking import compute_percentile, count_total, get_coin_rank
from db.users import get_user
from db.wallet import describe_coin_method, get_recent_coin_log

_EMPTY_HISTORY_LINE = "- 아직 동전 변동 내역이 없다"
_RECENT_LIMIT = 5

# 내가 나를 볼 때(/내정보 "자산" 탭) 전용 — bag.py 인트로 풀과 같은 톤.
_INTRO_LINES = (
    "내 자산 상황 보여줄게!! _(두근)_",
    "짜잔, 햄미 자산이야!! _(자랑)_",
    "동전이 얼마나 모였을까?? _(궁금)_",
    "내 재산 목록 공개!! _(뿌듯)_",
    "자산 내역 살짝 보여줄게!! _(수줍)_",
    "지금까지 모은 동전들이야!! _(신남)_",
    "내 지갑 사정 공개할게!! _(당당)_",
    "동전 내역 구경할래?? _(호기심)_",
    "이게 다 내가 모은 동전이야!! _(으쓱)_",
    "자산 정리 겸 보여주는 거야!! _(뿌듯)_",
    "최근 동전 기록도 같이 보여줄게!! _(들뜸)_",
    "짠, 이게 내 전 재산이야!! _(자신감)_",
    "동전이 오간 기록 공개!! _(진지)_",
    "내 자산 탈탈 털어볼게!! _(장난)_",
    "얼마나 벌고 썼는지 보여줄게!! _(기대)_",
    "동전 내역 살펴보자!! _(호기심)_",
    "자산 현황 공개할게!! _(당당)_",
    "내 동전 발자국도 보인다!! _(흥미)_",
    "자산 구경하고 갈래?? _(방긋)_",
    "내 자산, 짜잔 공개!! _(활짝)_",
)

# 다른 사람을 볼 때(/니정보 "자산" 탭) 전용 — info.py/bag.py의 조사 처리 패턴을 그대로 따른다.
_INTRO_OTHER_LINES = (
    "{name}의 자산 상황 보여줄게!! _(두근)_",
    "짜잔!! {name} 자산이야!! _(자랑)_",
    "{name}{의} 동전이 얼마나 모였을까?? _(궁금)_",
    "{name}의 재산 목록 공개!! _(뿌듯)_",
    "{name} 자산 내역 살짝 보여줄게!! _(수줍)_",
    "{name}{이가} 지금까지 모은 동전들이야!! _(신남)_",
    "{name}의 지갑 사정 공개할게!! _(당당)_",
    "{name} 동전 내역 구경해볼까?? _(호기심)_",
    "이게 다 {name}{이가} 모은 동전이래!! _(으쓱)_",
    "{name} 자산 정리 겸 보여주는 거야!! _(뿌듯)_",
    "{name}의 최근 동전 기록도 같이 보여줄게!! _(들뜸)_",
    "짠, 이게 {name}의 전 재산이야!! _(자신감)_",
    "{name}의 동전이 오간 기록 공개!! _(진지)_",
    "{name} 자산 탈탈 털어볼게!! _(장난)_",
    "{name}{이가} 얼마나 벌고 썼는지 보여줄게!! _(기대)_",
    "{name} 동전 내역 살펴보자!! _(호기심)_",
    "{name}의 자산 현황 공개할게!! _(당당)_",
    "{name}의 동전 발자국도 보인다!! _(흥미)_",
    "{name} 자산 구경하고 갈래?? _(방긋)_",
    "{name}의 자산, 짜잔 공개!! _(활짝)_",
)


def _format_other_line(name: str) -> str:
    return random.choice(_INTRO_OTHER_LINES).format(
        name=name, 의="의", 이가=josa(name, "이", "가")
    )


def _format_history_line(row: dict) -> str:
    delta = row["delta"]
    sign = "+" if delta > 0 else ""
    label = describe_coin_method(row["method"])
    when = format_footer_time(datetime.fromisoformat(row["created_at"]).astimezone(KST))
    return f"- {sign}{delta} ({label}) · {when}"


async def handle(
    user_id: int, *, target_name: str | None = None, guild: discord.Guild | None = None
) -> tuple[str, discord.Embed]:
    """/내정보·/니정보의 "자산" 탭(2026-09-11 신규) — 舊 "정보"/"가방" 탭에 흩어져
    있던 🪙 동전 요약(보유량+서버/전체 순위)을 이리로 모으고, `/동전` 기본 획득량과
    최근 동전 변동 내역(최신 5건, coin_log 기반)을 새로 추가했다."""
    is_self = target_name is None

    user, recent_log = await asyncio.gather(
        get_user(user_id), get_recent_coin_log(user_id, _RECENT_LIMIT)
    )
    coins = user["coins"]
    member_ids = [m.id for m in guild.members if not m.bot] if guild is not None else None

    if member_ids is not None:
        global_rank, global_total, guild_rank, guild_total = await asyncio.gather(
            get_coin_rank(user_id, coins),
            count_total(),
            get_coin_rank(user_id, coins, member_ids),
            count_total(member_ids),
        )
    else:
        global_rank, global_total = await asyncio.gather(get_coin_rank(user_id, coins), count_total())
        guild_rank = None
        guild_total = None

    title = "나의 자산" if is_self else f"{target_name}의 자산"
    embed = discord.Embed(title=title, color=EMBED_COLOR)

    coin_field_name = "🪙 동전" if is_self else f"🪙 {target_name}의 동전"
    coin_lines = [f"- 보유 동전 개수: **{coins}**"]
    if guild_rank is not None:
        guild_percentile = compute_percentile(guild_rank, guild_total)
        coin_lines.append(f"- 서버 동전 순위: **{guild_rank}**위 (상위 {guild_percentile}%)")
    global_percentile = compute_percentile(global_rank, global_total)
    coin_lines.append(f"- 전체 동전 순위: **{global_rank}**위 (상위 {global_percentile}%)")
    base_grant = coin_command.BASE_GRANT + user["coin_grant_bonus"]
    coin_lines.append(f"- `/동전` 사용 시 기본 획득량: **{base_grant}**개")
    embed.add_field(name=coin_field_name, value="\n".join(coin_lines), inline=False)

    history_field_name = "📜 최근 동전 내역" if is_self else f"📜 {target_name}의 최근 동전 내역"
    history_lines = [_format_history_line(row) for row in recent_log]
    embed.add_field(
        name=history_field_name,
        value="\n".join(history_lines) if history_lines else _EMPTY_HISTORY_LINE,
        inline=False,
    )

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return _format_other_line(target_name), embed
