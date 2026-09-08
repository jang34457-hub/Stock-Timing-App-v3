"""STEP 4: 최근 3개월 일평균 거래대금 TOP20 선정."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from top20 import format_top20, is_first_trading_day_of_week, latest_trading_day, select_top20


def _progress(i: int, total: int, day) -> None:
    if i == 1 or i == total or i % 10 == 0:
        print(f"  거래대금 {i}/{total}일 수집 ({day})")


def main() -> None:
    as_of = latest_trading_day()
    print(f"선정 기준일: {as_of}")
    print(f"해당 주 첫 영업일: {is_first_trading_day_of_week(as_of)}")
    top = select_top20(as_of, progress=_progress)
    if len(top) != 20:
        raise RuntimeError(f"expected 20 rows, got {len(top)}")
    if list(top["rank"]) != list(range(1, 21)):
        raise RuntimeError("rank is not 1..20")
    if not top["avg_trading_value"].is_monotonic_decreasing:
        raise RuntimeError("avg_trading_value is not sorted desc")
    print()
    print(format_top20(top))
    print()
    print(
        f"[OK] TOP20 / {top.iloc[0]['window_start']} ~ {as_of} "
        f"/ 거래일 {int(top.iloc[0]['trading_days'])}일"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise
