import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

# 쉼표로 여러 서버 ID를 나열하면 그 서버들의 모든 채널에서 자동 응답한다 (예: "111,222")
ALLOWED_GUILD_IDS = frozenset(
    int(guild_id.strip())
    for guild_id in os.environ["ALLOWED_GUILD_IDS"].split(",")
    if guild_id.strip()
)

# 메시지 맨 앞이 이 중 하나와 정확히 일치(startswith)해야 응답한다. 쉼표로 구분.
CALL_PREFIXES = tuple(
    prefix.strip() for prefix in os.environ["CALL_PREFIXES"].split(",") if prefix.strip()
)

# 관리자 콘솔("주인님 가라사대 ...")을 쓸 수 있는 단 1명의 Discord user ID (§13-F).
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.environ["OPENAI_MODEL"]
OPENAI_MAX_OUTPUT_TOKENS = int(os.environ["OPENAI_MAX_OUTPUT_TOKENS"])

# 자연어 답변 검수(judge)에 쓰는 모델. 생성용 OPENAI_MODEL과 분리해서,
# 판정 정확도가 더 필요하면 여기만 올릴 수 있게 한다.
OPENAI_JUDGE_MODEL = os.environ["OPENAI_JUDGE_MODEL"]

# 동일한 정적 프롬프트(instructions)를 반복 호출할 때 OpenAI가 캐시를 안정적으로
# 재사용하도록 붙이는 라우팅 힌트. instructions 내용을 바꾸면 값을 같이 올려서
# 새 프롬프트가 이전 캐시와 섞이지 않게 한다.
OPENAI_PROMPT_CACHE_KEY = os.environ["OPENAI_PROMPT_CACHE_KEY"]

# §51(2026-09-02): FastMode(OpenAI service_tier="fast"/"priority") 전역 on/off 스위치.
# 실측 결과(8회씩 비교) 평균 차이가 노이즈 범위 안이라 확실한 속도 개선이라고 보장할 수
# 없어서(사용자에게 이미 안내함), 코드 수정·재배포 없이 .env 값 하나만 바꾸고 재시작하면
# 즉시 롤백할 수 있도록 플래그로 뺐다. "관리자 자연어 설명(10배 토큰 완화, §44-6/§49/§50)에는
# 적용 안 함"이라는 예외는 이 플래그와 무관하게 항상 유지된다 — 그 호출부는 이 값을 아예
# 안 본다(responses/engine.py::get_admin_command_response 참고).
OPENAI_FAST_MODE = os.environ["OPENAI_FAST_MODE"].strip().lower() == "true"


def openai_service_tier_kwargs() -> dict:
    """OPENAI_FAST_MODE가 꺼져 있으면 빈 dict(=service_tier 파라미터 자체를 생략)를,
    켜져 있으면 {"service_tier": "fast"}를 반환한다 — responses.create(**이 결과) 형태로
    쓴다. 파라미터를 생략하는 것과 명시적으로 None을 넘기는 게 OpenAI SDK에서 항상 동일하게
    처리된다는 보장이 없어서, 꺼져 있을 때도 "생략" 쪽으로 안전하게 처리한다."""
    return {"service_tier": "fast"} if OPENAI_FAST_MODE else {}


# Supabase 프로젝트 URL. Project Settings > Data API 에서 확인.
SUPABASE_URL = os.environ["SUPABASE_URL"]

# service_role 키. RLS를 우회하므로 anon 키와 절대 혼동하면 안 되고,
# 다른 비밀값들과 마찬가지로 .env에만 두고 절대 커밋/클라이언트 노출 금지.
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
