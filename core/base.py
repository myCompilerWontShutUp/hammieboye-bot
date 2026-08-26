# 시스템 형태 메시지(호감도/대화 횟수 등)를 embed로 보일 때 쓰는 시그니처 컬러 (연주황색).
EMBED_COLOR = 0xFFCC99


def normalize(text: str) -> str:
    return text.replace(" ", "").strip().lower()
