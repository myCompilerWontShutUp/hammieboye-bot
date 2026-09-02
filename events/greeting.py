import logging
import random
from datetime import date, datetime, timedelta

import discord
from korean_lunar_calendar import KoreanLunarCalendar
from openai import AsyncOpenAI

from config import (
    ALLOWED_GUILD_IDS,
    OPENAI_API_KEY,
    OPENAI_JUDGE_MODEL,
    OPENAI_MAX_OUTPUT_TOKENS,
    openai_service_tier_kwargs,
)
from events.scheduler import KST, resolve_broadcast_channel_id
from db.guild_channels import get_last_channel
from responses.engine import SYSTEM_PROMPT

_client: discord.Client | None = None
_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

_BIRTH_DATE = date(2017, 12, 22)

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

# 2026-08-27: 취침 중 방해금지 모드(누가 깨워서 30분 더 잔 컨셉)가 발동한 날 전용 기상
# 문구 20개. 평소 인사(LLM 생성, 날짜/기념일 인식)와 달리 API 호출 없이 이 풀에서 무작위로
# 고른다 — 피곤하고 누가 깨워서 늦었다는 톤(사용자 확정).
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

# 고정 양력 기념일. (월, 일) -> 예시 문구. 예시일 뿐이라 LLM이 매번 표현을 바꿔서 전달한다.
_FIXED_ANNIVERSARIES: dict[tuple[int, int], str] = {
    (1, 1): "새해다! 올해도 햄미랑 가치 놀자!",
    (1, 14): "오늘은 다이어리 데이래. 햄미 얘기도 적어조!",
    (2, 14): "밸런타인데이래! 초콜릿 말고 씨앗 조라.",
    (3, 1): "오늘은 삼일절이야. 태극기 다는 날이야!",
    (3, 3): "삼겹살데이래! 햄미는 해바라기씨 머글래.",
    (3, 14): "화이트데이야! 햄미 사탕도 이써?",
    (4, 1): "만우절이야! 햄미 사실 햄스터 아님… 뻥이야.",
    (4, 14): "블랙데이래. 짜장면에 햄미 빠뜨리면 안 대!",
    (4, 22): "지구의 날이야! 페트병은 햄미 주고 잘 재활용해조.",
    (5, 5): "어린이날이야! 햄미도 아직 애기니까 선물 조라.",
    (5, 14): "로즈데이래! 장미보다 해바라기씨가 조아.",
    (6, 6): "오늘은 현충일이야. 고마운 분들을 기억하자.",
    (7, 17): "오늘은 제헌절이야! 대한민국 헌법이 만들어진 날이래.",
    (8, 8): "세계 고양이의 날이래. 햄미는 오늘 쪼금 숨어 이쓸게.",
    (8, 15): "오늘은 광복절이야. 아주 소중한 날이니까 기억하자!",
    (10, 3): "오늘은 개천절이야! 하늘이 열린 날이래.",
    (10, 9): "오늘은 한글날이야! 햄미도 한글 조아해!",
    (10, 25): "오늘은 독도의 날이야! 독도는 우리 땅이야.",
    (10, 31): "핼러윈이야! 간식 안 주면 페트병 흔들 거야!",
    (11, 11): "빼빼로데이래! 햄미한텐 해바라기씨 막대기 조라.",
    (12, 22): "오늘은 햄미 생일이야! 햄미가 주인공이야! 간식 조라! 🎂",
    (12, 24): "크리스마스이브야! 산타 햄미 오는 중이야.",
    (12, 25): "메리 크리스마스! 선물 상자에 해바라기씨 이써?",
    (12, 31): "올해 마지막 날이야! 햄미랑 놀아줘서 고마워.",
}


def init(client: discord.Client) -> None:
    global _client
    _client = client


def _lunar_to_solar(year: int, month: int, day: int) -> date:
    calendar = KoreanLunarCalendar()
    calendar.setLunarDate(year, month, day, False)
    return date.fromisoformat(calendar.SolarIsoFormat())


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Monday=0 ... Sunday=6. n번째(1부터)에 해당하는 날짜."""
    d = date(year, month, 1)
    count = 0
    while True:
        if d.weekday() == weekday:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)


def _lunar_based_anniversaries(year: int) -> dict[date, str]:
    seollal = _lunar_to_solar(year, 1, 1)
    daeboreum = _lunar_to_solar(year, 1, 15)
    buddha = _lunar_to_solar(year, 4, 8)
    chuseok = _lunar_to_solar(year, 8, 15)
    suneung = _nth_weekday_of_month(year, 11, 3, 3)  # 11월 셋째 목요일

    return {
        seollal - timedelta(days=1): "낼은 설날이야! 햄미도 간식 받을 준비해써.",
        seollal: "새해 복 마니 받아! 세뱃돈은 해바라기씨로 조도 대.",
        seollal + timedelta(days=1): "설날 간식 남은 거 이써? 햄미가 도와줄게.",
        daeboreum: "달이 엄청 동그래! 햄미 볼주머니 같아.",
        buddha: "오늘은 부처님오신날이야. 마음을 편하게 가지자.",
        chuseok - timedelta(days=1): "낼은 추석이야! 햄미 송편 기다리는 중이야.",
        chuseok: "즐거운 추석이야! 보름달처럼 볼주머니도 채워조.",
        chuseok + timedelta(days=1): "추석 간식 남아찌? 버리면 안 대니까 햄미 조.",
        suneung: "오늘은 수능날이야! 수험생 인간들 모두 힘내!",
    }


def _anniversary_notes(today: date) -> list[str]:
    notes = []
    fixed = _FIXED_ANNIVERSARIES.get((today.month, today.day))
    if fixed:
        notes.append(fixed)
    lunar_note = _lunar_based_anniversaries(today.year).get(today)
    if lunar_note:
        notes.append(lunar_note)
    return notes


def _build_task(today: date) -> str:
    days_since_birth = (today - _BIRTH_DATE).days
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
    if today.month == _BIRTH_DATE.month and today.day == _BIRTH_DATE.day:
        task += " 오늘은 당신(햄미)의 생일입니다. 축하해달라는 메시지를 추가하세요."

    return task


async def _generate(task: str) -> str:
    try:
        result = await _openai_client.responses.create(
            model=OPENAI_JUDGE_MODEL,
            instructions=SYSTEM_PROMPT,
            input=task,
            max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
            reasoning={"effort": "low"},
            # §51: config.OPENAI_FAST_MODE로 켜고 끈다(.env만 바꾸면 즉시 롤백 가능).
            **openai_service_tier_kwargs(),
        )
        return result.output_text.strip()
    except Exception:
        logging.exception("Daily greeting generation failed")
        return random.choice(_FALLBACK_GREETINGS)


async def post_daily_greeting(*, tired: bool = False) -> None:
    """매일 기상 시각에 아침 인사를 게시한다.

    tired=True면(그날 밤 방해금지 이벤트가 발동해 기상이 07:00로 늦춰진 경우, §28) 평소의
    LLM 생성 인사(날짜/기념일 인식) 대신, API 호출 없이 `_TIRED_WAKE_LINES` 20개 중 하나를
    무작위로 골라 "누가 깨워서 늦었다"는 피곤한 톤으로 대체한다.
    """
    if _client is None:
        return

    if tired:
        text = random.choice(_TIRED_WAKE_LINES)
    else:
        today = datetime.now(KST).date()
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
