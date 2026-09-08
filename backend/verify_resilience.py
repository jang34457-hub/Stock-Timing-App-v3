"""STEP 17: 잡 부분 실패, 결측 구간 API, listing 날짜 캐시."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ["SMS_PROVIDER"] = "test"
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["APP_ENV"] = "development"
os.environ.pop("API_SECRET_KEY", None)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

import prices_db

TEST_ROOT = Path(tempfile.mkdtemp(prefix="sta-resilience-"))
prices_db.DB_PATH = TEST_ROOT / "prices.db"

import incremental
import jobs
import notify
from incremental import (
    fetch_missing_prices,
    missing_date_ranges,
    save_prices,
)
from jobs import run_daily_job
from prices_db import load_prices, save_top20, upsert_prices, upsert_stock

jobs.LOG_PATH = TEST_ROOT / "job_log.jsonl"

MONDAY = date(2026, 9, 7)
CAL = [date(2026, 9, d) for d in range(1, 8)]


def _ok(label: str) -> None:
    print(f"[PASS] {label}")


def _fake_top20(*codes: str, session: date = MONDAY) -> pd.DataFrame:
    rows = []
    for i, code in enumerate(codes, start=1):
        rows.append(
            {
                "rank": i,
                "selection_date": session,
                "window_start": date(2026, 6, 7),
                "trading_days": 63,
                "stock_code": code,
                "stock_name": code,
                "market": "KOSPI",
                "avg_trading_value": 1.0,
                "total_trading_value": 1.0,
                "days_present": 63,
            }
        )
    return pd.DataFrame(rows)


def _seed_days(code: str, days: list[date], close: float = 10.0) -> None:
    upsert_stock(code, code, "KOSPI")
    upsert_prices(
        pd.DataFrame(
            {
                "stock_code": code,
                "date": days,
                "close": [close] * len(days),
                "volume": [1] * len(days),
                "trading_value": [100.0] * len(days),
            }
        )
    )


def _job_env(session: date, codes: list[str]):
    snap = {
        "selection_date": session.isoformat(),
        "items": [{"stock_code": c, "rank": i} for i, c in enumerate(codes, start=1)],
    }
    return (
        patch("jobs.is_trading_day", return_value=True),
        patch("jobs.latest_trading_day", return_value=session),
        patch("jobs.is_first_trading_day_of_week", return_value=False),
        patch("jobs.first_trading_day_of_week", return_value=session),
        patch("jobs.load_latest_top20", return_value=snap),
        patch("jobs.select_top20", return_value=_fake_top20(*codes, session=session)),
    )


def test_partial_prices() -> None:
    for table in ("daily_prices", "stocks", "top20_history", "signal_history", "user_watchlist", "user_settings"):
        with prices_db.connect() as conn:
            conn.execute(f"DELETE FROM {table}")
    codes = ["000001", "000002", "000003"]
    for code in codes:
        _seed_days(code, [MONDAY], 10)

    def fetch(code, as_of=None, **kwargs):
        if str(code).zfill(6) == "000002":
            raise RuntimeError("price API down")
        return pd.DataFrame(
            {
                "stock_code": str(code).zfill(6),
                "date": [MONDAY],
                "close": [11.0],
                "volume": [1],
                "trading_value": [110.0],
            }
        )

    env = _job_env(MONDAY, codes)
    with ExitStack() as stack:
        for cm in env:
            stack.enter_context(cm)
        stack.enter_context(patch("jobs.trading_days", return_value=[MONDAY]))
        stack.enter_context(patch("jobs.subtract_months", return_value=MONDAY))
        stack.enter_context(patch("jobs.get_missing_dates", return_value=[MONDAY]))
        stack.enter_context(patch("jobs.fetch_missing_prices", side_effect=fetch))
        stack.enter_context(patch("jobs.save_prices", return_value=1))
        stack.enter_context(patch("jobs.update_ma3", return_value=1))
        stack.enter_context(patch("jobs.list_user_ids", return_value=["local"]))
        stack.enter_context(
            patch("jobs.get_settings", return_value={"x1": 10, "x2": 20, "y1": 10, "y2": 20})
        )
        stack.enter_context(patch("jobs.signals_for_sms", return_value=[]))
        result = run_daily_job(MONDAY, force=True)

    items = {row["stock_code"]: row["status"] for row in result["prices"]["items"]}
    assert items["000001"] == "success"
    assert items["000002"] == "error"
    assert items["000003"] == "success"
    assert result["status"] == "partial"
    assert result["prices_status"] == "partial"
    assert result["error_count"] >= 1
    err = next(e for e in result["errors"] if e.get("stock_code") == "000002")
    assert err["stage"] == "price_update"
    assert err["error_type"] == "RuntimeError"
    log = jobs.LOG_PATH.read_text(encoding="utf-8").strip().splitlines()[-1]
    logged = json.loads(log)
    assert logged["status"] == "partial"
    assert any(e.get("stock_code") == "000002" for e in logged["errors"])
    _ok("부분 실패 처리")
    _ok("실패 로그")


def test_top20_fatal() -> None:
    jobs.LOG_PATH.write_text("", encoding="utf-8")
    with (
        patch("jobs.is_trading_day", return_value=True),
        patch("jobs.latest_trading_day", return_value=MONDAY),
        patch("jobs.is_first_trading_day_of_week", return_value=True),
        patch("jobs.first_trading_day_of_week", return_value=MONDAY),
        patch("jobs.load_latest_top20", return_value=None),
        patch("jobs.select_top20", side_effect=RuntimeError("top20 failed")),
        patch("jobs._update_prices") as prices,
        patch("jobs._scan_signals") as signals,
    ):
        result = run_daily_job(MONDAY, force=True)
    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["top20"] == "error"
    assert result["prices"] == "skipped"
    assert result["signals"] == "skipped"
    prices.assert_not_called()
    signals.assert_not_called()
    logged = json.loads(jobs.LOG_PATH.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert logged["errors"][0]["stage"] == "top20"
    _ok("TOP20 치명적 오류")


def test_gap_ranges() -> None:
    cal = CAL
    assert missing_date_ranges(
        [date(2026, 9, 4), date(2026, 9, 6)], cal
    ) == [(date(2026, 9, 4), date(2026, 9, 4)), (date(2026, 9, 6), date(2026, 9, 6))]
    assert missing_date_ranges(
        [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 5)], cal
    ) == [(date(2026, 9, 3), date(2026, 9, 5))]
    fri = date(2026, 9, 4)
    mon = date(2026, 9, 7)
    weekdays = [date(2026, 9, 3), fri, mon, date(2026, 9, 8)]
    assert missing_date_ranges([fri, mon], weekdays) == [(fri, mon)]
    _ok("거래일 연속 구간 계산")


def test_fetch_ranges_and_protect() -> None:
    incremental.clear_listing_cache()
    code = "099901"
    with prices_db.connect() as conn:
        conn.execute("DELETE FROM daily_prices")
        conn.execute("DELETE FROM stocks")
    stored = [date(2026, 9, d) for d in (1, 2, 3, 5, 7)]
    _seed_days(code, stored, close=10.0)
    before = {row.date: (row.close, row.trading_value) for row in load_prices(code).itertuples()}
    requests: list[tuple[str, str]] = []

    def fake_prices(stock_code, start, end):
        requests.append((start, end))
        cur = date.fromisoformat(start)
        last = date.fromisoformat(end)
        rows = []
        while cur <= last:
            rows.append({"date": cur, "open": 1, "high": 1, "low": 1, "close": 99.0, "volume": 1})
            cur += timedelta(days=1)
        return pd.DataFrame(rows)

    with (
        patch("incremental.latest_trading_day", return_value=CAL[-1]),
        patch("incremental.subtract_months", return_value=CAL[0]),
        patch("incremental.trading_days", return_value=CAL),
        patch("incremental.fetch_prices", side_effect=fake_prices),
        patch("incremental.fetch_listing_on", side_effect=RuntimeError("no listing")),
    ):
        fetched = fetch_missing_prices(code)
        save_prices()

    assert requests == [("2026-09-04", "2026-09-04"), ("2026-09-06", "2026-09-06")]
    after = load_prices(code)
    for day, pair in before.items():
        row = after[after["date"] == day].iloc[0]
        assert (row["close"], row["trading_value"]) == pair
    assert set(fetched["date"]) == {date(2026, 9, 4), date(2026, 9, 6)}
    _ok("비연속 결측")
    _ok("기존 데이터 보호")

    requests.clear()
    with prices_db.connect() as conn:
        conn.execute("DELETE FROM daily_prices")
        conn.execute("DELETE FROM stocks")
    window = CAL[:6]
    _seed_days(code, [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 6)], 10)
    with (
        patch("incremental.latest_trading_day", return_value=window[-1]),
        patch("incremental.subtract_months", return_value=window[0]),
        patch("incremental.trading_days", return_value=window),
        patch("incremental.fetch_prices", side_effect=fake_prices),
        patch("incremental.fetch_listing_on", side_effect=RuntimeError("no listing")),
    ):
        fetch_missing_prices(code)
    assert requests == [("2026-09-03", "2026-09-05")]
    _ok("연속 결측")


def test_listing_once_per_date() -> None:
    incremental.clear_listing_cache()
    calls: list[date] = []

    def fake_listing(day):
        calls.append(day)
        return pd.DataFrame(
            [
                {
                    "stock_code": "000010",
                    "stock_name": "A",
                    "market": "KOSPI",
                    "trading_value": 7.0,
                },
                {
                    "stock_code": "000011",
                    "stock_name": "B",
                    "market": "KOSPI",
                    "trading_value": 8.0,
                },
            ]
        )

    def fake_prices(stock_code, start, end):
        return pd.DataFrame(
            [
                {"date": date(2026, 9, 1), "open": 1, "high": 1, "low": 1, "close": 10, "volume": 2},
                {"date": date(2026, 9, 2), "open": 1, "high": 1, "low": 1, "close": 11, "volume": 2},
            ]
        )

    cal = [date(2026, 9, 1), date(2026, 9, 2)]
    for code in ("000010", "000011"):
        with prices_db.connect() as conn:
            conn.execute("DELETE FROM daily_prices WHERE stock_code=?", (code,))
        with (
            patch("incremental.latest_trading_day", return_value=cal[-1]),
            patch("incremental.subtract_months", return_value=cal[0]),
            patch("incremental.trading_days", return_value=cal),
            patch("incremental.fetch_prices", side_effect=fake_prices),
            patch("incremental.fetch_listing_on", side_effect=fake_listing),
        ):
            rows = fetch_missing_prices(code)
            assert list(rows["trading_value"]) == ([7.0, 7.0] if code == "000010" else [8.0, 8.0])

    assert calls.count(date(2026, 9, 1)) == 1
    assert calls.count(date(2026, 9, 2)) == 1
    _ok("listing 날짜별 1회")


def test_sms_continues() -> None:
    notify.take_sms_errors()
    upsert_stock("001111", "A", "KOSPI")
    upsert_stock("002222", "B", "KOSPI")
    from prices_db import add_watchlist, save_settings

    save_settings("local", {"phone_number": "01011112222"})
    add_watchlist("local", "001111", True)
    add_watchlist("local", "002222", True)
    n = {"i": 0}

    def flaky(to, text, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("sms fail")
        return {"status": "test", "to": to, "text": text, "provider": "test"}

    sigs = [
        {"stock_code": "001111", "signal_type": "buy", "signal_level": 1, "ma3": 1},
        {"stock_code": "002222", "signal_type": "buy", "signal_level": 1, "ma3": 1},
    ]
    with patch("notify.send_sms", side_effect=flaky):
        sent = notify.notify_signals("local", sigs)
    errs = notify.take_sms_errors()
    assert len(sent) == 1
    assert len(errs) == 1
    assert errs[0]["stage"] == "sms"
    _ok("SMS 실패 후 다음 종목")


def test_insert_or_ignore_prices() -> None:
    from prices_db import upsert_prices as insert_prices

    code = "088888"
    with prices_db.connect() as conn:
        conn.execute("DELETE FROM daily_prices WHERE stock_code=?", (code,))
        conn.execute("DELETE FROM stocks WHERE stock_code=?", (code,))
    upsert_stock(code, "보호", "KOSPI")
    insert_prices(
        pd.DataFrame(
            {
                "stock_code": [code, code],
                "date": [date(2026, 9, 1), date(2026, 9, 2)],
                "close": [100.0, 200.0],
                "volume": [1, 1],
                "trading_value": [10.0, 20.0],
            }
        )
    )
    added = insert_prices(
        pd.DataFrame(
            {
                "stock_code": [code, code, code],
                "date": [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)],
                "close": [999.0, 888.0, 300.0],
                "volume": [9, 9, 9],
                "trading_value": [1.0, 1.0, 30.0],
            }
        )
    )
    assert added == 1
    prices = load_prices(code).sort_values("date")
    by_date = {row.date: float(row.close) for row in prices.itertuples()}
    assert by_date[date(2026, 9, 1)] == 100.0
    assert by_date[date(2026, 9, 2)] == 200.0
    assert by_date[date(2026, 9, 3)] == 300.0
    with prices_db.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE stock_code=?", (code,)
        ).fetchone()[0]
    assert n == 3
    _ok("기존 가격 보호 / 신규 저장 / 중복 방지")


def test_trading_days_once_for_many_stocks() -> None:
    n = {"c": 0}
    cal = [date(2026, 9, 1), date(2026, 9, 2)]

    def td(start, end):
        n["c"] += 1
        return cal

    codes = ["000001", "000002", "000003"]
    with prices_db.connect() as conn:
        for code in codes:
            conn.execute("DELETE FROM daily_prices WHERE stock_code=?", (code,))
    with (
        patch("jobs.trading_days", side_effect=td),
        patch("jobs.subtract_months", return_value=cal[0]),
        patch(
            "incremental.trading_days",
            side_effect=AssertionError("trading_days should be reused"),
        ),
        patch(
            "incremental.fetch_prices",
            return_value=pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]),
        ),
        patch("jobs.update_ma3", return_value=0),
    ):
        from jobs import _update_prices

        _update_prices(codes, date(2026, 9, 2), [])
    assert n["c"] == 1
    _ok("trading_days 종목 공통 1회")


def main() -> None:
    test_partial_prices()
    test_top20_fatal()
    test_gap_ranges()
    test_fetch_ranges_and_protect()
    test_listing_once_per_date()
    test_sms_continues()
    test_insert_or_ignore_prices()
    test_trading_days_once_for_many_stocks()
    print("resilience 검증을 통과했습니다.")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print(f"테스트 DB 삭제: {TEST_ROOT}")
