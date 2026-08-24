import logging
from datetime import date, datetime, timedelta

import discord
from korean_lunar_calendar import KoreanLunarCalendar
from openai import AsyncOpenAI

from config import ALLOWED_GUILD_IDS, OPENAI_API_KEY, OPENAI_JUDGE_MODEL, OPENAI_MAX_OUTPUT_TOKENS
from core.scheduler import KST
from db.guild_channels import get_last_channel
from responses.engine import SYSTEM_PROMPT

_client: discord.Client | None = None
_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

_BIRTH_DATE = date(2017, 12, 22)

_FALLBACK_GREETING = "조은 아침이야! 햄미 일어나써 뾱 🐹"

# 고정 양력 기념일. (월, 일) -> 예시 문구. 예시일 뿐이라 LLM이 매번 표현을 바꿔서 전달한다.
_FIXED_ANNIVERSARIES: dict[tuple[int, int], str] = {
    (1, 1): "새해다! 올해도 햄미랑 가치 놀자뾱!",
    (1, 14): "오늘은 다이어리 데이래. 햄미 얘기도 적어조!",
    (2, 14): "밸런타인데이래! 초콜릿 말고 씨앗 조라뾱.",
    (3, 1): "오늘은 삼일절이야. 태극기 다는 날이야!",
    (3, 3): "삼겹살데이래! 햄미는 해바라기씨 머글래.",
    (3, 14): "화이트데이야! 햄미 사탕도 이써?",
    (4, 1): "만우절이야! 햄미 사실 햄스터 아님… 뻥이야뾱.",
    (4, 14): "블랙데이래. 짜장면에 햄미 빠뜨리면 안 대!",
    (4, 22): "지구의 날이야! 페트병은 햄미 주고 잘 재활용해조.",
    (5, 5): "어린이날이야! 햄미도 아직 애기니까 선물 조라.",
    (5, 14): "로즈데이래! 장미보다 해바라기씨가 조아.",
    (6, 6): "오늘은 현충일이야. 고마운 분들을 기억하자.",
    (7, 17): "오늘은 제헌절이야! 대한민국 헌법이 만들어진 날이래.",
    (8, 8): "세계 고양이의 날이래. 햄미는 오늘 쪼금 숨어 이쓸게.",
    (8, 15): "오늘은 광복절이야. 아주 소중한 날이니까 기억하자!",
    (10, 3): "오늘은 개천절이야! 하늘이 열린 날이래뾱.",
    (10, 9): "오늘은 한글날이야! 햄미도 한글 조아해. 뾱뾱!",
    (10, 25): "오늘은 독도의 날이야! 독도는 우리 땅이야뾱.",
    (10, 31): "핼러윈이야! 간식 안 주면 페트병 흔들 거야. 쟈쟈쟉!",
    (11, 11): "빼빼로데이래! 햄미한텐 해바라기씨 막대기 조라.",
    (12, 22): "오늘은 햄미 생일이야! 햄미가 주인공이야! 간식 조라뾱! 🎂",
    (12, 24): "크리스마스이브야! 산타 햄미 오는 중이야.",
    (12, 25): "메리 크리스마스! 선물 상자에 해바라기씨 이써?",
    (12, 31): "올해 마지막 날이야! 햄미랑 놀아줘서 고마워뾱.",
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
        daeboreum: "달이 엄청 동그래! 햄미 볼주머니 같아뾱.",
        buddha: "오늘은 부처님오신날이야. 마음을 편하게 가지자뾱.",
        chuseok - timedelta(days=1): "낼은 추석이야! 햄미 송편 기다리는 중이야.",
        chuseok: "즐거운 추석이야! 보름달처럼 볼주머니도 채워조.",
        chuseok + timedelta(days=1): "추석 간식 남아찌? 버리면 안 대니까 햄미 조.",
        suneung: "오늘은 수능날이야! 수험생 인간들 모두 힘내뾱!",
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
        "(예: '조은 아침이야! 햄미 일어나써 뾱 🐹' 같은 톤). 매일 표현을 조금씩 다르게 하고, "
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
        )
        return result.output_text.strip()
    except Exception:
        logging.exception("Daily greeting generation failed")
        return _FALLBACK_GREETING


async def post_daily_greeting() -> None:
    if _client is None:
        return

    today = datetime.now(KST).date()
    text = await _generate(_build_task(today))

    for guild in _client.guilds:
        if guild.id not in ALLOWED_GUILD_IDS:
            continue
        channel_id = await get_last_channel(guild.id)
        if channel_id is None:
            continue
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue
        try:
            await channel.send(text)
        except discord.HTTPException:
            logging.exception("Failed to post daily greeting in guild %s", guild.id)
