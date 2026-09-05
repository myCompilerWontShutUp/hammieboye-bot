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


# 순서가 그대로 /자판기-리스트 표시 순서. price는 전부 舊 "원" 가격을 100으로 나눈
# 값(joke 품목 제외 — op 권한은 999,999,999 그대로 유지, 결제 자체를 안 해서 원래도
# //100 환산이 안 쓰였다).
ITEMS: tuple[VendingItem, ...] = (
    VendingItem("sunflower_seed", "해바라기 씨", 5, "snack", 1),
    VendingItem("almond", "아몬드", 23, "snack", 4),
    VendingItem("dandelion", "민들레 꽃", 69, "snack", 10),
    VendingItem("frozen_yolk", "동결된 노른자", 740, "snack", 36),
    # 추가 효과는 아직 미정 — 지금은 간식 지급만 하고 자리만 남겨둔다(note에만 표시).
    VendingItem(
        "premium_mealworm", "프리미엄 건조 밀웜", 10_001, "snack", 99,
        note="추가 효과 ???",
    ),
    # "coin" 품목(2026-09-05, 舊 "capacity" — 동전 보유 상한 폐지와 함께 용도 전환)은
    # 이제 /동전의 기본 지급량(1개)에 더해지는 보너스를 늘린다.
    VendingItem("coin_wallet", "동전 지갑", 10, "coin", 1),
    VendingItem("piggy_bank", "돼지 저금통", 158, "coin", 20),
    VendingItem("hammie_account", "햄미 계좌 개설", 1_670, "coin", 500),
    # 장난 상품 — 실제로 구매할 수 있을 만큼 동전이 있어도 아무 효과가 없고, 동전도
    # 차감하지 않는다(command/vending.py에서 이 id만 결제 자체를 건너뛴다).
    VendingItem("op_permission", "햄미 op 권한", 999_999_999, "joke", 0),
)

BY_ID: dict[str, VendingItem] = {item.id: item for item in ITEMS}
# 슬래시 커맨드의 Literal 선택지는 표시 이름을 그대로 값으로 받으므로(내부 id가 아니라
# 사람이 읽는 한글 이름), 그 이름으로 다시 품목을 찾을 때 쓴다.
BY_NAME: dict[str, VendingItem] = {item.name: item for item in ITEMS}
ITEM_NAMES: tuple[str, ...] = tuple(item.name for item in ITEMS)

SNACK_NAMES: dict[str, str] = {
    item.id: item.name for item in ITEMS if item.kind == "snack"
}
