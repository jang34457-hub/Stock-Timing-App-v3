"""STEP 17-2: historical TOP20 데이터 준비 검증."""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import historical_top20, rank_top20_from_history
from incremental import missing_date_ranges
from prepare_backtest_data import (
    PrepareStats,
    fetch_missing_stock_prices,
    prepare_backtest_data,
    quality_report,
    unique_top20_codes,
)
import backtest_db

TMP = Path(tempfile.mkdtemp(prefix="sta-bt-data-"))
LISTING = TMP / "krx_listing"
DB = TMP / "backtest_prices.db"
LISTING.mkdir(parents=True)


def _ok(label: str, detail: str = "") -> None:
    print(f"[PASS] {label}" + (f": {detail}" if detail else ""))


def _listing_csv(day: date, rows: list[tuple[str, str, str, float]]) -> None:
    recs = []
    for code, name, mid, amount in rows:
        recs.append(
            {
                "Code": code,
                "ISU_CD": code,
                "Name": name,
                "Market": "KOSPI" if mid == "STK" else "KOSDAQ",
                "Dept": "",
                "Close": 1000,
                "ChangeCode": 0,
                "Changes": 0,
                "ChagesRatio": 0,
                "Open": 1000,
                "High": 1000,
                "Low": 1000,
                "Volume": 10,
                "Amount": amount,
                "Marcap": 1,
                "Stocks": 1,
                "MarketId": mid,
            }
        )
    pd.DataFrame(recs).to_csv(LISTING / f"{day.isoformat()}.csv", encoding="utf-8")


def _dir_patches():
    return (
        patch("backtest.LISTING_CACHE_DIR", LISTING),
        patch("prepare_backtest_data.LISTING_CACHE_DIR", LISTING),
        patch("market_data.LISTING_CACHE_DIR", LISTING),
    )


def test_top20_reconstruction() -> None:
    d1, d2 = date(2026, 6, 8), date(2026, 6, 15)
    hist = pd.concat(
        [
            pd.DataFrame(
                {
                    "date": [d1, d1, d1],
                    "stock_code": ["000001", "000002", "000003"],
                    "stock_name": ["A", "B", "C"],
                    "market": ["KOSPI", "KOSPI", "KOSDAQ"],
                    "trading_value": [100.0, 80.0, 10.0],
                }
            ),
            pd.DataFrame(
                {
                    "date": [d2, d2, d2],
                    "stock_code": ["000001", "000002", "000003"],
                    "stock_name": ["A", "B", "C"],
                    "market": ["KOSPI", "KOSPI", "KOSDAQ"],
                    "trading_value": [10.0, 20.0, 900.0],
                }
            ),
        ],
        ignore_index=True,
    )
    w1 = rank_top20_from_history(hist, end=d1, start=date(2026, 3, 8), n=2)
    w2 = rank_top20_from_history(hist, end=d2, start=date(2026, 3, 15), n=2)
    assert list(w1["stock_code"]) == ["000001", "000002"]
    assert list(w2["stock_code"])[0] == "000003"
    _ok("historical TOP20 reconstruction", f"w1={list(w1['stock_code'])} w2={list(w2['stock_code'])}")


def test_look_ahead_top20() -> None:
    d1, d2 = date(2026, 6, 8), date(2026, 6, 9)
    p0, p1, p2 = _dir_patches()
    with p0, p1, p2:
        _listing_csv(d1, [("000001", "A", "STK", 100.0), ("000002", "B", "STK", 50.0)])
        _listing_csv(d2, [("000099", "FUTURE", "STK", 9_999_999.0), ("000001", "A", "STK", 1.0)])
        cache: dict = {}
        before = historical_top20(d1, n=2, listing_cache=cache)
        assert "000099" not in set(before["stock_code"])
        assert list(before["stock_code"])[0] == "000001"
        after = historical_top20(d1, n=2, listing_cache=cache)
        assert list(after["stock_code"]) == list(before["stock_code"])
    _ok("TOP20 look-ahead", "000099 not in 2026-06-08")


def test_unique_codes() -> None:
    weekly = {
        date(2026, 6, 8): ["000001", "000002"],
        date(2026, 6, 15): ["000002", "000003"],
    }
    assert unique_top20_codes(weekly) == ["000001", "000002", "000003"]
    _ok("unique stock_code", "3 codes from overlapping weeks")


