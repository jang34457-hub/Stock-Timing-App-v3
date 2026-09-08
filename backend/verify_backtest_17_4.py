"""STEP 17-4: same-date TOP20 excess 검증 및 실행."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from backtest import first_trading_day_in_week, future_returns
from backtest_17_4 import (
    SIGNALS_17_3,
    attach_context,
    date_aggregated,
    excess_return,
    is_excess_win,
    load_signals_17_3,
    pre_return,
    run_analysis_17_4,
    same_date_top20_baseline,
    signal_key,
)
from backtest_17_3 import RESULT_CSV as CSV_17_3


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


def _px(code: str, days: list[date], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock_code": [code] * len(days),
            "date": days,
            "close": closes,
            "volume": [1] * len(days),
            "trading_value": [1.0] * len(days),
        }
    )


def test_a_same_date_top20() -> None:
    days = _dates(12)
    prices = {
        "000001": _px("000001", days, [100 + i for i in range(12)]),
        "000002": _px("000002", days, [200 + 2 * i for i in range(12)]),
        "000003": _px("000003", days, [50] * 12),
    }
    weekly = {first_trading_day_in_week(d, days): ["000001", "000002"] for d in days}
    d0 = days[2]
    base = same_date_top20_baseline(d0, weekly, days, prices)
    a = future_returns(days, dict(zip(days, [100.0 + i for i in range(12)])), d0)["return_5d"]
    b = future_returns(days, dict(zip(days, [200.0 + 2 * i for i in range(12)])), d0)["return_5d"]
    assert base["avg_5d"] == (a + b) / 2
    assert base["n_stocks"] == 2
    _ok("A same-date TOP20 baseline", f"avg5={base['avg_5d']}")


def test_b_baseline_forward() -> None:
    days = _dates(15)
    closes = [10.0 * (i + 1) for i in range(15)]
    got = future_returns(days, dict(zip(days, closes)), days[1])
    ref = closes[2]
    assert got["next_close"] == ref
    assert abs(got["return_5d"] - (closes[7] / ref - 1) * 100) < 1e-9
    _ok("B baseline forward return", str(got["return_5d"]))


def test_c_excess() -> None:
    assert excess_return(-12.0, -5.0) == -7.0
    assert excess_return(3.0, 1.0) == 2.0
    assert excess_return(None, 1.0) is None
    assert excess_return(1.0, None) is None
    _ok("C excess return", "-12 - (-5) = -7")


def test_d_excess_win() -> None:
    assert is_excess_win("buy", 0.1) is True
    assert is_excess_win("buy", 0.0) is False
    assert is_excess_win("buy", -0.1) is False
    assert is_excess_win("sell", -0.1) is True
    assert is_excess_win("sell", 0.0) is False
    assert is_excess_win("sell", 0.1) is False
    _ok("D BUY/SELL excess win", "BUY>0 / SELL<0")


def test_e_pre_return() -> None:
    days = _dates(12)
    closes = [100.0 + i for i in range(12)]
    cmap = dict(zip(days, closes))
    d = days[7]
    pre5 = pre_return(days, cmap, d, 5)
    assert abs(pre5 - (closes[7] / closes[2] - 1) * 100) < 1e-9
    assert pre_return(days, cmap, days[3], 5) is None
    _ok("E pre-return", f"pre5={pre5}")


def test_f_date_aggregation() -> None:
    d1, d2 = date(2026, 6, 8), date(2026, 6, 9)
    rows = [
        {"signal_date": d1, "signal_type": "buy", "excess_5d": 2.0},
        {"signal_date": d1, "signal_type": "buy", "excess_5d": 4.0},
        {"signal_date": d2, "signal_type": "buy", "excess_5d": 10.0},
    ]
    got = date_aggregated("buy", rows, "excess_5d")
    assert abs(got["event_avg"] - (16.0 / 3)) < 1e-9
    assert abs(got["date_avg"] - 6.5) < 1e-9
    assert got["date_n"] == 2 and got["event_n"] == 3
    _ok("F same-date aggregation", f"event={got['event_avg']} date={got['date_avg']}")


def test_g_signal_count() -> None:
    rows = load_signals_17_3()
    assert CSV_17_3 == SIGNALS_17_3
    assert len(rows) == 116
    keys = [signal_key(r) for r in rows]
    assert len(keys) == len(set(keys))
    _ok("G 17-3 signal count", str(len(rows)))


def test_h_look_ahead() -> None:
    days = _dates(16)
    prices = {
        "000001": _px("000001", days, [100 + i for i in range(16)]),
        "000002": _px("000002", days, [100] * 16),
    }
    weekly = {first_trading_day_in_week(d, days): ["000001", "000002"] for d in days}
    sig = {
        "signal_date": days[8],
        "stock_code": "000001",
        "signal_type": "buy",
        "signal_level": 1,
        "return_5d": 1.0,
        "return_10d": 2.0,
        "return_20d": None,
    }
    before = attach_context([sig], prices, weekly, days)[0]
    mutated = {
        "000001": prices["000001"].copy(),
        "000002": prices["000002"].copy(),
    }
    mutated["000001"].loc[mutated["000001"]["date"] > days[8], "close"] = 1.0
    after = attach_context([sig], mutated, weekly, days)[0]
    assert after["pre_5d_return"] == before["pre_5d_return"]
    assert after["pre_10d_return"] == before["pre_10d_return"]
    assert after["baseline_5d"] != before["baseline_5d"]
    _ok("H look-ahead", "pre/universe 불변, 전방 baseline만 미래 가격 사용")


def test_i_fixture() -> None:
    days = _dates(16)
    prices = {
        "000001": _px("000001", days, [100 + i for i in range(16)]),
        "000002": _px("000002", days, [80 + i for i in range(16)]),
    }
    weekly = {first_trading_day_in_week(d, days): ["000001", "000002"] for d in days}
    sigs = [
        {
            "signal_date": days[8],
            "stock_code": "000001",
            "signal_type": "sell",
            "signal_level": 1,
            "return_5d": -3.0,
            "return_10d": -6.0,
            "return_20d": None,
        }
    ]
    rows = attach_context(sigs, prices, weekly, days)
    assert len(rows) == 1
    assert rows[0]["excess_5d"] is not None
    assert rows[0]["pre_5d_return"] is not None
    _ok("I fixture 전체", f"excess5={rows[0]['excess_5d']}")


def main() -> None:
    test_a_same_date_top20()
    test_b_baseline_forward()
    test_c_excess()
    test_d_excess_win()
    test_e_pre_return()
    test_f_date_aggregation()
    test_g_signal_count()
    test_h_look_ahead()
    test_i_fixture()
    print("verify_backtest_17_4 단위 검증을 통과했습니다.")
    print()
    run_analysis_17_4(save=True)


if __name__ == "__main__":
    main()
