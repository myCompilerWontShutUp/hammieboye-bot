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
