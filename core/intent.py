import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from responses.engine import NEGATIVE_EMOTIONS, POSITIVE_EMOTIONS

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

_ALL_EMOTIONS = POSITIVE_EMOTIONS + NEGATIVE_EMOTIONS
_CATEGORIES = (
    "profile",
    "commands",
    "prefixes",
    "affection_guide",
    "achievements",
    "admin_commands",
)

# 자연어마다 매번 호출되는 단일 분류기. RAG 문서 카테고리 판별(복수 선택)과
# 감정 판정(기존 judge가 하던 것)을 한 번의 호출로 같이 처리해서 API 호출을 늘리지 않는다.
_INSTRUCTIONS = """\
너는 디스코드 챗봇 "Hammie(햄미)"에게 온 자연어 메시지를 분류하는 라우터다.
아래 두 가지를 판단해라.

[1] 카테고리 분류 (해당하는 것 전부 고른다, 복수 선택 가능. 하나도 해당 안 되면 빈 배열):
- profile: 햄미 자신의 프로필(나이/생일/키/몸무게/좋아하는 것/싫어하는 것/성격 등)을 물어봄
- commands: 햄미가 쓸 수 있는 명령어가 뭔지, 어떻게 쓰는지 물어봄
- prefixes: 햄미를 어떻게 부르면 되는지(호출 단어)를 묻거나, 특정 단어를 호출 단어로
  써도 되는지/추가할 수 있는지 묻는 경우 (예: "이 단어로 불러도 돼?", "새 호출 단어 추가해줘")
- affection_guide: 호감도를 올리는 방법/공략을 물어봄
- achievements: 업적(도전과제)에 관해 물어봄 — 업적이 뭐가 있는지, 특정 업적을 어떻게 얻는지 등
- admin_commands: 관리자 콘솔("주인님 가라사대"로 여는 la/tc/sh/gn/rm/ac/op 등) 명령어가
  뭐가 있는지, 어떻게 쓰는지 물어봄 (일반 유저가 쓰는 명령어를 묻는 "commands"와는 다른
  카테고리 — 관리자 전용 명령어 언급이 있어야 해당)
해당하는 카테고리가 하나도 없으면 categories를 빈 배열로 둬라 (일반 대화).

[2] 감정 분류: 이 대화가 Hammie에게 어떤 감정을 불러일으켰는지 아래 20개 중
정확히 하나를 emotion에 골라라 (반드시 하나 선택, null 금지).
긍정: {positive}
부정: {negative}

[3] 심각한 유해 표현 감지 (has_severe_abuse): 이 메시지에 아래 중 하나라도 명확하게
해당하는 표현이 실제로 있으면 true, 아니면 false로 표시해라.
- 심각한 욕설(단순히 거친 말투가 아니라 명확한 비속어/욕)
- 비방(특정 대상을 깎아내리거나 헐뜯는 표현)
- 타인(제3자)에 대한 모욕
- 성희롱(성적으로 불쾌감을 주는 표현)
- 패드립(상대방의 부모/가족을 욕되게 하는 표현)
단순히 화가 났거나, 기분이 안 좋거나, 퉁명스럽거나, 장난스럽게 놀리는 정도로는 해당하지
않는다. 애매하면 false로 표시해라 — 과잉 적용(false positive)을 피하는 게 더 중요하다.\
""".format(
    positive=", ".join(POSITIVE_EMOTIONS),
    negative=", ".join(NEGATIVE_EMOTIONS),
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "array",
            "items": {"type": "string", "enum": list(_CATEGORIES)},
        },
        "emotion": {"type": "string", "enum": list(_ALL_EMOTIONS)},
        "has_severe_abuse": {"type": "boolean"},
    },
    "required": ["categories", "emotion", "has_severe_abuse"],
    "additionalProperties": False,
}


@dataclass
class ClassifyResult:
    categories: list[str]
    emotion: str | None
    has_severe_abuse: bool = False


async def classify(text: str) -> ClassifyResult:
    try:
        result = await _client.responses.create(
            model=OPENAI_MODEL,
            instructions=_INSTRUCTIONS,
            input=text,
            max_output_tokens=150,
            reasoning={"effort": "none"},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "classification",
                    "schema": _SCHEMA,
                    "strict": True,
                }
            },
        )
        data = json.loads(result.output_text)
        return ClassifyResult(
            categories=data["categories"],
            emotion=data["emotion"],
            has_severe_abuse=data["has_severe_abuse"],
        )
    except Exception:
        logging.exception("Classification failed")
        # 분류 실패 시 안전한 기본값 — 아무 페널티도 적용하지 않는다.
        return ClassifyResult(categories=[], emotion=None, has_severe_abuse=False)
