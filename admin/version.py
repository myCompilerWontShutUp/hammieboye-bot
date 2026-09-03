import os
import re
import subprocess
from datetime import datetime, timezone

# Railway가 배포 시 자동으로 심어주는 커밋 해시 env var를 우선 쓰고, 없으면
# (로컬 실행 등) git 명령으로 폴백한다.
_START_TIME = datetime.now(timezone.utc)


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


_MERGE_PR_PATTERN = re.compile(r"Merge pull request #(\d+)")


def get_semantic_version() -> str | None:
    """"0.{PR 번호}.{그 PR 안의 커밋 개수}" 형식(예: "0.25.2"). 이 저장소는 PR을 항상 병합
    커밋으로 합치므로("Merge pull request #N from ..."), HEAD에서 가장 가까운 병합 커밋의
    제목에서 PR 번호를 뽑고, 그 병합 커밋의 두 부모 사이(`merge^1..merge^2`, 즉 병합된
    브랜치에만 있던 커밋들)의 개수를 센다. 병합 커밋이 없거나(얕은 클론 등) 형식이
    안 맞으면 None — 호출부가 해시만 보여주는 쪽으로 폴백해야 한다."""
    merge_hash = _git("log", "--merges", "-1", "--format=%H")
    if not merge_hash:
        return None
    subject = _git("log", "-1", "--format=%s", merge_hash)
    if not subject:
        return None
    match = _MERGE_PR_PATTERN.search(subject)
    if not match:
        return None
    count = _git("rev-list", "--count", f"{merge_hash}^1..{merge_hash}^2")
    if not count or not count.isdigit():
        return None
    return f"0.{match.group(1)}.{count}"


def get_version_label() -> str:
    """`v` 명령어와 `an update` 임베드가 공유하는 버전 표시 — "aaaaaa (0.25.2)" 형태.
    get_semantic_version()이 None이면(병합 커밋을 못 찾음 등) 해시만 반환한다."""
    commit = get_commit_hash()
    semantic = get_semantic_version()
    return f"{commit} ({semantic})" if semantic else commit
