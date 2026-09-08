from datetime import datetime

import discord

import command.slot as slot
from command.black_market_catalog import SNACK_ITEMS as _BLACK_MARKET_SNACKS
from core.base import SYSTEM_EMBED_COLOR
from events.scheduler import KST, format_footer_time

# `/봇정보-확률공개` — 확률형 콘텐츠(슬롯머신·내기·암시장 확률형 간식)의 확률을 전부
# 공개하는 상시 조회용 명령어(2026-09-09 신규). `/수집항목`과 동일한 원칙으로 완전히
# 독립적인 정보 명령어라 어떤 가입/등록 절차와도 무관하고, 메인/서브 채널 지정과
# 무관하게 항상 어느 채널에서나 동작한다(core/client.py::_ONBOARDING_COMMAND_NAMES).
# 업적(특히 전설 등급)은 "발견의 재미" 원칙(§15)이 적용되는 별개 보상 체계라 여기
# 공개 대상에서 제외한다 — 슬롯머신 배율/확률처럼 순수하게 결제·확률에 관한 정보만
# 다룬다.

_TITLE = "📊 확률 공개"
_INTRO = "햄미가 제공하는 확률형 콘텐츠의 확률을 모두 공개합니다."

_ODD_EVEN_PROBABILITY_TEXT = "승리 확률 50%, 패배 확률 50%."
_RPS_PROBABILITY_TEXT = "승리 확률 약 33.3%, 무승부 확률 약 33.3%, 패배 확률 약 33.3%."


def _slot_field_value() -> str:
    cell_count = len(slot.SYMBOLS)
    return (
        slot.probability_summary()
        + f"\n\n한 줄(가로·세로·대각선)이 완성될 확률: 약 {100 / (cell_count ** 2):.2f}%\n"
        f"최종 배율은 최대 x{slot.MAX_MULTIPLIER}로 제한됩니다."
    )


def _black_market_field_value() -> str:
    lines = []
    for item in _BLACK_MARKET_SNACKS:
        if item.double_or_halve:
            good, bad = "호감도가 현재의 2배로 증가", "호감도가 현재의 절반으로 감소"
        else:
            good, bad = f"호감도 +{item.good_delta}", f"호감도 {item.bad_delta}"
        lines.append(f"**{item.name}** — 좋은 결과 50%({good}) / 나쁜 결과 50%({bad})")
    return "\n\n".join(lines)


def build_embed() -> discord.Embed:
    embed = discord.Embed(title=_TITLE, description=_INTRO, color=SYSTEM_EMBED_COLOR)
    embed.add_field(name="🎰 도박 — 슬롯머신", value=_slot_field_value(), inline=False)
    embed.add_field(name="🪙 내기 — 홀짝", value=_ODD_EVEN_PROBABILITY_TEXT, inline=False)
    embed.add_field(name="✂️ 내기 — 가위바위보", value=_RPS_PROBABILITY_TEXT, inline=False)
    embed.add_field(name="🌙 암시장 — 확률형 간식", value=_black_market_field_value(), inline=False)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed
