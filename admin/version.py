import os
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
