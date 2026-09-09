"""STEP 15: 장 마감 스케줄 파이프라인."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["CATCHUP_ON_START"] = "0"
os.environ["CATCHUP_ON_READ"] = "0"
os.environ["APP_ENV"] = "development"
os.environ.pop("API_SECRET_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from app import app
from jobs import catch_up_if_stale, data_is_stale, run_daily_job
from top20 import first_trading_day_of_week, is_first_trading_day_of_week

os.environ["APP_ENV"] = "development"
os.environ.pop("API_SECRET_KEY", None)


def _ok(label: str, detail: str) -> None:
    print(f"[OK] {label}: {detail}")


def _fake_top20(as_of=None, n=20, *, progress=None):
    import pandas as pd

    day = as_of or date(2026, 9, 7)
    return pd.DataFrame(
        [
            {
                "rank": 1,
                "selection_date": day,
                "window_start": date(2026, 6, 7),
                "trading_days": 63,
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "market": "KOSPI",
                "avg_trading_value": 1.0,
                "total_trading_value": 63.0,
                "days_present": 63,
            }
        ]
    )


def _snapshot(day: date) -> dict:
    return {
        "selection_date": day.isoformat(),
        "window_start": "2026-06-07",
        "trading_days": 63,
        "items": [
            {"stock_code": "005930" if i == 0 else f"{i:06d}", "rank": i + 1}
            for i in range(20)
        ],
    }


def _run_job(
    session: date,
    *,
    first_session: bool,
    week_start: date,
    cached: dict,
    saved: dict | None = None,
    select_top20=True,
    missing=None,
    sms_batch=None,
):
    load_values = [cached]
    if saved is not None:
        load_values.append(saved)
    select_patch = (
        patch("jobs.select_top20", side_effect=_fake_top20)
        if select_top20
        else patch("jobs.select_top20")
    )
    fetch_return = type("Rows", (), {"__len__": lambda self: 1})() if missing else None
    with (
        patch("jobs.is_trading_day", return_value=True),
        patch("jobs.latest_trading_day", return_value=session),
        patch("jobs.is_first_trading_day_of_week", return_value=first_session),
        patch("jobs.first_trading_day_of_week", return_value=week_start),
        patch("jobs.trading_days", return_value=[session]),
        patch("jobs.subtract_months", return_value=session),
        select_patch as select,
        patch("jobs.save_top20") as save,
        patch("jobs.load_latest_top20", side_effect=load_values, return_value=cached),
        patch("jobs.get_missing_dates", return_value=missing or []),
        patch("jobs.fetch_missing_prices", return_value=fetch_return) as fetch,
        patch("jobs.save_prices", return_value=1 if missing else 0),
        patch("jobs.update_ma3", return_value=10),
        patch("jobs.list_user_ids", return_value=["local"]),
        patch("jobs.get_settings", return_value={"x1": 10, "x2": 20, "y1": 10, "y2": 20}),
        patch("jobs.signals_for_sms", return_value=sms_batch or []),
    ):
        result = run_daily_job(session, force=True)
    return result, select, save, fetch


def main() -> None:
    holiday = date(2026, 9, 6)
    with patch("jobs.is_trading_day", return_value=False):
        skipped = run_daily_job(holiday)
    assert skipped.get("skipped") == "holiday_or_weekend", skipped
    _ok("휴장·주말 생략", skipped["skipped"])

    monday = date(2026, 9, 7)
    tuesday = date(2026, 9, 8)
    prev_week = date(2026, 8, 31)

    with patch("top20.trading_days", return_value=[monday]):
        assert first_trading_day_of_week(monday) == monday
        assert is_first_trading_day_of_week(monday) is True
    with patch("top20.trading_days", return_value=[tuesday]):
        assert first_trading_day_of_week(tuesday) == tuesday
        assert is_first_trading_day_of_week(tuesday) is True
        assert is_first_trading_day_of_week(monday) is False
    _ok("주 첫 거래일 계산", "월요일 / 월요일 휴장 시 화요일")

    first, select, save, _fetch = _run_job(
        monday,
        first_session=True,
        week_start=monday,
        cached=_snapshot(prev_week),
        saved=_snapshot(monday),
        missing=[monday],
        sms_batch=[
            {"stock_code": "005930", "signal_type": "buy", "signal_level": 1, "sms_sent": True}
        ],
    )
    assert first["first_trading_day_of_week"] is True
    assert first["top20"]["action"] == "reselected"
    assert select.called and save.called
    assert first["prices"]["fetched_rows"] == 20
    assert first["signals"]["sms_sent"] == 20
    _ok("월요일 정상 실행 TOP20 재선정", first["top20"]["action"])

    retry, select_retry, save_retry, _ = _run_job(
        tuesday,
        first_session=False,
        week_start=monday,
        cached=_snapshot(prev_week),
        saved=_snapshot(tuesday),
    )
    assert retry["top20"]["action"] == "reselected"
    assert select_retry.called and save_retry.called
    _ok("월요일 실패 → 화요일 재실행", retry["top20"]["action"])

    holiday_monday, select_h, save_h, _ = _run_job(
        tuesday,
        first_session=True,
        week_start=tuesday,
        cached=_snapshot(prev_week),
        saved=_snapshot(tuesday),
    )
    assert holiday_monday["first_trading_day_of_week"] is True
    assert holiday_monday["top20"]["action"] == "reselected"
    assert select_h.called and save_h.called
    _ok("월요일 휴장 → 화요일 정상 선정", holiday_monday["top20"]["action"])

    kept, select2, _save, fetch = _run_job(
        tuesday,
        first_session=False,
        week_start=monday,
        cached=_snapshot(monday),
        select_top20=False,
    )
    assert kept["top20"]["action"] == "kept"
    select2.assert_not_called()
    fetch.assert_not_called()
    _ok("월요일 선정 완료 → 화요일 재선정 없음", kept["top20"]["action"])

    twenty = [{"stock_code": f"{i:06d}"} for i in range(20)]
    with (
        patch(
            "jobs.load_latest_top20",
            return_value={"selection_date": "2026-09-07", "items": twenty},
        ),
        patch("jobs.first_trading_day_of_week", return_value=date(2026, 9, 7)),
        patch("jobs.get_last_date", return_value=date(2026, 9, 8)),
    ):
        assert data_is_stale(date(2026, 9, 9)) is True
        assert data_is_stale(date(2026, 9, 8)) is False
    _ok("시세 날짜가 뒤처지면 stale", "2026-09-08 < 2026-09-09")

    with (
        patch("jobs.latest_trading_day", return_value=date(2026, 9, 9)),
        patch("jobs.data_is_stale", return_value=False),
    ):
        skipped = catch_up_if_stale()
    assert skipped["skipped"] == "up_to_date"
    _ok("이미 최신이면 catch-up 생략", skipped["skipped"])

    with (
        patch("jobs.latest_trading_day", return_value=date(2026, 9, 9)),
        patch("jobs.data_is_stale", return_value=True),
        patch(
            "jobs._run_daily_job",
            return_value={"ok": True, "status": "success", "session": "2026-09-09"},
        ) as delayed,
    ):
        caught = catch_up_if_stale()
    assert delayed.called
    assert caught["job"] == "catch_up"
    _ok("로그인 catch-up이 일일 파이프라인 실행", caught["session"])

    client = TestClient(app)
    status = client.get("/jobs/status")
    assert status.status_code == 200, status.text
    assert status.json()["timezone"] == "Asia/Seoul"
    _ok("GET /jobs/status", str(status.json()["cron"]))

    with patch("app.catch_up_if_stale", return_value={"ok": True, "skipped": "up_to_date"}):
        synced = client.post("/jobs/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json()["skipped"] == "up_to_date"
    _ok("POST /jobs/sync", synced.json()["skipped"])

    with patch("app.run_daily_job", return_value={"ok": True, "skipped": "dry-run"}):
        posted = client.post("/jobs/run")
    assert posted.status_code == 200, posted.text
    assert posted.json()["ok"] is True
    _ok("POST /jobs/run (개발, 키 없음)", posted.json()["skipped"])

    previous = os.environ.get("API_SECRET_KEY")
    os.environ["API_SECRET_KEY"] = "test-secret"
    try:
        denied = client.post("/jobs/run")
        assert denied.status_code == 401, denied.text
        _ok("POST /jobs/run 키 없음 거부", str(denied.status_code))
        allowed = client.post("/jobs/run", headers={"X-API-Key": "test-secret"})
        assert allowed.status_code == 200, allowed.text
        _ok("POST /jobs/run 키 있음", allowed.json().get("skipped", "ok"))
        sms_denied = client.post("/sms/test", json={"phone_number": "01012345678"})
        assert sms_denied.status_code == 401, sms_denied.text
        _ok("POST /sms/test 키 없음 거부", str(sms_denied.status_code))
    finally:
        if previous is None:
            os.environ.pop("API_SECRET_KEY", None)
        else:
            os.environ["API_SECRET_KEY"] = previous


if __name__ == "__main__":
    main()
