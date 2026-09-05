import logging
import random
from datetime import datetime
from typing import Awaitable, Callable

import discord

from core.base import EphemeralAutoDeleteView
from core.korean import josa
from db.users import get_user
from events.scheduler import KST, format_footer_time

# /자판기·/자판기-리스트 전용 색(하늘색) — command/info.py 등의 EMBED_COLOR(연주황색)와
# 구분해 자판기만의 색으로 쓴다.
VENDING_EMBED_COLOR = 0x87CEEB

# /내기·/내기-규칙·/도박·/도박-규칙(및 그 안의 모든 게임 뷰) 전용 색(밝은 노란색,
# 2026-09-06 舊 command/slot.py::_SLOT_EMBED_COLOR를 여기로 옮기고 이름을 바꿔
# 도박 도메인 전체가 공유하게 함 — 자판기와는 다른 도메인이라 색을 분리한다).
GAMBLING_EMBED_COLOR = 0xFFEB3B

# /내기·/도박 버튼 게임 공통 타임아웃(초).
TIMEOUT_SECONDS = 60

# 다른 사람이 남의 버튼 게임(내기/도박)을 눌렀을 때(ephemeral 거부) — 이 코드베이스에
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


async def reject_if_already_resolved(view: discord.ui.View, interaction: discord.Interaction) -> bool:
    """True면 계속 진행. 연타나 중복 이벤트로 이미 끝난(또는 이미 처리 중인) 판에 대한
    추가 클릭이 도착했으면 조용히 defer만 하고 False를 반환한다 — 안 그러면 같은 클릭이
    보상을 두 번 지급해버릴 수 있다."""
    if view.is_finished():
        if not interaction.response.is_done():
            await interaction.response.defer()
        return False
    return True

# 슬롯머신(/도박) 최대 배율(세븐 8라인 동시 완성 = 77^8 = 1,235,736,291,547,681)을
# 곱해도 Postgres bigint 상한(9,223,372,036,854,775,807, 약 7,463배 여유)을 넘지
# 않도록 배팅액 자체에 안전 상한을 둔다. /내기·/도박의 BetAmountModal이 이 값으로
# 직접 범위 검증을 한다 — 슬래시 파라미터가 아니라 모달 입력이라 app_commands.Range는
# 안 쓴다.
MAX_BET = 1_000


# "다시하기" 버튼은 게임 자체(60초)보다 훨씬 짧게 준다 — 이미 한 판을 끝낸 뒤라 오래
# 붙잡아둘 이유가 없다. /내기·/도박이 공유.
REPLAY_TIMEOUT_SECONDS = 10

INVALID_AMOUNT_RESPONSE = f"1~{MAX_BET} 사이의 숫자로 적어줘!! _(갸웃)_"

# reject_if_wrong_user_with_cta의 미가입자용 안내 — 가입자용 안내는 호출부마다 자기
# 커맨드 이름("/내기"/"/도박")을 끼워 넣어야 해서 own_command 인자로 매번 조립한다.
_CTA_UNREGISTERED = "너도 /가입하면 함께 즐길 수 있어!!"


async def reject_if_wrong_user_with_cta(
    interaction: discord.Interaction, user_id: int, own_command: str
) -> bool:
    """True면 계속 진행. 공개 메시지(선택 버튼/다시하기)는 누구나 볼 수 있어서, 주인이
    아닌 사람이 눌렀을 때 기존 거절 문구에 이어 그 사람의 가입 여부에 맞는 안내를
    덧붙인다 — 가입자는 own_command로 직접 해보라고, 미가입자는 /가입부터 하라고.
    /내기·/도박이 공유(own_command만 서로 다름)."""
    if interaction.user.id == user_id:
        return True
    clicker = await get_user(interaction.user.id)
    cta = (
        f"너도 {own_command}{josa(own_command, '으로', '로')} 직접 해볼 수 있어!!"
        if (clicker is not None and clicker["consent_given"])
        else _CTA_UNREGISTERED
    )
    message = f"{random.choice(NOT_YOUR_GAME_LINES)}\n{cta}"
    await interaction.response.send_message(message, ephemeral=True)
    return False


