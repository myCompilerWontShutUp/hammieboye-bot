"""levels.py의 레벨 테이블(LEVELS)이 지켜야 할 불변식을 검증하는 점검 스크립트.
레벨표를 손으로 조정할 때마다(임계값 변경, 새 레벨 추가 등) 단조성이 실수로
깨지지 않았는지 확인하는 용도 — 런타임에는 안 쓰이고, 수정 후 직접 실행해서
확인한다.

    python -m tools.verify_levels

전부 통과하면 요약 한 줄만 출력한다. 어긋난 항목이 있으면 어떤 레벨의 어떤
필드가 문제인지 전부 나열하고 종료 코드 1을 반환한다.
"""

import sys

import levels

# 레벨이 오를수록 절대 줄어들면 안 되는(=단조 비증가 금지) 권한 필드 — 한 번
# 열리면 다시 닫히지 않는다는 게 레벨 시스템의 기본 전제(CLAUDE.md §23-2).
_MONOTONIC_BOOL_FIELDS = (
    "vending_allowed",
    "black_market_allowed",
    "gambling_allowed",
    "gift_command_allowed",
    "file_upload_allowed",
    "image_gen_allowed",
)


def check_invariants() -> list[str]:
    problems: list[str] = []
    if levels.LEVELS[0].xp_required != 0:
        problems.append("레벨 0의 필요 경험치가 0이 아님")

    prev: levels.Level | None = None
    for level in levels.LEVELS:
        if prev is not None:
            if level.xp_required <= prev.xp_required:
                problems.append(f"레벨 {level.number}: 필요 경험치가 이전 레벨 이하")
            if level.daily_nl_limit < prev.daily_nl_limit:
                problems.append(f"레벨 {level.number}: 하루 대화 횟수가 이전 레벨보다 적음")
            if level.double_drop_chance < prev.double_drop_chance:
                problems.append(f"레벨 {level.number}: /동전 2배 확률이 이전 레벨보다 낮음")
            if level.quintuple_drop_chance < prev.quintuple_drop_chance:
                problems.append(f"레벨 {level.number}: /동전 5배 확률이 이전 레벨보다 낮음")
            for field in _MONOTONIC_BOOL_FIELDS:
                if getattr(prev, field) and not getattr(level, field):
                    problems.append(f"레벨 {level.number}: {field}가 이전 레벨보다 닫힘")
        prev = level
    return problems


if __name__ == "__main__":
    found = check_invariants()
    if found:
        for problem in found:
            print(f"[FAIL] {problem}")
        sys.exit(1)
    print(f"레벨 {len(levels.LEVELS)}개 전부 불변식 통과.")
