"""STEP 18-1: Postgres adapter + sqlite fallback. Does not require a live Supabase project."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from db_backend import (  # noqa: E402
    DEFAULT_SQLITE,
    sql_for_postgres,
    sqlite_path_is_default,
    use_postgres,
)


def _ok(name: str, cond: bool) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(1)


def main() -> None:
    print("STEP 18-1 verify_supabase")

    converted = sql_for_postgres(
        "INSERT OR IGNORE INTO daily_prices (stock_code, date) VALUES (?, ?)"
    )
    _ok(
        "INSERT OR IGNORE → ON CONFLICT DO NOTHING + %s",
        "ON CONFLICT DO NOTHING" in converted
        and converted.count("%s") == 2
        and "?" not in converted
        and "OR IGNORE" not in converted.upper(),
    )

    upsert = sql_for_postgres(
        "INSERT INTO stocks (stock_code) VALUES (?) ON CONFLICT(stock_code) DO UPDATE SET stock_name = excluded.stock_name"
    )
    _ok("plain INSERT keeps ON CONFLICT, placeholders %s", "%s" in upsert and "ON CONFLICT" in upsert)

    tmp = Path(tempfile.mkdtemp()) / "prices.db"
    prev = {k: os.environ.get(k) for k in ("STA_FORCE_SQLITE", "STA_USE_POSTGRES", "APP_ENV", "DATABASE_URL")}
    try:
        os.environ.pop("STA_FORCE_SQLITE", None)
        os.environ["STA_USE_POSTGRES"] = "1"
        os.environ["APP_ENV"] = "development"
        os.environ["DATABASE_URL"] = "postgresql://example/db"
        _ok("temp DB_PATH stays sqlite even with DATABASE_URL", not use_postgres(tmp))
        _ok("default path + STA_USE_POSTGRES uses postgres", use_postgres(DEFAULT_SQLITE))
        os.environ["STA_FORCE_SQLITE"] = "1"
        _ok("STA_FORCE_SQLITE wins", not use_postgres(DEFAULT_SQLITE))
        os.environ.pop("STA_FORCE_SQLITE", None)
        os.environ.pop("STA_USE_POSTGRES", None)
        os.environ["APP_ENV"] = "development"
        _ok("development without flag stays sqlite", not use_postgres(DEFAULT_SQLITE))
        os.environ["APP_ENV"] = "production"
        _ok("production + URL uses postgres", use_postgres(DEFAULT_SQLITE))
    finally:
        for key, val in prev.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    schema = ROOT.parent / "database" / "003_runtime_compat.sql"
    text = schema.read_text(encoding="utf-8")
    for table in (
        "daily_prices",
        "stocks",
        "top20_history",
        "user_settings",
        "user_watchlist",
        "signal_history",
    ):
        _ok(f"schema mentions {table}", table in text)
    _ok("schema keeps threshold + TEXT user_id", "threshold" in text and "user_id TEXT" in text)

    migrate = (ROOT / "migrate_to_supabase.py").read_text(encoding="utf-8")
    _ok("migrate skips backtest_prices.db", "backtest_prices" in migrate)
    _ok("sqlite_path helper", sqlite_path_is_default(DEFAULT_SQLITE))

    import prices_db  # noqa: E402

    prices_db.DB_PATH = tmp
    prices_db.upsert_stock("005930", "삼성전자", "KOSPI")
    import pandas as pd
    from datetime import date

    n = prices_db.upsert_prices(
        pd.DataFrame(
            [
                {
                    "stock_code": "005930",
                    "date": date(2026, 1, 2),
                    "close": 100.0,
                    "volume": 1,
                    "trading_value": 100.0,
                }
            ]
        )
    )
    n2 = prices_db.upsert_prices(
        pd.DataFrame(
            [
                {
                    "stock_code": "005930",
                    "date": date(2026, 1, 2),
                    "close": 999.0,
                    "volume": 1,
                    "trading_value": 100.0,
                }
            ]
        )
    )
    df = prices_db.load_prices("005930")
    _ok("overridden DB_PATH still sqlite insert-or-ignore", n == 1 and n2 == 0 and float(df.iloc[0]["close"]) == 100.0)

    from backtest_db import DB_PATH as BACKTEST_PATH

    _ok("backtest DB path unchanged", BACKTEST_PATH.name == "backtest_prices.db")
    print("All STEP 18-1 checks passed.")


if __name__ == "__main__":
    main()
