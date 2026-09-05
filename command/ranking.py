import asyncio
import logging
import random
from datetime import datetime

import discord

from core.base import EMBED_COLOR, LIST_EMBED_COLOR, reject_if_wrong_invoker
from core.discord_names import resolve_real_name
from events.scheduler import KST, format_footer_time
from db.ranking import get_last_increase_time, get_top_candidates, get_top_coin_candidates

_TOP_N = 10

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

_COIN_INTRO_LINES = (
    "두근두근!! 햄미의 동전 순위 보여줄게!! _(두근)_",
    "자, 누가 제일 부자일까?? _(궁금)_",
    "햄미가 꼼꼼히 세어봤어!! 지금 공개할게!! _(뿌듯)_",
    "동전 지갑 순위 열어본다아!! _(신남)_",
    "과연 1등은 누구일까?? 떨린다!! _(긴장)_",
    "동전 순위표 준비 완료야!! 짜잔!! _(기대)_",
    "누가 제일 많이 모았는지 살짝 보여줄게!! _(설렘)_",
    "이제 결과 나와!! 놀라지 마아!! _(조마)_",
    "누가 햄미보다 부자일까?? _(호기심)_",
    "동전 랭킹 출발이야!! 꽉 잡아!! _(활기)_",
    "자자, 동전 순위표가 도착해써!! _(반짝)_",
    "햄미가 세어봤어!! 바로 공개할게?? _(집중)_",
    "동전 부자 순위표를 열어볼 시간이야!! _(진지)_",
    "두구두구!! 결과를 보여주겠슴다!! _(흥분)_",
    "햄미의 귀여운 동전표 나간다!! _(방긋)_",
    "누가 위에 있을지 같이 보자!! _(재미)_",
    "동전 순위, 살포시 공개할게!! _(살짝)_",
    "준비됐지?? 햄미가 보여줄 거야!! _(용기)_",
    "이건 진짜 동전 실력이야!! _(솔직)_",
    "마지막으로 한 번 짤랑이고 공개한다!! _(통통)_",
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


def _coin_sort_key(candidate: dict) -> tuple:
    # affection_log 같은 "동전 획득 이력" 테이블이 없어서(coins에는 로그가 안 남음)
    # "먼저 그 값에 도달한 사람" 타이브레이크는 재현할 수 없다 — 가입일이 이른 사람 ->
    # user_id가 낮은 사람 두 단계만 적용한다(호감도 랭킹에서 "증가로 도달한 적 없는"
    # 동점자가 이미 이 두 단계로 자연스럽게 밀려나는 것과 동일한 골격).
    return (-candidate["coins"], candidate["created_at"], candidate["user_id"])


async def build_affection_embed(client: discord.Client) -> tuple[str, discord.Embed]:
    candidates = await get_top_candidates()
    sort_keys = await asyncio.gather(*(_sort_key(c) for c in candidates))
    keyed = list(zip(sort_keys, candidates))
    keyed.sort(key=lambda pair: pair[0])
    top = [c for _, c in keyed[:_TOP_N]]

    embed = discord.Embed(title="햄미의 호감도 랭킹", color=LIST_EMBED_COLOR)
    if not top:
        embed.description = "아직 등록된 사용자가 없어."
    else:
        # 멘션(<@id>) 대신 실제 이름을 쓴다 — 조회도 서로 독립적이라 병렬 처리한다.
        names = await asyncio.gather(*(resolve_real_name(client, c["user_id"]) for c in top))
        lines = [f"{i + 1}. {name} — {c['affection']}" for i, (name, c) in enumerate(zip(names, top))]
        embed.description = "\n".join(lines)

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    return random.choice(_INTRO_LINES), embed


async def build_coin_embed(client: discord.Client) -> tuple[str, discord.Embed]:
    candidates = await get_top_coin_candidates()
    top = sorted(candidates, key=_coin_sort_key)[:_TOP_N]

    embed = discord.Embed(title="햄미의 동전 랭킹", color=LIST_EMBED_COLOR)
    if not top:
        embed.description = "아직 등록된 사용자가 없어."
    else:
        names = await asyncio.gather(*(resolve_real_name(client, c["user_id"]) for c in top))
        lines = [
            f"{i + 1}. {name} — {c['coins']}개"
            for i, (name, c) in enumerate(zip(names, top))
        ]
        embed.description = "\n".join(lines)

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    return random.choice(_COIN_INTRO_LINES), embed


# /랭킹 통합(2026-09-06) — 舊 /랭킹-호감도·/랭킹-동전을 자판기-리스트와 동일한
# "카테고리 버튼" UI로 합쳤다. 기본값은 호감도.
_CATEGORY_LABELS: dict[str, str] = {"affection": "호감도", "coin": "동전"}
_CATEGORY_ORDER: tuple[str, ...] = ("affection", "coin")
_DEFAULT_CATEGORY = "affection"
_BUILDERS = {"affection": build_affection_embed, "coin": build_coin_embed}


class _CategoryButton(discord.ui.Button):
    def __init__(self, kind: str, *, active: bool) -> None:
        # 활성=초록(success), 비활성=회색(secondary) — command/vending.py의
        # _CategoryButton과 동일한 배색 규칙.
        style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary
        super().__init__(label=_CATEGORY_LABELS[kind], style=style)
        self._kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _RankingView = self.view
        if not await reject_if_wrong_invoker(interaction, view.user_id):
            return
        for child in view.children:
            if isinstance(child, _CategoryButton):
                child.style = (
                    discord.ButtonStyle.success if child is self else discord.ButtonStyle.secondary
                )
        # content는 안 건드린다 — 취침 중이라 고정 문구로 대체된 상태였다면(sleep_guard),
        # 카테고리를 바꾼다고 그 문구가 평소 인트로로 되돌아가면 안 되기 때문
        # (command/vending.py::_CategoryButton과 동일한 원칙).
        _, embed = await _BUILDERS[self._kind](interaction.client)
        await interaction.response.edit_message(embed=embed, view=view)


class _RankingView(discord.ui.View):
    """1분간 상호작용이 없으면 버튼만 지운다(내용은 그대로 둠) — 명령어 실행자
    (user_id) 외에는 카테고리 버튼을 못 누른다(자판기-리스트와 동일한 원칙)."""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.message: discord.Message | None = None
        for kind in _CATEGORY_ORDER:
            self.add_item(_CategoryButton(kind, active=(kind == _DEFAULT_CATEGORY)))

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logging.exception("Failed to clear ranking list buttons on timeout")


async def handle(user_id: int, client: discord.Client) -> tuple[str, discord.Embed, discord.ui.View]:
    text, embed = await _BUILDERS[_DEFAULT_CATEGORY](client)
    return text, embed, _RankingView(user_id)
