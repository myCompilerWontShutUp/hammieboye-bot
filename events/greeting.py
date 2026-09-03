import logging
import random
from datetime import date, datetime

import discord
from openai import AsyncOpenAI

from config import (
    ALLOWED_GUILD_IDS,
    OPENAI_API_KEY,
    OPENAI_JUDGE_MODEL,
    OPENAI_MAX_OUTPUT_TOKENS,
    openai_service_tier_kwargs,
)
from events.scheduler import KST, resolve_broadcast_channel_id
from events.special_days import (
    BIRTH_DATE,
    FIXED_ANNIVERSARIES,
    DAY_TYPE_NORMAL,
    get_day_type,
    get_day_type_label,
    get_multiplier,
    lunar_based_anniversaries,
)
from db.guild_channels import get_last_channel
from responses.engine import SYSTEM_PROMPT

_client: discord.Client | None = None
_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# 평소엔 매일 새로 생성하는 아침 인사(날짜/생일/기념일 인식이 필요해 LLM 생성을 유지한다).
# 이 풀은 API 호출 자체가 실패했을 때만 쓰는 폴백 — 날짜 인식 없이도 자연스러운 인사 20개.
_FALLBACK_GREETINGS = (
    "조은 아침이야! 햄미 일어나써 🐹 _(방긋)_",
    "일어나써!! 오늘도 조은 하루 보내자!! _(신남)_",
    "굿모닝!! 햄미 눈 떠써!! _(기지개)_",
    "아침이다!! 오늘도 페트병 흔들 준비 완료!! _(활기)_",
    "잘 잤어!! 오늘도 잘 부탁해!! _(포근)_",
    "일어났다구!! 다들 좋은 아침!! _(방실)_",
    "조은 아침!! 오늘 하루도 힘내자!! _(응원)_",
    "쿨쿨 잘 자고 이제 일어나써!! _(기지개)_",
    "굿모닝이야!! 오늘도 신나게 놀자!! _(신남)_",
    "일어나써!! 다들 잘 잤어?? _(궁금)_",
    "아침이야!! 햄미 벌써 눈이 초롱초롱해!! _(초롱)_",
    "좋은 아침!! 오늘도 햄미랑 놀자!! _(기대)_",
    "일어났어!! 오늘 하루도 화이팅!! _(파이팅)_",
    "잘 자고 일어나써!! 다들 좋은 하루!! _(방긋)_",
    "아침이다아!! 햄미 기운 넘쳐!! _(활기)_",
    "굿모닝!! 오늘은 또 어떤 하루가 될까!! _(설렘)_",
    "일어나써!! 조은 아침 맞이하자!! _(반가움)_",
    "잘 잤다!! 오늘도 신나게 시작해보자!! _(신남)_",
    "아침이야!! 다들 잘 일어났어?? _(방실)_",
    "좋은 아침이야!! 오늘도 잘 부탁해!! _(포근)_",
)

# 방해금지 모드가 발동한 날 전용 기상 문구. 평소 인사와 달리 API 호출 없이 무작위로 고른다.
_TIRED_WAKE_LINES = (
    "으...누가 자꾸 깨워서 늦게 일어나써... 오늘은 쪼금 피곤해. _(피곤)_",
    "한밤중에 누가 불러서 진짜 늦잠 자버려써... 굿모닝... _(하품)_",
    "아 몰라, 누구 때문에 30분이나 더 잤잖아!! 그래도 일어나써. _(투덜)_",
    "누가 깨웠는지 알아도 모른 척할래... 아무튼 늦게 일어나써. _(삐죽)_",
    "밤에 누가 불러서 잠을 설쳐써... 그래서 오늘은 좀 늦어써. _(나른)_",
    "하암... 누구 때문에 늦게 잤더니 몸이 무거워... 그래도 좋은 아침!! _(하품)_",
    "누가 자꾸 불러대서 결국 30분 더 자버려써... 다들 좋은 아침이야. _(피곤)_",
    "밤새 누가 깨워서 컨디션이 별로야... 그래도 일어났어!! _(지침)_",
    "늦잠 잔 거 누구 탓인지 알지?? 아무튼 이제 일어나써. _(흘김)_",
    "누가 한밤중에 불러서... 결국 늦게 일어나버려써. 조은 아침. _(멍)_",
    "몸이 천근만근이야... 누구 때문에 더 잤는지 알면서... _(늘어짐)_",
    "밤중에 깨워서 잠이 부족해... 그래도 다들 좋은 하루 보내!! _(피곤)_",
    "누가 불러서 못 자고 뒤척였어... 그래서 오늘은 늦게 일어나써. _(끄덕)_",
    "아침부터 피곤해... 누구 때문인지는 안 비밀이야. _(한숨)_",
    "누가 자꾸 불러서 늦잠 자버려써... 이제 겨우 일어나써. _(부스스)_",
    "밤새 시달려써... 그래도 하루는 시작해야지. 좋은 아침!! _(지침)_",
    "누구 때문에 30분 더 잔 거 안 이저버릴 거야... 아무튼 굿모닝. _(삐짐)_",
    "한밤중 소동 때문에 늦게 일어났어... 다들 잘 잤어?? _(나른)_",
    "잠을 설쳐서 그런지 눈이 안 떠져... 그래도 일어나써!! _(꾸벅)_",
    "누가 불러서 결국 늦잠 자버려써... 오늘은 좀 봐줘. _(피곤)_",
)

