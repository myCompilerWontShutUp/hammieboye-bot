import random
from datetime import datetime

import discord

from core.base import EMBED_COLOR
from core.korean import josa
from events.scheduler import KST, format_footer_time
from command.black_market_catalog import ITEMS as _BLACK_MARKET_ITEMS
from command.vending_catalog import ITEMS as _VENDING_ITEMS
from db.snacks import get_inventory

_EMPTY_BAG_LINE = "- 텅 비어있다"

# 가방을 3개 칸으로 분류한다(2026-09-11 사용자 지시) — /자판기·/암시장 두 카탈로그를
# 합쳐서(舊에는 vending_catalog만 봐서 암시장 간식·도구가 가방에 아예 안 보이던
# 사각지대였다) item.kind 기준으로 나눈다. "beverage"(vending_catalog)/"potion"
# (black_market_catalog)은 두 카탈로그가 서로 다른 Literal을 쓰지만 문자열 값만
# 보고 판정하므로 상관없다 — 지금은 둘 다 해당 kind의 실제 품목이 없어 "음료
# 바구니"가 항상 비어있지만, 나중에 그런 품목이 추가되면 자동으로 여기 들어간다.
# "기타 물품"은 간식도 음료도 아닌 나머지 전부(현재는 금서/햄미 일정표 같은
# "tool" 품목) — 화이트리스트가 아니라 else 분기라 새 kind가 생겨도 자동으로
# 어딘가에는 걸린다.
_ALL_ITEMS = _VENDING_ITEMS + _BLACK_MARKET_ITEMS
_BEVERAGE_KINDS = frozenset({"beverage", "potion"})

# 내가 나를 볼 때(/내가방) 전용. 2026-09-11 동전 부분이 "자산" 탭으로 옮겨지며
# 이 풀도 간식(만) 언급하도록 다시 썼다(舊 "동전이랑 간식" 표현 제거).
_INTRO_LINES = (
    "내 가방 열어볼게!! _(두근)_",
    "짜잔, 햄미 가방이야!! _(자랑)_",
    "가방 속 살짝 보여줄게!! _(수줍)_",
    "내가 모은 간식들 구경할래?? _(신남)_",
    "햄미 보물 가방 공개!! _(반짝)_",
    "가방 안엔 뭐가 있을까?? _(궁금)_",
    "내 간식 보따리 보여줄게!! _(뿌듯)_",
    "가방 지퍼 열어본다!! _(설렘)_",
    "이게 다 내가 모은 간식이야!! _(으쓱)_",
    "가방 탈탈 털어볼게!! _(장난)_",
    "내 간식 보관함 공개할게!! _(당당)_",
    "간식이 얼마나 모였을까?? _(기대)_",
    "가방 속 간식들 보여줄게!! _(들뜸)_",
    "짠, 이게 내 간식 창고야!! _(자신감)_",
    "가방 정리 겸 보여주는 거야!! _(뿌듯)_",
    "내가 모은 간식들!! _(신기)_",
    "가방 열어보니 이만큼 있었어!! _(놀람)_",
    "햄미 간식 사정 공개!! _(진지)_",
    "가방 구경하고 갈래?? _(호기심)_",
    "내 가방, 짜잔 공개!! _(활짝)_",
)

# 다른 사람을 볼 때(/니가방) 전용. info.py의 조사 처리 패턴을 그대로 따른다.
_INTRO_OTHER_LINES = (
    "{name}의 가방 열어볼게!! _(두근)_",
    "짜잔!! {name} 가방이야!! _(자랑)_",
    "{name}{의} 가방 속 살짝 보여줄게!! _(호기심)_",
    "{name}{이가} 모은 간식들 구경해볼까?? _(신남)_",
    "{name}의 보물 가방 공개!! _(반짝)_",
    "{name} 가방 안엔 뭐가 있을까?? _(궁금)_",
    "{name}의 간식 보따리 보여줄게!! _(뿌듯)_",
    "{name} 가방 지퍼 열어본다!! _(설렘)_",
    "이게 다 {name}{이가} 모은 간식이래!! _(으쓱)_",
    "{name} 가방 탈탈 털어볼게!! _(장난)_",
    "{name}의 간식 보관함 공개할게!! _(당당)_",
    "{name}{을를} 위해 가방을 열어볼게!! _(기대)_",
    "{name} 가방 속 간식들 보여줄게!! _(들뜸)_",
    "짠, 이게 {name}의 간식 창고야!! _(자신감)_",
    "{name} 가방 정리 겸 보여주는 거야!! _(뿌듯)_",
    "{name}{이가} 모은 간식들!! _(신기)_",
    "{name} 가방 열어보니 이만큼 있었어!! _(놀람)_",
    "{name}의 간식 사정 공개!! _(진지)_",
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
    2026-09-11부로 동전(보유량+순위)은 "자산" 탭(command/assets.py)으로 옮겨져
    이 탭은 인벤토리(간식/음료/기타)만 보여준다. guild 파라미터는 assets.handle()과
    시그니처를 맞추기 위해 그대로 남겨뒀지만(info.py::_render_category가 두 핸들러를
    동일한 방식으로 호출) 이 함수 안에서는 더 이상 쓰이지 않는다."""
    is_self = target_name is None

    inventory = await get_inventory(user_id)
    qty_by_id = {row["snack_id"]: row["quantity"] for row in inventory}

    title = "나의 가방" if is_self else f"{target_name}의 가방"
    embed = discord.Embed(title=title, color=EMBED_COLOR)

    snack_lines: list[str] = []
    beverage_lines: list[str] = []
    other_lines: list[str] = []
    for item in _ALL_ITEMS:
        if item.id not in qty_by_id:
            continue
        line = f"- {item.name} x {qty_by_id[item.id]}"
        if item.kind == "snack":
            snack_lines.append(line)
        elif item.kind in _BEVERAGE_KINDS:
            beverage_lines.append(line)
        else:
            other_lines.append(line)

    who = "" if is_self else f"{target_name}의 "
    embed.add_field(
        name=f"🍪 {who}간식 보따리" if who else "🍪 간식 보따리",
        value="\n".join(snack_lines) if snack_lines else _EMPTY_BAG_LINE,
        inline=False,
    )
    embed.add_field(
        name=f"🥤 {who}음료 바구니" if who else "🥤 음료 바구니",
        value="\n".join(beverage_lines) if beverage_lines else _EMPTY_BAG_LINE,
        inline=False,
    )
    embed.add_field(
        name=f"📦 {who}기타 물품" if who else "📦 기타 물품",
        value="\n".join(other_lines) if other_lines else _EMPTY_BAG_LINE,
        inline=False,
    )

    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    if is_self:
        return random.choice(_INTRO_LINES), embed
    return _format_other_line(target_name), embed
