from dataclasses import dataclass
from typing import Literal

_ItemKind = Literal["snack", "coin", "joke"]


@dataclass(frozen=True)
class VendingItem:
    id: str
    name: str
    price: int  # 코인 단위(2026-09-05부터 "원" 개념 폐지 — 코인이 유일한 화폐).
    kind: _ItemKind
    effect: int  # snack이면 호감도, coin이면 /동전 획득량 증가분, joke면 0.
    note: str | None = None
    # 관리자 콘솔 itm 명령어 전용 8자리 코드(tools/codegen.py, 업적 코드와 동일한
    # 알고리즘/알파벳으로 생성해 정적으로 고정 — achievements/*.py의 CODE 상수와 동일한
    # 관례). 장난 품목(joke)은 코드 자체가 없다("op권은 없습니다").
    code: str | None = None
    # True면 itm get이 count를 무시하고 항상 1개만 지급한다("1회만 구매 가능한 획득성
    # 아이템" 전용 플래그, 2026-09-08 신규) — 지금은 이 조건에 해당하는 품목이 없어
    # 전부 False, 향후 그런 품목이 추가되면 여기만 True로 바꾸면 된다.
    is_one_time: bool = False


# 순서가 그대로 /자판기 표시 순서. price는 전부 舊 "원" 가격을 100으로 나눈
# 값(joke 품목 제외 — op 권한은 999,999,999 그대로 유지, 결제 자체를 안 해서 원래도
# //100 환산이 안 쓰였다).
ITEMS: tuple[VendingItem, ...] = (
    VendingItem("sunflower_seed", "해바라기 씨", 5, "snack", 1, code="5zhssk4k"),
    VendingItem("almond", "아몬드", 22, "snack", 2, code="u0i0h6kd"),
    VendingItem("dandelion", "민들레 꽃", 69, "snack", 5, code="b8g4js9u"),
    VendingItem("frozen_yolk", "동결된 노른자", 740, "snack", 19, code="z7fqtf3z"),
    VendingItem("premium_mealworm", "프리미엄 건조 밀웜", 10_001, "snack", 33, code="mxb34cjm"),
    # "coin" 품목(2026-09-05, 舊 "capacity" — 동전 보유 상한 폐지와 함께 용도 전환)은
    # 이제 /동전의 기본 지급량(1개)에 더해지는 보너스를 늘린다. 2026-09-08 3종 -> 5종
    # 재편(舊 돼지 저금통 -> 쪼꼬미 금고 개명+재조정, 舊 햄미 계좌 개설 -> 햄미 볼주머니
    # 강화 개명, 꿀돼지 저금통/HM은행 계좌 개설 신규 추가)과 함께 가격만 구매할 때마다
    # 2배씩 오르는 방식으로 너프됐다(command/vending.py::_current_price(), 효과량은
    # 안 바뀜) — 여기 적힌 price는 그 유저의 "첫 구매" 기준 가격이다.
    VendingItem("coin_wallet", "동전 지갑", 10, "coin", 1, code="oze7i7do"),
    VendingItem("tiny_safe", "쪼꼬미 금고", 152, "coin", 20, code="4jvray47"),
    VendingItem("honey_piggy_bank", "꿀돼지 저금통", 419, "coin", 70, code="l99fkhk4"),
    VendingItem("hm_bank_account", "HM은행 계좌 개설", 899, "coin", 200, code="gcnueozs"),
    VendingItem("cheek_pouch", "햄미 볼주머니 강화", 1_670, "coin", 500, code="gpym4udn"),
    # 장난 상품 — 실제로 구매할 수 있을 만큼 동전이 있어도 아무 효과가 없고, 동전도
    # 차감하지 않는다(command/vending.py에서 이 id만 결제 자체를 건너뛴다). code 없음.
    VendingItem("op_permission", "햄미 op 권한", 999_999_999, "joke", 0),
)

BY_ID: dict[str, VendingItem] = {item.id: item for item in ITEMS}
# 표시 이름(내부 id가 아니라 사람이 읽는 한글 이름)으로 품목을 찾을 때 쓴다 — 舊
# 슬래시 커맨드 Literal 선택지가 이 이름을 값으로 썼던 흔적(2026-09-08 /자판기가
# 카테고리 탭+Select UI로 개편되며 그 용도는 사라졌지만, command/eat.py의 카탈로그
# 병합 조회 등 다른 곳에서 여전히 쓰인다).
BY_NAME: dict[str, VendingItem] = {item.name: item for item in ITEMS}

SNACK_NAMES: dict[str, str] = {
    item.id: item.name for item in ITEMS if item.kind == "snack"
}
