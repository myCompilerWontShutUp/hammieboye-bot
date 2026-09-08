import math
from datetime import datetime, timedelta, timezone

from db.client import delete, insert, select

# /암시장 "금서" — 유저가 가르친 키워드/내용을 자연어 생성에 주입하는 기능(2026-09-08
# 신규). 디버깅 성격이 아니라 실제 게임 컨텐츠라 chat_history류와 달리 보관 기간이
# 짧다 — "햄미는 일주일 뒤에 까먹는다"는 컨셉 그대로 7일.
RETENTION_DAYS = 7
MAX_PER_USER = 5


def normalize_keyword(keyword: str) -> str:
    """공백 제거+소문자화 — 부분일치 매칭과 중복 검사 둘 다 이 정규화를 거친 뒤 비교한다
    (core/base.py::normalize와 동일한 원칙, 다만 여긴 하나의 필드만 다뤄서 별도 함수로 둔다)."""
    return keyword.replace(" ", "").strip().lower()


async def get_active_entries() -> list[dict]:
    """만료(7일)되지 않은 금서 전체 — 자연어 키워드 매칭(core/chat.py)과 유효기간 표시
    양쪽에서 쓴다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    return await select(
        "forbidden_book_entries",
        {"created_at": f"gte.{cutoff.isoformat()}", "select": "*"},
    )


async def get_entries_for_user(user_id: int) -> list[dict]:
    """이 유저가 가르친(아직 안 만료된) 금서 — 등록 전 "현재 내 금서 목록" 표시,
    5개 한도 판정 둘 다에 쓴다. 오래된 순으로 정렬."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    return await select(
        "forbidden_book_entries",
        {
            "teacher_user_id": f"eq.{user_id}",
            "created_at": f"gte.{cutoff.isoformat()}",
            "select": "*",
            "order": "created_at.asc",
        },
    )


async def keyword_taken(keyword: str) -> bool:
    """완전히 동일한 키워드(정규화 후)가 이미 활성 상태로 등록돼 있는지 — 전역 유일성
    검사(가르친 사람 무관, 2026-09-08 사용자 확인)."""
    normalized = normalize_keyword(keyword)
    entries = await get_active_entries()
    return any(normalize_keyword(entry["keyword"]) == normalized for entry in entries)


async def add_entry(user_id: int, keyword: str, content: str) -> dict:
    rows = await insert(
        "forbidden_book_entries",
        {"teacher_user_id": user_id, "keyword": keyword, "content": content},
    )
    return rows[0]


def days_remaining(entry: dict) -> int:
    """표시용 — 만료까지 남은 일수를 올림 처리한다(몇 시간만 남아도 최소 1일로 표시,
    이미 지났으면 0)."""
    created_at = datetime.fromisoformat(entry["created_at"])
    elapsed = datetime.now(timezone.utc) - created_at
    remaining_seconds = timedelta(days=RETENTION_DAYS).total_seconds() - elapsed.total_seconds()
    if remaining_seconds <= 0:
        return 0
    return max(1, math.ceil(remaining_seconds / 86400))


async def purge_old() -> None:
    """`RETENTION_DAYS`(7일)보다 오래된 금서를 완전히 삭제한다 — "일주일 뒤에 까먹고,
    까먹은 후엔 완전히 제거하며 별도로 알리지 않는다"는 요청 그대로 조용히 지운다.
    매일 04:00(취침 중 한산한 시간, core/dispatcher.py)에 스케줄러가 호출한다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    await delete("forbidden_book_entries", {"created_at": f"lt.{cutoff.isoformat()}"})


def find_matches(text: str, entries: list[dict]) -> list[dict]:
    """정규화된 text 안에 정규화된 키워드가 부분 문자열로 포함되면 매칭으로 인정한다
    ("적당히 비슷하면 인정" — 오타/조사 변형까지는 못 잡지만 띄어쓰기·대소문자 차이는
    흡수한다, 2026-09-08 사용자 확인: 추가 API 호출 없는 단순 매칭)."""
    normalized_text = normalize_keyword(text)
    return [entry for entry in entries if normalize_keyword(entry["keyword"]) in normalized_text]
