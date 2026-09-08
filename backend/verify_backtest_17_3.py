"""STEP 17-3: backtest_prices.db 신호 성과 검증 및 실행."""

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
    detect_signals_asof,
    first_trading_day_in_week,
    future_returns,
    prices_with_ma3,
    rolling_high_low,
    run_signal_backtest,
)
from backtest_17_3 import (
    by_signal_level,
    inspect_and_load,
    is_win,
    run_backtest_17_3,
    sample_note,
)
from ma3 import ma3_from_closes
from market_data import subtract_months
from prices_db import save_ma3, upsert_prices, upsert_stock
from prepare_backtest_data import unique_top20_codes
from signals import _cross_down, _cross_up, detect_signal

PROD_PRICE_DB = prices_db.DB_PATH
TEST_ROOT = Path(tempfile.mkdtemp(prefix="sta-bt-17-3-"))


def _ok(label: str, detail: str = "") -> None:
    print(f"[PASS] {label}" + (f": {detail}" if detail else ""))


def _dates(n: int, start: date = date(2026, 6, 1)) -> list[date]:
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


def test_a_load_historical_db() -> None:
    loaded = inspect_and_load()
    cov = loaded["coverage"]
    assert cov["stock_count"] == len(cov["stocks"])
    assert cov["price_rows"] == sum(s["rows"] for s in cov["stocks"])
    assert cov["stock_count"] > 1
    assert cov["data_start"] and cov["data_end"]
    prices = loaded["prices"]
    assert len(prices) == cov["stock_count"]
    _ok(
        "A historical data loading",
        f"stocks={cov['stock_count']} rows={cov['price_rows']} {cov['data_start']}~{cov['data_end']}",
    )


def test_b_historical_top20() -> None:
    weekly = {
        date(2026, 6, 8): ["000001", "000002"],
        date(2026, 6, 15): ["000002", "000003"],
    }
    assert unique_top20_codes(weekly) == ["000001", "000002", "000003"]
    cal = [date(2026, 6, d) for d in (8, 9, 10, 15, 16)]
    assert first_trading_day_in_week(date(2026, 6, 10), cal) == date(2026, 6, 8)
    assert first_trading_day_in_week(date(2026, 6, 16), cal) == date(2026, 6, 15)
    _ok("B historical TOP20 적용", "주 첫 거래일 유지 + unique union")


def test_c_ma3() -> None:
    got = ma3_from_closes([10000, 11000, 12000, 9000, 15000])
    assert got == [None, None, 11000.0, 10666.67, 12000.0]
    _ok("C MA3", str(got))


def test_d_rolling_hl() -> None:
    days = _dates(8)
    prices = _prices("HL0001", days, [10, 11, 12, 9, 15, 14, 8, 20])
    as_of = days[4]
    hl = rolling_high_low(prices, as_of)
    assert hl is not None
    start = subtract_months(as_of, 3)
    window = prices_with_ma3(prices[prices["date"] <= as_of]).dropna(subset=["ma3"])
    window = window[(window["date"] >= start) & (window["date"] <= as_of)]
    assert hl["high"] == float(window["ma3"].max())
    assert hl["low"] == float(window["ma3"].min())
    assert hl["window_end"] <= as_of
    _ok("D rolling 3-month H/L", f"H={hl['high']} L={hl['low']}")


def test_e_look_ahead() -> None:
    days = _dates(12)
    closes = [100, 101, 102, 100, 90, 89, 88, 87, 86, 85, 84, 83]
    prices = _prices("LA0001", days, closes)
    as_of = days[6]
    before = detect_signals_asof(prices, as_of, stock_code="LA0001")
    hl_b = rolling_high_low(prices, as_of)
    mutated = prices.copy()
    mutated.loc[mutated["date"] >= days[7], "close"] = 1.0
    after = detect_signals_asof(mutated, as_of, stock_code="LA0001")
    hl_a = rolling_high_low(mutated, as_of)
    assert before == after
    assert hl_b == hl_a
    _ok("E look-ahead bias", f"as_of={as_of} unchanged")


