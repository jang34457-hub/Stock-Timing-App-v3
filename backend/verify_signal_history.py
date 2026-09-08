"""STEP 8: 같은 조건에서 SMS가 매일 반복되지 않는지 확인."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prices_db import (
    add_watchlist,
    connect,
    delete_signals,
    last_signal,
    save_ma3,
    save_settings,
    upsert_prices,
    upsert_stock,
)
from signals import is_duplicate_condition, record_for_sms, signals_for_sms

FIXTURE = "TEST03"
USER = "tester"


def _ok(label: str, detail: str) -> None:
    print(f"[OK] {label}: {detail}")


def _seed(days: list[date], ma3s: list[float]) -> None:
    upsert_stock(FIXTURE, "중복방지테스트", "KOSPI")
    save_settings(USER, {"phone_number": "01012345678"})
    add_watchlist(USER, FIXTURE, True)
    rows = pd.DataFrame(
        {
            "stock_code": FIXTURE,
            "date": days,
            "close": ma3s,
            "volume": [1] * len(days),
            "trading_value": [1] * len(days),
        }
    )
    upsert_prices(rows)
    save_ma3(FIXTURE, pd.DataFrame({"date": days, "ma3": ma3s}))


def main() -> None:
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 9, 7)]
    _seed(days, [100_000.0, 100_000.0, 89_000.0])

    first = signals_for_sms(FIXTURE, date(2026, 9, 7), user_id=USER)
    if [(s["signal_type"], s["signal_level"]) for s in first] != [("sell", 1)]:
        raise RuntimeError(f"first sms {first}")
    if not first[0]["sms_sent"]:
        raise RuntimeError("sms_sent should be true")
    _ok("첫 SMS", "매도1 1건 기록")

    second = signals_for_sms(FIXTURE, date(2026, 9, 7), user_id=USER)
    if second:
        raise RuntimeError(f"same-day rerun sent again: {second}")
    _ok("같은 날 재실행", "SMS 0건")

    still_below = {
        "stock_code": FIXTURE,
        "signal_date": date(2026, 9, 8),
        "signal_type": "sell",
        "signal_level": 1,
        "ma3": 87_000.0,
        "reference_price": 100_000.0,
        "threshold": 90_000.0,
    }
    if not is_duplicate_condition(still_below, user_id=USER):
        raise RuntimeError("still-below should be duplicate")
    if record_for_sms([still_below], user_id=USER):
        raise RuntimeError("next day still below sent SMS")
    _ok("다음날에도 기준 아래", "동일 조건으로 SMS 없음")

    days2 = [date(2026, 8, 3), date(2026, 9, 9), date(2026, 9, 10)]
    _seed(days2, [100_000.0, 91_000.0, 88_000.0])
    again = signals_for_sms(FIXTURE, date(2026, 9, 10), user_id=USER)
    if [(s["signal_type"], s["signal_level"]) for s in again] != [("sell", 1)]:
        raise RuntimeError(f"new breakout should sms again: {again}")
    last = last_signal(USER, FIXTURE, "sell", 1)
    if last is None or last["signal_date"] != date(2026, 9, 10):
        raise RuntimeError(f"history not updated {last}")
    _ok("기준 위로 회복 후 재돌파", "새로운 매도1 SMS 허용")

    print("signal_history 중복 SMS 방지를 통과했습니다.")


def _cleanup() -> None:
    delete_signals(FIXTURE)
    with connect() as conn:
        conn.execute("DELETE FROM daily_prices WHERE stock_code = ?", (FIXTURE,))
        conn.execute("DELETE FROM stocks WHERE stock_code = ?", (FIXTURE,))


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
