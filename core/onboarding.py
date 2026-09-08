import random
from datetime import datetime, timedelta, timezone

from events.scheduler import KST
from db.users import ensure_user
from db.withdrawals import get_withdrawal

# 2026-09-08부로 별도 동의(/가입) 절차가 폐지됐다 — 정책 검토 결과 명시적 opt-in을
# 받을 법적 의무가 없다고 확인되어, 한 번이라도 햄미와 상호작용하면(자연어 호출 단어든
# 슬래시 커맨드든) 그 즉시 정보 수집을 시작한다. 이 모듈은 이제 "언제든 안내 문구를
# 골라 보여주는" 역할이 아니라, 모든 상호작용 진입점(core/dispatcher.py의 on_message,
# core/slash_commands.py의 _prepare)이 공통으로 거치는 자동 등록 게이트다.
#
# 유일한 예외는 /탈퇴 직후 재가입 쿨타임 — 탈퇴로 데이터를 지운 뒤 곧바로 다시
# 말을 걸어 상태를 초기화하는 악용(예: 낮은 호감도 회피, 업적/카운터 리셋)을 막기
# 위해 그대로 유지한다(사용자 확인 완료). 이 기간에는 등록 자체를 보류하고 안내
# 문구만 돌려준다. 기간은 최초 24시간에서 2026-09-08 30일로 연장됐다(사용자 요청) —
# 이 기간이 지나면 별도 절차 없이 그냥 다시 말을 걸거나 슬래시 커맨드를 쓰기만 해도
# provision()이 자동으로 재등록한다(아래 안내 문구 풀에 이 사실을 명시).
_COOLDOWN = timedelta(days=30)

# {withdrawn}/{eligible}은 각각 탈퇴 시각/재상호작용 가능 시각(KST)으로 채워진다.
# 2026-09-08 30일 연장과 함께, "그 이후엔 별도 절차 없이 자동으로 다시 등록된다"는
# 사실을 매 문구에 명시했다 — 탈퇴 후 오래 지난 사용자가 "/가입" 같은 걸 따로 찾을
# 필요가 없다는 걸 이 문구 자체로 전달하기 위함.
_COOLDOWN_TEMPLATE_LINES = (
    "너 {withdrawn}에 탈퇴했었잖아!! {eligible}부터는 그냥 아무 말이나 걸어도 자동으로 다시 등록돼!! _(안내)_",
    "음... {withdrawn}에 떠났었네?? {eligible} 이후에 다시 말 걸면 자동으로 등록될 거야!! _(아쉬움)_",
    "잠깐, {withdrawn}에 탈퇴 기록이 이써!! {eligible}부터는 따로 뭘 안 해도 자동으로 다시 등록돼!! _(설명)_",
    "탈퇴한 지 얼마 안 됐어!! ({withdrawn} 탈퇴, {eligible}부터 아무 명령어나 쓰면 자동 등록) _(진지)_",
    "{withdrawn}에 헤어졌었지... {eligible}에 다시 말 걸어주면 자동으로 등록될게!! _(그리움)_",
    "아직은 안 대!! {withdrawn}에 탈퇴했으니까 {eligible}부터 다시 말 걸어줘, 그럼 자동으로 등록돼!! _(단호)_",
    "너 {withdrawn}에 나갔었어!! {eligible} 지나면 그냥 말 걸기만 해도 자동으로 다시 등록돼!! _(기다림)_",
    "조금만 기다려줘!! {withdrawn} 탈퇴 → {eligible}부터는 따로 가입 안 해도 자동 등록이야!! _(부탁)_",
    "탈퇴 기록이 남아 이써!! ({withdrawn}) {eligible}부터 아무 때나 말 걸면 자동으로 등록될 거야!! _(안내)_",
    "아직 30일이 안 지났어!! {withdrawn}에 탈퇴, {eligible}부터 가능하고 그땐 자동으로 등록돼!! _(설명)_",
    "{withdrawn}에 떠났던 거 기억나!! {eligible}에 다시 이야기해주면 자동으로 다시 등록돼!! _(서운)_",
    "지금은 안 대!! {withdrawn} 탈퇴니까 {eligible}부터 다시 시도해줘, 자동으로 등록될 거야!! _(진지)_",
    "잠깐 헤어진 사이잖아!! {withdrawn} 탈퇴, {eligible}에 말만 걸어도 자동으로 다시 등록돼!! _(기대)_",
    "쿨타임 중이야!! {withdrawn}에 탈퇴했고 {eligible}부터 풀려, 그때 말 걸면 자동 등록!! _(안내)_",
    "{withdrawn}에 나갔었지?? {eligible}까지 조금만 기다려줘, 그 이후엔 자동으로 다시 등록돼!! _(부탁)_",
    "다시 시작하는 건 {eligible}부터야!! ({withdrawn}에 탈퇴했었어, 그때 말만 걸면 자동 등록) _(단호)_",
    "아직 시간이 덜 지났어!! {withdrawn} 탈퇴 → {eligible} 이후 아무 말이나 걸면 자동 등록!! _(설명)_",
    "{withdrawn}에 헤어졌으니 {eligible}까지 기다려줘, 그 뒤엔 따로 뭘 안 해도 자동으로 다시 등록돼!! _(아쉬움)_",
    "우리 다시 만나려면 {eligible}까지 기다려야 해!! ({withdrawn} 탈퇴, 그 이후엔 자동 등록) _(그리움)_",
    "탈퇴한 지 30일이 안 지났어!! {withdrawn} → {eligible}부터 말만 걸면 자동으로 다시 등록돼!! _(안내)_",
)


def _format_kst(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")


async def provision(user_id: int) -> tuple[dict | None, str | None]:
    """모든 상호작용 진입점이 공통으로 거치는 자동 등록 — 반환값은
    (유저 행, None) 정상 진행 또는 (None, 안내 문구) 쿨타임 차단.

    호출부는 두 번째 값이 None이 아니면 아무 것도 더 진행하지 않아야 한다. 안내 문구를
    실제로 응답에 쓸지는 호출부 재량이다 — 슬래시 커맨드(`core/slash_commands.py
    ::_prepare`)는 ephemeral로 그대로 보여주지만, 자연어 경로(`core/dispatcher.py
    ::on_message`)는 2026-09-08부로 이 문구를 아예 쓰지 않고 완전히 무응답 처리한다
    (공개 채널에 탈퇴 사실이 반복 노출되지 않도록)."""
    withdrawal = await get_withdrawal(user_id)
    if withdrawal is not None:
        withdrawn_at = datetime.fromisoformat(withdrawal["withdrawn_at"])
        eligible_at = withdrawn_at + _COOLDOWN
        if datetime.now(timezone.utc) < eligible_at:
            message = random.choice(_COOLDOWN_TEMPLATE_LINES).format(
                withdrawn=_format_kst(withdrawn_at), eligible=_format_kst(eligible_at)
            )
            return None, message

    user = await ensure_user(user_id)
    return user, None
