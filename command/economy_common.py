import random

import discord
from discord import app_commands

from db.users import get_user

# /자판기·/자판기-리스트 전용 색(하늘색) — command/info.py 등의 EMBED_COLOR(연주황색)와
# 구분해 자판기만의 색으로 쓴다.
VENDING_EMBED_COLOR = 0x87CEEB

# /내기-*·/슬롯머신 버튼 게임 공통 타임아웃(초).
TIMEOUT_SECONDS = 60

# 다른 사람이 남의 버튼 게임(내기/슬롯머신)을 눌렀을 때(ephemeral 거부) — 이 코드베이스에
# 선례 없는 패턴이라 다른 20줄 풀들과 통일된 스타일로 새로 작성.
NOT_YOUR_GAME_LINES = (
    "어라, 이건 너랑 하는 내기가 아니야!! _(단호)_",
    "이 내기는 다른 사람 거야!! _(갸웃)_",
    "네 차례 아니야!! 눌러도 소용없어!! _(웃음)_",
    "잠깐, 이건 다른 사람 내기라구!! _(당황)_",
    "네가 배팅한 거 아니잖아!! _(단호)_",
    "이 버튼은 너를 위한 게 아니야!! _(장난)_",
    "다른 사람 내기에 끼어들면 안 돼!! _(단호)_",
    "이건 남의 승부야!! 구경만 해줘!! _(웃음)_",
    "네 내기가 아니라서 못 눌러!! _(갸웃)_",
    "잠깐만, 이건 다른 사람 게임이야!! _(놀람)_",
    "이 승부엔 너 없어!! _(단호)_",
    "남의 배팅에 손대면 안 되지!! _(장난)_",
    "이건 다른 주인님의 내기야!! _(단호)_",
    "네가 건 게 아니잖아?? _(의아)_",
    "이 판은 다른 사람 차지야!! _(웃음)_",
    "구경은 좋지만 버튼은 안 돼!! _(장난)_",
    "이 내기 주인공은 따로 있어!! _(단호)_",
    "네 순서가 아니야, 기다려줘!! _(갸웃)_",
    "이건 다른 사람이 배팅한 판이야!! _(당황)_",
    "미안하지만 이건 네 내기가 아니야!! _(미안)_",
)


async def reject_if_wrong_user(interaction: discord.Interaction, user_id: int) -> bool:
    """True면 계속 진행, False면 이미 거절 응답을 보냈으니 콜백은 그대로 return해야 한다."""
    if interaction.user.id != user_id:
        await interaction.response.send_message(random.choice(NOT_YOUR_GAME_LINES), ephemeral=True)
        return False
    return True


async def reject_if_already_resolved(view: discord.ui.View, interaction: discord.Interaction) -> bool:
    """True면 계속 진행. 연타나 중복 이벤트로 이미 끝난(또는 이미 처리 중인) 판에 대한
    추가 클릭이 도착했으면 조용히 defer만 하고 False를 반환한다 — 안 그러면 같은 클릭이
    보상을 두 번 지급해버릴 수 있다."""
    if view.is_finished():
        if not interaction.response.is_done():
            await interaction.response.defer()
        return False
    return True

# 슬롯머신 최대 배율(세븐 8라인 동시 완성 = 77^8 = 1,235,736,291,547,681)을 곱해도
# Postgres bigint 상한(9,223,372,036,854,775,807, 약 7,463배 여유)을 넘지 않도록
# 배팅액 자체에 안전 상한을 둔다. /내기-*·/슬롯머신이 전부 이 값을 공유
# (app_commands.Range로 강제).
MAX_BET = 1_000


