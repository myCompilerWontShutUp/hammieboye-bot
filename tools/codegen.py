"""1회성 개발 도구 모음. 지금은 업적 코드 생성기 하나뿐이지만, 앞으로 비슷한 "한 번 실행해서
정적으로 박아 넣는" 도구가 생기면 이 패키지(tools/)에 파일을 추가하는 식으로 관리한다.

업적 코드는 영소문자+숫자 8자리이고, 한 번 생성하면 각 achievements/*.py 파일에 CODE 상수로
정적으로 고정한다(런타임에 매번 새로 만드는 게 아니다). 새 업적을 추가할 때는:

    python -m tools.codegen 1

처럼 몇 개가 필요한지 인자로 주고 실행하면, 이미 achievements.CODE_REGISTRY에 등록된 코드와
겹치지 않는 새 코드를 그 개수만큼 뽑아서 출력해준다. 그 값을 새 achievements/*.py 파일의
CODE 상수에 그대로 붙여넣으면 된다.
"""
import random
import string
import sys

_ALPHABET = string.ascii_lowercase + string.digits
_CODE_LENGTH = 8


def generate_codes(count: int, existing: frozenset[str] = frozenset()) -> list[str]:
    """existing과 서로 겹치지 않는 8자리 코드를 count개 생성한다(자기들끼리도 중복 없음).
    이 알파벳(영소문자+숫자 36종)으로 8자리를 뽑으면 겹칠 확률이 극히 낮지만, 그래도 확률이지
    보장이 아니므로 겹치면 다시 뽑는 방식(rejection sampling)으로 절대 겹치지 않게 만든다."""
    used = set(existing)
    codes: list[str] = []
    while len(codes) < count:
        candidate = "".join(random.choices(_ALPHABET, k=_CODE_LENGTH))
        if candidate in used:
            continue
        used.add(candidate)
        codes.append(candidate)
    return codes


def _existing_codes_from_registry() -> frozenset[str]:
    """achievements 패키지가 이미 구성돼 있으면 그 CODE_REGISTRY를 읽어 충돌을 피한다.
    아직 achievements 쪽에 CODE_REGISTRY가 없는 초기 상태(맨 처음 10개를 만들 때)라면
    빈 집합으로 시작한다."""
    try:
        import achievements

        return frozenset(achievements.CODE_REGISTRY.keys())
    except (ImportError, AttributeError):
        return frozenset()


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    for code in generate_codes(count, _existing_codes_from_registry()):
        print(code)
