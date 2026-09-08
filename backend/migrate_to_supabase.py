"""Copy production SQLite (prices.db) into Supabase Postgres. Leaves backtest_prices.db alone."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from db_backend import (
    DEFAULT_SQLITE,
    apply_runtime_schema,
    database_url,
    open_postgres,
)

SKIP_CODE_PREFIXES = ("TEST",)


def _skip_code(code: str) -> bool:
    raw = str(code).upper()
    return any(raw.startswith(p) for p in SKIP_CODE_PREFIXES)


def _copy_table(src, dst, table: str, columns: list[str], skip_test_code: bool = False) -> int:
    col_sql = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = src.execute(f"SELECT {col_sql} FROM {table}").fetchall()
    payload = []
    for row in rows:
        data = dict(row)
        if skip_test_code and _skip_code(str(data.get("stock_code", ""))):
            continue
        payload.append(tuple(data[c] for c in columns))
    if not payload:
        return 0
    dst.executemany(
        f"INSERT OR IGNORE INTO {table} ({col_sql}) VALUES ({placeholders})",
        payload,
    )
    return len(payload)


def migrate(sqlite_path: Path) -> dict:
    if not database_url():
        raise SystemExit(
            "DATABASE_URL 또는 SUPABASE_DB_URL 이 필요합니다. backend/.env 에 연결 문자열을 넣으세요."
        )
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite 파일이 없습니다: {sqlite_path}")

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    dst = open_postgres()
    try:
        apply_runtime_schema(dst)
        dst.commit()
        missing = src.execute(
            """
            SELECT DISTINCT p.stock_code
            FROM daily_prices p
            LEFT JOIN stocks s ON s.stock_code = p.stock_code
            WHERE s.stock_code IS NULL
            """
        ).fetchall()
        for row in missing:
            code = str(row[0])
            if _skip_code(code):
                continue
            dst.execute(
                "INSERT OR IGNORE INTO stocks (stock_code, stock_name, market) VALUES (?, ?, ?)",
                (code, code, ""),
            )
        counts = {
            "stocks": _copy_table(
                src, dst, "stocks", ["stock_code", "stock_name", "market"], skip_test_code=True
            ),
            "daily_prices": _copy_table(
                src,
                dst,
                "daily_prices",
                ["stock_code", "date", "close", "volume", "trading_value", "ma3"],
                skip_test_code=True,
            ),
            "top20_history": _copy_table(
                src,
                dst,
                "top20_history",
                [
                    "selection_date",
                    "stock_code",
                    "stock_name",
                    "market",
                    "rank",
                    "avg_trading_value",
                    "window_start",
                    "trading_days",
                ],
                skip_test_code=True,
            ),
            "user_settings": _copy_table(
                src,
                dst,
                "user_settings",
                ["user_id", "phone_number", "x1", "x2", "y1", "y2", "buy_alert", "sell_alert"],
            ),
            "user_watchlist": _copy_table(
                src,
                dst,
                "user_watchlist",
                ["user_id", "stock_code", "alert_enabled", "created_at"],
                skip_test_code=True,
            ),
            "signal_history": _copy_table(
                src,
                dst,
                "signal_history",
                [
                    "user_id",
                    "stock_code",
                    "signal_date",
                    "signal_type",
                    "signal_level",
                    "ma3",
                    "reference_price",
                    "threshold",
                    "sms_sent",
                    "created_at",
                ],
                skip_test_code=True,
            ),
        }
        dst.commit()
        return counts
    except Exception:
        dst._raw.rollback()
        raise
    finally:
        dst.close()
        src.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate production SQLite → Supabase")
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=DEFAULT_SQLITE,
        help="Source prices.db (default: backend/data/prices.db)",
    )
    args = parser.parse_args()
    print("Source:", args.sqlite)
    print("Backtest DB is not copied.")
    counts = migrate(args.sqlite.resolve())
    for name, n in counts.items():
        print(f"  {name}: attempted {n} rows (INSERT ON CONFLICT DO NOTHING)")
    print("Done.")


if __name__ == "__main__":
    main()
    sys.exit(0)
