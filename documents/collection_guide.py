"""`/봇정보-수집항목`이 보여주는 내용을 자연어로도 물어볼 수 있게 하는 RAG
문서(2026-09-10 신규). 실제 문구는 command/collection_info.py::COLLECTION_NOTICE를
그대로 재사용한다."""

from command.collection_info import COLLECTION_NOTICE


def get_text() -> str:
    return (
        f"{COLLECTION_NOTICE}\n\n"
        "이 목록에 없는 정보를 저장한다고 지어내서 알려주면 안 된다. 더 자세한 내용은 "
        "`/봇정보-수집항목`에서도 확인할 수 있다고 안내해줘야 한다."
    )
