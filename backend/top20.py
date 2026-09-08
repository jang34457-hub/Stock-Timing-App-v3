"""KOSPI+KOSDAQ 최근 3개월 일평균 거래대금 기준 TOP20."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from market_data import (
    fetch_trading_value_history,
    latest_trading_day,
    subtract_months,
    trading_days,
)

TOP_N = 20
LOOKBACK_MONTHS = 3


def first_trading_day_of_week(day: date) -> date:
    """그 주 월요일부터 본 실제 첫 영업일. 월요일이 휴장이면 다음 거래일."""
    monday = day - timedelta(days=day.weekday())
    days = trading_days(monday, day)
    if not days:
        raise RuntimeError(f"no trading days in week of {day}")
    return days[0]


def is_first_trading_day_of_week(day: date) -> bool:
    """월요일 휴장이면 그 주 첫 영업일이 True."""
    monday = day - timedelta(days=day.weekday())
    days = trading_days(monday, day)
    return bool(days) and days[0] == day


def select_top20(
    as_of: date | None = None,
    n: int = TOP_N,
    *,
    progress=None,
) -> pd.DataFrame:
    """최근 3개월 거래대금 합계 ÷ 실제 거래일 수로 1~n위를 고른다."""
    end = latest_trading_day(as_of)
    start = subtract_months(end, LOOKBACK_MONTHS)
    history = fetch_trading_value_history(start, end, progress=progress)
    session_count = int(history["date"].nunique())
    if session_count == 0:
        raise RuntimeError("no trading sessions in lookback window")

    ranked = (
        history.dropna(subset=["trading_value"])
        .groupby("stock_code", as_index=False)
        .agg(
            stock_name=("stock_name", "last"),
            market=("market", "last"),
            total_trading_value=("trading_value", "sum"),
            days_present=("date", "nunique"),
        )
    )
    ranked["avg_trading_value"] = ranked["total_trading_value"] / session_count
    ranked = ranked.sort_values(
        ["avg_trading_value", "stock_code"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    top = ranked.head(n).copy()
    top.insert(0, "rank", range(1, len(top) + 1))
    top.insert(1, "selection_date", end)
    top.insert(2, "window_start", start)
    top.insert(3, "trading_days", session_count)
    return top[
        [
            "rank",
            "selection_date",
            "window_start",
            "trading_days",
            "stock_code",
            "stock_name",
            "market",
            "avg_trading_value",
            "total_trading_value",
            "days_present",
        ]
    ]


def format_top20(df: pd.DataFrame) -> str:
    view = df.copy()
    view["avg_trading_value"] = view["avg_trading_value"].map(lambda v: f"{v:,.0f}")
    view["total_trading_value"] = view["total_trading_value"].map(lambda v: f"{v:,.0f}")
    return view.to_string(index=False)
