import random
from typing import Awaitable, Callable

import discord

import achievements
from core.base import clear_on_timeout
from core.korean import josa
from db.achievements import award as award_achievement
from db.users import get_user

# /자판기 전용 색(하늘색) — command/info.py 등의 EMBED_COLOR(연주황색)와
# 구분해 자판기만의 색으로 쓴다. /암시장은 별도로 discord.Color.dark_purple()을 쓴다
# (같은 "상점" 계열이지만 밤에만 여는 다른 컨셉이라 색을 분리).
VENDING_EMBED_COLOR = 0x87CEEB

# /내기·/도박(및 그 안의 모든 게임 뷰, /봇정보-규칙 포함) 전용 색(밝은 노란색,
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

# /내기와 /도박은 배팅 상한 자체가 다르다(2026-09-10) — 위험도가 낮은 /내기는
# 100코인, 크게 걸 수 있는 /도박(슬롯머신·승부예측)은 1,000,000코인. 배율은 이미
# 최종 지급 전에 하드 캡되므로(슬롯머신 MAX_MULTIPLIER=77, 승부예측
# JACKPOT_MULTIPLIER=100) `배팅액 x 배율`은 최대 1억 수준 — Postgres bigint
# 상한(약 9.2 x 10^18)과 비교해 넉넉히 안전하다. BetAmountModal이 이 값으로 직접
# 범위 검증을 한다(모달 입력이라 app_commands.Range는 안 씀). 더블오어낫띵은
# 올인/하프만 선택하므로 이 상한 자체가 적용되지 않는다(AllInHalfModal 참고).
MAX_BET_BETTING = 100
MAX_BET_GAMBLING = 1_000_000


# "다시하기" 버튼은 게임 자체(60초)보다 훨씬 짧게 준다 — 이미 한 판을 끝낸 뒤라 오래
# 붙잡아둘 이유가 없다. /내기·/도박이 공유.
REPLAY_TIMEOUT_SECONDS = 10

# 유저별 "진행 중인 판" 추적(2026-09-09 신규) — /내기·/도박(슬롯머신·승부예측 전부
# 포함)은 서로 크로스로 막는다: 이미 한쪽에서 판이 진행 중이면(정산 후 "다시하기"
# 버튼이 사라지기 전까지) 다른 쪽 슬래시 커맨드도 재진입을 막는다. 프로세스 메모리
# 전용이라 재시작 시 유실되지만, 진행 중이던 판 자체도 재시작하면 같이 유실되는
# 기존 한계(CLAUDE.md §18)와 동일선상이라 별도 영속화는 불필요하다.
_ACTIVE_PLAYERS: dict[int, str] = {}  # user_id -> own_command("/내기" 또는 "/도박")


def mark_active(user_id: int, own_command: str) -> None:
    _ACTIVE_PLAYERS[user_id] = own_command


def mark_inactive(user_id: int) -> None:
    _ACTIVE_PLAYERS.pop(user_id, None)


_ALREADY_PLAYING_LINES = (
    "잠깐, 아직 {command}{josa} 안 끝났어!! 그것부터 마무리해줘!! _(단호)_",
    "어라, {command} 진행 중이잖아!! 다 끝내고 다시 와줘!! _(갸웃)_",
    "지금 {command} 하고 있는 거 안 잊었지?? 그거부터!! _(웃음)_",
    "판이 아직 안 끝났어!! {command} 먼저 마무리해줘!! _(단호)_",
    "동시에 두 판은 안 돼!! {command}{josa} 끝나야 새로 할 수 있어!! _(장난)_",
    "{command} 판이 아직 진행 중이야!! 거기부터 끝내줘!! _(안내)_",
    "이미 시작한 {command}{josa} 있잖아!! 그거 먼저!! _(단호)_",
    "하나씩 하자!! {command} 마무리하고 다시 불러줘!! _(웃음)_",
    "아직 {command} 결과가 안 나왔어!! 기다려줘!! _(안내)_",
    "지금 진행 중인 {command}{josa} 있어서 못 열어줘!! _(미안)_",
)


