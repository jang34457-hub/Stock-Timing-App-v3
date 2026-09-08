"""STEP 17-5: MA3 Timing vs Buy & Hold 검증."""

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

import backtest_db
import prices_db
from backtest import detect_signals_asof, prices_with_ma3, rolling_high_low
from backtest_17_5 import (
    INITIAL,
    apply_buy,
    buy_and_hold,
    max_drawdown,
    next_trading_day,
    run_backtest_17_5,
    select_ten_stocks,
    timing_strategy,
)
from ma3 import ma3_from_closes
from prices_db import save_ma3, upsert_prices, upsert_stock
from signals import _cross_down, _cross_up, detect_signal

PROD_DB = prices_db.DB_PATH
TEST_ROOT = Path(tempfile.mkdtemp(prefix="sta-bt-17-5-"))


def _ok(label: str, detail: str = "") -> None:
    print(f"[PASS] {label}" + (f": {detail}" if detail else ""))


def _dates(n: int, start: date = date(2026, 6, 1)) -> list[date]:
    out = []
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


def test_a_db() -> None:
    cov = backtest_db.inspect_coverage()
    assert cov["stock_count"] > 0 and cov["price_rows"] > 0
    assert cov["data_start"] and cov["data_end"]
    _ok("A DB loading", f"{cov['stock_count']} stocks {cov['data_start']}~{cov['data_end']}")


def test_b_select10() -> None:
    sel = select_ten_stocks()
    assert len(sel["stocks"]) == 10
    codes = [s["stock_code"] for s in sel["stocks"]]
    assert len(set(codes)) == 10
    _ok("B 10개 종목 선정", ",".join(codes))


def test_c_same_dates() -> None:
    sel = select_ten_stocks()
    starts = {s["start"] for s in sel["stocks"]}
    ends = {s["end"] for s in sel["stocks"]}
    assert starts == {sel["start"]}
    assert ends == {sel["end"]}
    _ok("C 동일한 시작/종료일", f"{sel['start']} ~ {sel['end']}")


def test_d_initial() -> None:
    days = _dates(5)
    closes = {d: 1000.0 for d in days}
    bh = buy_and_hold(days, closes, days[0], days[-1])
    assert abs(bh["final"] - INITIAL) < 1e-6
    cash, shares = apply_buy(INITIAL, 2500.0, 0.0)
    assert cash == 0 and abs(shares * 2500 - INITIAL) < 1e-6
    _ok("D 초기 투자금 1,000,000", f"shares={shares}")


def test_e_bh() -> None:
    days = _dates(4)
    closes = {days[0]: 100.0, days[1]: 110.0, days[2]: 90.0, days[3]: 120.0}
    bh = buy_and_hold(days, closes, days[0], days[3])
    assert abs(bh["final"] - 1_200_000) < 1e-4
    assert abs(bh["return_pct"] - 20.0) < 1e-9
    _ok("E Buy & Hold", f"final={bh['final']}")


def test_f_ma3() -> None:
    got = ma3_from_closes([10000, 11000, 12000, 9000, 15000])
    assert got == [None, None, 11000.0, 10666.67, 12000.0]
    _ok("F MA3 vs production", str(got))


def test_g_hl() -> None:
    days = _dates(8)
    prices = _px("HL17", days, [10, 11, 12, 9, 15, 14, 8, 20])
    hl = rolling_high_low(prices, days[4])
    window = prices_with_ma3(prices[prices["date"] <= days[4]]).dropna(subset=["ma3"])
    assert hl["high"] == float(window["ma3"].max())
    assert hl["low"] == float(window["ma3"].min())
    _ok("G H/L", f"H={hl['high']}")


def test_h_i_cross_production() -> None:
    assert _cross_down(90.5, 89.0, 90.0)
    assert _cross_up(54.0, 56.0, 55.0)
    prices_db.DB_PATH = TEST_ROOT / "prices.db"
    code = "CR175"
    days = _dates(10, date(2026, 8, 3))
    closes = [100.0] * 8 + [85.0, 80.0]
    prices = _px(code, days, closes)
    frame = prices_with_ma3(prices)
    upsert_stock(code, "c", "KOSPI")
    upsert_prices(prices)
    save_ma3(code, frame[["date", "ma3"]])
    prod = detect_signal(code, days[-1])
    local = detect_signals_asof(prices, days[-1], stock_code=code)
    assert {(s["signal_type"], s["signal_level"]) for s in prod} == {
        (s["signal_type"], s["signal_level"]) for s in local
    }
    prices_db.DB_PATH = PROD_DB
    _ok("H/I SELL/BUY crossing vs production", str([(s['signal_type'], s['signal_level']) for s in prod]))


def test_j_next_day() -> None:
    days = _dates(5)
    assert next_trading_day(days, days[0]) == days[1]
    assert next_trading_day(days, days[-1]) is None
    _ok("J D+1 체결 규칙", "last day has no execution")