async def autocomplete_동전개수(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """배팅/스핀 금액 입력칸에 호출한 본인의 현재 보유 동전을 참고용 선택지로 띄워준다.
    슬래시 커맨드 파라미터 설명(app_commands.describe)은 디스코드가 인터랙션 컨텍스트
    없이(모두에게 똑같이) 보여주는 고정 텍스트라 개인화가 불가능하다 — 자동완성만
    유일하게 "지금 이걸 치는 사람이 누구인지" 알고 응답할 수 있어서, 개인화된 잔액
    표시는 이 경로로만 가능하다. /내기-*·/슬롯머신이 전부 공유."""
    user = await get_user(interaction.user.id)
    if user is None or user["coins"] < 1:
        return []
    balance = min(user["coins"], MAX_BET)
    return [app_commands.Choice(name=f"현재 보유: {balance}개", value=balance)]


# 동전 지급이 max_coins에 걸려 요청량보다 적게 들어갔을 때(/동전·/내기-*·/슬룻머신
# 공통) 답변 끝에 덧붙이는 안내 — 용량을 늘리라고 권하되 강제하지는 않는다.
_CAPACITY_ADVICE_LINES = (
    "동전 지갑이 꽉 찼나봐!! 자판기에서 동전 지갑이라도 사 와!! _(권유)_",
    "이런, 동전이 넘쳐버려써!! 용량 좀 늘리고 오는 게 어때?? _(안타까움)_",
    "동전 자리가 모자라!! 자판기에서 지갑 하나 사면 더 담을 수 있어!! _(추천)_",
    "헉, 동전이 흘러넘쳐써!! 저금통 하나 장만하는 거 어때?? _(권유)_",
    "지갑이 작아서 다 못 담았어!! 용량 업그레이드 한번 생각해봐!! _(아쉬움)_",
    "동전이 자꾸 넘쳐써... 자판기 들러서 용량 좀 늘려조!! _(칭얼)_",
    "이만큼은 다 못 넣었어!! 큰 지갑이 있으면 좋을 텐데!! _(안타까움)_",
    "동전 자리가 부족해!! 돼지 저금통 어때?? _(추천)_",
    "다 담기엔 지갑이 좁아써!! 자판기에서 업그레이드 해봐!! _(권유)_",
    "아깝게 흘려버려써!! 용량 늘리면 다음엔 다 받을 수 있어!! _(속상)_",
    "지갑 용량 좀 늘리고 오면 더 챙길 수 있을 텐데!! _(아쉬움)_",
    "동전이 넘쳐서 못 받은 게 있어!! 자판기 한번 들러봐!! _(권유)_",
    "이 정도면 지갑 업그레이드 할 때 된 것 같아!! _(넌지시)_",
    "동전 자리 모자란 거 봐써!! 저금통 하나 사는 거 추천이야!! _(추천)_",
    "다 못 받아서 아쉬워!! 용량 늘리면 다음엔 문제없을 거야!! _(속상)_",
    "지갑이 작아서 넘쳐버려써!! 자판기에서 큰 걸로 바꿔봐!! _(권유)_",
    "동전 자리 부족한 거 눈치챘어?? 자판기 좀 들러조!! _(넌지시)_",
    "이만큼 흘려버리다니!! 용량부터 늘리고 오는 게 좋겠어!! _(안타까움)_",
    "지갑 용량이 딱 부족했어!! 자판기에서 채워볼래?? _(권유)_",
    "다음엔 다 받고 싶으면 지갑부터 키우고 오자!! _(제안)_",
)


# 잔액 부족 안내 — /자판기·/내기-*·/슬롯머신이 전부 공유(다들 spend_coins 실패 시
# 이 풀에서 하나 골라 그대로 응답한다).
INSUFFICIENT_FUNDS_LINES = (
    "어라, 동전이 모자라!! 좀 더 모아서 와줄래?? _(아쉬움)_",
    "동전이 부족해!! /동전으로 더 모아보자!! _(속상)_",
    "앗, 그만큼 동전이 없어!! 조금만 더 모아줘!! _(미안)_",
    "동전이 모자라써!! 다음에 다시 와줄래?? _(아쉬움)_",
    "이런, 잔액이 부족해!! 더 모아서 다시 와줘!! _(속상)_",
)


def maybe_append_capacity_advice(text: str, requested: int, result: dict) -> str:
    """add_coins 결과가 max_coins에 걸려 요청량보다 적게 지급됐으면 안내 문구를
    덧붙인다 — /동전·/내기-*·/슬룻머신이 전부 이 헬퍼 하나를 공유한다."""
    if result["applied_amount"] < requested:
        return f"{text}\n{random.choice(_CAPACITY_ADVICE_LINES)}"
    return text


def _display_width(text: str) -> int:
    """코드블록(모노스페이스) 기준 표시 폭 — 한글/전각 문자는 Discord 코드블록 폰트에서
    영문/숫자의 2배 폭으로 렌더링되므로 2, 그 외는 1로 계산해 표 정렬에 쓴다."""
    width = 0
    for ch in text:
        code = ord(ch)
        if (
            0x1100 <= code <= 0x115F  # 한글 자모
            or 0x2E80 <= code <= 0xA4CF  # CJK 부수~
            or 0xAC00 <= code <= 0xD7A3  # 한글 음절
            or 0xF900 <= code <= 0xFAFF  # CJK 호환 한자
            or 0xFF00 <= code <= 0xFF60  # 전각 형태
            or 0xFFE0 <= code <= 0xFFE6
        ):
            width += 2
        else:
            width += 1
    return width


def format_table(
    header: tuple[str, ...], rows: list[tuple[str, ...]], *, right_align: tuple[int, ...] = ()
) -> str:
    """헤더+행을 코드블록 표로 렌더링 — `/자판기-리스트`처럼 여러 줄을 표 형태로 보여줘야
    하는 곳에서 공유한다. `_display_width`로 한글 폭을 보정해 열을 맞추고,
    `right_align`(열 인덱스)에 지정된 열만 오른쪽 정렬(가격처럼 자릿수가 다른 숫자용)."""
    all_rows = [header, *rows]
    widths = [max(_display_width(row[i]) for row in all_rows) for i in range(len(header))]

    def pad(cell: str, width: int, align_right: bool) -> str:
        filler = " " * max(0, width - _display_width(cell))
        return filler + cell if align_right else cell + filler

    def render(row: tuple[str, ...]) -> str:
        return "  ".join(pad(cell, widths[i], i in right_align) for i, cell in enumerate(row))

    lines = [render(header), "-" * (sum(widths) + 2 * (len(widths) - 1))]
    lines.extend(render(row) for row in rows)
    return "```\n" + "\n".join(lines) + "\n```"


def format_coin_notice(delta: int, new_coins: int) -> str:
    """동전 변화량 알림 — format_affection_notice(db/affection.py)와 동일한 원칙(델타+
    현재 잔액)을 동전에 적용한 버전. /동전·/내기-*·/슬롯머신이 공유. delta==0이면
    빈 문자열(호출부가 그냥 이어 붙이면 되게)."""
    if delta == 0:
        return ""
    sign = "+" if delta > 0 else ""
    return f"\n🪙 동전 {sign}{delta} (현재 {new_coins})"
