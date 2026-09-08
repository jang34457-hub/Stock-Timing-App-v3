"""STEP 3: 종목 목록 / 가격 / 거래대금 조회가 실제로 되는지 확인."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_data import (
    default_lookback_days,
    fetch_market_snapshot,
    fetch_prices,
    fetch_stock_list,
    fetch_trading_value,
)

SAMPLE_KOSPI = "005930"  # 삼성전자
SAMPLE_KOSDAQ = "247540"  # 에코프로비엠


def _ok(label: str, detail: str) -> None:
    print(f"[OK] {label}: {detail}")


def check_listings() -> None:
    kospi = fetch_stock_list("KOSPI")
    kosdaq = fetch_stock_list("KOSDAQ")
    if len(kospi) < 100:
        raise RuntimeError(f"KOSPI listing too small: {len(kospi)}")
    if len(kosdaq) < 100:
        raise RuntimeError(f"KOSDAQ listing too small: {len(kosdaq)}")

    kospi_names = ", ".join(f"{r['stock_code']} {r['stock_name']}" for r in kospi[:3])
    kosdaq_names = ", ".join(f"{r['stock_code']} {r['stock_name']}" for r in kosdaq[:3])
    _ok("종목 목록", f"KOSPI {len(kospi)}종목 / KOSDAQ {len(kosdaq)}종목")
    print(f"     KOSPI 예시: {kospi_names}")
    print(f"     KOSDAQ 예시: {kosdaq_names}")


def check_prices() -> None:
    start, end = default_lookback_days(10)
    df = fetch_prices(SAMPLE_KOSPI, start, end)
    if df.empty or "close" not in df.columns:
        raise RuntimeError(f"no price rows for {SAMPLE_KOSPI}")
    last = df.iloc[-1]
    _ok(
        "가격",
        f"{SAMPLE_KOSPI} {start}~{end} / {len(df)}행 / 최근 종가 {int(last['close']):,}",
    )
    print(df.tail(3).to_string(index=False))


def check_trading_value() -> None:
    kospi_row = fetch_trading_value(SAMPLE_KOSPI, "KOSPI")
    kosdaq_row = fetch_trading_value(SAMPLE_KOSDAQ, "KOSDAQ")
    snap = fetch_market_snapshot("KOSPI")
    if snap["trading_value"].isna().all() or (snap["trading_value"] <= 0).all():
        raise RuntimeError("KOSPI trading_value is empty")

    _ok(
        "거래대금",
        f"{kospi_row['stock_name']}({SAMPLE_KOSPI}) {kospi_row['trading_value']:,}원",
    )
    _ok(
        "거래대금(코스닥)",
        f"{kosdaq_row['stock_name']}({SAMPLE_KOSDAQ}) {kosdaq_row['trading_value']:,}원",
    )


def main() -> None:
    check_listings()
    print()
    check_prices()
    print()
    check_trading_value()
    print()
    print("세 가지 조회 모두 성공했습니다.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise
