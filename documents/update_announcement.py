from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateEntry:
    changes: tuple[str, ...]  # 실제 서비스에 체감되는 변경만 — 관리자 명령어/내부 도구
    # 변경은 여기 넣지 않는다.
    #
    # 날짜/버전은 여기에 안 담는다 — "an update"가 실제로 방송되는 시점의 날짜/버전
    # (admin/version.py::get_version_label())을 그때그때 그대로 보여준다(admin/console.py
    # ::_build_update_embed). 이 항목은 "무엇이 바뀌었는지"만 기록한다.


# 최신 항목이 맨 앞. 앞으로 "푸시해주세요" 요청이 있을 때마다 이 튜플 맨 앞에 새 항목을
# 추가한다(append) — 사용자가 명시적으로 초기화를 지시하기 전까지는 기존 항목을 지우지
# 않는다. 작성 스타일은 tools/update_log_persona.py 참고.
ENTRIES: tuple[UpdateEntry, ...] = (
    UpdateEntry(
        changes=(
            "주말과 기념일에는 호감도를 2배, 햄미 생일에는 3배 받을 수 있게 되었습니다. "
            "부름 이벤트도 이런 날에는 더 자주 발생합니다.",
            "하루에 얻을 수 있는 호감도 상한이 100으로 크게 늘어났습니다.",
            "햄미 생일에 축하 인사를 하거나 아침에 인사를 건네면 호감도를 받을 수 있는 "
            "새로운 방법이 생겼습니다.",
            "업적을 달성할 때마다 추가로 호감도를 받을 수 있게 되었습니다. (현재까지 얻은 업적의 호감도는 모두 반영되었습니다.)",
            "호감도가 늘어난 이유를 더 자세히 보여주는 알림으로 개선되었습니다.",
        ),
    ),
)


def latest() -> UpdateEntry | None:
    """admin/console.py의 "an update" 명령어가 그대로 읽어 보내는 최신 항목(LLM 미개입)."""
    return ENTRIES[0] if ENTRIES else None


def get_text() -> str:
    """RAG 문서 인터페이스. 지금은 documents/__init__.py의 REGISTRY에 등록하지 않는다 —
    "햄미야 뭐가 업데이트됐어?" 자연어 라우팅은 향후 과제로 미뤄졌다. 구조만 미리 맞춰둔다."""
    if not ENTRIES:
        return "아직 등록된 업데이트 기록이 없어."
    return "\n\n".join(
        f"업데이트 {i}\n" + "\n".join(f"- {c}" for c in entry.changes)
        for i, entry in enumerate(ENTRIES, start=1)
    )
