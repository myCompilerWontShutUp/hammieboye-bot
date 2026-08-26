from achievements import (
    almond_worthy,
    call_event_help,
    daily_top_talker,
    first_chat,
    great_owner,
    hammie_love_you,
    nightmare_freed,
    plastic_dance,
    plastic_dance_god,
    speech_bubble,
)

# ID -> 모듈. 각 모듈은 ID/NAME/HOW_TO_EARN/RARITY/CODE를 갖는다 (command/ 패키지와 동일하게
# "업적 1개당 파일 1개" 구조 — 새 업적을 추가하려면 파일 하나 만들고 여기 등록만 하면 된다).
# 새 CODE가 필요하면 `python -m tools.codegen 1`로 생성해서 그대로 붙여넣는다.
_MODULES = (
    plastic_dance,
    plastic_dance_god,
    hammie_love_you,
    speech_bubble,
    first_chat,
    call_event_help,
    great_owner,
    daily_top_talker,
    nightmare_freed,
    almond_worthy,
)

REGISTRY = {module.ID: module for module in _MODULES}
CODE_REGISTRY = {module.CODE: module for module in _MODULES}
TOTAL_COUNT = len(_MODULES)

# 희귀도(사용자 확정, 2026-08-27): 일반은 아무것도 안 붙고, 전설은 이름 앞에 왕관 표시가 붙는다.
NORMAL = "일반"
LEGENDARY = "전설"

_LEGENDARY_PREFIX = "**__[👑]__** "


def format_name(module) -> str:
    """업적 이름을 희귀도에 맞게 표시용으로 포맷한다 — 전설이면 이름 앞에 왕관을 붙인다.
    /내정보 목록, "🏆 업적 달성" 알림, ac list* 관리자 명령어가 전부 이 함수 하나로
    왕관 표시를 통일해서 쓴다(사용자 확정: 표시 방식이 여러 곳에서 어긋나지 않도록)."""
    if module.RARITY == LEGENDARY:
        return f"{_LEGENDARY_PREFIX}{module.NAME}"
    return module.NAME
