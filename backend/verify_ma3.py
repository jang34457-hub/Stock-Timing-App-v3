"""STEP 6: 손으로 계산한 MA3와 프로그램 결과가 같은지 확인."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ma3 import calculate_ma3, ma3_from_closes
from prices_db import connect, load_prices, upsert_prices, upsert_stock

FIXTURE_CODE = "TEST01"
# 손계산:
# 1일 10,000 → 없음
# 2일 11,000 → 없음
# 3일 12,000 → (10000+11000+12000)/3 = 11,000
# 4일  9,000 → (11000+12000+9000)/3 = 10,666.67
# 5일 15,000 → (12000+9000+15000)/3 = 12,000
HAND_CLOSES = [10000, 11000, 12000, 9000, 15000]
HAND_MA3 = [None, None, 11000.00, 10666.67, 12000.00]


def _ok(label: str, detail: str) -> None:
    print(f"[OK] {label}: {detail}")


def test_hand_series() -> None:
    got = ma3_from_closes(HAND_CLOSES)
    if got != HAND_MA3:
        raise RuntimeError(f"series mismatch {got} != {HAND_MA3}")
    _ok("손계산 시계열", str(got))


def test_fixture_stock() -> None:
    upsert_stock(FIXTURE_CODE, "MA3테스트", "KOSPI")
    rows = pd.DataFrame(
        {
            "stock_code": FIXTURE_CODE,
            "date": [date(2026, 9, d) for d in range(1, 6)],
            "close": HAND_CLOSES,
            "volume": [1] * 5,
            "trading_value": [1] * 5,
        }
    )
    upsert_prices(rows)
    result = calculate_ma3(FIXTURE_CODE)
    got = [None if pd.isna(v) else float(v) for v in result["ma3"]]
    if got != HAND_MA3:
        raise RuntimeError(f"calculate_ma3 mismatch {got} != {HAND_MA3}")

    stored = load_prices(FIXTURE_CODE)
    stored_ma3 = [None if pd.isna(v) else float(v) for v in stored["ma3"]]
    if stored_ma3 != HAND_MA3:
        raise RuntimeError("DB ma3 was not persisted")
    _ok("calculate_ma3(TEST01)", "손계산과 동일, DB 저장 확인")


def test_real_stock() -> None:
    prices = load_prices("005930")
    if len(prices) < 3:
        print("[SKIP] 005930 가격이 아직 없습니다.")
        return
    result = calculate_ma3("005930")
    last = result.iloc[-1]
    prev = result.iloc[-3:]["close"].tolist()
    expected = round(sum(prev) / 3, 2)
    actual = float(last["ma3"])
    if abs(actual - expected) > 0.001:
        raise RuntimeError(f"005930 ma3 {actual} != {expected}")
    _ok(
        "calculate_ma3(005930)",
        f"{last['date']} MA3 {actual:,.2f} = ({prev[0]:,.0f}+{prev[1]:,.0f}+{prev[2]:,.0f})/3",
    )


def _cleanup_fixture() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM daily_prices WHERE stock_code = ?", (FIXTURE_CODE,))
        conn.execute("DELETE FROM stocks WHERE stock_code = ?", (FIXTURE_CODE,))


def main() -> None:
    test_hand_series()
    try:
        test_fixture_stock()
    finally:
        _cleanup_fixture()
    test_real_stock()
    print("MA3 엔진 검증을 통과했습니다.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise
