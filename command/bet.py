import logging
import random
from datetime import datetime

import discord

import achievements
from core.base import EphemeralAutoDeleteView
from command.economy_common import (
    GAMBLING_EMBED_COLOR,
    INSUFFICIENT_FUNDS_LINES,
    MAX_BET,
    TIMEOUT_SECONDS,
    BetAmountModal,
    ReplayView,
    RulesView,
    claim_active_or_reject,
    format_bet_receipt,
    mark_active,
    mark_inactive,
    reject_if_already_playing,
    reject_if_already_resolved,
    reject_if_wrong_user_with_cta,
)
from db.achievements import award as award_achievement
from db.users import get_user
from db.wallet import add_coins, spend_coins
from events.scheduler import KST, format_footer_time

# CTA 문구("너도 {own_command}로 직접 해볼 수 있어!!")와 ReplayView에 그대로 넘긴다 —
# economy_common.reject_if_wrong_user_with_cta/ReplayView가 /내기·/도박 공용이라 자기
# 커맨드 이름을 매번 받는다.
_OWN_COMMAND = "/내기"

_ODD_EVEN = "odd_even"
_RPS = "rps"
_UP_DOWN = "up_down"

# 게임 진행 중(홀짝/가위바위보/업다운 선택 대기) 라운드 전용 타임아웃(2026-09-09,
# 기존 60초 → 10분으로 연장) — command/slot.py::_SLOT_ROUND_TIMEOUT_SECONDS와 동일한
# 패턴. economy_common.TIMEOUT_SECONDS(60)는 게임 선택 프롬프트 등 다른 용도에
# 계속 쓰이므로 그대로 둔다. **동시에 정책도 바뀐다** — 이 10분 안에 아무것도
# 고르지 않으면 이제 환불이 아니라 전액 몰수로 처리한다(_forfeit_timeout 참고).
_BET_ROUND_TIMEOUT_SECONDS = 600

# "으흠! 나에게 내기를 걸다니..." — /내기 실행 직후, 게임 종류를 고르는 ephemeral
# 프롬프트에 붙는 인트로 문구.
_GAME_SELECT_INTRO_LINES = (
    "으흠! 나에게 내기를 걸다니... 받아주게써!! _(자신감)_",
    "오호, 내기라니!! 좋아, 받아줄게!! _(흥미)_",
    "흐음, 도전하는 거야?? 좋아!! _(도전)_",
    "내기?? 재밌겠다!! 뭘로 해볼까?? _(신남)_",
    "오, 승부욕 발동!! 받아주겠어!! _(투지)_",
    "좋아, 내기 한판 해보자!! _(자신감)_",
    "으흐흐, 내기라니 흥미로운데?? _(웃음)_",
    "내기 신청 접수!! 뭐부터 해볼래?? _(기대)_",
    "오호라, 한판 붙어보자는 거지?? _(흥미)_",
    "내기라면 자신 있지!! 골라봐!! _(자신)_",
    "흠, 좋은 승부가 되겠는걸?? _(기대)_",
    "내기 좋아!! 어떤 걸로 할래?? _(설렘)_",
    "으흠, 받아주지!! 종류를 골라봐!! _(자신감)_",
    "오오, 내기 타임!! 신난다!! _(신남)_",
    "좋아, 승부다!! 뭐로 할까?? _(투지)_",
    "내기라니, 피가 끓는걸?? _(흥분)_",
    "으흠! 재밌겠어, 받아주게써!! _(자신감)_",
    "오호, 좋은 생각이야!! 골라봐!! _(흥미)_",
    "내기 접수 완료!! 종류 골라줘!! _(정리)_",
    "좋아, 이번엔 내가 이길 거야!! _(자신감)_",
)

_RULES_INTRO_LINES = (
    "내기 규칙 알려줄게!! _(진지)_",
    "이렇게 하면 이길 수 있어!! _(자신감)_",
    "내기 하는 법 설명할게!! _(친절)_",
    "규칙부터 익히고 시작하자!! _(꼼꼼)_",
    "내기, 이렇게 굴러가!! _(설명)_",
    "먼저 규칙 확인해볼래?? _(권유)_",
    "내기 공략법이야!! _(자랑)_",
    "이거 알면 유리해!! 규칙이야!! _(웃음)_",
    "내기 설명서 가져왔어!! _(뿌듯)_",
    "규칙 모르면 손해야!! 알려줄게!! _(진지)_",
    "내기는 이렇게 하는 거야!! _(설명)_",
    "짜잔, 내기 규칙!! _(공개)_",
    "이거 읽고 도전해봐!! _(응원)_",
    "내기 하기 전에 이거부터!! _(추천)_",
    "규칙 요약해줄게!! _(친절)_",
    "내기 룰 정리했어!! _(정리)_",
    "이렇게 승부가 갈려!! _(설명)_",
    "내기, 알고 하면 더 재밌어!! _(웃음)_",
    "규칙 확인하고 배팅해봐!! _(권유)_",
    "내기 가이드 여기 있어!! _(안내)_",
)

# RulesView가 embed.description으로 그대로 보여주는 문구라 시스템 정중체로 고정한다
# (2026-09-09 — "~이야!!"/"~있어!!" 같은 페르소나 말투가 섞여 있던 걸 발견해 정정,
# command/black_market.py와 동일한 원칙).
_RULES_OVERVIEW_TEXT = (
    "동전을 걸고 하는 미니게임입니다. 지금은 세 가지가 있으며(앞으로 더 늘어날 수도 "
    "있습니다) 아래 버튼에서 원하는 게임을 골라주세요.\n\n"
    f"배팅액은 1~{MAX_BET}동전까지 걸 수 있고, 게임 진행 중 10분 동안 아무것도 "
    "고르지 않으면 포기한 것으로 간주해 배팅액을 모두 잃습니다."
)

_ODD_EVEN_RULE_TEXT = (
    "🪙 홀짝\n\n홀 또는 짝을 골라서 맞히면 배팅액의 2배를 받고, 틀리면 배팅액을 전부 "
    "잃습니다."
)
_RPS_RULE_TEXT = (
    "✂️ 가위바위보\n\n가위/바위/보 중 하나를 내서 햄미를 이기면 배팅액의 2배, 비기면 "
    "배팅액을 그대로 돌려받고(번 것이 아니라 순수 반환), 지면 배팅액을 전부 "
    "잃습니다."
)
_UPDOWN_RULE_TEXT = (
    "🔢 업다운\n\n햄미가 1~20 사이의 숫자를 하나 생각합니다. 셀렉트 메뉴로 숫자를 "
    "골라 맞히면 되고, 기회는 3번입니다. 고른 숫자보다 정답이 크면 \"업\", 작으면 "
    "\"다운\" 힌트가 나오고 다음 셀렉트에는 그 범위의 숫자만 남습니다. 3번째 안에 "
    "맞히면 배팅액의 3배를 받고, 끝까지 못 맞히면 배팅액을 전부 잃습니다."
)

# 2026-09-09 — 舊 _BET_TIMEOUT_LINES(환불 전제)를 몰수 전제로 전면 교체했다.
# 10분 동안 아무것도 안 고르면 포기한 걸로 간주해 배팅액을 그대로 가져간다(환불 없음).
_BET_FORFEIT_LINES = (
    "너무 오래 기다렸어!! 포기한 걸로 알고 배팅액은 가져갈게!! _(단호)_",
    "시간 다 됐어!! 응답이 없어서 그냥 몰수했어!! _(냉정)_",
    "아무도 안 골라서 배팅액은 그대로 가져갈게!! _(정리)_",
    "선택이 없어서 이번 판은 포기로 처리했어!! 동전은 안 돌아가!! _(단호)_",
    "10분이 지났어!! 배팅액은 이제 내 거야!! _(으쓱)_",
    "결정을 안 내려서 배팅액을 몰수했어!! _(냉정)_",
    "시간 초과!! 이번엔 환불 없이 그대로 가져갈게!! _(단호)_",
    "아무 반응이 없어서 포기 처리했어!! 배팅액은 안 돌아와!! _(정리)_",
    "너무 늦었어!! 배팅액은 이제 못 돌려줘!! _(미안)_",
    "시간이 다 됐다구!! 배팅액은 그대로 가져갈게!! _(단호)_",
)

_ODD_EVEN_WIN_LINES = (
    "정답은 {actual}이었어!! 완전 딱 맞혔다!! _(환호)_",
    "우와, {actual}!! 정확히 맞혔어!! _(놀람)_",
    "짜잔, {actual}이었어!! 실력이야 운이야?? _(웃음)_",
    "{actual}!! 완벽하게 맞혔네!! _(감탄)_",
    "정답 {actual}!! 눈치가 좋은데?? _(칭찬)_",
    "오, {actual} 맞혔다!! 대단해!! _(박수)_",
    "{actual}이었어!! 딱 걸렸다, 정답!! _(신남)_",
    "역시!! {actual} 정확히 맞혔어!! _(뿌듯)_",
    "{actual}!! 완전 촉 좋은데?? _(감탄)_",
    "정답은 {actual}, 완벽하게 맞혔어!! _(환호)_",
    "우와아, {actual}!! 소름 돋았어!! _(놀람)_",
    "{actual} 정답!! 다음에도 잘할 듯?? _(웃음)_",
    "빙고!! {actual} 맞혔다!! _(신남)_",
    "{actual}이었네!! 운이 좋았나 봐!! _(감탄)_",
    "정답 {actual}!! 이건 실력인 듯!! _(칭찬)_",
    "오옷, {actual} 정확히!! 대박!! _(놀람)_",
    "{actual}!! 완전 잘 맞혔어!! _(박수)_",
    "정답은 {actual}이었다구!! 축하해!! _(환호)_",
    "{actual} 맞혔네!! 다음 판도 기대돼!! _(웃음)_",
    "짠, {actual}!! 완벽한 정답이야!! _(감탄)_",
)
_ODD_EVEN_LOSE_LINES = (
    "정답은 {actual}이었는데... 아쉽다!! _(안타까움)_",
    "어이쿠, {actual}이었어!! 다음엔 맞혀봐!! _(아쉬움)_",
    "땡!! 정답은 {actual}이었어!! _(장난)_",
    "{actual}이었는데 틀렸네!! 다음 기회에!! _(위로)_",
    "아깝다, 정답은 {actual}!! _(안타까움)_",
    "정답은 {actual}!! 이번엔 햄미가 이겼다!! _(으쓱)_",
    "땡땡!! {actual}이었어!! 다음엔 잘될 거야!! _(웃음)_",
    "{actual}이었네!! 살짝 빗나갔어!! _(아쉬움)_",
    "정답 {actual}, 아쉽게 틀렸어!! _(안타까움)_",
    "이런, {actual}이었다구!! 다음엔 맞힐 수 이써!! _(응원)_",
    "{actual}이 정답이었어!! 다음 판 노려봐!! _(웃음)_",
    "아쉽지만 {actual}이었어!! _(안타까움)_",
    "땡!! {actual}!! 햄미가 이겼네?? _(장난)_",
    "정답은 {actual}이었는데 놓쳤어!! _(아쉬움)_",
    "{actual}이었다구!! 다음엔 꼭 맞혀봐!! _(응원)_",
    "이번엔 {actual}!! 아깝게 틀렸네!! _(위로)_",
    "정답 {actual}, 다음엔 더 잘할 거야!! _(격려)_",
    "{actual}이 나왔어!! 다음번엔 이길지도?? _(웃음)_",
    "아깝게 빗나갔어!! 정답은 {actual}!! _(안타까움)_",
    "정답은 {actual}이었다구!! 재도전 해볼래?? _(권유)_",
)

_RPS_WIN_LINES = (
    "햄미는 {actual}!! 네가 이겼어!! _(감탄)_",
    "우와, 햄미가 {actual} 냈는데 졌다!! _(놀람)_",
    "햄미 {actual}!! 완패했어!! _(박수)_",
    "{actual} 냈는데도 졌어!! 잘했다!! _(칭찬)_",
    "햄미는 {actual}이었어!! 네가 한 수 위야!! _(감탄)_",
    "이런, 햄미 {actual}!! 완전 졌네!! _(웃음)_",
    "햄미가 {actual} 냈는데 발렸어!! _(놀람)_",
    "{actual}!! 햄미 패배 인정!! _(박수)_",
    "햄미는 {actual}로 도전했지만 졌어!! _(칭찬)_",
    "우와아, {actual} 냈는데도 졌다!! _(놀람)_",
    "햄미 {actual}!! 이번엔 완전 밀렸어!! _(웃음)_",
    "{actual} 냈는데 상대가 안 됐어!! _(감탄)_",
    "햄미는 {actual}이었는데... 졌다!! _(박수)_",
    "짜잔, 햄미 {actual}!! 그래도 졌어!! _(웃음)_",
    "햄미가 {actual} 내고 완패!! _(칭찬)_",
    "{actual}!! 햄미 실력이 안 됐어!! _(감탄)_",
    "햄미는 {actual}!! 완전히 읽혔나 봐!! _(놀람)_",
    "이번 햄미는 {actual}, 패배!! _(박수)_",
    "햄미가 {actual} 냈는데 밀렸어!! _(웃음)_",
    "{actual} 냈지만 졌다!! 축하해!! _(감탄)_",
)
_RPS_LOSE_LINES = (
    "햄미는 {actual}!! 이번엔 햄미가 이겼다!! _(으쓱)_",
    "우와, 햄미 {actual}로 승리!! _(환호)_",
    "햄미 {actual}!! 완전 이겼어!! _(신남)_",
    "{actual} 낸 햄미가 이겼네?? _(으쓱)_",
    "햄미는 {actual}이었어!! 이번엔 이겼다!! _(환호)_",
    "짜잔, 햄미 {actual}!! 승리!! _(신남)_",
    "햄미가 {actual} 내고 이겼어!! _(자랑)_",
    "{actual}!! 햄미 승리 축하!! _(환호)_",
    "햄미는 {actual}로 이겼다구!! _(으쓱)_",
    "우와아, {actual} 내고 햄미 승리!! _(신남)_",
    "햄미 {actual}!! 이번엔 완전 이겼어!! _(자랑)_",
    "{actual} 낸 게 신의 한 수였나 봐!! 햄미 승!! _(환호)_",
    "햄미는 {actual}이었는데... 이겼다!! _(신남)_",
    "짠, 햄미 {actual}!! 그리고 승리!! _(으쓱)_",
    "햄미가 {actual} 내고 완승!! _(자랑)_",
    "{actual}!! 햄미 실력 인정?? _(환호)_",
    "햄미는 {actual}!! 완전히 읽었나 봐!! _(신남)_",
    "이번 햄미는 {actual}, 승리!! _(으쓱)_",
    "햄미가 {actual} 내고 이겼어!! 다음엔 조심해!! _(자랑)_",
    "{actual} 냈고 햄미 승!! 아쉽게 됐네!! _(환호)_",
)
_RPS_DRAW_LINES = (
    "우와, 둘 다 {actual}!! 비겼어!! 배팅금은 돌려줄게!! _(놀람)_",
    "어라, 똑같이 {actual}!! 무승부야!! _(웃음)_",
    "{actual} 대 {actual}!! 비겼네, 동전은 그대로 돌려줄게!! _(정리)_",
    "동시에 {actual}!! 무승부, 배팅금 환불할게!! _(끄덕)_",
    "둘 다 {actual}이라니!! 이번엔 비긴 걸로!! _(웃음)_",
    "우연히 같은 {actual}!! 무승부, 동전은 안전해!! _(안심)_",
    "{actual}이 겹쳤어!! 비겼으니까 동전 돌려줄게!! _(정리)_",
    "완전 똑같이 {actual}!! 무승부야!! _(놀람)_",
    "둘 다 {actual} 냈네?? 비겼다!! _(웃음)_",
    "무승부!! {actual}가 겹쳤어, 배팅금 그대로!! _(끄덕)_",
    "이야, {actual} 대 {actual}!! 승부는 다음에!! _(웃음)_",
    "동전은 그대로!! 둘 다 {actual}로 비겼어!! _(안심)_",
    "{actual}이 똑같아!! 이번엔 무승부로!! _(정리)_",
    "우와, 텔레파시 통했나?? 둘 다 {actual}!! _(놀람)_",
    "비겼어!! {actual}가 겹쳐서 동전은 돌려줄게!! _(끄덕)_",
    "{actual} 대 {actual}, 승부를 못 가렸어!! _(웃음)_",
    "무승부다!! 배팅금은 안전하게 돌아가!! _(안심)_",
    "둘이 똑같이 {actual}!! 다음 판을 노려보자!! _(웃음)_",
    "{actual}이 겹쳐서 이번엔 비겼어!! _(정리)_",
    "완전 똑같은 선택!! {actual} 무승부야!! _(놀람)_",
)

_UPDOWN_UP_LINES = (
    "업이야!! 더 큰 숫자로 골라봐!! _(안내)_",
    "업!! 정답은 그거보다 커!! _(힌트)_",
    "더 위쪽이야!! 업!! _(안내)_",
    "업이야, 숫자를 더 키워봐!! _(웃음)_",
    "그것보단 커!! 업이야!! _(안내)_",
    "위로 올라가야 돼!! 업!! _(힌트)_",
    "업!! 더 큰 숫자를 노려봐!! _(응원)_",
    "정답은 더 위에 있어!! 업이야!! _(안내)_",
)
_UPDOWN_DOWN_LINES = (
    "다운이야!! 더 작은 숫자로 골라봐!! _(안내)_",
    "다운!! 정답은 그거보다 작아!! _(힌트)_",
    "더 아래쪽이야!! 다운!! _(안내)_",
    "다운이야, 숫자를 더 줄여봐!! _(웃음)_",
    "그것보단 작아!! 다운이야!! _(안내)_",
    "아래로 내려가야 돼!! 다운!! _(힌트)_",
    "다운!! 더 작은 숫자를 노려봐!! _(응원)_",
    "정답은 더 아래에 있어!! 다운이야!! _(안내)_",
)
_UPDOWN_WIN_LINES = (
    "정답!! {target} 맞았어!! _(환호)_",
    "우와, {target}!! 정확히 맞혔어!! _(놀람)_",
    "짜잔, 정답은 {target}이었어!! 맞혔다!! _(신남)_",
    "{target}!! 완벽하게 맞혔네!! _(감탄)_",
    "정답 {target}!! 대단해!! _(박수)_",
    "빙고!! {target} 맞혔다!! _(환호)_",
    "역시!! {target} 정확히 맞혔어!! _(뿌듯)_",
    "{target}이었어!! 딱 걸렸다, 정답!! _(신남)_",
)
_UPDOWN_LOSE_LINES = (
    "아쉽다, 정답은 {target}이었는데!! _(안타까움)_",
    "이런, 정답은 {target}이었어!! 다음엔 맞혀봐!! _(아쉬움)_",
    "땡!! 정답은 {target}이었어!! _(장난)_",
    "3번 다 놓쳤어!! 정답은 {target}!! _(아쉬움)_",
    "아깝다, 정답은 {target}이었어!! _(안타까움)_",
    "결국 못 맞혔네!! 정답은 {target}!! _(위로)_",
    "이번엔 놓쳤어!! 정답은 {target}이었어!! _(아쉬움)_",
    "정답은 {target}!! 다음엔 꼭 맞혀봐!! _(응원)_",
)


async def _forfeit_timeout(message: discord.Message, user_id: int, bet: int) -> None:
    """10분 동안 아무도 안 누르면 포기한 것으로 간주해 배팅액을 전액 몰수한다
    (2026-09-09 — 舊 60초/환불 정책에서 변경, 환불 add_coins 호출 없음). 인터랙션
    토큰이 아니라 메시지 객체를 직접 들고 있다가 edit한다 — 이 메시지가 최초 슬래시
    응답으로 생겼는지(첫 판) 모달 제출로 edit된 건지(다시하기)와 무관하게 항상
    동작한다."""
    # 여기선 ReplayView 자체가 안 뜨니(선택도 안 하고 시간 초과) 그 on_timeout의
    # mark_inactive를 못 거친다 — 이 경로가 "게임 종료"의 유일한 지점이라 직접 호출.
    mark_inactive(user_id)
    try:
        await message.edit(content=random.choice(_BET_FORFEIT_LINES), embed=None, view=None)
    except discord.HTTPException:
        logging.exception("Failed to edit bet prompt on timeout")


async def _maybe_award_win_achievement(user_id: int) -> None:
    """2026-09-10부로 업적 달성 알림(호감도 보너스 포함)은 award() 내부에서 별도
    글로벌 방송으로 처리된다 — 여기서는 조건이 맞을 때 부여만 시도한다."""
    await award_achievement(user_id, achievements.hammie_ez_noob.ID)


def _build_replay_view(user_id: int, game_kind: str) -> ReplayView:
    """다시하기를 누르면 같은 게임 종류로 새 판을 연다 — game_kind를 클로저로 감싸서
    ReplayView(범용, economy_common.py)에 넘긴다. 2026-09-07부터 새 판은
    old_message(이 판의 메시지)를 고쳐쓰지 않고 새 공개 메시지로 열리고,
    old_message는 버튼만 제거해 기록으로 남긴다."""

    async def _on_replay(
        interaction: discord.Interaction, amount: int, old_message: "discord.Message | None"
    ) -> None:
        await _start_round(interaction, user_id, game_kind, amount, is_replay=True)
        if old_message is not None:
            try:
                await old_message.edit(view=None)
            except discord.HTTPException:
                logging.exception("Failed to clear old bet message buttons after replay")

    return ReplayView(user_id, _OWN_COMMAND, _on_replay)


async def _start_round(
    interaction: discord.Interaction, user_id: int, game_kind: str, bet: int, *, is_replay: bool = False
) -> None:
    """모달에서 유효한 금액을 받은 뒤 실제 판을 새 공개 메시지로 연다 — 첫 판이든
    "다시하기"든 항상 새 메시지다(2026-09-07, 이전엔 다시하기가 같은 메시지를
    고쳐써서 이전 판 기록이 사라졌다). 금액 검증(1~MAX_BET)은 모달이 이미 끝냈으니
    여기서는 잔액만 확인한다.

    is_replay=False(신규 진입, 게임 선택 버튼)일 때만 claim_active_or_reject로
    spend_coins보다 먼저 원자적 크로스블록 체크를 한다(2026-09-09) — 이미 다른
    판이 활성 상태면 배팅 자체를 하지 않고 바로 거절해서 환불 로직이 필요 없다.
    is_replay=True(ReplayView 다시하기)는 이미 그 판이 활성 상태인 게 보장돼 있어
    이 체크를 건너뛰고 항상 그대로 갱신한다(economy_common.claim_active_or_reject
    docstring 참고)."""
    if not is_replay and not await claim_active_or_reject(interaction, user_id, _OWN_COMMAND):
        return

    if not await spend_coins(user_id, bet):
        if not is_replay:
            mark_inactive(user_id)
        await interaction.response.send_message(random.choice(INSUFFICIENT_FUNDS_LINES), ephemeral=True)
        return

    # 배팅이 실제로 성립한 시점부터 "진행 중"으로 표시한다(2026-09-09) — 정산 후
    # "다시하기" 버튼이 사라지기 전까지 /내기·/도박 재진입을 막는다
    # (reject_if_already_playing, economy_common.py 참고). "다시하기"로 이어지는
    # 판도 이 함수를 다시 거치므로 계속 갱신되며 끊기지 않는다.
    if is_replay:
        mark_active(user_id, _OWN_COMMAND)

    # vending.py::_execute_purchase와 동일한 역산 — spend_coins가 차감 전 잔액을
    # 반환하지 않아서, 차감 후 조회한 잔액에 배팅액을 다시 더해 "기존 금액"을 구한다.
    user = await get_user(user_id)
    before_coins = user["coins"] + bet
    receipt = format_bet_receipt(before_coins, bet, None)
    # 공개 메시지라 누구의 판인지 한눈에 보이게 도전자 이름을 맨 위에 적는다(2026-09-07
    # 신규) — 서버 안이면 그 서버 별명, 아니면 실제 이름(discord.py의 display_name이
    # 알아서 골라줌). interaction.user는 항상 이 판을 시작한 본인(모달을 연 사람)이다.
    # 2026-09-09 — 마크다운 헤딩(`## `)을 붙여 크게 표시(슬롯머신 그리드에 이미 쓰인
    # 트릭과 동일 — Discord는 일반 메시지 content=에서도 헤딩을 렌더링한다).
    challenger_line = f"## 🎯 도전자: {interaction.user.display_name}"

    if game_kind == _ODD_EVEN:
        view: discord.ui.View = _OddEvenView(user_id, bet, before_coins, interaction.user.display_name)
        content = f"{challenger_line}\n홀?? 짝?? 골라봐!! (배팅: {bet}동전) _(두근)_\n\n{receipt}"
    elif game_kind == _RPS:
        view = _RPSView(user_id, bet, before_coins, interaction.user.display_name)
        content = f"{challenger_line}\n가위?? 바위?? 보?? 골라봐!! (배팅: {bet}동전) _(긴장)_\n\n{receipt}"
    else:
        target = random.randint(1, 20)
        view = _UpDownView(
            user_id, bet, before_coins, interaction.user.display_name, target, 1, 20, 0
        )
        content = (
            f"{challenger_line}\n1~20 사이 숫자를 하나 골라봐!! (배팅: {bet}동전, "
            f"기회 3번) _(두근)_\n\n{receipt}"
        )

    await interaction.response.send_message(content=content, view=view)
    view.message = await interaction.original_response()


