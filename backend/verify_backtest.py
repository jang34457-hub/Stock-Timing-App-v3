"""STEP 17-1: 신호 성과 백테스트 검증."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ["SMS_PROVIDER"] = "test"
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["APP_ENV"] = "development"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

import prices_db
from backtest import (
    attach_returns,
    detect_signals_asof,
    first_trading_day_in_week,
    frame_as_of,
    future_returns,
    prices_with_ma3,
    rank_top20_from_history,
    rolling_high_low,
    run_backtest_from_db,
    run_signal_backtest,
    summarize_signals,
)
from ma3 import ma3_from_closes
from market_data import subtract_months
from prices_db import save_ma3, upsert_prices, upsert_stock
from signals import _cross_down, _cross_up, detect_signal

PROD_DB = prices_db.DB_PATH
TEST_ROOT = Path(tempfile.mkdtemp(prefix="sta-backtest-"))


def _ok(label: str, detail: str = "") -> None:
    suffix = f": {detail}" if detail else ""
    print(f"[PASS] {label}{suffix}")


def _dates(n: int, start: date = date(2026, 6, 1)) -> list[date]:
    """평일만 n개. 3개월 window 안에 들어가도록 6월부터."""
    out: list[date] = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _prices(code: str, days: list[date], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock_code": [code] * len(days),
            "date": days,
            "close": closes,
            "volume": [1] * len(days),
            "trading_value": [1.0] * len(days),
        }
    )


def test_ma3() -> None:
    got = ma3_from_closes([10000, 11000, 12000, 9000, 15000])
    assert got == [None, None, 11000.0, 10666.67, 12000.0]
    days = _dates(5)
    frame = prices_with_ma3(_prices("BT0001", days, [10000, 11000, 12000, 9000, 15000]))
    series = [None if pd.isna(v) else float(v) for v in frame["ma3"]]
    assert series == got
    _ok("MA3", str(got))


def test_rolling_high_low() -> None:
    days = _dates(8)
    closes = [10.0, 11.0, 12.0, 9.0, 15.0, 14.0, 8.0, 20.0]
    prices = _prices("BT0002", days, closes)
    as_of = days[4]
    hl = rolling_high_low(prices, as_of)
    assert hl is not None
    window = frame_as_of(prices, as_of).dropna(subset=["ma3"])
    start = subtract_months(as_of, 3)
    window = window[(window["date"] >= start) & (window["date"] <= as_of)]
    assert hl["high"] == float(window["ma3"].max())
    assert hl["low"] == float(window["ma3"].min())
    assert hl["window_end"] <= as_of
    future = prices.copy()
    future.loc[future["date"] == days[-1], "close"] = 9999.0
    hl2 = rolling_high_low(future, as_of)
    assert hl2 == hl
    _ok("Rolling High/Low", f"H={hl['high']} L={hl['low']} as_of={as_of}")


def test_cross_matches_production() -> None:
    assert _cross_down(90.5, 89.0, 90.0) is True
    assert _cross_down(89.0, 88.0, 90.0) is False
    assert _cross_up(54.0, 56.0, 55.0) is True
    assert _cross_up(56.0, 57.0, 55.0) is False

    prices_db.DB_PATH = TEST_ROOT / "prices.db"
    code = "BT0003"
    days = _dates(10, date(2026, 8, 3))
    closes = [100.0] * 8 + [85.0, 80.0]
    prices = _prices(code, days, closes)
    frame = prices_with_ma3(prices)
    upsert_stock(code, "cross", "KOSPI")
    upsert_prices(prices)
    save_ma3(code, frame[["date", "ma3"]])
    as_of = days[-1]
    prod = detect_signal(code, as_of)
    local = detect_signals_asof(prices, as_of, stock_code=code)
    prod_keys = {(s["signal_type"], s["signal_level"]) for s in prod}
    local_keys = {(s["signal_type"], s["signal_level"]) for s in local}
    assert prod_keys == local_keys
    assert ("sell", 1) in prod_keys
    for p, loc in zip(
        sorted(prod, key=lambda s: (s["signal_type"], s["signal_level"])),
        sorted(local, key=lambda s: (s["signal_type"], s["signal_level"])),
    ):
        assert abs(float(p["threshold"]) - float(loc["threshold"])) < 1e-9
        assert abs(float(p["ma3"]) - float(loc["ma3"])) < 1e-9
    prices_db.DB_PATH = PROD_DB
    _ok("Cross", f"production detect_signal 과 동일 {sorted(prod_keys)}")


def test_future_returns() -> None:
    days = _dates(30)
    closes = [100.0 + i for i in range(30)]
    cmap = dict(zip(days, closes))
    sig = days[3]
    got = future_returns(days, cmap, sig)
    ref = closes[4]
    assert got["next_close"] == ref
    assert abs(got["return_5d"] - (closes[9] / ref - 1) * 100) < 1e-9
    assert abs(got["return_10d"] - (closes[14] / ref - 1) * 100) < 1e-9
    assert abs(got["return_20d"] - (closes[24] / ref - 1) * 100) < 1e-9
    short = future_returns(days[:6], dict(zip(days[:6], closes[:6])), days[3])
    assert short["next_close"] == closes[4]
    assert short["return_5d"] is None
    prices = _prices("BT0004", days, closes)
    row = attach_returns(
        {"stock_code": "BT0004", "signal_date": sig, "signal_type": "buy", "signal_level": 1},
        prices,
    )
    assert row["return_5d"] == got["return_5d"]
    _ok("Future return", f"5d={got['return_5d']:.4f}")


def test_look_ahead() -> None:
    days = _dates(12)
    closes = [100.0, 101.0, 102.0, 100.0, 90.0, 89.0, 88.0, 87.0, 86.0, 85.0, 84.0, 83.0]
    prices = _prices("BT0005", days, closes)
    as_of = days[6]
    before = detect_signals_asof(prices, as_of, stock_code="BT0005")
    hl_before = rolling_high_low(prices, as_of)
    mutated = prices.copy()
    mutated.loc[mutated["date"] >= days[7], "close"] = 1.0
    after = detect_signals_asof(mutated, as_of, stock_code="BT0005")
    hl_after = rolling_high_low(mutated, as_of)
    assert before == after
    assert hl_before == hl_after
    _ok("Look-ahead bias", f"as_of={as_of} signals={len(before)}")


def test_full_backtest_fixture() -> None:
    days = _dates(40)
    closes = [100.0] * 10 + [120.0] * 10 + [80.0] * 10 + [90.0] * 10
    code = "BT0006"
    prices = _prices(code, days, closes)
    weekly = {}
    for day in days:
        first = first_trading_day_in_week(day, days)
        weekly[first] = [code]
    result = run_signal_backtest({code: prices}, days, weekly, start=days[5], end=days[-1])
    assert "summary" in result
    assert "quality" in result
    stats = summarize_signals(result["signals"])
    assert stats["BUY"]["ALL"]["signals"] + stats["SELL"]["ALL"]["signals"] == result["quality"]["total_signals"]
    _ok(
        "전체 백테스트 fixture",
        f"signals={result['quality']['total_signals']} days={result['trading_days_evaluated']}",
    )


def test_top20_rank_formula() -> None:
    rows = []
    for i, day in enumerate(_dates(3, date(2026, 9, 1))):
        rows.append(
            {
                "date": day,
                "stock_code": "000001",
                "stock_name": "A",
                "market": "KOSPI",
                "trading_value": 10.0,
            }
        )
        rows.append(
            {
                "date": day,
                "stock_code": "000002",
                "stock_name": "B",
                "market": "KOSDAQ",
                "trading_value": 30.0,
            }
        )
    history = pd.DataFrame(rows)
    top = rank_top20_from_history(history, end=date(2026, 9, 3), start=date(2026, 6, 3), n=2)
    assert list(top["stock_code"]) == ["000002", "000001"]
    assert top.iloc[0]["avg_trading_value"] == 90.0 / 3
    _ok("TOP20 분모", "unique 거래일 3, B가 1위")


def main() -> None:
    test_ma3()
    test_rolling_high_low()
    test_cross_matches_production()
    test_future_returns()
    test_look_ahead()
    test_full_backtest_fixture()
    test_top20_rank_formula()
    print("verify_backtest 단위 검증을 통과했습니다.")
    prices_db.DB_PATH = PROD_DB
    print()
    run_backtest_from_db(save=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        prices_db.DB_PATH = PROD_DB
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print(f"테스트 DB 삭제: {TEST_ROOT}")
