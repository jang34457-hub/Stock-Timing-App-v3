"""STEP 17-2: historical TOP20 유니버스 가격을 백테스트 DB에 확보한다.

production prices.db 는 쓰지 않는다. 현재 TOP20만 받는 방식은 사용하지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

import backtest_db
from backtest import (
    build_weekly_top20,
    first_trading_day_in_week,
    historical_top20,
    load_local_listing,
    local_listing_dates,
)
from incremental import missing_date_ranges
from market_data import LISTING_CACHE_DIR, LISTING_CACHE_URL, fetch_listing_on, fetch_prices
from top20 import TOP_N

REPORT_PATH = Path(__file__).resolve().parent / "data" / "backtest_data_report.json"
TARGET_YEARS = 2


@dataclass
class PrepareStats:
    listing_http: int = 0
    listing_reused: int = 0
    listing_missing: int = 0
    price_api: int = 0
    calendar_api: int = 0
    price_rows_reused: int = 0
    price_rows_inserted: int = 0
    notes: list[str] = field(default_factory=list)


def unique_top20_codes(weekly: dict[date, list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for codes in weekly.values():
        for code in codes:
            z = str(code).zfill(6)
            if z not in seen:
                seen.add(z)
                out.append(z)
    return out


def listing_exists_local(day: date, listing_dir: Path | None = None) -> bool:
    root = listing_dir or LISTING_CACHE_DIR
    return (root / f"{day.isoformat()}.csv").exists()


def listing_exists_remote(day: date, http_get=None) -> bool:
    getter = http_get or requests.get
    url = LISTING_CACHE_URL.format(day=day.isoformat())
    try:
        response = getter(url, timeout=20)
    except requests.RequestException:
        return False
    return response.status_code == 200 and bool(response.content)


def find_listing_start(
    target_start: date,
    end: date,
    *,
    local_days: list[date] | None = None,
    remote_exists=None,
) -> date:
    """2년 목표가 없어도 실제 listing이 있는 첫날만 반환한다."""
    local = local_days if local_days is not None else local_listing_dates()
    probe = remote_exists or listing_exists_remote
    if local:
        first_local = min(local)
        if first_local <= target_start:
            return first_local
        if probe(target_start):
            return target_start
        return first_local
    cursor = target_start
    while cursor <= end:
        if probe(cursor):
            return cursor
        cursor += timedelta(days=7)
    raise RuntimeError("no historical listing cache available")


def sync_listings(
    calendar: list[date],
    *,
    stats: PrepareStats,
    fetch_one=fetch_listing_on,
) -> list[date]:
    """거래일별 listing. 로컬 파일이 있으면 HTTP를 치지 않는다."""
    have: list[date] = []
    for day in calendar:
        if listing_exists_local(day):
            stats.listing_reused += 1
            have.append(day)
            continue
        try:
            fetch_one(day)
            stats.listing_http += 1
            have.append(day)
        except (requests.HTTPError, OSError, ValueError, RuntimeError):
            stats.listing_missing += 1
    return have


def trading_value_map(day: date, cache: dict[date, pd.DataFrame]) -> dict[str, float]:
    if day not in cache:
        loaded = load_local_listing(day)
        if loaded is None:
            return {}
        cache[day] = loaded
    snap = cache[day]
    out: dict[str, float] = {}
    for row in snap.itertuples(index=False):
        tv = row.trading_value
        if pd.isna(tv):
            continue
        out[str(row.stock_code).zfill(6)] = float(tv)
    return out


def stock_meta(code: str, cache: dict[date, pd.DataFrame]) -> tuple[str, str]:
    for day in sorted(cache, reverse=True):
        snap = cache[day]
        hit = snap.loc[snap["stock_code"] == code]
        if not hit.empty:
            return str(hit.iloc[0]["stock_name"]), str(hit.iloc[0]["market"])
    return code, "KOSPI"


def fetch_missing_stock_prices(
    code: str,
    calendar: list[date],
    *,
    listing_cache: dict[date, pd.DataFrame],
    stats: PrepareStats,
    db_path: Path,
    price_fetch=fetch_prices,
) -> int:
    """결측 거래일 구간만 start~end 범위로 요청한다."""
    have = backtest_db.stored_dates(code, db_path)
    stats.price_rows_reused += len(have)
    missing = [day for day in calendar if day not in have]
    if not missing:
        return 0
    inserted = 0
    for start, end in missing_date_ranges(missing, calendar):
        stats.price_api += 1
        raw = price_fetch(code, start.isoformat(), end.isoformat())
        if raw is None or raw.empty:
            continue
        chunk = raw.copy()
        chunk["date"] = pd.to_datetime(chunk["date"]).dt.date
        wanted = set(missing)
        chunk = chunk[chunk["date"].isin(wanted)].copy()
        if chunk.empty:
            continue
        tvs = []
        for day, close, volume in zip(chunk["date"], chunk["close"], chunk["volume"]):
            amounts = trading_value_map(day, listing_cache)
            tvs.append(amounts.get(code))
        chunk["stock_code"] = code
        chunk["trading_value"] = tvs
        for col in ("open", "high", "low"):
            if col not in chunk.columns:
                chunk[col] = None
        name, market = stock_meta(code, listing_cache)
        backtest_db.upsert_stock(code, name, market, db_path)
        inserted += backtest_db.insert_prices(chunk, db_path)
    stats.price_rows_inserted += inserted
    return inserted


def quality_report(
    db_path: Path,
    calendar: list[date],
    universe: list[str],
) -> dict:
    issues: list[str] = []
    per: list[dict] = []
    with backtest_db.connect(db_path) as conn:
        dup = conn.execute(
            """
            SELECT stock_code, date, COUNT(*) c
            FROM daily_prices
            GROUP BY stock_code, date
            HAVING c > 1
            """
        ).fetchall()
        if dup:
            issues.append(f"duplicate_pk:{len(dup)}")
        bad_close = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE close IS NULL OR close <= 0"
        ).fetchone()[0]
        if bad_close:
            issues.append(f"bad_close:{bad_close}")
        bad_tv = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE trading_value IS NOT NULL AND trading_value < 0"
        ).fetchone()[0]
        if bad_tv:
            issues.append(f"negative_trading_value:{bad_tv}")
        missing_tv = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE trading_value IS NULL"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT stock_code, MIN(date), MAX(date), COUNT(*)
            FROM daily_prices
            GROUP BY stock_code
            """
        ).fetchall()

    cal_set = set(calendar)
    gap_total = 0
    by_code = {r[0]: r for r in rows}
    for code in universe:
        row = by_code.get(code)
        if row is None:
            per.append(
                {
                    "stock_code": code,
                    "start": None,
                    "end": None,
                    "rows": 0,
                    "gaps": None,
                    "coverage": 0.0,
                }
            )
            continue
        start_s, end_s, n = row[1], row[2], int(row[3])
        have = backtest_db.stored_dates(code, db_path)
        window = [d for d in calendar if date.fromisoformat(start_s) <= d <= date.fromisoformat(end_s)]
        gaps = [d for d in window if d not in have]
        gap_total += len(gaps)
        per.append(
            {
                "stock_code": code,
                "start": start_s,
                "end": end_s,
                "rows": n,
                "gaps": len(gaps),
                "coverage": (n / len(calendar)) if calendar else 0.0,
            }
        )
    priced = [p for p in per if p["rows"] > 0]
    avg_cov = sum(p["coverage"] for p in priced) / len(priced) if priced else 0.0
    missing_codes = [p["stock_code"] for p in per if p["rows"] == 0]
    return {
        "issues": issues,
        "duplicate_ok": not dup,
        "unique_ok": not dup,
        "bad_close": int(bad_close),
        "negative_trading_value": int(bad_tv),
        "missing_trading_value_rows": int(missing_tv),
        "gap_count": gap_total,
        "stocks": per,
        "missing_price_codes": missing_codes,
        "avg_coverage": avg_cov,
        "calendar_days": len(calendar),
        "calendar_span": [calendar[0].isoformat(), calendar[-1].isoformat()] if calendar else None,
    }


