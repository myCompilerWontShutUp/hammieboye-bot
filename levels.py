"""0~7레벨 XP 시스템(2026-09-10 신규, CLAUDE.md §23) — 레벨이 오를수록 자연어 일일
횟수·입출력 글자수·자판기/암시장/도박 접근권·`/동전` 2배 드랍률 등 실질적인 권한이
늘어난다. 레벨 자체는 DB에 저장하지 않고 `users.total_xp`에서 이 테이블로 매번
계산한다("업적 1개당 파일 1개" 원칙과 동일하게, 레벨표는 이 파일 하나가 유일한
출처 — 자판기/암시장/도박/자연어 파이프라인 등 다른 모든 모듈은 여기를 통해서만
권한을 확인한다).

파일 업로드/그림 요청/`/선물`은 아직 실제 명령어가 없다 — 레벨표에 권한 필드만
미리 마련해두고(추후 그 기능이 생기면 여기 값을 그대로 참조), 지금은 게이팅 대상이
없다."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    number: int
    name: str
    xp_required: int
    daily_nl_limit: int
    input_char_limit: int | None  # None = 무제한
    output_char_limit: int | None
    file_upload_allowed: bool
    daily_file_uploads: int | None
    image_gen_allowed: bool
    daily_image_gens: int | None
    vending_allowed: bool
    black_market_allowed: bool
    gambling_allowed: bool  # /도박만 해당 — /내기는 레벨 무관 항상 허용
    gift_command_allowed: bool
    double_drop_chance: float
    quintuple_drop_chance: float = 0.0  # /동전 x5 확률(2026-09-10 신규, x2와 별개로 중첩 판정)
    achievement_id: str | None = None  # 이 레벨 도달 시 자동 부여(없으면 None)


LEVELS: tuple[Level, ...] = (
    Level(0, "뉴비", 0, 10, 100, 100, False, None, False, None, False, False, False, False, 0.10, 0.00),
    Level(1, "로보로브스키", 50, 30, 100, 100, False, None, False, None, True, False, True, False, 0.10, 0.01),
    Level(2, "정글리안", 150, 60, 1000, 100, False, None, True, 3, True, False, True, False, 0.10, 0.01),
    Level(3, "시리안", 300, 100, 1000, 100, False, None, True, 5, True, True, True, False, 0.20, 0.02),
    Level(4, "와일드", 500, 150, 1000, 100, True, 3, True, 7, True, True, True, False, 0.20, 0.02, "wild_owner"),
    Level(5, "킹스터", 750, 210, None, 500, True, 3, True, 10, True, True, True, True, 0.50, 0.05),
    Level(6, "그랜드 킹스터", 1050, 280, None, 500, True, 5, True, 10, True, True, True, True, 0.50, 0.05),
    Level(
        7, "앱솔루트 햄로드", 1400, 360, None, 500, True, 10, True, 10, True, True, True, True, 1.00, 0.10,
        "ultra_king_god_general_majesty_alltime_legend_owner",
    ),
)

MAX_LEVEL = LEVELS[-1].number


def get_level_for_xp(total_xp: int) -> Level:
    """total_xp가 도달한 가장 높은 레벨을 반환한다(임계값 내림차순 탐색, 항상 최소
    레벨 0은 보장됨 — xp_required=0이라 매칭 실패가 없다)."""
    for level in reversed(LEVELS):
        if total_xp >= level.xp_required:
            return level
    return LEVELS[0]


def get_next_level(level: Level) -> Level | None:
    """다음 레벨(레벨 7이면 None — 이미 최고 레벨)."""
    if level.number >= MAX_LEVEL:
        return None
    return LEVELS[level.number + 1]


def min_level_for(feature: str) -> Level:
    """해당 boolean 권한 필드(예: "vending_allowed")가 처음 True가 되는 레벨을
    반환한다 — 레벨표의 권한 필드는 전부 레벨이 오를수록 단조 증가(한 번 열리면
    다시 안 닫힘)라 "최초로 열리는 레벨" 하나로 항상 잘 정의된다. `/자판기`·
    `/암시장`·`/도박` 등 레벨 게이팅 거절 문구에 "레벨 N부터"를 보여줄 때 쓴다."""
    for level in LEVELS:
        if getattr(level, feature):
            return level
    return LEVELS[-1]


_BAR_LENGTH = 10
_BAR_FILLED = "◼"
_BAR_EMPTY = "◻"


def xp_progress_bar(total_xp: int, level: Level, next_level: Level | None) -> str:
    """레벨 구간 안에서의 진행률을 10칸 텍스트 바로 시각화한다. 최고 레벨이면
    꽉 찬 바 + "MAX"."""
    if next_level is None:
        return _BAR_FILLED * _BAR_LENGTH + " MAX"
    span = next_level.xp_required - level.xp_required
    progress = total_xp - level.xp_required
    ratio = progress / span if span > 0 else 1.0
    filled = min(max(round(ratio * _BAR_LENGTH), 0), _BAR_LENGTH)
    percent = min(max(round(ratio * 100), 0), 100)
    return f"{_BAR_FILLED * filled}{_BAR_EMPTY * (_BAR_LENGTH - filled)} {percent}%"