async def reject_if_already_playing(interaction: discord.Interaction, user_id: int) -> bool:
    """True면 계속 진행. 이미 이 유저의 /내기 또는 /도박 판이 진행 중이면(정산 후
    "다시하기" 버튼이 사라지기 전까지) 안내하고 False — /내기·/도박 슬래시 커맨드
    진입점(bet.py::handle_bet/slot.py::handle_gamble)이 이미 ephemeral로 defer된
    상태에서 제일 먼저 호출한다.

    **이것만으로는 TOCTOU 허점이 있다**(2026-09-09에 발견, claim_active_or_reject
    참고) — 여기 체크와 실제 mark_active() 호출(게임 선택 → 배팅 모달 제출을 거친
    한참 뒤) 사이에 시간차가 있어, 슬래시 커맨드 진입 직후 시점에는 항상 이 체크를
    통과한다. 그래서 이 함수는 어디까지나 "빠른 UX 안내"(모달까지 다 채우게 하고
    나서야 거절하는 걸 피하기 위함) 용도로만 남기고, 실제 크로스블록 방지는
    claim_active_or_reject가 담당한다."""
    active_command = _ACTIVE_PLAYERS.get(user_id)
    if active_command is None:
        return True
    line = random.choice(_ALREADY_PLAYING_LINES).format(
        command=active_command, josa=josa(active_command, "이", "가")
    )
    await interaction.edit_original_response(content=line, embed=None, view=None)
    return False


async def claim_active_or_reject(interaction: discord.Interaction, user_id: int, own_command: str) -> bool:
    """True면 성공(원자적으로 _ACTIVE_PLAYERS에 기록됨) — bet.py/slot.py/
    horse_race.py/double_or_nothing.py의 start_round류가 **신규 진입**(다시하기가
    아닌 최초 배팅)일 때만 spend_coins 이전에 호출한다.

    2026-09-09 신설 — reject_if_already_playing 하나만으로는 TOCTOU 허점이 있었다:
    그 체크는 슬래시 커맨드 진입 시점(아직 베팅 전)에 한 번만 실행되고, 실제
    mark_active()는 게임 선택 → 배팅 모달 제출까지 거친 뒤에야 불렸다. 그 사이 유저가
    /내기·/도박 ephemeral 프롬프트를 둘 다(또는 /내기를 두 번) 미리 열어두면 둘 다
    이 최초 체크를 통과하고, 이후 순서대로 베팅을 마치면 두 판이 동시에 진행되는데
    `_ACTIVE_PLAYERS`는 유저당 값 하나만 들고 있어 나중 판만 추적된다 — 먼저 끝난
    판의 mark_inactive가 아직 진행 중인 다른 판의 잠금까지 지워버려 세 번째 판까지
    바로 열릴 수 있었다(일반 유저가 클릭 순서만 조정하면 재현 가능).

    실제 상태 변경(mark_active) 시점에 다시 한번 원자적으로 확인해야 막을 수
    있어서(파이썬 asyncio는 단일 스레드라 딕셔너리 조회+대입 자체엔 락이 필요
    없다 — 문제는 "체크"와 "반영" 시점이 서로 다른 상호작용으로 갈라져 있다는
    것) 이 함수가 신설됐다. 이미 다른 판이 활성 상태면(같은 own_command로 이미
    활성 중이어도 — "다시하기"가 아닌 완전히 새로운 진입이라 무조건 거절, 신규
    진입은 절대 valid replay가 아니다) ephemeral 거절 메시지를 보내고 False.
    spend_coins **이전**에 호출해야 거절 시 환불 로직이 필요 없다. "다시하기"
    (ReplayView.replay → on_replay)는 이미 그 판이 활성 상태인 게 보장돼 있어 이
    함수를 거치지 않고 기존 mark_active()를 그대로 무조건 호출한다."""
    active_command = _ACTIVE_PLAYERS.get(user_id)
    if active_command is not None:
        line = random.choice(_ALREADY_PLAYING_LINES).format(
            command=active_command, josa=josa(active_command, "이", "가")
        )
        await interaction.response.send_message(line, ephemeral=True)
        return False
    _ACTIVE_PLAYERS[user_id] = own_command
    return True


async def reject_if_wrong_user_with_cta(
    interaction: discord.Interaction, user_id: int, own_command: str
) -> bool:
    """True면 계속 진행. 공개 메시지(선택 버튼/다시하기)는 누구나 볼 수 있어서, 주인이
    아닌 사람이 눌렀을 때 기존 거절 문구에 이어 own_command를 직접 해보라는 안내를
    덧붙인다. /내기·/도박이 공유(own_command만 서로 다름).

    2026-09-08부로 별도 동의(/가입) 절차가 폐지되어 "미가입자용" 분기가 사라졌다 —
    누구든 own_command를 실행하면(자동 등록되므로) 그 자리에서 바로 즐길 수 있다."""
    if interaction.user.id == user_id:
        return True
    cta = f"너도 {own_command}{josa(own_command, '으로', '로')} 직접 해볼 수 있어!!"
    message = f"{random.choice(NOT_YOUR_GAME_LINES)}\n{cta}"
    await interaction.response.send_message(message, ephemeral=True)
    return False


