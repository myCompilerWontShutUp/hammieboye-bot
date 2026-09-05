import logging
import random

import discord

from db.guild_channels import touch

# 임베드 색상 4종(2026-09-06) — 유형이 비슷한 임베드는 같은 색을 쓰고, 서로 다른
# 유형은 확실히 구분되게 4가지를 나눈다. 도메인 전용 색(자판기의 VENDING_EMBED_COLOR,
# 도박의 GAMBLING_EMBED_COLOR)은 command/economy_common.py에 그대로 둔다.
EMBED_COLOR = 0xFFCC99  # 개인 정보 계열 — /내정보·/니정보의 모든 카테고리 결과.
SYSTEM_EMBED_COLOR = 0x95A5A6  # 시스템 공지성 임베드 — 헬프 미/디저트 타임 안내, ann update 등.
LIST_EMBED_COLOR = 0x9B59B6  # 특정 도메인에 안 속하는 카탈로그/순위형 — 업적 리스트, 랭킹.


def normalize(text: str) -> str:
    return text.replace(" ", "").strip().lower()


async def touch_channel(interaction: discord.Interaction) -> None:
    """헬프 미/취침/아침 인사 이벤트가 어느 채널에 올릴지는 유저가 봇을 실제로 부른 채널
    기준으로 정하므로, 여러 슬래시 커맨드 핸들러가 동일하게 이 갱신을 필요로 한다
    (db/guild_channels.py::touch가 "마지막 사용 채널" 갱신 + 메인 채널 자동 지정을 같이 함)."""
    if interaction.guild is not None and interaction.channel_id is not None:
        await touch(interaction.guild.id, interaction.channel_id)


# 게임이 아닌 일반 명령어(업적 리스트 페이지 넘기기, 자판기 카테고리 전환 등)에서
# 남의 버튼을 눌렀을 때 거절하는 무CTA 버전 — command/economy_common.py의
# reject_if_wrong_user_with_cta(게임 전용, "너도 해봐" CTA 포함)와는 용도가 다르다.
NOT_YOUR_COMMAND_LINES = (
    "이건 네가 실행한 명령어가 아니야!! _(단호)_",
    "어라, 이 버튼은 다른 사람 거야!! _(갸웃)_",
    "네가 부른 게 아니잖아!! _(단호)_",
    "이건 다른 사람이 실행한 거야!! 구경만 해줘!! _(웃음)_",
    "잠깐, 이건 네 명령어가 아니야!! _(당황)_",
    "이 화면은 다른 사람을 위한 거야!! _(장난)_",
    "네가 부른 명령어가 아니라서 못 눌러!! _(갸웃)_",
    "이건 남의 결과창이야!! _(단호)_",
    "다른 사람 명령어에 손대면 안 되지!! _(장난)_",
    "이 버튼 주인은 따로 있어!! _(단호)_",
    "네가 실행한 게 아니잖아?? _(의아)_",
    "이건 다른 사람 화면이야!! _(웃음)_",
    "구경은 좋지만 버튼은 안 돼!! _(장난)_",
    "이 명령어 주인공은 따로 있어!! _(단호)_",
    "네 차례가 아니야, 직접 실행해봐!! _(갸웃)_",
    "이건 다른 사람이 부른 화면이야!! _(당황)_",
    "미안하지만 이건 네 명령어가 아니야!! _(미안)_",
    "이 버튼은 실행한 사람만 누를 수 있어!! _(안내)_",
    "다른 사람 것까지 건드리면 안 돼!! _(단호)_",
    "이건 네가 아니라 다른 사람이 부른 거야!! _(갸웃)_",
)


async def reject_if_wrong_invoker(interaction: discord.Interaction, user_id: int) -> bool:
    """True면 계속 진행, False면 이미 거절 응답을 보냈으니 콜백은 그대로 return해야 한다."""
    if interaction.user.id != user_id:
        await interaction.response.send_message(random.choice(NOT_YOUR_COMMAND_LINES), ephemeral=True)
        return False
    return True


class EphemeralAutoDeleteView(discord.ui.View):
    """ephemeral(나에게만 보이는) 전용 뷰의 공통 베이스 — 60초(기본값) 동안 아무 버튼도
    안 누르면 메시지 자체를 완전히 지운다(버튼만 지우는 게 아님). 다른 쿨타임이 이미
    명시된 곳(예: /탈퇴의 30초)은 timeout을 그 값으로 넘긴다. 자식 클래스는 각 버튼
    콜백 맨 앞에서 bump()를 호출해 "인터랙트 발생 시 60초로 재초기화"를 구현한다."""

    def __init__(self, *, timeout: float = 60) -> None:
        super().__init__(timeout=timeout)
        self.interaction: discord.Interaction | None = None

    def bump(self) -> None:
        """discord.py의 View.timeout setter는 대입할 때마다 만료 시각을 지금+값으로
        다시 계산한다 — 같은 값을 재대입하는 것만으로 타이머가 재시작된다."""
        self.timeout = self.timeout

    async def on_timeout(self) -> None:
        if self.interaction is None:
            return
        try:
            await self.interaction.delete_original_response()
        except discord.HTTPException:
            logging.exception("Failed to delete ephemeral message on timeout")
