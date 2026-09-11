import asyncio
from datetime import datetime, timezone

from db.achievements import maybe_award_affection_milestones
from db.client import rpc
from events.announcements import grant_affection_xp
from events.scheduler import KST
from events.special_days import get_event_label, get_multiplier


def _multiplied(amount: int) -> int:
    """양수(획득)에만 오늘의 주말/기념일/생일 배율을 곱한다. 하락(음수)은 그대로."""
    if amount <= 0:
        return amount
    return amount * get_multiplier(datetime.now(timezone.utc).astimezone(KST).date())


async def add_affection(
    user_id: int,
    amount: int,
    method: str | None = None,
    *,
    apply_day_multiplier: bool = True,
    guild_id: int | None = None,
) -> dict:
    """호감도를 원자적으로 증감시킨다 (일일 획득 상한은 DB 함수가 알아서 처리 — 2026-09-10부로
    상한값 자체가 2147483647(사실상 무제한)로 올라갔지만, 메커니즘은 그대로라 필요하면
    언제든 값만 다시 낮출 수 있다. supabase/schema.sql의 add_affection RPC 참고).

    amount는 양수(획득)/음수(하락) 둘 다 가능. 반환값은
    {applied_amount, new_affection, new_daily_gain, achievement_notice}.
    achievement_notice는 이번 호출로 새로 얻은 업적이 있으면 그 안내 문구(여러 개면
    줄바꿈으로 합쳐짐), 없으면 None.

    apply_day_multiplier=False면 주말/기념일/생일 배율을 건너뛴다 — 업적 달성 보너스
    (db/achievements.py::award())처럼 날짜와 무관하게 항상 고정 수치여야 하는 호출 전용.

    guild_id는 이 호감도 변화를 유발한 활동이 있었던 서버 — 알고 있으면(호출부가
    interaction/message에서 넘겨주면) 이로 인해 트리거되는 레벨업/업적 전 서버
    방송에서 그 서버를 가장 먼저 보낸다(2026-09-11).
    """
    if apply_day_multiplier:
        amount = _multiplied(amount)
    rows = await rpc(
        "add_affection",
        {"p_user_id": user_id, "p_amount": amount, "p_method": method},
    )
    result = rows[0]
    # 호감도 마일스톤 확인과 XP 적립은 서로 독립적인 부수 효과라(하나는
    # user_achievements를, 하나는 daily_stats의 XP 캡 컬럼+users.total_xp를 건드림)
    # 순차로 기다릴 이유가 없다 — asyncio.gather로 동시에 보내 왕복 시간을 겹친다
    # (2026-09-11, 체감 지연 개선). 레벨/XP 시스템(2026-09-10)이 호감도가 바뀌는
    # 모든 경로(관리자 fl 조작, 암시장 확률형 간식 uncapped 경로 포함)를 놓치지 않기
    # 위해 add_affection_uncapped와 함께 이 성공 경로 끝에서 호출한다.
    # applied_amount<=0이면 grant_affection_xp가 알아서 아무것도 안 한다.
    achievement_notice, _ = await asyncio.gather(
        maybe_award_affection_milestones(
            user_id, result["applied_amount"], result["new_affection"], guild_id=guild_id
        ),
        grant_affection_xp(user_id, result["applied_amount"], guild_id=guild_id),
    )
    result["achievement_notice"] = achievement_notice
    return result


async def add_affection_uncapped(
    user_id: int,
    amount: int,
    method: str | None = None,
    *,
    check_achievements: bool = True,
    apply_day_multiplier: bool = True,
    guild_id: int | None = None,
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

    guild_id는 add_affection과 동일 — 전 서버 방송 시 우선 전송할 서버.
    """
    if apply_day_multiplier:
        amount = _multiplied(amount)
    rows = await rpc(
        "add_affection_uncapped",
        {"p_user_id": user_id, "p_amount": amount, "p_method": method},
    )
    new_affection = rows[0]["new_affection"]
    # check_achievements 플래그와 무관하게 grant_affection_xp는 항상 호출한다 —
    # 업적 마일스톤 재귀 방지와 XP 적립은 서로 다른 관심사다(관리자 fl 조작도
    # 호감도가 실제로 바뀌었으면 XP는 받는다, 계획 확정 사항). 둘 다 필요한 경우
    # asyncio.gather로 동시에 보내 왕복 시간을 겹친다(2026-09-11, add_affection과
    # 동일한 이유).
    if check_achievements:
        achievement_notice, _ = await asyncio.gather(
            maybe_award_affection_milestones(user_id, amount, new_affection, guild_id=guild_id),
            grant_affection_xp(user_id, amount, guild_id=guild_id),
        )
    else:
        achievement_notice = None
        await grant_affection_xp(user_id, amount, guild_id=guild_id)
    return {
        "applied_amount": amount,
        "new_affection": new_affection,
        "achievement_notice": achievement_notice,
    }


def format_affection_notice(delta: int, current: int, *, multiplier_eligible: bool = True) -> str:
    """호감도 변화를 하트 이모지와 함께 알려주는 문구. delta==0이면 호출하지 않는다.

    양수 획득이고 오늘이 배율이 붙는 날(주말/기념일/생일)이면서 delta가 그 배율로 딱
    나누어떨어지면 "호감도 10 x 2배 (주말 이벤트)"처럼 배율을 눈에 띄게 보여주고 하트도
    더 특별한 색(🩷)으로 바꾼다.

    multiplier_eligible=False면 이 분해를 아예 시도하지 않는다 — delta가 나누어
    떨어지는지는 순전히 우연일 뿐, 실제로 배율이 적용됐는지는 알려주지 않기 때문이다.
    업적 달성 보너스(§15)는 항상 apply_day_multiplier=False로 지급되는데, 예를 들어
    전설 보너스 +10이 주말(배율 2배)과 겹치면 10%2==0이라 아무 정보 없이 "5 x 2"처럼
    잘못 분해해 보여주는 문제가 있었다 — 배율이 실제로 적용됐는지("하루 최대 획득
    호감도"에 포함되는 경로를 탔는지)를 호출부가 명시적으로 알려주게 해서 고쳤다.
    호출부가 여러 성분(예: 기본 지급 + 업적 보너스)을 하나의 delta로 합산했다면,
    그중 하나라도 배율 미적용 성분이 섞이는 순간 반드시 False를 넘겨야 한다.

    2026-09-06부터 "(현재 N)"이 아니라 "(전 → 후)"로 보여준다 — delta와 current(변경
    후 값)만 있으면 before = current - delta로 계산 가능해서 호출부 변경은 불필요."""
    before = current - delta
    if delta > 0:
        if multiplier_eligible:
            today = datetime.now(timezone.utc).astimezone(KST).date()
            multiplier = get_multiplier(today)
            if multiplier > 1 and delta % multiplier == 0:
                base = delta // multiplier
                label = get_event_label(today)
                return f"\n🩷 호감도 {base} x {multiplier}배 ({label}) ({before} → {current})"
        return f"\n💕 호감도 +{delta} ({before} → {current})"
    return f"\n💔 호감도 {delta} ({before} → {current})"
