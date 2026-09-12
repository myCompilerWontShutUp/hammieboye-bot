from db.client import rpc

# "드링킹 타임"(§24, 2026-09-12 신규) 도입에 따라, 디저트 타임 3슬롯(아침/점심/저녁)
# 각각이 그날 "dessert"(간식)인지 "drink"(음료)인지를 전역(서버 무관, 유저 무관)으로
# 딱 하루 한 번만 결정해야 한다 — events/dessert_time.py::get_or_roll_slot_kind()가
# 이 모듈을 감싸 실제 50/50 굴림을 담당한다.


async def claim_slot_kind(stat_date: str, slot: str, kind: str) -> str:
    """(stat_date, slot) 조합이 아직 없으면 kind로 확정하고, 이미 있으면(다른 호출이
    먼저 확정했으면) 그 커밋된 값을 그대로 돌려준다 — claim_dessert_slot과 동일한
    "먼저 도착한 쪽이 이긴다" idiom. 오픈 방송(events/dessert_time.py::broadcast_open)과
    /사용 급여 시점(command/eat.py) 둘 다 이 함수를 호출하므로, 어느 쪽이 먼저 와도
    항상 같은 결과로 수렴한다."""
    return await rpc(
        "claim_dessert_slot_kind",
        {"p_stat_date": stat_date, "p_slot": slot, "p_kind": kind},
    )
