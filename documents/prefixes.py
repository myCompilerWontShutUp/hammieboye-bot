from config import CALL_PREFIXES


def get_text() -> str:
    names = ", ".join(CALL_PREFIXES)
    return f"햄미를 부를 수 있는 호출 단어(메시지 맨 앞에 붙이면 됨, 이 중 아무거나 써도 됨): {names}"
