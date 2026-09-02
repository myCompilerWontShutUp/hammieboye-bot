import achievements

# "발견의 재미"가 핵심이라 목록을 통째로 물어보면 얼버무리고, 특정 업적을 정확히
# 짚어 물어볼 때만 획득 방법을 알려준다.
_INSTRUCTION = (
    "아래는 업적 목록과 정확한 획득 방법이다. 사용자가 '업적이 뭐 있어?', '업적 목록 알려줘', "
    "'업적 다 뭐야?'처럼 두루뭉술하게 전체 목록을 물어보면 절대 목록을 다 알려주지 말고 "
    "얼버무려라 (예: '그건 비밀이야!! 직접 찾아봐!!'). 사용자가 특정 업적의 이름을 정확히 "
    "언급하며 어떻게 얻는지 물어볼 때만, 그 업적의 획득 방법을 정확하게 알려줘라."
)


def get_text() -> str:
    lines = "\n".join(f"- {module.NAME}: {module.HOW_TO_EARN}" for module in achievements.REGISTRY.values())
    return f"{_INSTRUCTION}\n\n{lines}"
