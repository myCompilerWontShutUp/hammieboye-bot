from typing import Union

from command.black_market_catalog import ITEMS as _BLACK_MARKET_ITEMS
from command.black_market_catalog import BlackMarketItem
from command.vending_catalog import ITEMS as _VENDING_ITEMS
from command.vending_catalog import VendingItem

# 관리자 콘솔 itm 명령어 전용 — /자판기(vending_catalog)와 /암시장(black_market_catalog)
# 두 카탈로그를 하나로 합쳐서 다룬다. 순서는 "list"/"help"/"code" 표시 순서이기도 하다
# (자판기 먼저, 그다음 암시장 — 각 카탈로그 내부는 원래 파일의 선언 순서 그대로).
AnyItem = Union[VendingItem, BlackMarketItem]

ALL_ITEMS: tuple[AnyItem, ...] = _VENDING_ITEMS + _BLACK_MARKET_ITEMS
BY_ID: dict[str, AnyItem] = {item.id: item for item in ALL_ITEMS}

# 코드가 있는(=장난 품목이 아닌) 품목만 등록된다 — "op권은 없습니다"를 여기서 자연스럽게
# 강제한다(joke 품목은 애초에 code=None이라 이 딕셔너리에 안 들어감).
CODE_REGISTRY: dict[str, AnyItem] = {
    item.code: item for item in ALL_ITEMS if item.code is not None
}


def is_joke(item: AnyItem) -> bool:
    """VendingItem은 kind=="joke", BlackMarketItem은 is_joke 필드로 장난 품목 여부를
    표시한다 — 두 카탈로그의 서로 다른 표현을 여기서 하나로 통일해서 판정한다."""
    if isinstance(item, VendingItem):
        return item.kind == "joke"
    return item.is_joke


def source_label(item: AnyItem) -> str:
    """itm list에서 "op권은 가짜 상품임도 표시"처럼 출처를 밝힐 때 쓰는 한글 라벨."""
    return "자판기" if isinstance(item, VendingItem) else "암시장"


def is_coin_item(item: AnyItem) -> bool:
    """/동전 획득량을 늘리는 "투자" 품목인지 — vending_catalog에만 존재하는 kind다.
    이 품목만 user_snacks 대신 users.coin_grant_bonus를 직접 조작한다."""
    return isinstance(item, VendingItem) and item.kind == "coin"


def describe(item: AnyItem) -> str:
    """itm help/list에서 쓰는 한 줄 효과 설명. is_joke()는 이미 호출부가 따로 표시하므로
    여기선 정상 품목 기준의 효과만 서술한다."""
    if is_coin_item(item):
        return f"`/동전` 획득량 +{item.effect}"
    if isinstance(item, VendingItem):  # kind == "snack"
        return f"먹일 시 호감도 +{item.effect}"
    if item.kind == "snack":  # BlackMarketItem 확률적 간식
        if item.double_or_halve:
            return "먹일 시 호감도가 현재의 2배가 되거나 절반으로 줄어듦"
        return f"먹일 시 호감도 +{item.good_delta} 또는 {item.bad_delta}"
    return item.description or "???"  # BlackMarketItem "도구"