class _OddEvenView(discord.ui.View):
    def __init__(self, user_id: int, bet: int, before_coins: int, challenger_name: str) -> None:
        super().__init__(timeout=_BET_ROUND_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.before_coins = before_coins
        self.challenger_name = challenger_name
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        await _forfeit_timeout(self.message, self.user_id, self.bet)

    async def _resolve(self, interaction: discord.Interaction, guess: str) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
            return
        self.stop()

        actual = random.choice(("홀", "짝"))
        if guess == actual:
            result = await add_coins(self.user_id, self.bet * 2, method="bet_odd_even_win")
            text = random.choice(_ODD_EVEN_WIN_LINES).format(actual=actual)
            text += "\n\n" + format_bet_receipt(self.before_coins, self.bet, result["new_coins"])
            if result["achievement_notice"]:
                text += f"\n{result['achievement_notice']}"
            await _maybe_award_win_achievement(self.user_id)
        else:
            user = await get_user(self.user_id)
            text = random.choice(_ODD_EVEN_LOSE_LINES).format(actual=actual)
            text += "\n\n" + format_bet_receipt(self.before_coins, self.bet, user["coins"])
        text = f"## 🎯 도전자: {self.challenger_name}\n{text}"

        replay_view = _build_replay_view(self.user_id, _ODD_EVEN)
        try:
            await interaction.response.edit_message(content=text, view=replay_view)
            replay_view.message = await interaction.original_response()
        except discord.HTTPException:
            # ReplayView가 메시지에 못 붙으면 그 on_timeout이 영영 안 불려
            # mark_inactive도 영영 안 불린다 — 여기서 직접 풀어준다(horse_race.py
            # 감사 중 발견된 동일 계열 버그, 2026-09-09 수정).
            logging.exception("Failed to edit odd-even settlement message")
            mark_inactive(self.user_id)

    @discord.ui.button(label="홀", style=discord.ButtonStyle.primary)
    async def odd(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "홀")

    @discord.ui.button(label="짝", style=discord.ButtonStyle.primary)
    async def even(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "짝")


class _RPSView(discord.ui.View):
    # key가 value를 이긴다 (가위는 보를 이기고, 바위는 가위를 이기고, 보는 바위를 이긴다).
    _BEATS = {"가위": "보", "바위": "가위", "보": "바위"}

    def __init__(self, user_id: int, bet: int, before_coins: int, challenger_name: str) -> None:
        super().__init__(timeout=_BET_ROUND_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.before_coins = before_coins
        self.challenger_name = challenger_name
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        await _forfeit_timeout(self.message, self.user_id, self.bet)

    async def _resolve(self, interaction: discord.Interaction, choice: str) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
            return
        self.stop()

        actual = random.choice(("가위", "바위", "보"))
        actual_bold = f"**{actual}**"
        if choice == actual:
            result = await add_coins(self.user_id, self.bet, method="bet_rps_draw", count_as_earned=False)
            text = random.choice(_RPS_DRAW_LINES).format(actual=actual_bold)
            text += "\n\n" + format_bet_receipt(self.before_coins, self.bet, result["new_coins"])
        elif self._BEATS[choice] == actual:
            result = await add_coins(self.user_id, self.bet * 2, method="bet_rps_win")
            text = random.choice(_RPS_WIN_LINES).format(actual=actual_bold)
            text += "\n\n" + format_bet_receipt(self.before_coins, self.bet, result["new_coins"])
            if result["achievement_notice"]:
                text += f"\n{result['achievement_notice']}"
            await _maybe_award_win_achievement(self.user_id)
        else:
            user = await get_user(self.user_id)
            text = random.choice(_RPS_LOSE_LINES).format(actual=actual_bold)
            text += "\n\n" + format_bet_receipt(self.before_coins, self.bet, user["coins"])
        text = f"## 🎯 도전자: {self.challenger_name}\n{text}"

        replay_view = _build_replay_view(self.user_id, _RPS)
        try:
            await interaction.response.edit_message(content=text, view=replay_view)
            replay_view.message = await interaction.original_response()
        except discord.HTTPException:
            logging.exception("Failed to edit rock-paper-scissors settlement message")
            mark_inactive(self.user_id)

    @discord.ui.button(label="가위", style=discord.ButtonStyle.primary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "가위")

    @discord.ui.button(label="바위", style=discord.ButtonStyle.primary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "바위")

    @discord.ui.button(label="보", style=discord.ButtonStyle.primary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "보")


class _UpDownSelect(discord.ui.Select):
    """현재 시도의 유효 범위(low~high)만 옵션으로 보여주는 셀렉트 — 시도마다
    `_UpDownView`가 통째로 새로 만들어지므로 이 컴포넌트도 매번 새로 생성된다
    (vending.py::_ItemSelect와 동일한 결 — 옵션이 바뀌면 기존 컴포넌트를 고쳐쓰지
    않고 새로 만든다)."""

    def __init__(self, low: int, high: int) -> None:
        options = [discord.SelectOption(label=str(n), value=str(n)) for n in range(low, high + 1)]
        super().__init__(placeholder="숫자를 골라줘!!", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: _UpDownView = self.view
        await view._handle_guess(interaction, int(self.values[0]))


class _UpDownView(discord.ui.View):
    """업다운(2026-09-09 신규) — 햄미가 1~20 중 하나(target)를 몰래 정하고, 유저는
    셀렉트로 최대 3번 시도한다. 시도마다 범위가 좁혀진 새 View+새 Select로 통째로
    갈아끼운다(한 라운드=한 메시지, 슬롯머신이 스핀마다 같은 메시지를 고쳐쓰는 것과
    동일한 결 — "다시하기"만 새 메시지)."""

    def __init__(
        self,
        user_id: int,
        bet: int,
        before_coins: int,
        challenger_name: str,
        target: int,
        low: int,
        high: int,
        attempts_used: int,
    ) -> None:
        super().__init__(timeout=_BET_ROUND_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.bet = bet
        self.before_coins = before_coins
        self.challenger_name = challenger_name
        self.target = target
        self.low = low
        self.high = high
        self.attempts_used = attempts_used
        self.message: discord.Message | None = None
        self.add_item(_UpDownSelect(low, high))

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        await _forfeit_timeout(self.message, self.user_id, self.bet)

    async def _handle_guess(self, interaction: discord.Interaction, guess: int) -> None:
        if not await reject_if_already_resolved(self, interaction):
            return
        if not await reject_if_wrong_user_with_cta(interaction, self.user_id, _OWN_COMMAND):
            return
        self.stop()

        attempts_used = self.attempts_used + 1

        if guess == self.target:
            result = await add_coins(self.user_id, self.bet * 3, method="bet_updown_win")
            text = random.choice(_UPDOWN_WIN_LINES).format(target=self.target)
            text += "\n\n" + format_bet_receipt(self.before_coins, self.bet, result["new_coins"])
            if result["achievement_notice"]:
                text += f"\n{result['achievement_notice']}"
            await _maybe_award_win_achievement(self.user_id)
            text = f"## 🎯 도전자: {self.challenger_name}\n{text}"
            replay_view = _build_replay_view(self.user_id, _UP_DOWN)
            try:
                await interaction.response.edit_message(content=text, view=replay_view)
                replay_view.message = await interaction.original_response()
            except discord.HTTPException:
                logging.exception("Failed to edit up-down win settlement message")
                mark_inactive(self.user_id)
            return

        if attempts_used >= 3:
            # 정상적으로 3번 다 틀려서 끝난 패배 — 무응답 몰수(_forfeit_timeout)와는
            # 다르다. 실제로 선택을 했으니 "다시하기"가 정상적으로 뜬다.
            user = await get_user(self.user_id)
            text = random.choice(_UPDOWN_LOSE_LINES).format(target=self.target)
            text += "\n\n" + format_bet_receipt(self.before_coins, self.bet, user["coins"])
            text = f"## 🎯 도전자: {self.challenger_name}\n{text}"
            replay_view = _build_replay_view(self.user_id, _UP_DOWN)
            try:
                await interaction.response.edit_message(content=text, view=replay_view)
                replay_view.message = await interaction.original_response()
            except discord.HTTPException:
                logging.exception("Failed to edit up-down lose settlement message")
                mark_inactive(self.user_id)
            return

        if guess < self.target:
            hint = random.choice(_UPDOWN_UP_LINES)
            new_low, new_high = guess + 1, self.high
        else:
            hint = random.choice(_UPDOWN_DOWN_LINES)
            new_low, new_high = self.low, guess - 1

        remaining = 3 - attempts_used
        receipt = format_bet_receipt(self.before_coins, self.bet, None)
        content = (
            f"## 🎯 도전자: {self.challenger_name}\n{hint} (남은 기회: {remaining}번)"
            f"\n\n{receipt}"
        )
        new_view = _UpDownView(
            self.user_id,
            self.bet,
            self.before_coins,
            self.challenger_name,
            self.target,
            new_low,
            new_high,
            attempts_used,
        )
        try:
            await interaction.response.edit_message(content=content, view=new_view)
            new_view.message = await interaction.original_response()
        except discord.HTTPException:
            # 여기서 실패하면 self.stop()은 이미 호출된 뒤라 이 뷰는 더 이상
            # 아무것도 안 하고, 새 view도 못 붙어 게임이 통째로 멈춘다 — 잠금을
            # 풀어줘야 유저가 새 판을 다시 시작할 수 있다.
            logging.exception("Failed to edit up-down continue message")
            mark_inactive(self.user_id)


class _GameSelectView(EphemeralAutoDeleteView):
    """/내기 실행 직후 뜨는 ephemeral 프롬프트 — 본인에게만 보이므로 "다른 사람이
    눌렀을 때" 처리는 애초에 불필요하다(디스코드가 다른 사람에게 아예 안 보여준다)."""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.user_id = user_id

    async def _select(self, interaction: discord.Interaction, game_kind: str) -> None:
        self.bump()
        user = await get_user(self.user_id)
        balance = user["coins"] if user is not None else 0

        async def _on_valid(modal_interaction: discord.Interaction, amount: int) -> None:
            await _start_round(modal_interaction, self.user_id, game_kind, amount)
            # 게임이 실제로 시작됐으니(= 공개 메시지가 새로 생겼으니) 애초의 ephemeral
            # 선택 프롬프트는 이제 볼일이 없다 — 지운다.
            try:
                await self.interaction.delete_original_response()
            except discord.HTTPException:
                logging.exception("Failed to delete game-select prompt after game start")

        await interaction.response.send_modal(BetAmountModal(balance=balance, on_valid=_on_valid))

    @discord.ui.button(label="홀짝", style=discord.ButtonStyle.primary)
    async def odd_even(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._select(interaction, _ODD_EVEN)

    @discord.ui.button(label="가위바위보", style=discord.ButtonStyle.primary)
    async def rps(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._select(interaction, _RPS)

    @discord.ui.button(label="업다운", style=discord.ButtonStyle.primary)
    async def up_down(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._select(interaction, _UP_DOWN)


async def handle_bet(interaction: discord.Interaction) -> None:
    """/내기 진입점 — 이미 ephemeral로 defer된 상태라고 가정하고 edit_original_response로
    게임 선택 프롬프트(인트로 문구 + 임베드 + 홀짝/가위바위보 버튼)를 보여준다.

    2026-09-09부터 이미 진행 중인 /내기·/도박 판이 있으면(크로스 포함) 여기서
    막힌다 — reject_if_already_playing이 이미 defer된 응답을 대신 채운다."""
    if not await reject_if_already_playing(interaction, interaction.user.id):
        return
    user = await get_user(interaction.user.id)
    balance = user["coins"] if user is not None else 0

    embed = discord.Embed(title="🎲 내기", color=GAMBLING_EMBED_COLOR)
    embed.description = (
        f"현재 보유 동전 : {balance}개\n"
        "햄미와 내기를 하여 승리 시 배팅 금액의 2배, 패배 시 모두 잃습니다.\n"
        "자세한 규칙은 `/내기-규칙` 을 통해 확인할 수 있습니다."
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))

    view = _GameSelectView(interaction.user.id)
    await interaction.edit_original_response(
        content=random.choice(_GAME_SELECT_INTRO_LINES), embed=embed, view=view
    )
    view.interaction = interaction


async def handle_rules() -> tuple[str, discord.Embed, discord.ui.View]:
    """/내기-규칙 진입점 — ephemeral. 개요 임베드 + 게임별 버튼(RulesView)을 보여주고,
    버튼을 누르면 그 게임의 상세 규칙으로 임베드만 바꿔치기한다."""
    embed = discord.Embed(title="🎲 내기 규칙", description=_RULES_OVERVIEW_TEXT, color=GAMBLING_EMBED_COLOR)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    view = RulesView(
        "🎲 내기 규칙",
        {"홀짝": _ODD_EVEN_RULE_TEXT, "가위바위보": _RPS_RULE_TEXT, "업다운": _UPDOWN_RULE_TEXT},
        color=GAMBLING_EMBED_COLOR,
    )
    return random.choice(_RULES_INTRO_LINES), embed, view
