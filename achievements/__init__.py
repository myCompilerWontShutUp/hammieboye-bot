from achievements import (
    almond_worthy,
    alone_on_a_happy_day,
    call_event_help,
    daily_top_talker,
    dev_never_tested_this,
    early_bird,
    first_chat,
    gambling_hotline_1336,
    great_owner,
    hammie_ez_noob,
    hammie_love_you,
    nightmare_freed,
    penny_pincher,
    plastic_dance,
    plastic_dance_god,
    savings_start,
    speech_bubble,
    strongest_snack_ever,
    three_meals_a_day,
    vending_first_purchase,
)

# ID -> 모듈. 각 모듈은 ID/NAME/HOW_TO_EARN/RARITY/CODE를 갖는다 ("업적 1개당 파일 1개").
# 새 CODE가 필요하면 `python -m tools.codegen 1`로 생성해서 붙여넣는다.
#
# 이 튜플의 순서는 "업적이 만들어진 순서"로 취급되어 /내업적·/니업적의 "획득하지 못한
# 업적" 정렬 기준으로도 쓰인다.
_MODULES = (
    plastic_dance,
    plastic_dance_god,
    hammie_love_you,
    first_chat,
    speech_bubble,
    call_event_help,
    great_owner,
    daily_top_talker,
    nightmare_freed,
    almond_worthy,
    early_bird,
    vending_first_purchase,
    savings_start,
    hammie_ez_noob,
    gambling_hotline_1336,
    dev_never_tested_this,
    three_meals_a_day,
    strongest_snack_ever,
    alone_on_a_happy_day,
    penny_pincher,
)

REGISTRY = {module.ID: module for module in _MODULES}
CODE_REGISTRY = {module.CODE: module for module in _MODULES}
TOTAL_COUNT = len(_MODULES)

# 일반은 아무것도 안 붙고, 전설은 이름 앞에 왕관 표시가 붙는다.
NORMAL = "일반"
LEGENDARY = "전설"

_LEGENDARY_PREFIX = "**__[👑]__** "


def format_name(module) -> str:
    """업적 이름을 희귀도에 맞게 포맷한다. 업적 관련 표시 전부가 이 함수 하나를 공유한다."""
    if module.RARITY == LEGENDARY:
        return f"{_LEGENDARY_PREFIX}{module.NAME}"
    return module.NAME


def format_hidden_legendary(module) -> str:
    """미획득 전설 업적을 이름 대신 ???+힌트로 표시한다("발견의 재미" 원칙, /업적-리스트
    전용 — module.HINT가 필수인 전설 업적에서만 호출할 것)."""
    return f"{_LEGENDARY_PREFIX}??? ({module.HINT})"
