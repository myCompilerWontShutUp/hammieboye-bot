_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_JONGSEONG_COUNT = 28


def has_batchim(text: str) -> bool:
    """문자열의 마지막 글자가 받침이 있는 완성형 한글 음절인지. 한글이 아닌 문자(영문/
    이모지 등)로 끝나면 받침 없음으로 취급한다(가장 무난한 기본값)."""
    if not text:
        return False
    code = ord(text[-1])
    if not (_HANGUL_BASE <= code <= _HANGUL_LAST):
        return False
    return (code - _HANGUL_BASE) % _JONGSEONG_COUNT != 0


def josa(name: str, with_batchim: str, without_batchim: str) -> str:
    """name의 받침 유무에 맞는 조사를 고른다 (예: josa(name, "을", "를"))."""
    return with_batchim if has_batchim(name) else without_batchim