def reconstruct_weekly_top20(
    calendar: list[date],
    *,
    listing_cache: dict[date, pd.DataFrame] | None = None,
    n: int = TOP_N,
) -> tuple[dict[date, list[str]], list[dict], list[pd.DataFrame]]:
    cache = listing_cache if listing_cache is not None else {}
    for day in calendar:
        if day not in cache:
            loaded = load_local_listing(day)
            if loaded is not None:
                cache[day] = loaded
    first = calendar[0]
    weekly, snapshots = build_weekly_top20(
        calendar,
        n=n,
        listing_cache=cache,
        min_selection=first_trading_day_in_week(first, calendar),
        max_selection=calendar[-1],
    )
    frames = []
    for day in weekly:
        frames.append(historical_top20(day, n=n, listing_cache=cache))
    return weekly, snapshots, frames


def prepare_backtest_data(
    *,
    db_path: Path | None = None,
    target_end: date | None = None,
    target_years: int = TARGET_YEARS,
    calendar: list[date] | None = None,
    fetch_listing=fetch_listing_on,
    price_fetch=fetch_prices,
    download_prices: bool = True,
    remote_exists=None,
) -> dict:
    db_path = db_path or backtest_db.DB_PATH
    stats = PrepareStats()
    local = local_listing_dates()
    end = target_end or (max(local) if local else date.today())
    target_start = date(end.year - target_years, end.month, min(end.day, 28))
    listing_start = find_listing_start(
        target_start,
        end,
        local_days=local,
        remote_exists=remote_exists or (lambda _d: False),
    )
    if calendar is None:
        from market_data import trading_days

        stats.calendar_api += 1
        try:
            calendar = trading_days(listing_start, end)
        except RuntimeError:
            calendar = [d for d in local if listing_start <= d <= end]
            stats.notes.append("KS11 calendar unavailable; used local listing dates")
    calendar = [d for d in calendar if listing_start <= d <= end]
    if not calendar:
        raise RuntimeError("empty listing/trading calendar")

    have_listings = sync_listings(calendar, stats=stats, fetch_one=fetch_listing)
    listing_cache: dict[date, pd.DataFrame] = {}
    for day in have_listings:
        loaded = load_local_listing(day)
        if loaded is not None:
            listing_cache[day] = loaded
    work_cal = sorted(listing_cache)
    if not work_cal:
        raise RuntimeError("no listing snapshots after sync")

    weekly, snapshots, frames = reconstruct_weekly_top20(work_cal, listing_cache=listing_cache)
    for df in frames:
        backtest_db.save_top20(df, db_path)

    universe = unique_top20_codes(weekly)
    stats.notes.append(
        "krx_listing GitHub 캐시가 2년을 커버하지 않으면 실제 있는 기간만 사용한다."
    )
    stats.notes.append(
        "과거 스냅샷에 있던 종목은 listing에 남지만, 상장폐지 후 FDR 가격이 없으면 가격 row는 비운다."
    )

    if download_prices:
        for code in universe:
            fetch_missing_stock_prices(
                code,
                work_cal,
                listing_cache=listing_cache,
                stats=stats,
                db_path=db_path,
                price_fetch=price_fetch,
            )

    quality = quality_report(db_path, work_cal, universe)
    report = {
        "target_start": target_start.isoformat(),
        "target_end": end.isoformat(),
        "actual_start": work_cal[0].isoformat(),
        "actual_end": work_cal[-1].isoformat(),
        "target_years": target_years,
        "trading_days": len(work_cal),
        "top20_selections": len(weekly),
        "unique_top20_stocks": len(universe),
        "unique_codes": universe,
        "priced_stocks": 0,
        "missing_price_stocks": len(quality["missing_price_codes"]),
        "price_rows": backtest_db.price_row_count(db_path),
        "avg_coverage": quality["avg_coverage"],
        "gap_count": quality["gap_count"],
        "api": {
            "listing_http": stats.listing_http,
            "listing_reused": stats.listing_reused,
            "listing_missing": stats.listing_missing,
            "price_api": stats.price_api,
            "calendar_api": stats.calendar_api,
            "price_rows_reused": stats.price_rows_reused,
            "price_rows_inserted": stats.price_rows_inserted,
        },
        "quality": quality,
        "top20": snapshots,
        "survivorship": {
            "source": "daily KRX listing snapshots (FinanceDataReader GitHub cache)",
            "not_current_listing_only": True,
            "limitation": (
                "캐시 시작일 이전 상장폐지·편출 종목은 재구성할 수 없다. "
                "캐시 기간 안에 있던 종목은 당시 스냅샷으로 TOP20에 포함되며, "
                "이후 거래 중단 시 가격 API가 비면 가격은 결측으로 남긴다."
            ),
        },
        "notes": stats.notes,
    }
    report["priced_stocks"] = len([s for s in quality["stocks"] if s["rows"] > 0])
    return report


