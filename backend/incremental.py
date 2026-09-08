"""TOP20 종목 가격 증분 업데이트.

신규 종목은 최근 6개월만 받고, 이후에는 빠진 영업일만 보완한다.
이미 저장된 날짜는 다시 API를 치지 않는다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from market_data import (
    fetch_listing_on,
    fetch_prices,
    latest_trading_day,
    subtract_months,
    trading_days,
)
from ma3 import calculate_ma3
from prices_db import (
    listed_stock_codes,
    stored_dates,
    upsert_prices,
    upsert_stock,
)

PRICE_LOOKBACK_MONTHS = 6

_pending_prices: pd.DataFrame | None = None
_listing_by_date: dict[date, pd.DataFrame | None] = {}


def clear_listing_cache() -> None:
    _listing_by_date.clear()


def get_last_date(stock_code: str) -> date | None:
    """DB에 저장된 해당 종목의 마지막 거래일."""
    days = stored_dates(stock_code)
    return max(days) if days else None


def get_missing_dates(
    stock_code: str,
    as_of: date | None = None,
    *,
    calendar: list[date] | None = None,
) -> list[date]:
    """6개월 창에서 거래 캘린더에는 있지만 DB에 없는 날짜."""
    days = _price_calendar(as_of, calendar)
    have = stored_dates(stock_code)
    return [day for day in days if day not in have]


def missing_date_ranges(missing: list[date], calendar: list[date]) -> list[tuple[date, date]]:
    """거래일 목록 기준으로 연속 결측 구간 (start, end)을 만든다."""
    if not missing:
        return []
    wanted = set(missing)
    ordered = [day for day in calendar if day in wanted]
    if not ordered:
        return [(missing[0], missing[-1])]
    index = {day: i for i, day in enumerate(calendar)}
    groups: list[tuple[date, date]] = []
    start = prev = ordered[0]
    for day in ordered[1:]:
        if index[day] == index[prev] + 1:
            prev = day
            continue
        groups.append((start, prev))
        start = prev = day
    groups.append((start, prev))
    return groups


def fetch_missing_prices(
    stock_code: str,
    as_of: date | None = None,
    *,
    calendar: list[date] | None = None,
    missing: list[date] | None = None,
) -> pd.DataFrame:
    """빠진 날짜 구간의 가격·거래대금만 가져온다. 연속 거래일 구간마다 API를 친다."""
    global _pending_prices
    code = str(stock_code).zfill(6)
    days = _price_calendar(as_of, calendar)
    if missing is None:
        have = stored_dates(code)
        missing = [day for day in days if day not in have]
    if not missing:
        _pending_prices = _empty_prices()
        return _pending_prices

    chunks: list[pd.DataFrame] = []
    for first, last in missing_date_ranges(missing, days):
        raw = fetch_prices(code, first.isoformat(), last.isoformat())
        if raw is None or raw.empty:
            continue
        chunk = raw.copy()
        chunk["date"] = pd.to_datetime(chunk["date"]).dt.date
        chunks.append(chunk)

    if not chunks:
        _pending_prices = _empty_prices()
        return _pending_prices

    rows = pd.concat(chunks, ignore_index=True)
    rows = rows[rows["date"].isin(set(missing))].copy()
    rows["stock_code"] = code
    listing_map = _listings_for_dates(rows["date"].tolist())
    rows["trading_value"] = [
        _trading_value_from_listing(code, day, close, volume, listing_map)
        for day, close, volume in zip(rows["date"], rows["close"], rows["volume"])
    ]
    _pending_prices = rows[
        ["stock_code", "date", "close", "volume", "trading_value"]
    ].reset_index(drop=True)
    return _pending_prices


def _price_calendar(as_of: date | None, calendar: list[date] | None) -> list[date]:
    if calendar is not None:
        return calendar
    end = latest_trading_day(as_of)
    start = subtract_months(end, PRICE_LOOKBACK_MONTHS)
    return trading_days(start, end)


def save_prices(rows: pd.DataFrame | None = None) -> int:
    """신규 (종목, 날짜)만 저장한다. 이미 있는 시세는 덮어쓰지 않는다."""
    payload = _pending_prices if rows is None else rows
    if payload is None or payload.empty:
        return 0
    for code in payload["stock_code"].astype(str).str.zfill(6).unique():
        name, market = _stock_meta(code)
        upsert_stock(code, name, market)
    return upsert_prices(payload)


def update_ma3(stock_code: str | None = None) -> int:
    """calculate_ma3를 종목별로 호출한다. 종목을 생략하면 저장된 전 종목."""
    codes = [str(stock_code).zfill(6)] if stock_code else listed_stock_codes()
    updated = 0
    for code in codes:
        result = calculate_ma3(code)
        updated += len(result)
    return updated


def _listings_for_dates(days: list[date]) -> dict[date, pd.DataFrame | None]:
    unique: list[date] = []
    seen: set[date] = set()
    for day in days:
        if day not in seen:
            seen.add(day)
            unique.append(day)
    return {day: _listing_on_cached(day) for day in unique}


def _listing_on_cached(day: date) -> pd.DataFrame | None:
    if day not in _listing_by_date:
        try:
            _listing_by_date[day] = fetch_listing_on(day)
        except Exception:
            _listing_by_date[day] = None
    return _listing_by_date[day]


def _trading_value_from_listing(
    stock_code: str,
    day: date,
    close: float,
    volume: float,
    listing_map: dict[date, pd.DataFrame | None],
) -> float:
    snap = listing_map.get(day)
    if snap is None:
        return float(close) * float(volume)
    try:
        hit = snap.loc[snap["stock_code"] == stock_code]
        if not hit.empty and pd.notna(hit.iloc[0]["trading_value"]):
            return float(hit.iloc[0]["trading_value"])
    except Exception:
        pass
    return float(close) * float(volume)


def _trading_value_or_fallback(
    stock_code: str, day: date, close: float, volume: float
) -> float:
    listing_map = _listings_for_dates([day])
    return _trading_value_from_listing(stock_code, day, close, volume, listing_map)


def _stock_meta(stock_code: str) -> tuple[str, str]:
    try:
        snap = _listing_on_cached(latest_trading_day())
        if snap is not None:
            hit = snap.loc[snap["stock_code"] == stock_code]
            if not hit.empty:
                return str(hit.iloc[0]["stock_name"]), str(hit.iloc[0]["market"])
    except Exception:
        pass
    return stock_code, "KOSPI"


def _empty_prices() -> pd.DataFrame:
    return pd.DataFrame(columns=["stock_code", "date", "close", "volume", "trading_value"])
