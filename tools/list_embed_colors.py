"""프로젝트 전체에서 embed 색상 상수(이름에 EMBED_COLOR가 들어간 것)를 모아
보여주는 점검 스크립트 — 새 도메인 전용 색을 고를 때 기존 색과 우연히 겹치지
않는지 확인하는 용도(2026-09-10, 배팅 정산 전용 색을 고르며 매번 손으로 grep
하던 걸 대체). 런타임에는 안 쓰인다.

    python -m tools.list_embed_colors

값(16진수) 기준으로 묶어서 출력하고, 같은 값을 쓰는 상수가 둘 이상이면
"[중복!]" 표시를 붙인다.
"""

import re
from pathlib import Path

_COLOR_PATTERN = re.compile(r"([A-Z_]*EMBED_COLOR[A-Z_]*)\s*=\s*(0x[0-9A-Fa-f]{6})")


def find_colors(root: Path) -> dict[str, list[tuple[str, str]]]:
    """{16진값(소문자): [(파일 경로, 상수명), ...]}."""
    by_value: dict[str, list[tuple[str, str]]] = {}
    for path in root.rglob("*.py"):
        if "tools" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for name, value in _COLOR_PATTERN.findall(text):
            by_value.setdefault(value.lower(), []).append((str(path.relative_to(root)), name))
    return by_value


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    for value, entries in sorted(find_colors(root).items()):
        marker = "  [중복!]" if len(entries) > 1 else ""
        print(f"{value}{marker}")
        for file, name in entries:
            print(f"  - {name} ({file})")
