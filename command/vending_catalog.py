from dataclasses import dataclass
from typing import Literal

_ItemKind = Literal["snack", "capacity", "joke"]


@dataclass(frozen=True)
class VendingItem:
    id: str
    name: str
    price: int  # 원 단위 표시용. 실제 결제는 price // 100(코인)로 환산한다.
    kind: _ItemKind
    effect: int  # snack이면 호감도, capacity면 동전 최대 보유량 증가량, joke면 0.
    note: str | None = None


# 순서가 그대로 /자판기-리스트 표시 순서. price는 전부 100의 배수(joke 품목 제외 —
# 그 품목은 결제 자체를 안 해서 //100 환산이 안 쓰인다).
ITEMS: tuple[VendingItem, ...] = (
    VendingItem("sunflower_seed", "해바라기 씨", 500, "snack", 1),
    VendingItem("almond", "아몬드", 2_300, "snack", 4),
    VendingItem("dandelion", "민들레 꽃", 6_900, "snack", 10),
    VendingItem("frozen_yolk", "동결된 노른자", 74_000, "snack", 36),
    # 추가 효과는 아직 미정 — 지금은 간식 지급만 하고 자리만 남겨둔다(note에만 표시).
    VendingItem(
        "premium_mealworm", "프리미엄 건조 밀웜", 1_000_100, "snack", 99,
        note="추가 효과 ???",
    ),
    VendingItem("coin_wallet", "동전 지갑", 1_000, "capacity", 10),
    VendingItem("piggy_bank", "돼지 저금통", 15_800, "capacity", 200),
    VendingItem("hammie_account", "햄미 계좌 개설", 167_000, "capacity", 5_000),
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
