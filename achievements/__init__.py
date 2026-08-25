from achievements import hammie_love_you, plastic_dance, plastic_dance_god, speech_bubble

# ID -> 모듈. 각 모듈은 ID/NAME/HOW_TO_EARN을 갖는다 (command/ 패키지와 동일하게 "업적 1개당
# 파일 1개" 구조 — 새 업적을 추가하려면 파일 하나 만들고 여기 등록만 하면 된다).
_MODULES = (plastic_dance, plastic_dance_god, hammie_love_you, speech_bubble)

REGISTRY = {module.ID: module for module in _MODULES}
TOTAL_COUNT = len(_MODULES)
