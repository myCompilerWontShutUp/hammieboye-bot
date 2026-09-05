import asyncio
import random
from datetime import datetime

import discord

from core.base import EMBED_COLOR, EphemeralAutoDeleteView, SYSTEM_EMBED_COLOR
from core.korean import josa
from events.scheduler import KST, format_footer_time
from events.special_days import get_help_me_event_count
from db.daily_stats import ensure_nl_cap
from db.ranking import compute_percentile, count_total, get_rank
from db.users import get_user
import command.achievements as achievements_view
import command.bag as bag_view

# add_affection() RPC(SQL.md/supabase/schema.sql)에 하드코딩된 일일 획득 상한과 반드시
# 같은 값을 유지해야 한다 — Python 쪽엔 이 값을 직접 참조할 데가 없어(SQL 함수 안에만
# 있음) 표시용으로 여기 따로 상수를 둔다.
_DAILY_AFFECTION_CAP = 100

# /내정보·/니정보 통합(2026-09-06) — 카테고리 선택 프롬프트(ephemeral, 본인만 봄) 전용
# 인트로 풀. 결과 임베드 자체의 인트로는 카테고리별로 아래에서 재사용한다(가방은
# command/bag.py, 업적은 command/achievements.py의 기존 풀을 그대로 씀 — 호감도/오늘
# 기록/전체 기록 3개는 舊 /내정보가 원래 한 임베드로 보여주던 것을 쪼갠 것이라, 새
# 풀을 따로 안 만들고 아래 _INTRO_LINES/_INTRO_OTHER_LINES를 셋이 공유한다).
_CATEGORY_PROMPT_LINES = (
    "뭐 보여주까?? _(궁금)_",
    "어떤 걸 볼래?? _(호기심)_",
    "뭐가 궁금해?? 골라봐!! _(신남)_",
    "어디부터 보여줄까?? _(설렘)_",
    "뭘 확인하고 시퍼?? _(찡긋)_",
    "골라봐!! 뭐든 보여줄게!! _(당당)_",
    "어떤 정보가 필요해?? _(궁금)_",
    "뭐부터 볼까?? 골라줘!! _(기대)_",
    "궁금한 거 있으면 골라봐!! _(방긋)_",
    "뭐 보고 시퍼?? _(호기심)_",
    "어떤 걸 확인해볼래?? _(두근)_",
    "뭐든 물어봐!! 골라줘!! _(자신감)_",
    "어느 쪽이 궁금해?? _(갸웃)_",
    "뭘 열어볼까?? _(설렘)_",
    "골라주면 바로 보여줄게!! _(뿌듯)_",
    "뭐가 제일 궁금해?? _(호기심)_",
    "어떤 기록을 볼래?? _(진지)_",
    "뭐 확인하러 왔어?? 골라봐!! _(신남)_",
    "뭘 보고 싶은지 알려줘!! _(기대)_",
    "골라봐, 다 보여줄 수 이써!! _(당당)_",
)
_CATEGORY_PROMPT_OTHER_LINES = (
    "{name}에 대해 뭘 알고시퍼?? _(궁금)_",
    "{name}의 뭐가 궁금해?? _(호기심)_",
    "{name} 정보 중 뭘 볼래?? _(신남)_",
    "{name}에 대해 어디부터 보여줄까?? _(설렘)_",
    "{name}의 뭘 확인하고 시퍼?? _(찡긋)_",
    "골라봐!! {name}에 대해 뭐든 보여줄게!! _(당당)_",
    "{name}의 어떤 정보가 필요해?? _(궁금)_",
    "{name} 정보, 뭐부터 볼까?? _(기대)_",
    "{name}에 대해 궁금한 거 골라봐!! _(방긋)_",
    "{name}의 뭐가 보고 시퍼?? _(호기심)_",
    "{name}의 어떤 걸 확인해볼래?? _(두근)_",
    "{name}에 대해 뭐든 물어봐!! _(자신감)_",
    "{name}의 어느 쪽이 궁금해?? _(갸웃)_",
    "{name}의 뭘 열어볼까?? _(설렘)_",
    "골라주면 {name} 정보를 바로 보여줄게!! _(뿌듯)_",
    "{name}의 뭐가 제일 궁금해?? _(호기심)_",
    "{name}의 어떤 기록을 볼래?? _(진지)_",
    "{name} 정보 중 뭘 확인하러 왔어?? _(신남)_",
    "{name}에 대해 뭘 보고 싶은지 알려줘!! _(기대)_",
    "골라봐, {name}에 대해 다 보여줄 수 이써!! _(당당)_",
)

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


# 카테고리 설명(시스템 메시지 색) — 5개 카테고리를 간단히 소개한다. 정책 고지 성격이라
# 무작위 풀 없이 고정 텍스트 하나만 쓴다(dessert_time/help_me_event의 안내 임베드와
# 동일한 원칙).
_CATEGORY_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("호감도", "햄미의 호감도를 보여줍니다."),
    ("가방", "보유한 동전과 간식을 보여줍니다."),
    ("업적", "획득한 업적을 보여줍니다."),
    ("오늘 기록", "오늘 하루의 활동 기록을 보여줍니다."),
    ("전체 기록", "지금까지의 전체 활동 기록을 보여줍니다."),
)


def _build_category_embed() -> discord.Embed:
    embed = discord.Embed(title="📋 무엇을 확인할까?", color=SYSTEM_EMBED_COLOR)
    embed.description = "\n".join(f"- {label}: {desc}" for label, desc in _CATEGORY_DESCRIPTIONS)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


