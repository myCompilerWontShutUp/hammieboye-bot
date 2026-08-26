import asyncio
import random
from datetime import datetime

import discord

from core.base import EMBED_COLOR
from core.discord_names import resolve_real_name
from core.scheduler import KST, format_footer_time
from db.ranking import get_last_increase_time, get_top_candidates

_TOP_N = 10

# 랭킹 embed를 보여주기 직전에 붙이는 인트로 한 줄 (API로 생성 후 검수해서 고정, 사용자 요청).
_INTRO_LINES = (
    "두근두근!! 햄미의 호감도 순위 보여줄게!! _(두근)_",
    "자, 누가 제일 가까운지 볼까?? _(궁금)_",
    "햄미가 꼼꼼히 매겨봤어!! 지금 공개할게!! _(뿌듯)_",
    "호감도 보따리 열어본다아!! _(신남)_",
    "과연 1등은 누구일까?? 떨린다!! _(긴장)_",
    "순위표 준비 완료야!! 짜잔!! _(기대)_",
    "햄미 마음속 순위, 살짝 보여줄게!! _(설렘)_",
    "이제 결과 나와!! 놀라지 마아!! _(조마)_",
    "누가 햄미랑 제일 친할까?? _(호기심)_",
    "호감도 랭킹 출발이야!! 꽉 잡아!! _(활기)_",
    "자자, 순위표가 도착해써!! _(반짝)_",
    "햄미가 세어봤어!! 바로 공개할게?? _(집중)_",
    "마음의 순위표를 열어볼 시간이야!! _(진지)_",
    "두구두구!! 결과를 보여주겠슴다!! _(흥분)_",
    "햄미의 귀여운 호감도표 나간다!! _(방긋)_",
    "누가 위에 있을지 같이 보자!! _(재미)_",
    "호감도 순위, 살포시 공개할게!! _(살짝)_",
    "준비됐지?? 햄미가 보여줄 거야!! _(용기)_",
    "이건 햄미의 진짜 마음이야!! _(솔직)_",
    "마지막으로 한 번 흔들고 공개한다!! _(통통)_",
)


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


async def build_embed(client: discord.Client) -> tuple[str, discord.Embed]:
    candidates = await get_top_candidates()
    # 후보자별 DB 조회(_sort_key)는 서로 완전히 독립적인데 예전엔 한 명씩 순차로 기다렸다 —
    # 후보 수(최대 20명)만큼 지연이 곱해지던 걸 병렬화해서 없앤다(지연시간 개선, 사용자 확정).
    sort_keys = await asyncio.gather(*(_sort_key(c) for c in candidates))
    keyed = list(zip(sort_keys, candidates))
    keyed.sort(key=lambda pair: pair[0])
    top = [c for _, c in keyed[:_TOP_N]]

    embed = discord.Embed(title="햄미의 호감도 랭킹", color=EMBED_COLOR)
    if not top:
        embed.description = "아직 등록된 사용자가 없어."
    else:
        # 실제 멘션(<@id>) 대신 실제 이름을 쓴다 — 햄미는 사용자를 맨션하지 않는다(사용자 확정).
        # 이름 조회도 서로 독립적이라 병렬로 처리한다.
        names = await asyncio.gather(*(resolve_real_name(client, c["user_id"]) for c in top))
        lines = [f"{i + 1}. {name} — {c['affection']}" for i, (name, c) in enumerate(zip(names, top))]
        embed.description = "\n".join(lines)

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    return random.choice(_INTRO_LINES), embed
