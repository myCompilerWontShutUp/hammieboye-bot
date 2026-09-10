import asyncio
import logging
import random
from datetime import datetime

import discord

import levels
from core.base import EMBED_COLOR, reject_if_wrong_invoker
from core.korean import josa
from events.scheduler import KST, format_footer_time
from events.special_days import get_help_me_event_count
from db.daily_stats import ensure_daily_stats
from db.ranking import compute_percentile, count_total, get_coin_rank, get_rank
from db.users import get_user
import command.achievements as achievements_view
import command.bag as bag_view

# 舊 /내정보가 한 임베드로 보여주던 호감도/오늘 기록/전체 기록 3개 카테고리가 전부
# 공유하는 결과 인트로 풀(가방·업적은 각자 기존 풀을 그대로 씀).
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

# 다른 사람을 소개할 때(/니정보) 전용 고정 문구 풀. "님" 존칭을 안 쓰므로 조사(을/를,
# 이야/야)가 이름의 받침 유무에 따라 달라지는 자리는 {을를}/{이야} 자리표시자로 남기고
# 포맷 시점에 josa()로 계산해 채운다.
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


def _format_other_line(pool: tuple[str, ...], name: str) -> str:
    return random.choice(pool).format(
        name=name,
        을를=josa(name, "을", "를"),
        이야=josa(name, "이야", "야"),
    )


def _format_date(iso_str: str) -> str:
    """"처음 만난 날" 전용 — 날짜 뒤에 (N일째)를 덧붙인다. 만난 날 당일을 1일째로 센다
    (예: 오늘 가입했으면 "1일째" — events/greeting.py의 "태어난 지 N일째"는 생일 당일을
    0일째로 세는 별개 관례라 여기 그대로 안 맞춘다)."""
    first_seen = datetime.fromisoformat(iso_str).astimezone(KST)
    days = (datetime.now(KST).date() - first_seen.date()).days + 1
    return f"{first_seen.strftime('%Y. %m. %d')} ({days}일째)"


async def _render_info(
    user_id: int, *, target_name: str | None, guild: discord.Guild | None
) -> tuple[str, discord.Embed]:
    """舊 "호감도" 탭 → "정보" 탭으로 확장(2026-09-09) — 호감도(+서버/전체 순위)와
    동전(+서버/전체 순위, command/bag.py::handle()의 동전 부분과 동일한 계산)을
    한 번에 보여준다. 전체 유저 수(count_total)는 호감도든 동전이든 동일한 값이라
    한 번만 조회해서 두 순위 계산에 같이 쓴다(중복 API 호출 방지)."""
    is_self = target_name is None
    user = await get_user(user_id)
    affection = user["affection"]
    coins = user["coins"]
    member_ids = [m.id for m in guild.members if not m.bot] if guild is not None else None

    if member_ids is not None:
        (
            affection_global_rank,
            affection_guild_rank,
            coin_global_rank,
            coin_guild_rank,
            global_total,
            guild_total,
        ) = await asyncio.gather(
            get_rank(user_id, affection),
            get_rank(user_id, affection, member_ids),
            get_coin_rank(user_id, coins),
            get_coin_rank(user_id, coins, member_ids),
            count_total(),
            count_total(member_ids),
        )
    else:
        affection_global_rank, coin_global_rank, global_total = await asyncio.gather(
            get_rank(user_id, affection), get_coin_rank(user_id, coins), count_total()
        )
        affection_guild_rank = None
        coin_guild_rank = None
        guild_total = None

    title = "나의 정보" if is_self else f"{target_name}의 정보"
    embed = discord.Embed(title=title, color=EMBED_COLOR)

    heart_field_name = "💕 햄미와 나" if is_self else f"💕 햄미와 {target_name}"
    heart_lines = [f"- 햄미의 호감도: **{affection}**"]
    if affection_guild_rank is not None:
        guild_percentile = compute_percentile(affection_guild_rank, guild_total)
        heart_lines.append(f"- 서버 호감도 순위: **{affection_guild_rank}**위 (상위 {guild_percentile}%)")
    affection_global_percentile = compute_percentile(affection_global_rank, global_total)
    heart_lines.append(
        f"- 전체 호감도 순위: **{affection_global_rank}**위 (상위 {affection_global_percentile}%)"
    )
    embed.add_field(name=heart_field_name, value="\n".join(heart_lines), inline=False)

    coin_field_name = "🪙 동전" if is_self else f"🪙 {target_name}의 동전"
    coin_lines = [f"- 보유 동전 개수: **{coins}**"]
    if coin_guild_rank is not None:
        coin_guild_percentile = compute_percentile(coin_guild_rank, guild_total)
        coin_lines.append(f"- 서버 동전 순위: **{coin_guild_rank}**위 (상위 {coin_guild_percentile}%)")
    coin_global_percentile = compute_percentile(coin_global_rank, global_total)
    coin_lines.append(f"- 전체 동전 순위: **{coin_global_rank}**위 (상위 {coin_global_percentile}%)")
    embed.add_field(name=coin_field_name, value="\n".join(coin_lines), inline=False)

    # 레벨/XP 시스템(2026-09-10 신규) — 동전 필드 바로 다음에 표시.
    level = levels.get_level_for_xp(user["total_xp"])
    next_level = levels.get_next_level(level)
    level_lines = [f"- 레벨 {level.number} ({level.name})"]
    if next_level is not None:
        level_lines.append(f"- 경험치: **{user['total_xp']}** / {next_level.xp_required}")
    else:
        level_lines.append(f"- 경험치: **{user['total_xp']}** (최고 레벨)")
    level_lines.append(levels.xp_progress_bar(user["total_xp"], level, next_level))
    embed.add_field(name="🎖️ 레벨", value="\n".join(level_lines), inline=False)

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return _format_other_line(_INTRO_OTHER_LINES, target_name), embed


async def _render_today(user_id: int, *, target_name: str | None) -> tuple[str, discord.Embed]:
    """舊 /내정보의 "📅 오늘의 기록" 필드."""
    is_self = target_name is None
    user = await get_user(user_id)
    stats = await ensure_daily_stats(user_id)
    # 레벨/XP 시스템(2026-09-10) — 자연어 일일 상한이 호감도 공식에서 레벨 기반으로
    # 바뀌며 nl_cap을 그 순간 레벨에서 실시간으로 조회한다(§chat.py와 동일 원칙).
    nl_cap = levels.get_level_for_xp(user["total_xp"]).daily_nl_limit

    today = datetime.now(KST).date()
    dessert_fed_count = len(stats.get("dessert_fed_today") or {})
    help_me_event_total = get_help_me_event_count(today)

    title = "나의 오늘 기록" if is_self else f"{target_name}의 오늘 기록"
    embed = discord.Embed(title=title, color=EMBED_COLOR)
    embed.add_field(
        name="📅 오늘의 기록",
        value=(
            f"- 간식 준 횟수: **{dessert_fed_count}**/3\n"
            f"- 도움 횟수: **{stats['help_me_events_helped_today']}**/{help_me_event_total}\n"
            f"- 대화 횟수: **{stats['nl_count']}**/{nl_cap}\n"
            # 명령어 사용 횟수(2026-09-10 신규) — 대화 횟수 바로 옆에 나란히.
            f"- 명령어 사용 횟수: **{stats['slash_count']}**\n"
            # 2026-09-10 — 일일 획득 상한이 사실상 무제한(2147483647)으로 올라가며
            # "/상한" 표기를 뺐다(사용자 지시 — 상한 자체를 노출하지 않음).
            f"- 획득 호감: **{stats['daily_gain_natural']}**"
        ),
        inline=False,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return _format_other_line(_INTRO_OTHER_LINES, target_name), embed


async def _render_lifetime(user_id: int, *, target_name: str | None) -> tuple[str, discord.Embed]:
    """舊 /내정보의 "📋 햄미와 나의 기록" 필드."""
    is_self = target_name is None
    user = await get_user(user_id)

    title = "나의 전체 기록" if is_self else f"{target_name}의 전체 기록"
    record_field_name = "📋 햄미와 나의 기록" if is_self else f"📋 햄미와 {target_name}의 기록"
    embed = discord.Embed(title=title, color=EMBED_COLOR)
    embed.add_field(
        name=record_field_name,
        value=(
            f"- 간식 준 횟수: **{user['total_snacks_given']}**\n"
            f"- 도움 횟수: **{user['help_count']}**\n"
            f"- 대화 횟수: **{user['chat_count']}**\n"
            f"- 획득한 동전: **{user['lifetime_coins_earned']}**개\n"
            f"- 처음 만난 날: {_format_date(user['first_seen_at'])}"
        ),
        inline=False,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return _format_other_line(_INTRO_OTHER_LINES, target_name), embed


async def render_admin_summary(user_id: int) -> tuple[str, discord.Embed]:
    """관리자 콘솔 `sh user stats` 전용 — 슬래시 커맨드처럼 카테고리를 하나씩 고르는
    대화형 흐름이 안 어울리는 텍스트 명령어라, 舊 /내정보(개편 전)가 한 임베드로
    보여주던 호감도/오늘 기록/전체 기록 3개 필드를 그대로 합쳐서 보여준다(가방·업적은
    원래도 별도 명령어였어서 이 요약엔 포함되지 않는다 — 필요하면 /니정보로 직접
    확인). guild 정보가 없는 컨텍스트라 순위는 항상 전체(글로벌) 기준만 계산한다."""
    user = await get_user(user_id)
    affection = user["affection"]
    nl_cap = levels.get_level_for_xp(user["total_xp"]).daily_nl_limit
    stats, global_rank, global_total = await asyncio.gather(
        ensure_daily_stats(user_id),
        get_rank(user_id, affection),
        count_total(),
    )

    embed = discord.Embed(title="유저 정보", color=EMBED_COLOR)

    global_percentile = compute_percentile(global_rank, global_total)
    embed.add_field(
        name="💕 호감도",
        value=(
            f"- 햄미의 호감도: **{affection}**\n"
            f"- 전체 호감도 순위: **{global_rank}**위 (상위 {global_percentile}%)\n​"
        ),
        inline=False,
    )
    embed.add_field(
        name="📋 전체 기록",
        value=(
            f"- 간식 준 횟수: **{user['total_snacks_given']}**\n"
            f"- 도와준 횟수: **{user['help_count']}**\n"
            f"- 대화한 횟수: **{user['chat_count']}**\n"
            f"- 획득한 동전: **{user['lifetime_coins_earned']}**개\n"
            f"- 처음 만난 날: {_format_date(user['first_seen_at'])}\n​"
        ),
        inline=False,
    )

    today = datetime.now(KST).date()
    dessert_fed_count = len(stats.get("dessert_fed_today") or {})
    help_me_event_total = get_help_me_event_count(today)
    embed.add_field(
        name="📅 오늘의 기록",
        value=(
            f"- 간식 준 횟수: **{dessert_fed_count}**/3\n"
            f"- 도움 횟수: **{stats['help_me_events_helped_today']}**/{help_me_event_total}\n"
            f"- 대화 횟수: **{stats['nl_count']}**/{nl_cap}\n"
            f"- 명령어 사용 횟수: **{stats['slash_count']}**\n"
            f"- 획득 호감: **{stats['daily_gain_natural']}**"
        ),
        inline=False,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return random.choice(_INTRO_LINES), embed


# /내정보·/니정보 카테고리 탭(2026-09-06, /자판기-리스트와 동일한 UI로 재개편) —
# ephemeral 선택 프롬프트를 없애고, 처음부터 공개(모두에게 보이는) 메시지로 기본
# 카테고리(정보)를 바로 보여준 뒤 버튼으로 같은 메시지 안에서 다른 카테고리로
# 전환한다. 2026-09-09 舊 "호감도" 탭을 "정보" 탭으로 확장(동전 요약 포함).
_CATEGORY_ORDER: tuple[str, ...] = ("info", "bag", "achievements", "today", "lifetime")
_CATEGORY_LABELS: dict[str, str] = {
    "info": "정보",
    "bag": "가방",
    "achievements": "업적",
    "today": "오늘 기록",
    "lifetime": "전체 기록",
}
_DEFAULT_CATEGORY = "info"


async def _render_category(
    kind: str, subject_id: int, *, target_name: str | None, guild: discord.Guild | None
) -> tuple[str, discord.Embed]:
    if kind == "info":
        return await _render_info(subject_id, target_name=target_name, guild=guild)
    if kind == "bag":
        return await bag_view.handle(subject_id, target_name=target_name, guild=guild)
    if kind == "achievements":
        return await achievements_view.handle(subject_id, target_name=target_name)
    if kind == "today":
        return await _render_today(subject_id, target_name=target_name)
    return await _render_lifetime(subject_id, target_name=target_name)


class _CategoryButton(discord.ui.Button):
    def __init__(self, kind: str, *, active: bool) -> None:
        # 활성=초록(success), 비활성=회색(secondary) — /자판기-리스트·/랭킹과 동일한 배색.
        style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary
        super().__init__(label=_CATEGORY_LABELS[kind], style=style)
        self._kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _InfoView = self.view
        if not await reject_if_wrong_invoker(interaction, view.user_id):
            return
        for child in view.children:
            if isinstance(child, _CategoryButton):
                child.style = (
                    discord.ButtonStyle.success if child is self else discord.ButtonStyle.secondary
                )
        # content는 안 건드린다 — /자판기-리스트·/랭킹과 동일한 이유(취침 중 대체 문구가
        # 카테고리 전환으로 되돌아가면 안 됨).
        _, embed = await _render_category(
            self._kind, view.subject_id, target_name=view.target_name, guild=view.guild
        )
        await interaction.response.edit_message(embed=embed, view=view)


class _InfoView(discord.ui.View):
    """공개(모두에게 보이는) 카테고리 탭 뷰 — 이 명령어를 실행한 사람(user_id)만 탭을
    바꿀 수 있다. 1분간 무클릭이면 버튼만 지운다(내용은 그대로 유지, /자판기-리스트와
    동일한 원칙). subject_id는 정보의 대상(본인 또는 /니정보의 상대방), user_id는
    명령어를 실행한 사람 — /니정보에서는 이 둘이 서로 다르다."""

    def __init__(
        self,
        user_id: int,
        subject_id: int,
        *,
        target_name: str | None,
        guild: discord.Guild | None,
    ) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.subject_id = subject_id
        self.target_name = target_name
        self.guild = guild
        self.message: discord.Message | None = None
        for kind in _CATEGORY_ORDER:
            self.add_item(_CategoryButton(kind, active=(kind == _DEFAULT_CATEGORY)))

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logging.exception("Failed to clear info category buttons on timeout")


async def handle_self(interaction: discord.Interaction) -> tuple[str, discord.Embed, discord.ui.View]:
    """/내정보 진입점 — 공개 응답으로 defer된 상태라고 가정."""
    text, embed = await _render_category(
        _DEFAULT_CATEGORY, interaction.user.id, target_name=None, guild=interaction.guild
    )
    view = _InfoView(interaction.user.id, interaction.user.id, target_name=None, guild=interaction.guild)
    return text, embed, view


async def handle_other(
    member: discord.Member, *, guild: discord.Guild, requester_id: int
) -> tuple[str, discord.Embed, discord.ui.View]:
    """/니정보 진입점 — 대상을 이미 찾은 뒤(command/intro.py::_resolve_target) 호출되며,
    호출부가 공개 응답으로 defer한 상태라고 가정. requester_id는 명령어를 실행한
    사람(카테고리 버튼 조작 권한자) — 조회 대상(member)과는 다르다."""
    text, embed = await _render_category(
        _DEFAULT_CATEGORY, member.id, target_name=member.display_name, guild=guild
    )
    view = _InfoView(requester_id, member.id, target_name=member.display_name, guild=guild)
    return text, embed, view