def test_f_cross_production() -> None:
    assert _cross_down(90.5, 89.0, 90.0)
    assert not _cross_down(89.0, 88.0, 90.0)
    assert _cross_up(54.0, 56.0, 55.0)
    assert not _cross_up(56.0, 57.0, 55.0)
    prices_db.DB_PATH = TEST_ROOT / "prices.db"
    code = "CR0001"
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
    for p, loc in zip(
        sorted(prod, key=lambda s: (s["signal_type"], s["signal_level"])),
        sorted(local, key=lambda s: (s["signal_type"], s["signal_level"])),
    ):
        assert abs(float(p["threshold"]) - float(loc["threshold"])) < 1e-9
        assert abs(float(p["ma3"]) - float(loc["ma3"])) < 1e-9
        assert int(p["signal_level"]) == int(loc["signal_level"])
    prices_db.DB_PATH = PROD_PRICE_DB
    _ok("F cross/threshold/level vs production", str(sorted(prod_keys)))


def test_g_next_day_ref() -> None:
    days = _dates(8)
    closes = [100.0 + i for i in range(8)]
    got = future_returns(days, dict(zip(days, closes)), days[2])
    assert got["next_close"] == closes[3]
    _ok("G next-day reference", f"P1={got['next_close']}")


def test_h_horizon_returns() -> None:
    days = _dates(30)
    closes = [100.0 + i for i in range(30)]
    cmap = dict(zip(days, closes))
    sig = days[3]
    got = future_returns(days, cmap, sig)
    ref = closes[4]
    assert abs(got["return_5d"] - (closes[9] / ref - 1) * 100) < 1e-9
    assert abs(got["return_10d"] - (closes[14] / ref - 1) * 100) < 1e-9
    assert abs(got["return_20d"] - (closes[24] / ref - 1) * 100) < 1e-9
    short = future_returns(days[:6], dict(zip(days[:6], closes[:6])), days[3])
    assert short["return_5d"] is None
    _ok("H 5D/10D/20D", f"5d={got['return_5d']:.4f}")


def test_i_win_definition() -> None:
    assert is_win("buy", 0.01) is True
    assert is_win("buy", 0.0) is False
    assert is_win("buy", -1.0) is False
    assert is_win("sell", -0.01) is True
    assert is_win("sell", 0.0) is False
    assert is_win("sell", 1.0) is False
    stats = by_signal_level(
        [
            {"signal_type": "buy", "signal_level": 1, "return_5d": 1.0},
            {"signal_type": "buy", "signal_level": 1, "return_5d": -1.0},
        ]
    )
    assert stats["BUY"]["LEVEL 1"]["5d"]["win_rate"] == 50.0
    assert sample_note(5) == "LOW SAMPLE"
    assert sample_note(15) == "LIMITED SAMPLE"
    assert sample_note(30) == "OK"
    _ok("I BUY/SELL win definition", "tie counts as LOSS")


def test_j_fixture_backtest() -> None:
    days = _dates(40)
    closes = [100.0] * 10 + [120.0] * 10 + [80.0] * 10 + [90.0] * 10
    code = "FX0001"
    prices = _prices(code, days, closes)
    weekly = {}
    for day in days:
        weekly[first_trading_day_in_week(day, days)] = [code]
    result = run_signal_backtest({code: prices}, days, weekly, start=days[5], end=days[-1])
    assert result["quality"]["total_signals"] >= 1
    _ok("J fixture 전체 실행", f"signals={result['quality']['total_signals']}")


def main() -> None:
    test_a_load_historical_db()
    test_b_historical_top20()
    test_c_ma3()
    test_d_rolling_hl()
    test_e_look_ahead()
    test_f_cross_production()
    test_g_next_day_ref()
    test_h_horizon_returns()
    test_i_win_definition()
    test_j_fixture_backtest()
    print("verify_backtest_17_3 단위 검증을 통과했습니다.")
    prices_db.DB_PATH = PROD_PRICE_DB
    print()
    run_backtest_17_3(save=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        prices_db.DB_PATH = PROD_PRICE_DB
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print(f"테스트 DB 삭제: {TEST_ROOT}")