async def _render_affection(
    user_id: int, *, target_name: str | None, guild: discord.Guild | None
) -> tuple[str, discord.Embed]:
    """舊 /내정보의 "💕 햄미와 나" 필드(호감도+서버/전체 순위)."""
    is_self = target_name is None
    user = await get_user(user_id)
    affection = user["affection"]
    member_ids = [m.id for m in guild.members if not m.bot] if guild is not None else None

    if member_ids is not None:
        global_rank, global_total, guild_rank, guild_total = await asyncio.gather(
            get_rank(user_id, affection),
            count_total(),
            get_rank(user_id, affection, member_ids),
            count_total(member_ids),
        )
    else:
        global_rank, global_total = await asyncio.gather(get_rank(user_id, affection), count_total())
        guild_rank = None
        guild_total = None

    title = "나의 호감도" if is_self else f"{target_name}의 호감도"
    heart_field_name = "💕 햄미와 나" if is_self else f"💕 햄미와 {target_name}"
    embed = discord.Embed(title=title, color=EMBED_COLOR)

    heart_lines = [f"- 햄미의 호감도: **{affection}**"]
    if guild_rank is not None:
        guild_percentile = compute_percentile(guild_rank, guild_total)
        heart_lines.append(f"- 서버 호감도 순위: **{guild_rank}**위 (상위 {guild_percentile}%)")
    global_percentile = compute_percentile(global_rank, global_total)
    heart_lines.append(f"- 전체 호감도 순위: **{global_rank}**위 (상위 {global_percentile}%)")
    embed.add_field(name=heart_field_name, value="\n".join(heart_lines), inline=False)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return _format_other_line(_INTRO_OTHER_LINES, target_name), embed


async def _render_today(user_id: int, *, target_name: str | None) -> tuple[str, discord.Embed]:
    """舊 /내정보의 "📅 오늘의 기록" 필드."""
    is_self = target_name is None
    user = await get_user(user_id)
    stats = await ensure_nl_cap(user_id, user["affection"])

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
            f"- 대화 횟수: **{stats['nl_count']}**/{stats['nl_cap']}\n"
            f"- 획득 호감: **{stats['daily_gain_natural']}**/{_DAILY_AFFECTION_CAP}"
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
    stats, global_rank, global_total = await asyncio.gather(
        ensure_nl_cap(user_id, affection),
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
            f"- 대화 횟수: **{stats['nl_count']}**/{stats['nl_cap']}\n"
            f"- 획득 호감: **{stats['daily_gain_natural']}**/{_DAILY_AFFECTION_CAP}"
        ),
        inline=False,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return random.choice(_INTRO_LINES), embed


class _InfoCategoryView(EphemeralAutoDeleteView):
    """/내정보·/니정보 통합(2026-09-06) 진입점 — ephemeral 전용이라 "다른 사람이
    눌렀을 때" 처리는 애초에 불필요하다. 카테고리 버튼을 누르면 결과를 답장이 아니라
    새 공개 메시지로 보내고(모두가 볼 수 있게), 이 ephemeral 프롬프트 자체는 지우지
    않는다 — 계속 다른 카테고리를 골라볼 수 있게(60초 무클릭 시에만 자동 삭제)."""

    def __init__(
        self, subject_id: int, *, target_name: str | None, guild: discord.Guild | None
    ) -> None:
        super().__init__(timeout=60)
        self.subject_id = subject_id
        self.target_name = target_name
        self.guild = guild

    async def _show(self, interaction: discord.Interaction, text: str, embed: discord.Embed) -> None:
        self.bump()
        await interaction.response.send_message(content=text, embed=embed, ephemeral=False)

    @discord.ui.button(label="호감도", style=discord.ButtonStyle.primary)
    async def affection_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        text, embed = await _render_affection(self.subject_id, target_name=self.target_name, guild=self.guild)
        await self._show(interaction, text, embed)

    @discord.ui.button(label="가방", style=discord.ButtonStyle.primary)
    async def bag_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        text, embed = await bag_view.handle(self.subject_id, target_name=self.target_name, guild=self.guild)
        await self._show(interaction, text, embed)

    @discord.ui.button(label="업적", style=discord.ButtonStyle.primary)
    async def achievements_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        text, embed = await achievements_view.handle(self.subject_id, target_name=self.target_name)
        await self._show(interaction, text, embed)

    @discord.ui.button(label="오늘 기록", style=discord.ButtonStyle.primary)
    async def today_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        text, embed = await _render_today(self.subject_id, target_name=self.target_name)
        await self._show(interaction, text, embed)

    @discord.ui.button(label="전체 기록", style=discord.ButtonStyle.primary)
    async def lifetime_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        text, embed = await _render_lifetime(self.subject_id, target_name=self.target_name)
        await self._show(interaction, text, embed)


async def handle_self(interaction: discord.Interaction) -> tuple[str, discord.Embed, discord.ui.View]:
    """/내정보 진입점 — 이미 ephemeral로 defer된 상태라고 가정."""
    view = _InfoCategoryView(interaction.user.id, target_name=None, guild=interaction.guild)
    return random.choice(_CATEGORY_PROMPT_LINES), _build_category_embed(), view


async def handle_other(
    member: discord.Member, *, guild: discord.Guild
) -> tuple[str, discord.Embed, discord.ui.View]:
    """/니정보 진입점 — 대상을 이미 찾은 뒤(command/intro.py::_resolve_target) 호출되며,
    호출부가 ephemeral로 defer한 상태라고 가정."""
    view = _InfoCategoryView(member.id, target_name=member.display_name, guild=guild)
    text = _format_other_line(_CATEGORY_PROMPT_OTHER_LINES, member.display_name)
    return text, _build_category_embed(), view
