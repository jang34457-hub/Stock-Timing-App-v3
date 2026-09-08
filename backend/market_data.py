"""KRX 시세 조회 (종목 목록, 일별 가격, 거래대금).

KRX 데이터 포털은 로그인이 필요해, FinanceDataReader의 GitHub 캐시/네이버 시세를 사용합니다.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd
import requests

MARKETS = ("KOSPI", "KOSDAQ")
MARKET_ID = {"STK": "KOSPI", "KSQ": "KOSDAQ"}
LISTING_CACHE_URL = (
    "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
    "master/data/listing/krx/{day}.csv"
)
LISTING_CACHE_DIR = Path(__file__).resolve().parent / "data" / "krx_listing"
_HTTP = requests.Session()
_HTTP.headers.update({"User-Agent": "Stock-Timing-App/0.1"})


def _normalize_listing(df: pd.DataFrame, market: str) -> pd.DataFrame:
    out = df.copy()
    out["stock_code"] = out["Code"].astype(str).str.zfill(6)
    out["stock_name"] = out["Name"].astype(str)
    out["market"] = market
    out["close"] = pd.to_numeric(out["Close"], errors="coerce")
    out["volume"] = pd.to_numeric(out["Volume"], errors="coerce")
    out["trading_value"] = pd.to_numeric(out["Amount"], errors="coerce")
    return out[
        ["stock_code", "stock_name", "market", "close", "volume", "trading_value"]
    ].reset_index(drop=True)


def fetch_market_snapshot(market: str) -> pd.DataFrame:
    """해당 시장의 최신 종목 스냅샷 (종가·거래량·거래대금)."""
    if market not in MARKETS:
        raise ValueError(f"market must be one of {MARKETS}, got {market!r}")
    return _normalize_listing(fdr.StockListing(market), market)


def fetch_stock_list(market: str) -> list[dict]:
    """해당 시장 종목 코드·이름 목록."""
    snap = fetch_market_snapshot(market)
    return snap[["stock_code", "stock_name", "market"]].to_dict(orient="records")


def fetch_prices(stock_code: str, start: str, end: str) -> pd.DataFrame:
    """일별 시가·고가·저가·종가·거래량.

    start/end: YYYY-MM-DD 또는 YYYYMMDD
    """
    start_s = _as_iso_date(start)
    end_s = _as_iso_date(end)
    df = fdr.DataReader(stock_code, start_s, end_s)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    out = df.reset_index()
    date_col = out.columns[0]
    out = out.rename(
        columns={
            date_col: "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in out.columns]
    return out[keep]


def fetch_trading_value(stock_code: str, market: str | None = None) -> dict:
    """해당 종목의 최신 거래대금 (원).

    전 종목 스냅샷의 Amount(거래대금)를 사용합니다.
    """
    markets = (market,) if market else MARKETS
    for mkt in markets:
        snap = fetch_market_snapshot(mkt)
        hit = snap.loc[snap["stock_code"] == str(stock_code).zfill(6)]
        if not hit.empty:
            row = hit.iloc[0]
            return {
                "stock_code": row["stock_code"],
                "stock_name": row["stock_name"],
                "market": row["market"],
                "close": int(row["close"]),
                "volume": int(row["volume"]),
                "trading_value": int(row["trading_value"]),
            }
    raise KeyError(f"stock_code not found: {stock_code}")


def default_lookback_days(days: int = 10) -> tuple[str, str]:
    end = datetime.now().date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def subtract_months(day: date, months: int = 3) -> date:
    month = day.month - months
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def trading_days(start: date, end: date) -> list[date]:
    """KOSPI 지수 거래일로 실제 영업일만 고른다.

    장 마감 직후 지수(KS11) 피드가 종목 시세보다 늦을 수 있어,
    삼성전자 일봉에 있는 날짜는 캘린더에 보강한다.
    """
    idx = fdr.DataReader("KS11", start.isoformat(), end.isoformat())
    days: list[date] = []
    if idx is not None and not idx.empty:
        days = [pd.Timestamp(ts).date() for ts in idx.index]
    extra = _stock_session_on_or_before(end)
    if extra is not None and start <= extra <= end and extra not in days:
        days.append(extra)
        days.sort()
    if not days:
        raise RuntimeError(f"no trading calendar between {start} and {end}")
    return days


def _stock_session_on_or_before(end: date) -> date | None:
    """지수보다 빨리 들어오는 종목 시세로 당일 세션 여부를 본다."""
    try:
        probe = fdr.DataReader("005930", end.isoformat(), end.isoformat())
    except Exception:
        return None
    if probe is None or probe.empty:
        return None
    return pd.Timestamp(probe.index[-1]).date()


def latest_trading_day(as_of: date | None = None) -> date:
    end = as_of or date.today()
    days = trading_days(end - timedelta(days=14), end)
    return days[-1]


def is_trading_day(day: date) -> bool:
    """휴장·주말이면 False. 캘린더는 KOSPI 지수 거래일을 쓴다."""
    try:
        return day in trading_days(day - timedelta(days=10), day)
    except RuntimeError:
        return False


def fetch_listing_on(day: date) -> pd.DataFrame:
    """특정 영업일의 KOSPI+KOSDAQ 종목 스냅샷 (거래대금 포함)."""
    path = LISTING_CACHE_DIR / f"{day.isoformat()}.csv"
    if not path.exists():
        url = LISTING_CACHE_URL.format(day=day.isoformat())
        response = _HTTP.get(url, timeout=30)
        if response.status_code == 404 and day == datetime.now().date():
            return _live_listing(day)
        response.raise_for_status()
        LISTING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)

    raw = pd.read_csv(path, dtype={"Code": str, "MarketId": str})
    return _normalize_cached_listing(raw, day)


def fetch_trading_value_history(start: date, end: date, progress=None) -> pd.DataFrame:
    """기간 중 종목별 일 거래대금. 이미 받은 날짜는 로컬 캐시를 재사용한다."""
    frames: list[pd.DataFrame] = []
    missing: list[date] = []
    days = trading_days(start, end)
    for i, day in enumerate(days, start=1):
        try:
            frames.append(fetch_listing_on(day))
        except requests.HTTPError:
            missing.append(day)
            continue
        if progress:
            progress(i, len(days), day)
    if not frames:
        raise RuntimeError("no daily trading-value snapshots in range")
    if missing:
        print(f"  캐시 없는 영업일 {len(missing)}일 제외: {missing[0]} ~ {missing[-1]}")
    return pd.concat(frames, ignore_index=True)


def _live_listing(day: date) -> pd.DataFrame:
    parts = [fetch_market_snapshot(market) for market in MARKETS]
    out = pd.concat(parts, ignore_index=True)
    out.insert(0, "date", pd.Timestamp(day))
    return out


def _normalize_cached_listing(df: pd.DataFrame, day: date) -> pd.DataFrame:
    out = df.copy()
    out["stock_code"] = out["Code"].astype(str).str.zfill(6)
    out["stock_name"] = out["Name"].astype(str)
    out["market"] = out["MarketId"].map(MARKET_ID)
    out = out[out["market"].isin(MARKETS)].copy()
    out["close"] = pd.to_numeric(out["Close"], errors="coerce")
    out["volume"] = pd.to_numeric(out["Volume"], errors="coerce")
    out["trading_value"] = pd.to_numeric(out["Amount"], errors="coerce")
    out["date"] = pd.Timestamp(day)
    return out[
        ["date", "stock_code", "stock_name", "market", "close", "volume", "trading_value"]
    ].reset_index(drop=True)


def _as_iso_date(value: str) -> str:
    compact = value.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return datetime.strptime(compact, "%Y%m%d").date().isoformat()
    return value
