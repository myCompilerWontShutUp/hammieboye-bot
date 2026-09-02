from db.achievements import maybe_award_affection_milestones
from db.client import rpc


async def add_affection(user_id: int, amount: int, method: str | None = None) -> dict:
    """호감도를 원자적으로 증감시킨다 (일일 +20 상한은 DB 함수가 알아서 처리).

    amount는 양수(획득)/음수(하락) 둘 다 가능. 반환값은
    {applied_amount, new_affection, new_daily_gain, achievement_notice}.
    achievement_notice는 이번 호출로 새로 얻은 업적이 있으면 그 안내 문구(여러 개면
    줄바꿈으로 합쳐짐), 없으면 None.
    """
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
    user_id: int, amount: int, method: str | None = None, *, check_achievements: bool = True
) -> dict:
    """일일 +20 획득 상한 계산을 건너뛰고 무조건 적용한다 (예: 취침 중 깨움 이벤트의 악몽 감사 +5).

    반환값은 {new_affection, achievement_notice} — uncapped RPC는 부분지급이 없어 amount가
    곧 실제 적용량이다.

    check_achievements=False면 마일스톤 확인을 건너뛴다 — 관리자 콘솔의 `la up`/`la down`
    전용: 관리자가 직접 수치를 조작하는 명령어로는 업적이 달성되면 안 된다. `la set`/
    `la reset`은 이 함수 자체를 안 쓰는 별도 RPC(`set_affection`)라 이미 안전하다.
    """
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
    return {"new_affection": new_affection, "achievement_notice": achievement_notice}


def format_affection_notice(delta: int, current: int) -> str:
    """호감도 변화를 하트 이모지와 함께 알려주는 문구. delta==0이면 호출하지 않는다."""
    emoji = "💕" if delta > 0 else "💔"
    sign = "+" if delta > 0 else ""
    return f"\n{emoji} 호감도 {sign}{delta} (현재 {current})"
