import admin.console as _console

_PREAMBLE = (
    "관리자 콘솔에서 쓸 수 있는 명령어 전부 (\"{호출 단어} 주인님 가라사대\"로 세션을 열고 "
    "그 안에서 실행함, 형식은 \"이름 : 파라미터 - 설명\"):\n"
)


def get_text() -> str:
    """admin/console.py의 명령어 레지스트리에서 직접 생성한다 — 문서와 실제 명령어가 항상
    같은 소스에서 나오도록. documents.REGISTRY에는 등록하지 않는다(권한 체크와 함께
    core/chat.py에서 별도로 처리해야 비권한자에게 새어 나가지 않아서)."""
    return _PREAMBLE + _console.all_commands_text()
