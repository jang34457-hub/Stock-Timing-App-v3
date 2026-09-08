"""FastAPI 없이 매일 장 마감 잡을 돌리는 전용 프로세스.

Windows 작업 스케줄러나 Linux systemd 로 이 파일만 항상 켜 두면
uvicorn / 모바일 앱이 내려가도 파이프라인이 실행된다.
(운영 권장: GitHub Actions .github/workflows/daily-job.yml)

  python scripts/job_worker.py
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    _load_dotenv()
    os.environ["JOB_RUNNER"] = "inprocess"
    os.environ["SCHEDULER_ENABLED"] = "1"
    if not os.getenv("SMS_PROVIDER", "").strip():
        os.environ["SMS_PROVIDER"] = "test"

    from scheduler import scheduler_status, start_scheduler, stop_scheduler

    started = start_scheduler()
    if started is None:
        raise SystemExit("APScheduler did not start")
    print(json_status(scheduler_status()), flush=True)

    stopping = False

    def _stop(_signum=None, _frame=None) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        stop_scheduler()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    while True:
        time.sleep(60)


def json_status(payload: dict) -> str:
    import json

    return json.dumps({"ok": True, "worker": True, **payload}, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