class BetAmountModal(discord.ui.Modal):
    """배팅 금액 입력 전용 모달 — 게임 종류 선택 버튼과 "다시하기" 버튼이 공유한다
    (/내기·/도박 공통). on_valid(interaction, amount)는 검증을 통과한 정수 금액을 받아
    실제로 판을 시작하는 콜백이다."""

    def __init__(self, *, balance: int, on_valid: Callable[[discord.Interaction, int], Awaitable[None]]) -> None:
        super().__init__(title="배팅 금액 입력")
        self._on_valid = on_valid
        # TextInput(label=...)는 discord.py 2.6부터 deprecated — 대신 Label로 감싼다
        # (모달 전용 최상위 레이아웃 컴포넌트, text가 곧 입력칸 위에 뜨는 라벨).
        self.amount_input = discord.ui.TextInput(
            placeholder=f"1~{MAX_BET} 사이 숫자로 입력",
            required=True,
            max_length=len(str(MAX_BET)),
        )
        self.add_item(
            discord.ui.Label(
                text=f"배팅할 동전 개수 (보유: {balance}개)", component=self.amount_input
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = int(self.amount_input.value.strip())
        except ValueError:
            await interaction.response.send_message(INVALID_AMOUNT_RESPONSE, ephemeral=True)
            return
        if not (1 <= amount <= MAX_BET):
            await interaction.response.send_message(INVALID_AMOUNT_RESPONSE, ephemeral=True)
            return
        await self._on_valid(interaction, amount)


class ReplayView(discord.ui.View):
    """게임이 끝난 뒤 기존 선택/스핀 버튼을 전부 걷어내고 이 뷰(버튼 1개)로 통째로
    교체한다(/내기·/도박 공유) — 10초 안에 안 누르면 버튼만 사라지고 결과 텍스트는
    그대로 남는다. own_command는 CTA 문구용("/내기"/"/도박"), on_replay(interaction,
    amount)는 모달 검증을 통과한 뒤 실제로 새 판을 여는 콜백(호출부가 game_kind를
    클로저로 감싸 전달)이다."""

    def __init__(
        self,
        user_id: int,
        own_command: str,
        on_replay: Callable[[discord.Interaction, int], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=REPLAY_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.own_command = own_command
        self._on_replay = on_replay
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logging.exception("Failed to clear replay button on timeout")

    @discord.ui.button(label="다시하기", style=discord.ButtonStyle.success)
    async def replay(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, self.own_command):
            return
        user = await get_user(self.user_id)
        balance = user["coins"] if user is not None else 0
        await interaction.response.send_modal(
            BetAmountModal(balance=balance, on_valid=self._on_replay)
        )


class _RuleButton(discord.ui.Button):
    """RulesView 안의 게임별 규칙 버튼 — 누르면 그 게임의 상세 규칙으로 임베드만
    바꿔치기한다(다른 버튼도 그대로 남아 있어 자유롭게 오갈 수 있다)."""

    def __init__(self, label: str, text: str, embed_title: str, color: int) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self._text = text
        self._embed_title = embed_title
        self._color = color

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.bump()
        embed = discord.Embed(title=self._embed_title, description=self._text, color=self._color)
        embed.set_footer(text=format_footer_time(datetime.now(KST)))
        await interaction.response.edit_message(embed=embed, view=self.view)


class RulesView(EphemeralAutoDeleteView):
    """/내기-규칙·/도박-규칙이 공유하는 게임별 규칙 버튼 뷰 — ephemeral 전용(본인만
    봄)이라 wrong-user 체크가 불필요하다. game_rules는 {버튼 라벨: 규칙 본문} — 게임이
    하나뿐이어도(예: /도박-규칙의 슬롯머신) 나중에 늘어날 걸 감안해 버튼 형태를
    유지한다. color는 버튼을 눌러 바뀌는 상세 규칙 임베드에도 그대로 쓰인다(도메인
    전용 색과 통일 — 개요 임베드와 다른 색으로 바뀌면 안 되므로)."""

    def __init__(self, embed_title: str, game_rules: dict[str, str], *, color: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        for label, text in game_rules.items():
            self.add_item(_RuleButton(label, text, embed_title, color))


# 잔액 부족 안내 — /자판기·/내기·/도박이 전부 공유(다들 spend_coins 실패 시 이
# 풀에서 하나 골라 그대로 응답한다).
INSUFFICIENT_FUNDS_LINES = (
    "어라, 동전이 모자라!! 좀 더 모아서 와줄래?? _(아쉬움)_",
    "동전이 부족해!! /동전으로 더 모아보자!! _(속상)_",
    "앗, 그만큼 동전이 없어!! 조금만 더 모아줘!! _(미안)_",
    "동전이 모자라써!! 다음에 다시 와줄래?? _(아쉬움)_",
    "이런, 잔액이 부족해!! 더 모아서 다시 와줘!! _(속상)_",
)


def format_coin_notice(delta: int, new_coins: int) -> str:
    """동전 변화량 알림 — format_affection_notice(db/affection.py)와 동일한 원칙(델타+
    변화 전후 값)을 동전에 적용한 버전. /동전·/내기·/도박이 공유. delta==0이면 빈
    문자열(호출부가 그냥 이어 붙이면 되게). 2026-09-06부터 "(현재 N)" 대신
    "(전 → 후)"로 보여준다."""
    if delta == 0:
        return ""
    sign = "+" if delta > 0 else ""
    before = new_coins - delta
    return f"\n🪙 동전 {sign}{delta} ({before} → {new_coins})"
