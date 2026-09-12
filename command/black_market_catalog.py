from dataclasses import dataclass
from typing import Literal

_ItemKind = Literal["snack", "potion", "tool"]


@dataclass(frozen=True)
class BlackMarketItem:
    id: str
    name: str
    price: int  # 코인 단위, /자판기와 동일한 화폐.
    kind: _ItemKind
    # 확률적 간식 전용 필드(kind == "snack") — 구매 시점이 아니라 /사용으로 실제
    # 먹였을 때(디저트 타임)만 굴린다(command/eat.py 참고).
    good_delta: int | None = None
    bad_delta: int | None = None
    # 좋은 결과가 나올 확률(2026-09-09 신규) — 기본 50/50, 산딸기?만 25%로 예외.
    good_chance: float = 0.5
    # True면 good_delta/bad_delta 대신 "현재 호감도의 2배" / "현재 호감도의 절반으로
    # 감소"를 적용한다(악마의 씨앗 전용 — 먹이는 시점의 호감도에 따라 결과가 달라지는
    # 유일한 품목). 2026-09-08 최초 설계는 "나쁘면 0으로 리셋"이었으나, 완전히
    # 초기화되는 건 너무 가혹하다는 사용자 피드백으로 "절반만 잃는" 쪽으로 완화했다.
    double_or_halve: bool = False
    good_reaction: str = ""
    bad_reaction: str = ""
    # True면 결제 자체를 건너뛰는 장난 품목이다(舊 command/vending_catalog.py의
    # op_permission과 동일한 컨셉, 2026-09-08 /암시장 도구 카테고리로 이전) — 실제로
    # 구매되지 않고 돈도 안 깎인다. `kind`는 여전히 "tool"이라 도구 탭에는 정상적으로
    # 나열된다("kind"는 탭 소속, "is_joke"는 구매 시점 분기 전용으로 서로 다른 축이다).
    is_joke: bool = False
    # "도구" 카테고리 표시용 한 줄 설명 — snack 품목은 good_delta/bad_delta/
    # double_or_halve로부터 상세 문구를 계산해서 만들기 때문에 이 필드가 필요 없다.
    description: str = ""
    # 관리자 콘솔 itm 명령어 전용 8자리 코드 — command/vending_catalog.py::VendingItem.code와
    # 동일한 원칙(업적 코드와 동일한 알고리즘, 정적 고정, 장난 품목은 코드 없음).
    code: str | None = None
    # True면 itm get이 count를 무시하고 항상 1개만 지급한다(command/vending_catalog.py
    # ::VendingItem.is_one_time과 동일한 향후 확장용 플래그, 지금은 해당 품목 없음).
    is_one_time: bool = False


# 2026-09-08 신규 — /암시장은 취침 시간대(is_sleep_time_for)에만 열리는 별도 상점.
# "간식" 카테고리는 위험한 확률적 간식 3종(전부 정확히 50/50).
SNACK_ITEMS: tuple[BlackMarketItem, ...] = (
    BlackMarketItem(
        "moldy_cheese", "곰팡이 치즈", 14, "snack",
        good_delta=4, bad_delta=-4,
        good_reaction="오히려 쿰쿰해서 더 맛있다!!",
        bad_reaction="곰팡이 때문에 배가 아파졌다...",
        code="rpn2x7s8",
    ),
    BlackMarketItem(
        "raspberry", "산딸기?", 666, "snack",
        good_delta=28, bad_delta=-7, good_chance=0.25,
        good_reaction="맛있고 잘 익은 산딸기다!!",
        bad_reaction="산딸기인 줄 알았는데 뱀딸기였다...",
        code="911ab85m",
    ),
    BlackMarketItem(
        "devil_seed", "악마의 씨앗", 11_111, "snack",
        double_or_halve=True,
        good_reaction="세상에서 먹어본 씨앗 중 가장 맛있었다!!",
        bad_reaction="세상에서 먹어본 씨앗 중 가장 최악이었다...",
        code="ob173yyt",
    ),
)
# "포션" 카테고리(2026-09-12 신규, §24) — 기존 "간식"(good/bad 확률형)과 완전히 다른
# 형태로, 확률 없이 햄미에게 하루/24시간짜리 고정 특수 효과를 부여한다. good_delta/
# bad_delta/double_or_halve 계열은 안 쓰고 description만 채운다(_item_block()의 else
# 분기가 이미 description을 그대로 렌더링). 실제 효과는 command/eat.py::_handle_potion()이
# item.id별로 하드코딩 분기한다 — "드링킹 타임" 슬롯에서만 /사용으로 급여 가능(디저트
# 타임 슬롯에서는 급여 불가).
POTION_ITEMS: tuple[BlackMarketItem, ...] = (
    BlackMarketItem(
        "treadmill_energy_drink", "쳇바퀴 에너지 드링크", 27, "potion",
        description="먹이면 호감도 +2를 주고, 그날 햄미의 취침 시각을 30분 늦춥니다.",
        code="wjxhkm1u",
    ),
    BlackMarketItem(
        "memory_ade", "추억이 담긴 에이드", 1_580, "potion",
        description="먹이면 경험치 1~100을 무작위로 지급합니다.",
        code="f1nog458",
    ),
    BlackMarketItem(
        "h_potion", "H미약", 3_000, "potion",
        description="준 시점부터 24시간 동안 그 사람이 얻는 모든 호감도가 2배가 되고 "
        "호감도가 전혀 떨어지지 않습니다.",
        code="cylm75ly",
    ),
)
# "도구" 카테고리(2026-09-08 신규) — 금서/햄미 일정표는 command/forbidden_book.py·
# command/hammie_schedule.py가 각자 전용 흐름으로 처리하고, 여기 price/name만
# 카탈로그 진입점으로 쓰인다. 관리자 권한은 순수 장난 품목(is_joke=True).
TOOL_ITEMS: tuple[BlackMarketItem, ...] = (
    BlackMarketItem(
        "forbidden_book", "금서", 100, "tool",
        description="/사용 금서로 키워드와 내용을 가르칠 수 있습니다(1인당 5개, 일주일 뒤 소멸).",
        code="tn5bz7mg",
    ),
    BlackMarketItem(
        "hammie_schedule", "햄미 일정표", 100, "tool",
        description="/사용 햄미 일정표로 오늘 하루 일과를 확인할 수 있습니다(본인에게만 보임).",
        code="2fqm19jl",
    ),
    BlackMarketItem(
        "bot_admin_permission", "햄미보이'봇' 관리자 권한", 999_999_999, "tool",
        is_joke=True,
    ),
)

ITEMS: tuple[BlackMarketItem, ...] = SNACK_ITEMS + POTION_ITEMS + TOOL_ITEMS
BY_ID: dict[str, BlackMarketItem] = {item.id: item for item in ITEMS}
BY_NAME: dict[str, BlackMarketItem] = {item.name: item for item in ITEMS}
