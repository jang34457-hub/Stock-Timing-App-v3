"""백테스트 전용 시세 DB. production prices.db 와 분리한다."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent / "data" / "backtest_prices.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS stocks (
  stock_code TEXT PRIMARY KEY,
  stock_name TEXT NOT NULL,
  market TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_prices (
  stock_code TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  volume INTEGER NOT NULL,
  trading_value REAL,
  open REAL,
  high REAL,
  low REAL,
  PRIMARY KEY (stock_code, date)
);

CREATE TABLE IF NOT EXISTS top20_history (
  selection_date TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT NOT NULL,
  market TEXT NOT NULL,
  rank INTEGER NOT NULL,
  avg_trading_value REAL NOT NULL,
  window_start TEXT NOT NULL,
  trading_days INTEGER NOT NULL,
  PRIMARY KEY (selection_date, stock_code)
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_CREATE_SQL)
    return conn


def _as_iso(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def stored_dates(stock_code: str, path: Path | None = None) -> set[date]:
    code = str(stock_code).zfill(6)
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT date FROM daily_prices WHERE stock_code = ?",
            (code,),
        ).fetchall()
    return {date.fromisoformat(row[0]) for row in rows}


def listed_codes(path: Path | None = None) -> list[str]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM daily_prices ORDER BY stock_code"
        ).fetchall()
    return [row[0] for row in rows]


def upsert_stock(stock_code: str, stock_name: str, market: str, path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO stocks (stock_code, stock_name, market)
            VALUES (?, ?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
              stock_name = excluded.stock_name,
              market = excluded.market
            """,
            (str(stock_code).zfill(6), stock_name, market),
        )


def insert_prices(rows: pd.DataFrame, path: Path | None = None) -> int:
    """이미 있는 stock_code+date 는 무시한다."""
    if rows is None or rows.empty:
        return 0
    payload = rows.copy()
    payload["stock_code"] = payload["stock_code"].astype(str).str.zfill(6)
    payload["date"] = payload["date"].map(_as_iso)
    for col in ("open", "high", "low", "trading_value"):
        if col not in payload.columns:
            payload[col] = None
    tuples = []
    for _, row in payload.iterrows():
        tv = row["trading_value"]
        tuples.append(
            (
                str(row["stock_code"]).zfill(6),
                _as_iso(row["date"]),
                float(row["close"]),
                int(row["volume"]) if pd.notna(row["volume"]) else 0,
                None if pd.isna(tv) else float(tv),
                None if pd.isna(row["open"]) else float(row["open"]),
                None if pd.isna(row["high"]) else float(row["high"]),
                None if pd.isna(row["low"]) else float(row["low"]),
            )
        )
    with connect(path) as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO daily_prices
              (stock_code, date, close, volume, trading_value, open, high, low)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuples,
        )
        return conn.total_changes - before


def save_top20(rows: pd.DataFrame, path: Path | None = None) -> None:
    if rows is None or rows.empty:
        return
    records = [
        (
            _as_iso(row.selection_date),
            str(row.stock_code).zfill(6),
            str(row.stock_name),
            str(row.market),
            int(row.rank),
            float(row.avg_trading_value),
            _as_iso(row.window_start),
            int(row.trading_days),
        )
        for row in rows.itertuples(index=False)
    ]
    with connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO top20_history (
              selection_date, stock_code, stock_name, market, rank,
              avg_trading_value, window_start, trading_days
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(selection_date, stock_code) DO UPDATE SET
              stock_name = excluded.stock_name,
              market = excluded.market,
              rank = excluded.rank,
              avg_trading_value = excluded.avg_trading_value,
              window_start = excluded.window_start,
              trading_days = excluded.trading_days
            """,
            records,
        )


def load_prices(stock_code: str, path: Path | None = None) -> pd.DataFrame:
    code = str(stock_code).zfill(6)
    with connect(path) as conn:
        df = pd.read_sql_query(
            """
            SELECT stock_code, date, close, volume, trading_value, open, high, low
            FROM daily_prices
            WHERE stock_code = ?
            ORDER BY date
            """,
            conn,
            params=(code,),
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def load_all_top20_codes(path: Path | None = None) -> list[str]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM top20_history ORDER BY stock_code"
        ).fetchall()
    return [row[0] for row in rows]


def price_row_count(path: Path | None = None) -> int:
    with connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0])


def inspect_coverage(path: Path | None = None) -> dict:
    """실행 시점 DB coverage. 기간·종목 수를 하드코딩하지 않는다."""
    with connect(path) as conn:
        total = conn.execute(
            """
            SELECT MIN(date), MAX(date), COUNT(*), COUNT(DISTINCT stock_code)
            FROM daily_prices
            """
        ).fetchone()
        per = conn.execute(
            """
            SELECT stock_code, MIN(date), MAX(date), COUNT(*)
            FROM daily_prices
            GROUP BY stock_code
            ORDER BY stock_code
            """
        ).fetchall()
    return {
        "data_start": total[0],
        "data_end": total[1],
        "price_rows": int(total[2] or 0),
        "stock_count": int(total[3] or 0),
        "stocks": [
            {
                "stock_code": row[0],
                "start": row[1],
                "end": row[2],
                "rows": int(row[3]),
            }
            for row in per
        ],
    }


def load_all_prices(path: Path | None = None) -> dict[str, pd.DataFrame]:
    codes = listed_codes(path)
    return {code: load_prices(code, path) for code in codes}