def save_report(report: dict, path: Path | None = None) -> Path:
    path = path or REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_report(report: dict) -> None:
    print("=== STEP 17-2 historical data ===")
    print(f"목표 기간: {report['target_start']} ~ {report['target_end']} ({report['target_years']}년)")
    print(f"실제 확보 기간: {report['actual_start']} ~ {report['actual_end']}")
    print(f"거래일 수: {report['trading_days']}")
    print(f"historical TOP20 선정 횟수: {report['top20_selections']}")
    print(f"unique TOP20 종목 수: {report['unique_top20_stocks']}")
    print(f"가격 확보 종목 수: {report['priced_stocks']}")
    print(f"가격 없는 종목 수: {report['missing_price_stocks']}")
    print(f"전체 가격 row 수: {report['price_rows']}")
    print(f"종목별 평균 coverage: {report['avg_coverage']:.4f}")
    print(f"데이터 gap 수: {report['gap_count']}")
    api = report["api"]
    print(f"API listing HTTP: {api['listing_http']} / 재사용 {api['listing_reused']} / 없음 {api['listing_missing']}")
    print(f"가격 API 호출: {api['price_api']}")
    print(f"기존 가격 재사용 row: {api['price_rows_reused']}")
    print(f"신규 가격 저장 row: {api['price_rows_inserted']}")
    print(f"survivorship: {report['survivorship']['limitation']}")
    if report["quality"]["missing_price_codes"]:
        print("가격 없는 종목:", ", ".join(report["quality"]["missing_price_codes"]))


def main() -> dict:
    def remote(day: date) -> bool:
        if listing_exists_local(day):
            return True
        return listing_exists_remote(day)

    report = prepare_backtest_data(remote_exists=remote, download_prices=True)
    save_report(report)
    print_report(report)
    print(f"DB: {backtest_db.DB_PATH}")
    print(f"report: {REPORT_PATH}")
    return report


if __name__ == "__main__":
    main()
