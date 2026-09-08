"""장 마감 파이프라인 스케줄.

개발: FastAPI 프로세스 안 APScheduler (JOB_RUNNER=inprocess).
운영: FastAPI와 분리. GitHub Actions / cron / scripts/job_worker.py (JOB_RUNNER=external).
파이프라인 자체(TOP20→가격→MA3→신호→SMS)는 jobs.run_daily_job 그대로다.
"""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from jobs import run_daily_job
import sms as _sms  # noqa: F401  # backend/.env

KST = ZoneInfo("Asia/Seoul")
_scheduler: BackgroundScheduler | None = None
_EXTERNAL_RUNNERS = {
    "external",
    "github",
    "actions",
    "cron",
    "worker",
    "cloud",
    "cloud_scheduler",
}
_INPROCESS_RUNNERS = {"inprocess", "in-process", "apscheduler", "fastapi"}


def _is_production() -> bool:
    return os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower() in {
        "production",
        "prod",
    }


def job_runner() -> str:
    raw = os.getenv("JOB_RUNNER", "").strip().lower()
    if raw in _EXTERNAL_RUNNERS:
        return "external"
    if raw in _INPROCESS_RUNNERS:
        return "inprocess"
    return "external" if _is_production() else "inprocess"


def cron_expr() -> str:
    return f"{os.getenv('SCHEDULER_MINUTE', '0')} {os.getenv('SCHEDULER_HOUR', '16')} * * 1-5"


def scheduler_enabled() -> bool:
    """True면 이 프로세스에서 APScheduler를 켠다. 외부 러너와 동시에 켜지지 않게 한다."""
    if job_runner() == "external":
        return False
    default = "0" if _is_production() else "1"
    return os.getenv("SCHEDULER_ENABLED", default).strip().lower() not in {"0", "false", "off"}


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not scheduler_enabled():
        return None
    if _scheduler and _scheduler.running:
        return _scheduler
    hour = int(os.getenv("SCHEDULER_HOUR", "16"))
    minute = int(os.getenv("SCHEDULER_MINUTE", "0"))
    _scheduler = BackgroundScheduler(timezone=KST)
    _scheduler.add_job(
        run_daily_job,
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=KST),
        id="daily_close",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def scheduler_status() -> dict:
    job = None
    if _scheduler:
        found = _scheduler.get_job("daily_close")
        if found and found.next_run_time:
            job = found.next_run_time.isoformat()
    runner = job_runner()
    enabled = scheduler_enabled()
    return {
        "enabled": enabled,
        "running": bool(_scheduler and _scheduler.running),
        "runner": runner,
        "timezone": "Asia/Seoul",
        "cron": cron_expr(),
        "next_run_at": job,
        "pipeline": [
            "market_close",
            "top20",
            "prices",
            "ma3",
            "signals",
            "watchlist",
            "sms",
        ],
        "note": (
            "external: GitHub Actions(.github/workflows/daily-job.yml) 또는 "
            "python scripts/job_worker.py / scripts/run_daily_job.py"
            if runner == "external"
            else "inprocess: FastAPI lifespan APScheduler"
        ),
    }
