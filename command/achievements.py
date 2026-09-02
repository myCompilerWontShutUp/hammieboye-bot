import random
from datetime import datetime

import discord

import achievements
from core.base import EMBED_COLOR
from events.scheduler import KST, format_footer_time
from db.achievements import get_earned

# 정책 고지 성격이라 고정 문구 1개 — 특정 업적을 짚어 물어보면 RAG 문서가 알려준다는
# "발견의 재미" 원칙과 이어지도록 자연어 질문을 유도한다.
_DESCRIPTION = "어떻게 얻는지 궁금하면 햄미에게 물어봐!! 내가 다 알려줄게!!"

_INTRO_LINES = (
    "내 업적 자랑해볼게!! _(으쓱)_",
    "짜잔!! 내 업적 목록이야!! _(뿌듯)_",
    "내가 모은 업적들 보여줄게!! _(신남)_",
    "햄미 업적판 열어본다!! _(기대)_",
    "이만큼 모아써!! 내 업적이야!! _(자랑)_",
    "내 업적 컬렉션 공개!! _(들뜸)_",
    "짠, 내 도전 기록이야!! _(당당)_",
    "업적 창고 열어볼게!! _(호기심)_",
    "내가 이만큼 해냈다구!! _(뿌듯)_",
    "업적 자랑 타임!! _(신남)_",
    "내 트로피들 구경할래?? _(반짝)_",
    "이게 다 내 업적이야!! _(으쓱)_",
    "업적판 공개합니다!! _(짠)_",
    "내가 모은 것들 보여줄게!! _(설렘)_",
    "짜잔, 업적 목록 나간다!! _(활짝)_",
    "내 도전 결과 확인해볼래?? _(궁금)_",
    "업적 자랑 좀 할게!! _(뿌듯)_",
    "내 업적들 한눈에 보여줄게!! _(자신감)_",
    "이만큼 모았어!! 놀랐지?? _(장난)_",
    "업적 리스트 대공개!! _(신남)_",
)

# {name} 바로 뒤에 조사가 붙는 자리는 전부 "의"(받침 무관 불변)로만 구성해서, 별도
# 조사 계산 없이도 어떤 이름이 와도 안전하다.
_INTRO_OTHER_LINES = (
    "{name}의 업적 보여줄게!! _(으쓱)_",
    "짜잔!! {name} 업적 목록이야!! _(자랑)_",
    "{name} 업적판 열어볼게!! _(신남)_",
    "{name}의 도전 기록이야!! _(기대)_",
    "{name} 업적 컬렉션 공개!! _(들뜸)_",
    "짠, {name}의 업적이야!! _(당당)_",
    "{name} 업적 창고 열어볼게!! _(호기심)_",
    "{name} 업적, 이만큼 모아써!! _(뿌듯)_",
    "{name} 업적 자랑 타임!! _(신남)_",
    "{name}의 트로피들 구경할래?? _(반짝)_",
    "이게 다 {name}의 업적이야!! _(으쓱)_",
    "{name} 업적판 공개합니다!! _(짠)_",
    "{name}의 업적, 짜잔!! _(설렘)_",
    "짜잔, {name} 업적 목록 나간다!! _(활짝)_",
    "{name}의 도전 결과 확인해볼래?? _(궁금)_",
    "{name} 업적 자랑 좀 할게!! _(뿌듯)_",
    "{name}의 업적들 한눈에 보여줄게!! _(자신감)_",
    "{name} 업적, 이만큼 모아써!! 놀랐지?? _(장난)_",
    "{name} 업적 리스트 대공개!! _(신남)_",
    "{name}의 업적을 소개할게!! _(설렘)_",
)

# 미획득 전설 등급은 이름을 숨겨 챌린지 성격을 유지한다.
_HIDDEN_LEGENDARY = "**__[👑]__** ???"
_NO_EARNED_LINE = "- 아직 획득한 업적이 없어"
_ALL_EARNED_LINE = "- 전부 다 모았어!!"


def _format_date(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).astimezone(KST).strftime("%Y. %m. %d")


def _earned_lines(earned: list[dict]) -> list[str]:
    """전설 등급을 항상 최상단에, 그 안에서는 획득 순으로. get_earned()가 이미 획득
    시각 오름차순으로 반환하므로 필터링만 해도 각 그룹 내 순서가 보존된다."""
    legendary = [row for row in earned if achievements.REGISTRY[row["achievement_id"]].RARITY == achievements.LEGENDARY]
    normal = [row for row in earned if achievements.REGISTRY[row["achievement_id"]].RARITY != achievements.LEGENDARY]
    lines = []
    for row in legendary + normal:
        module = achievements.REGISTRY[row["achievement_id"]]
        lines.append(f"- {achievements.format_name(module)} ({_format_date(row['earned_at'])})")
    return lines or [_NO_EARNED_LINE]


def _unearned_lines(earned_ids: set[str]) -> list[str]:
    """등급 상관없이 업적이 만들어진 순서로 정렬한다. 일반은 이름 그대로, 전설은 숨긴다."""
    lines = []
    for module in achievements.REGISTRY.values():
        if module.ID in earned_ids:
            continue
        if module.RARITY == achievements.LEGENDARY:
            lines.append(f"- {_HIDDEN_LEGENDARY}")
        else:
            lines.append(f"- {module.NAME}")
    return lines or [_ALL_EARNED_LINE]


async def handle(user_id: int, *, target_name: str | None = None) -> tuple[str, discord.Embed]:
    """target_name이 None이면 본인(/내업적) 조회, 아니면 그 이름의 다른 사람(/니업적) 조회."""
    is_self = target_name is None

    earned = await get_earned(user_id)
    earned_ids = {row["achievement_id"] for row in earned}

    title = "나의 업적" if is_self else f"{target_name}의 업적"
    embed = discord.Embed(title=title, description=_DESCRIPTION, color=EMBED_COLOR)

    embed.add_field(
        name=f"🏆 획득한 업적 ({len(earned)}/{achievements.TOTAL_COUNT})",
        value="\n".join(_earned_lines(earned)) + "\n​",
        inline=False,
    )
    embed.add_field(
        name="🔒 아직 못 얻은 업적",
        value="\n".join(_unearned_lines(earned_ids)),
        inline=False,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return random.choice(_INTRO_OTHER_LINES).format(name=target_name), embed
