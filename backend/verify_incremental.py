"""STEP 5: 빠진 날짜만 받아 저장하고 MA3를 갱신하는지 확인."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from incremental import (
    fetch_missing_prices,
    get_last_date,
    get_missing_dates,
    save_prices,
    update_ma3,
)
from prices_db import delete_from, load_prices

SAMPLE = "005930"


def _ok(label: str, detail: str) -> None:
    print(f"[OK] {label}: {detail}")


def main() -> None:
    first_missing = get_missing_dates(SAMPLE)
    print(f"최초 결측: {len(first_missing)}일", end="")
    if first_missing:
        print(f" ({first_missing[0]} ~ {first_missing[-1]})")
    else:
        print()

    fetched = fetch_missing_prices(SAMPLE)
    saved = save_prices()
    ma3_rows = update_ma3(SAMPLE)
    last = get_last_date(SAMPLE)
    leftover = get_missing_dates(SAMPLE)
    prices = load_prices(SAMPLE)

    _ok("get_last_date", str(last))
    _ok("fetch/save", f"{len(fetched)}행 수신, {saved}행 저장, 가격 {len(prices)}행")
    _ok("get_missing_dates", f"남은 결측 {len(leftover)}일")
    if leftover:
        raise RuntimeError(f"still missing: {leftover[:5]}")

    ready = prices.dropna(subset=["ma3"])
    if len(ready) < 3:
        raise RuntimeError("ma3 not filled")
    row = ready.iloc[-1]
    prev2 = ready.iloc[-3:]["close"].tolist()
    expected = sum(prev2) / 3
    if abs(float(row["ma3"]) - expected) > 0.01:
        raise RuntimeError(f"ma3 mismatch {row['ma3']} vs {expected}")
    _ok("update_ma3", f"{ma3_rows}행 / 마지막 MA3 {row['ma3']:,.2f} = {prev2[0]:,.0f}+{prev2[1]:,.0f}+{prev2[2]:,.0f} / 3")

    cut = prices.iloc[-2]["date"]
    deleted = delete_from(SAMPLE, cut)
    gap = get_missing_dates(SAMPLE)
    refill = fetch_missing_prices(SAMPLE)
    if set(gap) != set(refill["date"]):
        raise RuntimeError(f"refetch dates {list(refill['date'])} != missing {gap}")
    save_prices()
    update_ma3(SAMPLE)
    _ok("증분 보완", f"{deleted}행 지운 뒤 {len(refill)}일만 다시 받음: {gap}")
    print("증분 업데이트 함수 검증을 통과했습니다.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise
