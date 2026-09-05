import asyncio
import random
from datetime import datetime

import discord

from core.base import EMBED_COLOR
from core.korean import josa
from events.scheduler import KST, format_footer_time
from command.vending_catalog import ITEMS
from db.ranking import compute_percentile, count_total, get_coin_rank
from db.snacks import get_inventory
from db.users import get_user

_EMPTY_BAG_LINE = "- 텅 비어있다"

# 내가 나를 볼 때(/내가방) 전용.
_INTRO_LINES = (
    "내 가방 열어볼게!! _(두근)_",
    "짜잔, 햄미 가방이야!! _(자랑)_",
    "가방 속 살짝 보여줄게!! _(수줍)_",
    "내가 모은 것들 구경할래?? _(신남)_",
    "햄미 보물 가방 공개!! _(반짝)_",
    "가방 안엔 뭐가 있을까?? _(궁금)_",
    "내 동전이랑 간식 보여줄게!! _(뿌듯)_",
    "가방 지퍼 열어본다!! _(설렘)_",
    "이게 다 내가 모은 거야!! _(으쓱)_",
    "가방 탈탈 털어볼게!! _(장난)_",
    "내 가방 사정 공개할게!! _(당당)_",
    "동전이랑 간식, 얼마나 모았을까?? _(기대)_",
    "가방 속 보물들 보여줄게!! _(들뜸)_",
    "짠, 이게 내 전 재산이야!! _(자신감)_",
    "가방 정리 겸 보여주는 거야!! _(뿌듯)_",
    "내가 모은 동전이랑 간식들!! _(신기)_",
    "가방 열어보니 이만큼 있었어!! _(놀람)_",
    "햄미 지갑 사정 공개!! _(진지)_",
    "가방 구경하고 갈래?? _(호기심)_",
    "내 가방, 짜잔 공개!! _(활짝)_",
)

# 다른 사람을 볼 때(/니가방) 전용. info.py의 조사 처리 패턴을 그대로 따른다.
_INTRO_OTHER_LINES = (
    "{name}의 가방 열어볼게!! _(두근)_",
    "짜잔!! {name} 가방이야!! _(자랑)_",
    "{name}{의} 가방 속 살짝 보여줄게!! _(호기심)_",
    "{name}{이가} 모은 것들 구경해볼까?? _(신남)_",
    "{name}의 보물 가방 공개!! _(반짝)_",
    "{name} 가방 안엔 뭐가 있을까?? _(궁금)_",
    "{name}의 동전이랑 간식 보여줄게!! _(뿌듯)_",
    "{name} 가방 지퍼 열어본다!! _(설렘)_",
    "이게 다 {name}{이가} 모은 거래!! _(으쓱)_",
    "{name} 가방 탈탈 털어볼게!! _(장난)_",
    "{name}의 가방 사정 공개할게!! _(당당)_",
    "{name}{을를} 위해 가방을 열어볼게!! _(기대)_",
    "{name} 가방 속 보물들 보여줄게!! _(들뜸)_",
    "짠, 이게 {name}의 전 재산이야!! _(자신감)_",
    "{name} 가방 정리 겸 보여주는 거야!! _(뿌듯)_",
    "{name}{이가} 모은 동전이랑 간식들!! _(신기)_",
    "{name} 가방 열어보니 이만큼 있었어!! _(놀람)_",
    "{name}의 지갑 사정 공개!! _(진지)_",
    "{name} 가방 구경하고 갈래?? _(호기심)_",
    "{name}의 가방, 짜잔 공개!! _(활짝)_",
)


def _format_other_line(name: str) -> str:
    return random.choice(_INTRO_OTHER_LINES).format(
        name=name, 의="의", 을를=josa(name, "을", "를"), 이가=josa(name, "이", "가")
    )


async def handle(
    user_id: int, *, target_name: str | None = None, guild: discord.Guild | None = None
) -> tuple[str, discord.Embed]:
    """target_name이 None이면 본인(/내가방) 조회, 아니면 그 이름의 다른 사람(/니가방) 조회.
    guild를 주면 그 서버 안에서의 동전 순위도 같이 계산한다(/내정보의 호감도 순위와
    동일한 원칙, db/ranking.py::get_coin_rank — "먼저 그 값에 도달한 사람" 타이브레이크는
    동전엔 없어 가입일→user_id 2단계만 적용)."""
    is_self = target_name is None

    user, inventory = await asyncio.gather(get_user(user_id), get_inventory(user_id))
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

    title = "나의 가방" if is_self else f"{target_name}의 가방"
    embed = discord.Embed(title=title, color=EMBED_COLOR)

    wallet_field_name = "💰 동전 지갑" if is_self else f"💰 {target_name}의 동전 지갑"
    wallet_lines = [f"- 보유 동전 개수: **{coins}**"]
    if guild_rank is not None:
        guild_percentile = compute_percentile(guild_rank, guild_total)
        wallet_lines.append(f"- 서버 동전 순위: **{guild_rank}**위 (상위 {guild_percentile}%)")
    global_percentile = compute_percentile(global_rank, global_total)
    wallet_lines.append(f"- 전체 동전 순위: **{global_rank}**위 (상위 {global_percentile}%)")
    embed.add_field(
        name=wallet_field_name,
        value="\n".join(wallet_lines) + "\n​",
        inline=False,
    )

    snack_field_name = "🍪 간식 보따리" if is_self else f"🍪 {target_name}의 간식 보따리"
    qty_by_id = {row["snack_id"]: row["quantity"] for row in inventory}
    snack_lines = [f"- {item.name} x {qty_by_id[item.id]}" for item in ITEMS if item.id in qty_by_id]
    embed.add_field(
        name=snack_field_name,
        value="\n".join(snack_lines) if snack_lines else _EMPTY_BAG_LINE,
        inline=False,
    )

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return _format_other_line(target_name), embed
