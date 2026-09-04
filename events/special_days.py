from datetime import date, timedelta

from korean_lunar_calendar import KoreanLunarCalendar

DAY_TYPE_NORMAL = "normal"
DAY_TYPE_SPECIAL = "special"
DAY_TYPE_BIRTHDAY = "birthday"

# 우선순위: birthday > special > normal. special은 주말 전부 + 기존 기념일 목록(양력 고정
# 23개 + 음력 기반 9개) 전부 — 엄숙한 날/한국 로컬 기념일도 가리지 않고 전부 포함한다.
# 햄미 생일만 목록에서 빠져 birthday로 승격된다.
_MULTIPLIER = {DAY_TYPE_NORMAL: 1, DAY_TYPE_SPECIAL: 2, DAY_TYPE_BIRTHDAY: 3}
_HELP_ME_EVENT_COUNT = {DAY_TYPE_NORMAL: 3, DAY_TYPE_SPECIAL: 5, DAY_TYPE_BIRTHDAY: 5}

_WEEKEND_LABEL = "주말"

BIRTH_DATE = date(2017, 12, 22)

# 고정 양력 기념일. (월, 일) -> 예시 문구. events/greeting.py의 아침 인사 생성 프롬프트와
# 이 모듈의 날짜 타입 판정이 공유하는 단일 소스.
FIXED_ANNIVERSARIES: dict[tuple[int, int], str] = {
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


def lunar_based_anniversaries(year: int) -> dict[date, str]:
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


def _is_weekend(today: date) -> bool:
    return today.weekday() >= 5  # 토(5)/일(6)


def _is_birthday(today: date) -> bool:
    return today.month == BIRTH_DATE.month and today.day == BIRTH_DATE.day


def get_day_type(today: date) -> str:
    if _is_birthday(today):
        return DAY_TYPE_BIRTHDAY
    if _is_weekend(today):
        return DAY_TYPE_SPECIAL
    if (today.month, today.day) in FIXED_ANNIVERSARIES:
        return DAY_TYPE_SPECIAL
    if today in lunar_based_anniversaries(today.year):
        return DAY_TYPE_SPECIAL
    return DAY_TYPE_NORMAL


def get_multiplier(today: date) -> int:
    return _MULTIPLIER[get_day_type(today)]


def get_help_me_event_count(today: date) -> int:
    return _HELP_ME_EVENT_COUNT[get_day_type(today)]


def get_day_type_label(today: date) -> str | None:
    """기상 공지에 쓸 짧은 설명. normal이면 None(공지 자체를 안 함)."""
    day_type = get_day_type(today)
    if day_type == DAY_TYPE_BIRTHDAY:
        return "햄미 생일"
    if day_type == DAY_TYPE_SPECIAL:
        fixed = FIXED_ANNIVERSARIES.get((today.month, today.day))
        if fixed:
            return fixed
        lunar = lunar_based_anniversaries(today.year).get(today)
        if lunar:
            return lunar
        return _WEEKEND_LABEL
    return None


def get_event_label(today: date) -> str | None:
    """호감도 획득 알림(format_affection_notice)에 쓸 짧은 카테고리 라벨 — 생일 이벤트/
    주말 이벤트/기념일 이벤트, normal이면 None. get_day_type_label과 달리 실제 기념일
    문구(긴 문장)가 아니라 항상 이 3개 중 하나로 고정된 짧은 이름이다."""
    day_type = get_day_type(today)
    if day_type == DAY_TYPE_BIRTHDAY:
        return "생일 이벤트"
    if day_type == DAY_TYPE_SPECIAL:
        return "주말 이벤트" if _is_weekend(today) else "기념일 이벤트"
    return None
