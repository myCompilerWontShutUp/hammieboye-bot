import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from responses.engine import NEGATIVE_EMOTIONS, POSITIVE_EMOTIONS

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

_ALL_EMOTIONS = POSITIVE_EMOTIONS + NEGATIVE_EMOTIONS
_CATEGORIES = ("profile", "commands", "prefixes", "affection_guide", "achievements")

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
해당하는 카테고리가 하나도 없으면 categories를 빈 배열로 둬라 (일반 대화).

[2] 감정 분류: 이 대화가 Hammie에게 어떤 감정을 불러일으켰는지 아래 20개 중
정확히 하나를 emotion에 골라라 (반드시 하나 선택, null 금지).
긍정: {positive}
부정: {negative}\
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
    },
    "required": ["categories", "emotion"],
    "additionalProperties": False,
}


@dataclass
class ClassifyResult:
    categories: list[str]
    emotion: str | None


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
        return ClassifyResult(categories=data["categories"], emotion=data["emotion"])
    except Exception:
        logging.exception("Classification failed")
        return ClassifyResult(categories=[], emotion=None)
