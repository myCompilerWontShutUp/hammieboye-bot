from db.client import delete, rpc, select
from db.daily_stats import kst_today_str

# 쳇바퀴 에너지 드링크(암시장 포션, §24)가 그날 밤 취침 시각을 30분 늦추는 효과 —
# events/scheduler.py의 지연-기상 메커니즘(_late_wake_date/mark_late_wake)과 정확히
# 거울상인 지연-취침 메커니즘의 DB 저장분. 인메모리 플래그(events/scheduler.py::
# _late_sleep_date)는 재시작하면 사라지므로, 재시작 시 이 테이블을 다시 조회해
# 복원한다(방해금지 기반 지연-기상이 guild_sleep_state로 복원되는 것과 동일한 원칙).


async def claim_sleep_delay(delay_date: str) -> None:
    """delay_date(지연되는 그 자정의 날짜, "먹인 날+1일")에 대해 이미 기록이 있으면
    아무것도 안 하고, 없으면 새로 기록한다(ON CONFLICT DO NOTHING) — 같은 날 여러 번
    먹여도 지연은 항상 정확히 1회(30분)만 적용된다."""
    await rpc("claim_sleep_delay", {"p_delay_date": delay_date})


async def get_pending_sleep_delay() -> str | None:
    """오늘(KST) 이후로 아직 유효한 지연 기록이 있으면 그 날짜(문자열)를 반환한다 —
    봇 재시작 시 events/scheduler.py::mark_late_sleep()을 다시 호출해 인메모리 플래그를
    복원하는 용도. 여러 행이 있을 이유는 사실상 없지만(하루짜리 개념), 있다면 아무거나
    하나를 반환해도 무방하다."""
    rows = await select("sleep_delay_events", {"delay_date": f"gte.{kst_today_str()}", "limit": "1"})
    return rows[0]["delay_date"] if rows else None


async def purge_old_sleep_delay_events() -> None:
    """지난 날짜의 지연 기록은 디버깅 가치가 없으므로 매일 정리한다(chat_history 등
    다른 purge_old_* 작업과 동일한 04:00 스케줄)."""
    await delete("sleep_delay_events", {"delay_date": f"lt.{kst_today_str()}"})
