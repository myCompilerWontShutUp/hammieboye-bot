import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Railway가 배포 시 자동으로 심어주는 커밋 해시 env var를 우선 쓰고, 없으면
# (로컬 실행 등) git 명령으로 폴백한다.
_START_TIME = datetime.now(timezone.utc)

# 저장소 루트의 VERSION 파일 — admin/version.py 기준 한 단계 위.
_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5, check=True
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def get_commit_hash() -> str:
    env_sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    if env_sha:
        return env_sha[:7]
    return _git("rev-parse", "--short", "HEAD") or "알 수 없음"


def get_last_updated_iso() -> str:
    """마지막 커밋 일시(ISO 8601). git 정보를 못 구하면 프로세스 시작 시각으로 대체한다."""
    return _git("log", "-1", "--format=%cI") or _START_TIME.isoformat()


def get_previous_commit() -> tuple[str, str] | None:
    """(짧은 해시, ISO 일시) — HEAD 바로 이전 커밋. 이전 커밋이 없거나(첫 커밋) git 정보를
    못 구하면(배포 환경의 얕은 클론 등) None — get_commit_hash()와 달리 폴백 문자열을
    반환하지 않는다, 호출부가 "정보 없음"과 "정상인데 이전 커밋이 없음"을 구분해야 해서."""
    output = _git("log", "-2", "--format=%h %cI")
    if not output:
        return None
    lines = output.splitlines()
    if len(lines) < 2:
        return None
    h, iso = lines[1].split(" ", 1)
    return h, iso


def get_recent_commits(days: int = 30) -> list[tuple[str, str, str]]:
    """(짧은 해시, ISO 일시, 제목) 목록, 최신순. git 정보를 못 구하면 빈 리스트."""
    output = _git("log", f"--since={days}.days", "--format=%h|%cI|%s")
    if not output:
        return []
    result = []
    for line in output.splitlines():
        h, iso, subject = line.split("|", 2)
        result.append((h, iso, subject))
    return result


def get_semantic_version() -> str | None:
    """"0.{PR 번호}.{그 PR 이후 메인에 반영된 푸시 횟수}" 형식(예: "0.26.1", 그 뒤로 잔잔한
    후속 푸시가 4번 더 있었다면 "0.26.5"). git 로그로 병합 커밋을 찾아 즉석에서 계산하지
    않는다 — Railway 등 배포 환경이 얕은 클론이면 git 히스토리가 없어서 실패하는 게 실제로
    확인됐다. 대신 저장소 루트의 VERSION 파일(한 줄, 예: "0.26.1")을 그대로 읽는다 — 이
    값은 코드가 자동으로 갱신하지 않고, 커밋/푸시할 때마다 사람(또는 나, 어시스턴트)이
    직접 관리한다: 같은 PR 흐름 위에서 이어지는 사소한 후속 푸시면 마지막 숫자만 올리고,
    새 PR이 머지되면 "0.{새 PR 번호}.1"로 초기화한다. 파일이 없거나 비어 있으면 None —
    호출부가 해시만 보여주는 쪽으로 폴백해야 한다."""
    try:
        content = _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return content or None


def get_version_label() -> str:
    """`v` 명령어와 `ann update` 임베드가 공유하는 버전 표시 — "aaaaaa (0.25.2)" 형태.
    get_semantic_version()이 None이면(병합 커밋을 못 찾음 등) 해시만 반환한다."""
    commit = get_commit_hash()
    semantic = get_semantic_version()
    return f"{commit} ({semantic})" if semantic else commit
