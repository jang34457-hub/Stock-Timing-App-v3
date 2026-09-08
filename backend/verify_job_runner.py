"""STEP 18-2: job runs without FastAPI / mobile app."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))


def _ok(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    extra = f" ({detail})" if detail else ""
    print(f"  [{status}] {name}{extra}")
    if not cond:
        raise SystemExit(1)


def _with_scheduler(env: dict[str, str], check):
    keys = ("APP_ENV", "JOB_RUNNER", "SCHEDULER_ENABLED")
    previous = {k: os.environ.get(k) for k in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        os.environ.update(env)
        import scheduler

        mod = importlib.reload(scheduler)
        return check(mod)
    finally:
        for key, val in previous.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        import scheduler as sched_mod

        importlib.reload(sched_mod)


def main() -> None:
    print("STEP 18-2 verify_job_runner")
    workflow = REPO / ".github" / "workflows" / "daily-job.yml"
    text = workflow.read_text(encoding="utf-8")
    _ok("GitHub Actions workflow exists", workflow.is_file())
    _ok("weekday 16:00 KST cron (UTC 07:00)", 'cron: "0 7 * * 1-5"' in text)
    _ok("workflow runs scripts/run_daily_job.py", "scripts/run_daily_job.py" in text)
    _ok("workflow uses production + DATABASE_URL", "APP_ENV: production" in text and "DATABASE_URL" in text)

    run_script = REPO / "scripts" / "run_daily_job.py"
    worker = REPO / "scripts" / "job_worker.py"
    _ok("CLI runner exists", run_script.is_file())
    _ok("dedicated worker exists", worker.is_file())
    _ok("CLI does not import FastAPI app", "from app import" not in run_script.read_text(encoding="utf-8"))

    help_out = subprocess.run(
        [sys.executable, str(run_script), "--help"],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    _ok("CLI --help", "--force" in help_out.stdout and "--as-of" in help_out.stdout)

    def prod_external(mod) -> None:
        _ok("production default runner is external", mod.job_runner() == "external")
        _ok("production FastAPI does not start APScheduler", mod.scheduler_enabled() is False)

    _with_scheduler({"APP_ENV": "production"}, prod_external)

    def dev_inprocess(mod) -> None:
        _ok("development default runner is inprocess", mod.job_runner() == "inprocess")
        _ok("development can enable in-process scheduler", mod.scheduler_enabled() is True)

    _with_scheduler({"APP_ENV": "development"}, dev_inprocess)

    def ext(mod) -> None:
        _ok("JOB_RUNNER=external disables in-process", mod.scheduler_enabled() is False)

    _with_scheduler({"APP_ENV": "development", "JOB_RUNNER": "external"}, ext)

    def worker_mode(mod) -> None:
        _ok(
            "explicit inprocess still allowed on a dedicated worker",
            mod.job_runner() == "inprocess" and mod.scheduler_enabled() is True,
        )
        status = mod.scheduler_status()
        _ok(
            "status lists full pipeline",
            status["pipeline"]
            == [
                "market_close",
                "top20",
                "prices",
                "ma3",
                "signals",
                "watchlist",
                "sms",
            ],
        )
        _ok("cron is weekdays", status["cron"].endswith("* * 1-5"))

    _with_scheduler(
        {"APP_ENV": "production", "JOB_RUNNER": "inprocess", "SCHEDULER_ENABLED": "1"},
        worker_mode,
    )
    print("All STEP 18-2 checks passed.")


if __name__ == "__main__":
    main()