class BetAmountModal(discord.ui.Modal):
    """배팅 금액 입력 전용 모달 — 게임 종류 선택 버튼과 "다시하기" 버튼이 공유한다.
    max_bet은 호출부가 넘긴다(/내기=MAX_BET_BETTING, /도박=MAX_BET_GAMBLING —
    더블오어낫띵은 이 모달 자체를 안 씀, AllInHalfModal 참고). on_valid(interaction,
    amount)는 검증을 통과한 정수 금액을 받아 실제로 판을 시작하는 콜백이다."""

    def __init__(
        self, *, balance: int, max_bet: int, on_valid: Callable[[discord.Interaction, int], Awaitable[None]]
    ) -> None:
        super().__init__(title="배팅 금액 입력")
        self._on_valid = on_valid
        self._max_bet = max_bet
        self._invalid_amount_response = f"1~{max_bet} 사이의 숫자로 적어줘!! _(갸웃)_"
        # TextInput(label=...)는 discord.py 2.6부터 deprecated — 대신 Label로 감싼다
        # (모달 전용 최상위 레이아웃 컴포넌트, text가 곧 입력칸 위에 뜨는 라벨).
        self.amount_input = discord.ui.TextInput(
            placeholder=f"1~{max_bet} 사이 숫자로 입력",
            required=True,
            max_length=len(str(max_bet)),
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
            await interaction.response.send_message(self._invalid_amount_response, ephemeral=True)
            return
        if not (1 <= amount <= self._max_bet):
            await interaction.response.send_message(self._invalid_amount_response, ephemeral=True)
            return
        await self._on_valid(interaction, amount)


class ReplayView(discord.ui.View):
    """게임이 끝난 뒤 기존 선택/스핀 버튼을 전부 걷어내고 이 뷰(버튼 1개)로 통째로
    교체한다(/내기·/도박 공유) — 10초 안에 안 누르면 버튼만 사라지고 결과 텍스트는
    그대로 남는다. own_command는 CTA 문구용("/내기"/"/도박"), on_replay(interaction,
    amount, old_message)는 모달 검증을 통과한 뒤 실제로 새 판을 여는 콜백(호출부가
    game_kind를 클로저로 감싸 전달) — 2026-09-07부터 새 판은 이 메시지를 고쳐쓰지
    않고 **새 공개 메시지**로 열리고, old_message(=이 판의 메시지, self.message)는
    그 콜백이 버튼만 제거해 기록으로 남긴다(판마다 새 메시지로 이어지길 원한다는
    요청 — 기존엔 이 메시지 자체를 edit해서 이전 판 기록이 사라졌었다).

    **"다시하기" 버튼이 사라지는 순간이 "게임 완전 종료" 판정 기준이다**(2026-09-09,
    reject_if_already_playing 참고) — 이 버튼이 시간 초과로 사라질 때
    mark_inactive()를 호출해 이 유저가 다시 /내기·/도박을 새로 시작할 수 있게
    한다. 버튼을 눌러 다시하기를 선택하면 on_replay가 곧바로 다음 판을 시작하며
    (mark_active가 그 안에서 다시 호출됨) 이 시점엔 절대 mark_inactive를 호출하지
    않는다 — 계속 "진행 중" 상태로 이어진다.

    open_modal(interaction, balance, on_valid)는 실제로 어떤 모달을 띄울지
    호출부가 결정한다(2026-09-10 신규) — 게임마다 배팅 방식이 달라졌기 때문
    (`/내기`는 `BetAmountModal(max_bet=MAX_BET_BETTING)`, 슬롯머신·승부예측은
    `BetAmountModal(max_bet=MAX_BET_GAMBLING)`, 더블오어낫띵은
    `double_or_nothing.AllInHalfModal` — 셋 다 최초 진입과 다시하기가 동일한
    open_modal을 공유해 로직이 갈리지 않는다)."""

    def __init__(
        self,
        user_id: int,
        own_command: str,
        on_replay: Callable[[discord.Interaction, int, "discord.Message | None"], Awaitable[None]],
        *,
        open_modal: Callable[
            [discord.Interaction, int, Callable[[discord.Interaction, int], Awaitable[None]]], Awaitable[None]
        ],
    ) -> None:
        super().__init__(timeout=REPLAY_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.own_command = own_command
        self._on_replay = on_replay
        self._open_modal = open_modal
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        mark_inactive(self.user_id)
        if self.message is None:
            return
        await clear_on_timeout(lambda: self.message.edit(view=None), log_label="replay button")

    @discord.ui.button(label="다시하기", style=discord.ButtonStyle.success)
    async def replay(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, self.own_command):
            return
        user = await get_user(self.user_id)
        balance = user["coins"] if user is not None else 0
        old_message = self.message

        async def _on_valid(modal_interaction: discord.Interaction, amount: int) -> None:
            await self._on_replay(modal_interaction, amount, old_message)

        await self._open_modal(interaction, balance, _on_valid)


class PurchaseConfirmModal(discord.ui.Modal):
    """자판기류(`/자판기`·`/암시장`) 구매 확인 모달(2026-09-08 신규) — 가진 금액/상품
    가격/구매 후 잔액을 보여주고, "구매" 체크박스를 체크한 채 제출해야 실제 구매를
    실행하는 on_confirm 콜백을 부른다. 구매는 항상 1개 고정이라 수량은 이 모달
    어디에도 표시하지 않는다.

    최초 설계는 값을 안 받는 `TextInput(required=False)`을 형식상 자리만 채우는
    용도로 넣었었다 — Discord 모달은 컴포넌트가 최소 1개 있어야 해서였는데, "아무것도
    안 적어도 그냥 제출된다"는 게 실수로 결제될 위험이 있어 불친절하다는 지적으로,
    discord.py 2.7에서 새로 지원하는 `discord.ui.Checkbox`(모달 전용 체크박스)로
    교체했다 — 기본값 미체크, 체크한 채로 제출해야만 구매가 진행된다."""

    _NOT_CHECKED_MESSAGE = "'구매' 체크박스를 체크해야 진행돼!! _(갸웃)_"

    def __init__(
        self, *, item_name: str, before: int, price: int, on_confirm: Callable[[discord.Interaction], Awaitable[None]]
    ) -> None:
        super().__init__(title=f"{item_name} 구매 확인"[:45])
        self._on_confirm = on_confirm
        after = before - price
        self._confirm_checkbox = discord.ui.Checkbox(default=False)
        self.add_item(
            discord.ui.Label(
                text="구매",
                description=(
                    f"가진 금액: {before:,}코인 / 상품 가격: {price:,}코인 / "
                    f"구매 후 잔액: {after:,}코인"
                ),
                component=self._confirm_checkbox,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self._confirm_checkbox.value:
            await interaction.response.send_message(self._NOT_CHECKED_MESSAGE, ephemeral=True)
            return
        await self._on_confirm(interaction)


# RulesView/_RuleButton은 2026-09-10 command/rules_info.py(`/봇정보-규칙`)로
# 이전했다 — 그 시점부터 이 클래스들의 유일한 소비자가 됐다(舊 /내기-규칙·
# /도박-규칙은 폐지). GAMBLING_EMBED_COLOR/MAX_BET_BETTING/MAX_BET_GAMBLING 등 다른 경제 상수는 이
# 파일에 그대로 있고, 뷰 자체만 옮겼다.


# 잔액 부족 안내 — /자판기·/내기·/도박이 전부 공유(다들 spend_coins 실패 시 이
# 풀에서 하나 골라 그대로 응답한다).
INSUFFICIENT_FUNDS_LINES = (
    "어라, 동전이 모자라!! 좀 더 모아서 와줄래?? _(아쉬움)_",
    "동전이 부족해!! `/동전`으로 더 모아보자!! _(속상)_",
    "앗, 그만큼 동전이 없어!! 조금만 더 모아줘!! _(미안)_",
    "동전이 모자라써!! 다음에 다시 와줄래?? _(아쉬움)_",
    "이런, 잔액이 부족해!! 더 모아서 다시 와줘!! _(속상)_",
)


def format_coin_notice(
    delta: int, new_coins: int, *, multiplier: int = 1, bonus_reason: str | None = None
) -> str:
    """동전 변화량 알림 — format_affection_notice(db/affection.py)와 동일한 원칙(델타+
    변화 전후 값)을 동전에 적용한 버전. /동전·/내기·/도박이 공유. delta==0이면 빈
    문자열(호출부가 그냥 이어 붙이면 되게). 2026-09-06부터 "(현재 N)" 대신
    "(전 → 후)"로 보여준다.

    multiplier(2026-09-12부로 호감도와 동일하게 "기본값 x 배수" 형태로 분해 —
    舊에는 이미 배율이 곱해진 델타 총합 뒤에 "x5배 (동전 초대박 당첨)" 같은
    사전 조합 문자열을 그냥 붙이기만 해서, 사용자가 실제로 보는 게 호감도의
    "10 x 2배 (주말 이벤트)" 분해 형식과 달라 보인다는 지적이 있었다) > 1이고
    bonus_reason이 있으면 "동전 {델타//배수} x {배수}배 ({사유}) (전 → 후)"로
    분해해서 보여준다 — /동전의 배율은 (기본지급+coin_grant_bonus) x 배율로
    계산돼 델타가 항상 배수로 정확히 나누어떨어지므로(날짜 배율이 추가로 곱해져도
    정수 배수라 마찬가지) 호감도처럼 나머지 걱정 없이 안전하게 역산 가능하다."""
    if delta == 0:
        return ""
    sign = "+" if delta > 0 else ""
    before = new_coins - delta
    if multiplier > 1 and bonus_reason and delta % multiplier == 0:
        base = delta // multiplier
        return f"\n🪙 동전 {base} x {multiplier}배 ({bonus_reason}) ({before} → {new_coins})"
    return f"\n🪙 동전 {sign}{delta} ({before} → {new_coins})"


# 배팅 정산 임베드(build_bet_receipt_embed) 전용 색 — /내기·/도박 6개 게임이 전부
# 공유하지만, 이 임베드에서만 쓰고 다른 곳에서는 안 쓴다(2026-09-10).
BET_RECEIPT_EMBED_COLOR = 0x2ECC71


def build_bet_receipt_embed(before: int, bet: int, current: int | None) -> discord.Embed:
    """`/자판기` 구매 영수증과 동일한 형식(기존/배팅/현재 금액)의 배팅 정산
    임베드 — /내기·/도박 6개 게임이 공유한다. 판 시작 시 current=None("???")로
    먼저 보여주고, 정산 시 채운 새 임베드로 교체한다. 승패 전엔 확정 잔액이
    없어 "배팅 금액"이라는 라벨을 쓴다. 게임판 임베드(슬롯머신 그리드 등)와
    `embeds=[...]`로 나란히 붙으므로 footer는 안 붙인다(게임판 쪽 footer와
    중복 방지)."""
    current_label = f"{current:,}코인" if current is not None else "???"
    return discord.Embed(
        title="💰 배팅 정산",
        description=(
            f"- 기존 금액: {before:,}코인\n"
            f"- 배팅 금액: {bet:,}코인\n"
            f"- 현재 금액: {current_label}"
        ),
        color=BET_RECEIPT_EMBED_COLOR,
    )


# "제작자는 이 업적이 가능한지 테스트하지 않았습니다" 전설 업적 기준(2026-09-09) —
# 舊 슬롯머신 전용(배율 16배 초과)에서 /도박 전체 공용(배율 64배 이상)으로 확장됐다.
# 슬롯머신·승부예측·더블오어낫띵이 이 상수+헬퍼를 공유해 기준을 한 곳에서만 관리한다.
LEGENDARY_MULTIPLIER_THRESHOLD = 64


async def maybe_award_legendary_multiplier(
    user_id: int, multiplier: int, *, guild_id: int | None = None
) -> dict | None:
    """실현된 배율이 LEGENDARY_MULTIPLIER_THRESHOLD 이상이면 전설 업적 지급을
    시도한다 — XP 지급+글로벌 방송은 award() 내부가 알아서 처리하므로
    fire-and-forget으로 호출하면 된다. 실제 지급이 확정된 시점에서만 호출할 것
    (폭탄으로 잃거나 무응답 몰수된 판은 대상 아님). guild_id는 이 판이 진행된
    서버 — 알고 있으면 전 서버 방송에서 그 서버를 가장 먼저 보낸다."""
    if multiplier < LEGENDARY_MULTIPLIER_THRESHOLD:
        return None
    return await award_achievement(user_id, achievements.dev_never_tested_this.ID, guild_id=guild_id)