def test_incremental_ranges() -> None:
    calendar = [date(2026, 9, d) for d in (1, 2, 3, 4, 5)]
    have = {date(2026, 9, 1), date(2026, 9, 2)}
    missing = [d for d in calendar if d not in have]
    assert missing_date_ranges(missing, calendar) == [(date(2026, 9, 3), date(2026, 9, 5))]
    calls: list[tuple[str, str, str]] = []

    def fake_prices(code, start, end):
        calls.append((code, start, end))
        days = pd.date_range(start, end, freq="D")
        return pd.DataFrame(
            {
                "date": [d.date() for d in days],
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 10,
                "volume": 2,
            }
        )

    p0, p1, p2 = _dir_patches()
    with p0, p1, p2:
        for day in calendar:
            _listing_csv(day, [("000010", "X", "STK", 5.0)])
        backtest_db.insert_prices(
            pd.DataFrame(
                {
                    "stock_code": ["000010", "000010"],
                    "date": [date(2026, 9, 1), date(2026, 9, 2)],
                    "close": [10, 10],
                    "volume": [2, 2],
                    "trading_value": [5.0, 5.0],
                    "open": [1, 1],
                    "high": [1, 1],
                    "low": [1, 1],
                }
            ),
            DB,
        )
        stats = PrepareStats()
        fetch_missing_stock_prices(
            "000010",
            calendar,
            listing_cache={},
            stats=stats,
            db_path=DB,
            price_fetch=fake_prices,
        )
    assert calls == [("000010", "2026-09-03", "2026-09-05")]
    assert backtest_db.price_row_count(DB) == 5
    _ok("missing-date incremental", str(calls[0]))


def test_duplicate_ignore() -> None:
    db = TMP / "dup.db"
    rows = pd.DataFrame(
        {
            "stock_code": ["000011"],
            "date": [date(2026, 9, 1)],
            "close": [100.0],
            "volume": [1],
            "trading_value": [10.0],
            "open": [100.0],
            "high": [100.0],
            "low": [100.0],
        }
    )
    backtest_db.upsert_stock("000011", "Y", "KOSPI", db)
    n1 = backtest_db.insert_prices(rows, db)
    n2 = backtest_db.insert_prices(rows.assign(close=999.0), db)
    loaded = backtest_db.load_prices("000011", db)
    assert n1 == 1 and n2 == 0
    assert float(loaded.iloc[0]["close"]) == 100.0
    _ok("duplicate data 방지", "INSERT OR IGNORE, close stays 100")


def test_quality() -> None:
    db = TMP / "qual.db"
    calendar = [date(2026, 9, 1), date(2026, 9, 2)]
    backtest_db.insert_prices(
        pd.DataFrame(
            {
                "stock_code": ["000012", "000012"],
                "date": calendar,
                "close": [10.0, 11.0],
                "volume": [1, 1],
                "trading_value": [3.0, 4.0],
                "open": [10, 11],
                "high": [10, 11],
                "low": [10, 11],
            }
        ),
        db,
    )
    q = quality_report(db, calendar, ["000012", "000013"])
    assert q["duplicate_ok"]
    assert q["bad_close"] == 0
    assert q["negative_trading_value"] == 0
    assert "000013" in q["missing_price_codes"]
    assert q["gap_count"] == 0
    _ok("데이터 품질", f"missing={q['missing_price_codes']}")


def test_full_prepare() -> None:
    db = TMP / "full.db"
    calendar = [
        date(2026, 6, 8),
        date(2026, 6, 9),
        date(2026, 6, 10),
        date(2026, 6, 15),
        date(2026, 6, 16),
    ]
    for day in calendar:
        _listing_csv(
            day,
            [
                ("000021", "P", "STK", 50.0 if day.day < 15 else 5.0),
                ("000022", "Q", "STK", 40.0 if day.day < 15 else 80.0),
            ],
        )

    def fake_prices(code, start, end):
        days = [d for d in calendar if start <= d.isoformat() <= end]
        return pd.DataFrame(
            {
                "date": days,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 12,
                "volume": 3,
            }
        )

    def fake_listing(_day):
        raise AssertionError("existing listings must be reused")

    p0, p1, p2 = _dir_patches()
    with p0, p1, p2:
        report = prepare_backtest_data(
            db_path=db,
            target_end=date(2026, 6, 16),
            calendar=calendar,
            fetch_listing=fake_listing,
            price_fetch=fake_prices,
            download_prices=True,
            remote_exists=lambda _d: False,
        )
        assert report["top20_selections"] >= 2
        assert report["unique_top20_stocks"] == 2
        assert report["priced_stocks"] == 2
        assert report["api"]["listing_http"] == 0
        assert report["api"]["listing_reused"] == len(calendar)
        assert report["api"]["price_api"] == 2
        assert report["price_rows"] == 10
        report2 = prepare_backtest_data(
            db_path=db,
            target_end=date(2026, 6, 16),
            calendar=calendar,
            fetch_listing=fake_listing,
            price_fetch=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no refetch")),
            download_prices=True,
            remote_exists=lambda _d: False,
        )
    assert report2["api"]["price_api"] == 0
    assert report2["api"]["price_rows_inserted"] == 0
    _ok(
        "전체 historical data preparation",
        f"rows={report['price_rows']} unique={report['unique_top20_stocks']}",
    )


def main() -> None:
    test_top20_reconstruction()
    test_look_ahead_top20()
    test_unique_codes()
    test_incremental_ranges()
    test_duplicate_ignore()
    test_quality()
    test_full_prepare()
    print("verify_backtest_data 검증을 통과했습니다.")


if __name__ == "__main__":
    main()
