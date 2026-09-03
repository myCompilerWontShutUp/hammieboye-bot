from datetime import datetime, timezone

from db.achievements import maybe_award_affection_milestones
from db.client import rpc
from events.scheduler import KST
from events.special_days import get_event_label, get_multiplier


def _multiplied(amount: int) -> int:
    """양수(획득)에만 오늘의 주말/기념일/생일 배율을 곱한다. 하락(음수)은 그대로."""
    if amount <= 0:
        return amount
    return amount * get_multiplier(datetime.now(timezone.utc).astimezone(KST).date())


async def add_affection(
    user_id: int, amount: int, method: str | None = None, *, apply_day_multiplier: bool = True
) -> dict:
    """호감도를 원자적으로 증감시킨다 (일일 +100 상한은 DB 함수가 알아서 처리).

    amount는 양수(획득)/음수(하락) 둘 다 가능. 반환값은
    {applied_amount, new_affection, new_daily_gain, achievement_notice}.
    achievement_notice는 이번 호출로 새로 얻은 업적이 있으면 그 안내 문구(여러 개면
    줄바꿈으로 합쳐짐), 없으면 None.

    apply_day_multiplier=False면 주말/기념일/생일 배율을 건너뛴다 — 업적 달성 보너스
    (db/achievements.py::award())처럼 날짜와 무관하게 항상 고정 수치여야 하는 호출 전용.
    """
    if apply_day_multiplier:
        amount = _multiplied(amount)
    rows = await rpc(
        "add_affection",
        {"p_user_id": user_id, "p_amount": amount, "p_method": method},
    )
    result = rows[0]
    result["achievement_notice"] = await maybe_award_affection_milestones(
        user_id, result["applied_amount"], result["new_affection"]
    )
    return result


async def add_affection_uncapped(
    user_id: int,
    amount: int,
    method: str | None = None,
    *,
    check_achievements: bool = True,
    apply_day_multiplier: bool = True,
) -> dict:
    """일일 +100 획득 상한 계산을 건너뛰고 무조건 적용한다 (예: 취침 중 깨움 이벤트의 악몽 감사 +5).

    반환값은 {applied_amount, new_affection, achievement_notice} — uncapped RPC는 부분지급이
    없어 배율 적용 후의 amount가 곧 실제 적용량이다(apply_day_multiplier=True면 원래 넘긴
    amount와 다를 수 있으니, 알림 문구 등에는 파라미터로 받은 원본이 아니라 반드시 이
    applied_amount를 써야 한다).

    check_achievements=False면 마일스톤 확인을 건너뛴다 — 관리자 콘솔의 `fl up`/`fl down`
    전용: 관리자가 직접 수치를 조작하는 명령어로는 업적이 달성되면 안 된다. `fl set`/
    `fl reset`은 이 함수 자체를 안 쓰는 별도 RPC(`set_affection`)라 이미 안전하다.

    apply_day_multiplier=False면 주말/기념일/생일 배율을 건너뛴다 — `add_affection`과
    동일한 이유(업적 달성 보너스 전용).
    """
    if apply_day_multiplier:
        amount = _multiplied(amount)
    rows = await rpc(
        "add_affection_uncapped",
        {"p_user_id": user_id, "p_amount": amount, "p_method": method},
    )
    new_affection = rows[0]["new_affection"]
    achievement_notice = (
        await maybe_award_affection_milestones(user_id, amount, new_affection)
        if check_achievements
        else None
    )
    return {
        "applied_amount": amount,
        "new_affection": new_affection,
        "achievement_notice": achievement_notice,
    }


def format_affection_notice(delta: int, current: int) -> str:
    """호감도 변화를 하트 이모지와 함께 알려주는 문구. delta==0이면 호출하지 않는다.

    양수 획득이고 오늘이 배율이 붙는 날(주말/기념일/생일)이면서 delta가 그 배율로 딱
    나누어떨어지면 "호감도 10 x 2배 (주말 이벤트)"처럼 배율을 눈에 띄게 보여주고 하트도
    더 특별한 색(🩷)으로 바꾼다. 한 턴에 배율이 안 붙는 다른 획득(예: 업적 보너스)이
    같이 섞여 delta가 배율로 안 나누어떨어지면, 틀린 분해를 보여주느니 평소 형식(💕)으로
    그냥 총합만 보여준다."""
    if delta > 0:
        today = datetime.now(timezone.utc).astimezone(KST).date()
        multiplier = get_multiplier(today)
        if multiplier > 1 and delta % multiplier == 0:
            base = delta // multiplier
            label = get_event_label(today)
            return f"\n🩷 호감도 {base} x {multiplier}배 ({label}) (현재 {current})"
        return f"\n💕 호감도 +{delta} (현재 {current})"
    return f"\n💔 호감도 {delta} (현재 {current})"