def init(client: discord.Client) -> None:
    global _client
    _client = client


def _anniversary_notes(today: date) -> list[str]:
    notes = []
    fixed = FIXED_ANNIVERSARIES.get((today.month, today.day))
    if fixed:
        notes.append(fixed)
    lunar_note = lunar_based_anniversaries(today.year).get(today)
    if lunar_note:
        notes.append(lunar_note)
    return notes


def _build_task(today: date) -> str:
    days_since_birth = (today - BIRTH_DATE).days
    task = (
        "지금은 새벽 6시 30분, 막 일어난 상황이야. 짧은 아침 인사를 새로 만들어줘 "
        "(예: '조은 아침이야! 햄미 일어나써 🐹' 같은 톤). 매일 표현을 조금씩 다르게 하고, "
        f"오늘이 햄미가 태어난 지 {days_since_birth}일째라는 것도 자연스럽게 한 번씩 언급해도 좋아."
    )

    notes = _anniversary_notes(today)
    if notes:
        examples = " / ".join(notes)
        task += (
            " 오늘은 특별한 날이기도 해. 아래 내용을 네 말투로 자연스럽게 녹여서 인사 뒤에 덧붙여줘 "
            f"(예시 문장이니 그대로 베끼지 말고 같은 의미로 표현해): {examples}"
        )

    # 판단을 모델에 맡기지 않고, 생일인지는 여기서 직접 확정해서 명시적으로 지시한다.
    if today.month == BIRTH_DATE.month and today.day == BIRTH_DATE.day:
        task += " 오늘은 당신(햄미)의 생일입니다. 축하해달라는 메시지를 추가하세요."

    # 호감도 배율 안내도 판단을 모델에 맡기지 않고 직접 계산해서 주입한다(부름 이벤트
    # 횟수는 언급하지 않는다 — 배율만 알리는 게 사용자 요청 사항).
    if get_day_type(today) != DAY_TYPE_NORMAL:
        task += (
            f" 오늘은 호감도를 평소의 {get_multiplier(today)}배 받을 수 있는 날이야. "
            "이 사실도 신나는 톤으로 자연스럽게 언급해줘."
        )

    return task


async def _generate(task: str) -> str:
    try:
        result = await _openai_client.responses.create(
            model=OPENAI_JUDGE_MODEL,
            instructions=SYSTEM_PROMPT,
            input=task,
            max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
            reasoning={"effort": "low"},
            **openai_service_tier_kwargs(),
        )
        return result.output_text.strip()
    except Exception:
        logging.exception("Daily greeting generation failed")
        return random.choice(_FALLBACK_GREETINGS)


_MULTIPLIER_NOTICE_TEMPLATE = "오늘은 {label}이라 호감도가 {multiplier}배!! _(신남)_"


async def post_daily_greeting(*, tired: bool = False) -> None:
    """매일 기상 시각에 아침 인사를 게시한다. tired=True면(방해금지로 기상이 늦춰진 날)
    LLM 생성 대신 `_TIRED_WAKE_LINES`에서 무작위로 고른다 — 이 경로는 LLM이 없어서
    배율 공지가 자연스럽게 안 녹아드니, 배율이 있는 날만 고정 문구를 따로 이어붙인다."""
    if _client is None:
        return

    today = datetime.now(KST).date()
    if tired:
        text = random.choice(_TIRED_WAKE_LINES)
        if get_day_type(today) != DAY_TYPE_NORMAL:
            notice = _MULTIPLIER_NOTICE_TEMPLATE.format(
                label=get_day_type_label(today),
                multiplier=get_multiplier(today),
            )
            text = f"{text}\n\n{notice}"
    else:
        text = await _generate(_build_task(today))

    for guild in _client.guilds:
        if guild.id not in ALLOWED_GUILD_IDS:
            continue
        channel_id = resolve_broadcast_channel_id(guild.id, await get_last_channel(guild.id))
        if channel_id is None:
            continue
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue
        try:
            await channel.send(text)
        except discord.HTTPException:
            logging.exception("Failed to post daily greeting in guild %s", guild.id)