def test_k_state() -> None:
    days = _dates(20)
    closes = [100.0] * 8 + [70.0] * 6 + [90.0] * 6
    prices = _px("ST175", days, closes)
    tm = timing_strategy(prices, days[0], days[-1], stock_code="ST175")
    states = [r["state"] for r in tm["equity"]]
    assert "HOLDING" in states
    if tm["sells"] > 0:
        assert "CASH" in states
    for i, r in enumerate(tm["equity"]):
        if r["state"] == "CASH":
            assert r["shares"] == 0
            assert r["cash"] > 0
        if r["state"] == "HOLDING":
            assert r["shares"] > 0
    _ok("K 보유/CASH 상태", f"sells={tm['sells']} buys={tm['buys']}")


def test_l_trade_log() -> None:
    days = _dates(20)
    prices = _px("LG175", days, [100.0] * 8 + [70.0] * 6 + [90.0] * 6)
    tm = timing_strategy(prices, days[0], days[-1], stock_code="LG175")
    last = tm["equity"][-1]
    assert abs(last["equity"] - tm["final"]) < 1e-6
    if tm["trade_log"]:
        t0 = tm["trade_log"][0]
        assert t0["execution_date"] == next_trading_day(days, t0["signal_date"])
    _ok("L 거래 로그와 자산가치", f"final={tm['final']:.0f} trades={len(tm['trade_log'])}")


def test_m_mdd() -> None:
    eq = [100.0, 120.0, 60.0, 90.0]
    assert abs(max_drawdown(eq) - 50.0) < 1e-9
    _ok("M MDD", "50%")


def test_n_look_ahead() -> None:
    days = _dates(16)
    closes = [100.0] * 8 + [70.0] * 8
    prices = _px("LA175", days, closes)
    as_of = days[6]
    before = detect_signals_asof(prices, as_of, stock_code="LA175")
    mutated = prices.copy()
    mutated.loc[mutated["date"] >= days[7], "close"] = 1.0
    after = detect_signals_asof(mutated, as_of, stock_code="LA175")
    assert before == after
    _ok("N Look-ahead", "future close does not change signal at D")


def test_o_repro() -> None:
    a = [s["stock_code"] for s in select_ten_stocks()["stocks"]]
    b = [s["stock_code"] for s in select_ten_stocks()["stocks"]]
    assert a == b
    _ok("O 결과 재현성", ",".join(a))


def test_p_fixture() -> None:
    days = _dates(12)
    prices = _px("FX175", days, [100, 100, 100, 110, 120, 80, 70, 60, 65, 90, 95, 100])
    bh = buy_and_hold(days, dict(zip(days, prices["close"])), days[0], days[-1])
    tm = timing_strategy(prices, days[0], days[-1], stock_code="FX175")
    assert bh["final"] > 0 and tm["final"] > 0
    assert tm["equity"][0]["equity"] == INITIAL
    _ok("P fixture", f"timing={tm['final']:.0f} bh={bh['final']:.0f}")


def main() -> None:
    test_a_db()
    test_b_select10()
    test_c_same_dates()
    test_d_initial()
    test_e_bh()
    test_f_ma3()
    test_g_hl()
    test_h_i_cross_production()
    test_j_next_day()
    test_k_state()
    test_l_trade_log()
    test_m_mdd()
    test_n_look_ahead()
    test_o_repro()
    test_p_fixture()
    print("verify_backtest_17_5 단위 검증을 통과했습니다.")
    prices_db.DB_PATH = PROD_DB
    print()
    result = run_backtest_17_5(save=True)
    s0 = result["cost_0"]["summary"]
    sc = result["cost_illustrative"]["summary"]
    print("## 11. Look-ahead 검증")
    print("PASS (테스트 N)")
    print("## 12. 전체 테스트")
    print("A-P PASS")
    print("## 13. 최종 판단")
    start, end = result["metadata"]["start"], result["metadata"]["end"]
    if s0["timing_wins"] > s0["bh_wins"]:
        asset = "높였다"
    elif s0["timing_wins"] < s0["bh_wins"]:
        asset = "낮췄다"
    else:
        asset = "비슷했다"
    mdd_better = s0["timing_avg_mdd"] < s0["bh_avg_mdd"]
    print(
        f"현재 {start}~{end} 데이터의 10개 종목에서 MA3 Timing은 Buy & Hold보다 "
        f"최종 자산가치를 {asset} (Timing 승 {s0['timing_wins']}/10, "
        f"평균 수익률 Timing {s0['timing_avg_return']:.2f}% vs B&H {s0['bh_avg_return']:.2f}%)."
    )
    print(
        f"평균 MDD는 Timing {s0['timing_avg_mdd']:.2f}% / B&H {s0['bh_avg_mdd']:.2f}% 로 "
        f"{'줄였다' if mdd_better else '줄이지 못했다'}."
    )
    print(
        "종목별 승자가 갈리므로 이 짧은 구간의 10종목 결과를 장기 우위로 일반화하지 않는다. "
        f"예시 비용 적용 시 Timing 승 {sc['timing_wins']}/10."
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        prices_db.DB_PATH = PROD_DB
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print(f"테스트 DB 삭제: {TEST_ROOT}")
