"""STEP 7: 매수·매도 기준가와 돌파 신호가 손계산과 같은지 확인."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prices_db import connect, save_ma3, upsert_prices, upsert_stock
from signals import (
    calculate_buy_levels,
    calculate_sell_levels,
    detect_signal,
    find_3month_ma_high,
    find_3month_ma_low,
    _cross_down,
    _cross_up,
)

FIXTURE = "TEST02"


def _ok(label: str, detail: str) -> None:
    print(f"[OK] {label}: {detail}")


def test_levels() -> None:
    sell = calculate_sell_levels(100_000, 10, 20)
    buy = calculate_buy_levels(50_000, 10, 20)
    if sell["sell1"] != 90_000 or sell["sell2"] != 80_000:
        raise RuntimeError(f"sell levels {sell}")
    if buy["buy1"] != 55_000 or buy["buy2"] != 60_000:
        raise RuntimeError(f"buy levels {buy}")
    _ok("calculate_sell_levels", "H=100,000 → 90,000 / 80,000")
    _ok("calculate_buy_levels", "L=50,000 → 55,000 / 60,000")


def test_crossover_rules() -> None:
    if not _cross_down(90_500, 89_000, 90_000):
        raise RuntimeError("sell breakout should fire once")
    if _cross_down(89_000, 88_000, 90_000):
        raise RuntimeError("already below should not repeat")
    if not _cross_up(54_000, 56_000, 55_000):
        raise RuntimeError("buy breakout should fire once")
    if _cross_up(56_000, 57_000, 55_000):
        raise RuntimeError("already above should not repeat")
    _ok("돌파 규칙", "첫 돌파만 True, 이후 반복 False")


def test_detect_on_fixture() -> None:
    upsert_stock(FIXTURE, "신호테스트", "KOSPI")
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 9, 7)]
    ma3s = [100_000.0, 100_000.0, 89_000.0]
    rows = pd.DataFrame(
        {
            "stock_code": FIXTURE,
            "date": days,
            "close": ma3s,
            "volume": [1, 1, 1],
            "trading_value": [1, 1, 1],
        }
    )
    upsert_prices(rows)
    save_ma3(FIXTURE, pd.DataFrame({"date": days, "ma3": ma3s}))

    high = find_3month_ma_high(FIXTURE, date(2026, 9, 7))
    low = find_3month_ma_low(FIXTURE, date(2026, 9, 7))
    if high["value"] != 100_000 or low["value"] != 89_000:
        raise RuntimeError(f"high/low {high['value']} {low['value']}")
    _ok("find_3month_ma_high", f"H={high['value']:,.0f} ({high['date']})")
    _ok("find_3month_ma_low", f"L={low['value']:,.0f} ({low['date']})")

    signals = detect_signal(FIXTURE, date(2026, 9, 7))
    kinds = {(s["signal_type"], s["signal_level"]) for s in signals}
    if kinds != {("sell", 1)}:
        raise RuntimeError(f"expected sell1 only, got {signals}")
    _ok("detect_signal", "전일 100,000 → 오늘 89,000, 매도1만 발생")


def test_real_stock() -> None:
    try:
        high = find_3month_ma_high("005930")
        low = find_3month_ma_low("005930")
    except ValueError as exc:
        print(f"[SKIP] {exc}")
        return
    sell = calculate_sell_levels(high["value"])
    buy = calculate_buy_levels(low["value"])
    signals = detect_signal("005930")
    _ok(
        "005930",
        f"H={high['value']:,.2f} L={low['value']:,.2f} "
        f"매도1={sell['sell1']:,.2f} 매수1={buy['buy1']:,.2f} "
        f"오늘 신호 {len(signals)}건",
    )
    for item in signals:
        print(
            f"     {item['signal_type']}{item['signal_level']} "
            f"MA3={item['ma3']:,.2f} 기준={item['threshold']:,.2f}"
        )


def _cleanup() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM daily_prices WHERE stock_code = ?", (FIXTURE,))
        conn.execute("DELETE FROM stocks WHERE stock_code = ?", (FIXTURE,))


def main() -> None:
    test_levels()
    test_crossover_rules()
    try:
        test_detect_on_fixture()
    finally:
        _cleanup()
    test_real_stock()
    print("매수·매도 신호 엔진 검증을 통과했습니다.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise
