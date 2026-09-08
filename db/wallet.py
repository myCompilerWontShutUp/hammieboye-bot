from datetime import datetime, timezone

import achievements
from db.achievements import award as award_achievement
from db.client import rpc, update
from events.scheduler import KST
from events.special_days import get_multiplier

# 1,000코인("티끌 모아 티끌" 업적 기준 — 2026-09-05부터 "원" 단위 개념을 없애면서
# 문구도 "100,000원"에서 "1,000코인"으로 바뀌었지만, 코인 기준 수치 자체는 그대로다).
_PENNY_PINCHER_THRESHOLD = 1_000


def _multiplied(amount: int) -> int:
    """db/affection.py::_multiplied()와 동일한 원칙 — 양수(획득)에만 오늘의 주말/
    기념일/생일 배율을 곱한다."""
    if amount <= 0:
        return amount
    return amount * get_multiplier(datetime.now(timezone.utc).astimezone(KST).date())


async def add_coins(
    user_id: int,
    amount: int,
    method: str | None = None,
    *,
    count_as_earned: bool = True,
    apply_day_multiplier: bool = False,
) -> dict:
    """동전을 원자적으로 지급한다. 2026-09-05부로 보유 상한 개념이 폐지돼 클램프 없이
    그대로 더한다.

    amount는 항상 양수(획득)로 호출한다 — 차감은 spend_coins/deduct_coins_clamped를
    쓴다. 반환값은 {applied_amount, new_coins, new_lifetime_coins_earned,
    achievement_notice} — applied_amount는 이제 항상 amount와 같지만(과거 용량 클램프
    때의 반환 형태를 그대로 유지 — 호출부 다수가 이미 이 계약에 의존). achievement_notice는
    이번 호출로 "티끌 모아 티끌"을 새로 얻었으면 그 안내 문구(순수 🏆 텍스트만, 호감도
    델타는 안 섞임 — db/affection.py::maybe_award_affection_milestones과 동일한 계약),
    아니면 None. 모든 코인 획득 경로가 add_coins를 거치므로 이 함수 한곳에서만 체크하면
    전부 커버된다(add_affection의 마일스톤 훅과 동일한 중앙화 원칙).

    count_as_earned=False면 users.lifetime_coins_earned를 안 늘린다 — 무승부/배팅
    타임아웃 환불처럼 "실제로 번 게 아니라 원금을 그대로 돌려주는" 경우 전용(이 경우
    lifetime_coins_earned가 안 늘어나므로 아래 마일스톤도 자연히 새로 안 걸린다).

    apply_day_multiplier=True면 db/affection.py::add_affection()과 동일하게 오늘의
    주말/기념일/생일 배율을 곱한다(2026-09-07 신규) — 기본값은 False라서 대부분의
    호출부(내기/도박 승리금, 자판기, 관리자 조작 등)는 그대로 배율 미적용이고,
    /동전(command/coin.py) 지급 한 곳에서만 True로 넘긴다 — "내기·도박 승리금엔
    배율이 안 붙고 오직 /동전 지급에만 적용된다"는 요구사항이 이 파라미터
    하나로 정확히 구현된다.
    """
    if apply_day_multiplier:
        amount = _multiplied(amount)
    rows = await rpc(
        "add_coins",
        {
            "p_user_id": user_id,
            "p_amount": amount,
            "p_method": method,
            "p_count_as_earned": count_as_earned,
        },
    )
    result = rows[0]
    result["achievement_notice"] = await _maybe_award_penny_pincher(
        user_id, result["new_lifetime_coins_earned"]
    )
    return result


async def _maybe_award_penny_pincher(user_id: int, new_lifetime_coins_earned: int) -> str | None:
    if new_lifetime_coins_earned < _PENNY_PINCHER_THRESHOLD:
        return None
    result = await award_achievement(user_id, achievements.penny_pincher.ID)
    if not result["earned"]:
        return None
    return f"🏆 업적 달성: {achievements.format_name(achievements.penny_pincher)}!!"


async def spend_coins(user_id: int, amount: int) -> bool:
    """잔액이 충분할 때만 원자적으로 차감한다 (claim_call_event와 동일한 조건부
    UPDATE 방식) — 배팅/자판기 구매 전용. 실패(False)면 아무것도 안 바뀐다."""
    return await rpc("spend_coins", {"p_user_id": user_id, "p_amount": amount})


async def deduct_coins_clamped(user_id: int, amount: int) -> dict:
    """0 밑으로 안 내려가는 차감 — 슬롯머신 햄스터 페널티 전용. 항상 성공하고,
    잔액이 amount보다 적으면 있는 만큼만 뗀다. 반환값은 {deducted, new_coins}."""
    rows = await rpc("deduct_coins_clamped", {"p_user_id": user_id, "p_amount": amount})
    return rows[0]


async def increase_coin_grant_bonus(user_id: int, amount: int) -> int:
    """/동전 기본 지급량(1개)에 더해지는 보너스를 늘린다 (자판기 그랜트 부스터 품목
    전용, 반복 가능)."""
    return await rpc("increase_coin_grant_bonus", {"p_user_id": user_id, "p_amount": amount})


async def decrease_coin_grant_bonus(user_id: int, amount: int) -> dict:
    """coin_grant_bonus를 최대 amount만큼 원자적으로 줄인다 — 관리자 itm remove 전용
    (투자 품목 회수). 0 밑으로 안 내려가며, 보유량보다 많이 빼려 하면 있는 만큼만
    뺀다. 반환값은 {removed, new_bonus}."""
    rows = await rpc("decrease_coin_grant_bonus", {"p_user_id": user_id, "p_amount": amount})
    return rows[0]


async def reset_coin_grant_bonus(user_id: int) -> None:
    """coin_grant_bonus를 0으로 리셋한다 — 관리자 itm clear 전용."""
    await update("users", {"user_id": f"eq.{user_id}"}, {"coin_grant_bonus": 0})
