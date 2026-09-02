from documents import achievements, affection_guide, commands, prefixes, profile

# 카테고리 이름 -> 문서 텍스트를 만드는 함수. core/intent.py의 분류 결과가 이 키들을 그대로 쓴다.
REGISTRY = {
    "profile": profile.get_text,
    "commands": commands.get_text,
    "prefixes": prefixes.get_text,
    "affection_guide": affection_guide.get_text,
    "achievements": achievements.get_text,
}

_NO_HALLUCINATION_NOTE = (
    "아래는 참고 자료(사용자에게 안 보임)야. 이 자료에 실제로 나온 내용만 근거로 답해. "
    "자료에 없는 내용을 지어내지 말고, 모르면 모른다고 솔직하게 답해. "
    "**단, '자료에 없어', '문서에 안 나와 있어', '참고할 자료가 없어'처럼 네가 참고 자료를 "
    "보고 있다는 사실 자체를 사용자에게 언급하거나 암시하지 마 — 너는 자료를 참고한다는 "
    "메타 개념 자체를 모르는 것처럼 행동해야 한다.** 모를 때는 이유를 설명하지 말고 그냥 "
    "'기억이 안 나', '잘 모르겠어', '그건 잘 모르겠는데'처럼 햄미답게 자연스럽게 모른다고만 답해. "
    "이 자료가 '가능한 것들의 전체 목록'(예: 호출 단어 목록, 명령어 목록)이라면, "
    "사용자가 목록에 없는 걸 추가하거나 새로 써도 되는지 물어보면 절대 된다고 동의하지 말고 "
    "그건 안 된다고 명확하게 답해 — 이때도 '목록에 없어서' 같은 근거는 대지 말고 그냥 "
    "안 된다고만 자연스럽게 답해."
)


def build_context_note(categories: list[str]) -> str | None:
    """분류된 카테고리들의 문서 텍스트를 모아 생성 컨텍스트에 넣을 노트 하나로 합친다."""
    texts = [REGISTRY[c]() for c in categories if c in REGISTRY]
    if not texts:
        return None
    joined = "\n\n".join(texts)
    return f"{_NO_HALLUCINATION_NOTE}\n\n{joined}"
