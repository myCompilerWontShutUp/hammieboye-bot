import admin.console as _console


def get_text() -> str:
    """admin/console.py의 명령어 레지스트리에서 직접 생성한다(안내문 포함, 그쪽에서 이미
    조립됨) — 문서와 실제 명령어가 항상 같은 소스에서 나오도록. documents.REGISTRY에는
    등록하지 않는다(권한 체크와 함께 core/chat.py에서 별도로 처리해야 비권한자에게
    새어 나가지 않아서)."""
    return _console.all_commands_text()
