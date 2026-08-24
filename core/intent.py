import json
import logging

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# 자연어가 고정 명령어/프롬프트 캐시로 리다이렉트돼야 하는지 판단하는 라우터.
# 자주 호출되므로(자연어 메시지마다 1번) 저렴한 OPENAI_MODEL(nano)을 쓴다.
_INSTRUCTIONS = """\
너는 디스코드 챗봇 "Hammie(햄미)"에게 온 자연어 메시지의 의도를 분류하는 라우터다.
아래 중 하나를 정확히 골라라.

- help: 명령어 목록/도움말을 요청함 (예: "명령어 뭐 있어?", "뭐 할 수 있어?", "도움말 보여줘")
- info: 자기 자신의 호감도/정보를 확인하려 함 (예: "내 호감도 보여줘", "나 정보 좀 알려줘", "나 몇 번 대화했어?")
- self_intro: Hammie가 누구인지, 정체·소개를 물어봄 (예: "너 누구야?", "자기소개 해줘", "너에 대해 알려줘")
- none: 위 어디에도 해당하지 않는 일반 대화\
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["help", "info", "self_intro", "none"]},
    },
    "required": ["intent"],
    "additionalProperties": False,
}

SELF_INTRO = """\
나는 페트병 흔드는 작은 햄스터, **햄미**야!
영어로는 **Hammie Boye**라고 해.
12월 22일에 태어났고, 페트병 흔들기랑 간식 요구하기가 특기야. 인간 구경하는 것도 꽤 재미써~
해바라기씨랑 페트병, 관심받는 거랑 장난치는 걸 조아해. 하지만 간식 없는 호의랑 갑자기 들어오는 손가락, 고양이는 시러!
햄미의 좌우명은 언제나 하나야!! **Hamster >>>>> Human**\
"""


async def classify(text: str) -> str:
    try:
        result = await _client.responses.create(
            model=OPENAI_MODEL,
            instructions=_INSTRUCTIONS,
            input=text,
            max_output_tokens=50,
            reasoning={"effort": "minimal"},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "intent",
                    "schema": _SCHEMA,
                    "strict": True,
                }
            },
        )
        return json.loads(result.output_text)["intent"]
    except Exception:
        logging.exception("Intent classification failed")
        return "none"
