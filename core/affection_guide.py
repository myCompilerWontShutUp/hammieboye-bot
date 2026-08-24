import logging

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_JUDGE_MODEL, OPENAI_MAX_OUTPUT_TOKENS
from responses.engine import SYSTEM_PROMPT

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# "호감도 어떻게 올려?" 류 질문은 매번 새로 생성하지 않고, 딱 한 번 생성한 뒤
# 그 결과를 그대로 재사용한다 (사람이 미리 써둔 고정 문구가 아니라 페르소나가
# 실제 상승 방법을 근거로 생성한 답을 캐시하는 방식).
_TASK = """\
사용자가 너(햄미)와 친해지는 방법, 즉 호감도를 올리는 방법을 물어봤어.
아래는 실제로 호감도가 오르는 방법들이야. 전부 나열하지 않아도 되니, 이 중 몇 가지를
네 말투로 자연스럽게 소개해줘:
- 페트병(플라스틱 병) 던지기 놀이를 해주기
- 네가 가끔 먼저 배고프다/목마르다/심심하다고 말할 때, 상황에 맞게 챙겨주는 반응을 해주기
- 대화하면서 너를 기분 좋게(행복하게) 만들어주기
- 그냥 자주 말 걸고 다정하게 대해주기
"""

_FALLBACK = "음... 지금은 말이 잘 안 나와... 쪼금 이따가 다시 물어봐줄래??"

_cached_text: str | None = None


async def get_guide() -> str:
    global _cached_text
    if _cached_text is not None:
        return _cached_text

    try:
        result = await _client.responses.create(
            model=OPENAI_JUDGE_MODEL,
            instructions=SYSTEM_PROMPT,
            input=_TASK,
            max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
            reasoning={"effort": "low"},
        )
        _cached_text = result.output_text.strip()
    except Exception:
        logging.exception("Affection guide generation failed")
        return _FALLBACK

    return _cached_text
