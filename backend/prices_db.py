"""운영 가격 저장소. 기본은 SQLite, production(+ DATABASE_URL)이면 Supabase Postgres.

백테스트는 backtest_db.py / backtest_prices.db 만 사용한다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from db_backend import (
    PgConnection,
    apply_runtime_schema,
    coerce_date,
    date_str,
    open_postgres,
    sql_for_postgres,
    use_postgres,
)

DB_PATH = Path(__file__).resolve().parent / "data" / "prices.db"
_PG_SCHEMA_READY = False

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
  trading_value REAL NOT NULL,
  ma3 REAL,
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

CREATE TABLE IF NOT EXISTS user_settings (
  user_id TEXT PRIMARY KEY,
  phone_number TEXT,
  x1 REAL NOT NULL DEFAULT 10,
  x2 REAL NOT NULL DEFAULT 20,
  y1 REAL NOT NULL DEFAULT 10,
  y2 REAL NOT NULL DEFAULT 20,
  buy_alert INTEGER NOT NULL DEFAULT 1,
  sell_alert INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_watchlist (
  user_id TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  alert_enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS signal_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  signal_date TEXT NOT NULL,
  signal_type TEXT NOT NULL,
  signal_level INTEGER NOT NULL,
  ma3 REAL NOT NULL,
  reference_price REAL NOT NULL,
  threshold REAL NOT NULL,
  sms_sent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (user_id, stock_code, signal_date, signal_type, signal_level)
);
"""


def connect(path: Path | None = None):
    global _PG_SCHEMA_READY
    db_path = path or DB_PATH
    if use_postgres(db_path):
        conn = open_postgres()
        if not _PG_SCHEMA_READY:
            apply_runtime_schema(conn)
            conn.commit()
            _PG_SCHEMA_READY = True
        return conn
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_CREATE_SQL)
    return conn


def _read_sql(sql: str, conn, params=()):
    if isinstance(conn, PgConnection):
        return pd.read_sql_query(sql_for_postgres(sql), conn._raw, params=params)
    return pd.read_sql_query(sql, conn, params=params)


def _count_rows(conn, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0])


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def stored_dates(stock_code: str) -> set[date]:
    code = str(stock_code).zfill(6)
    with connect() as conn:
        rows = conn.execute(
            "SELECT date FROM daily_prices WHERE stock_code = ?",
            (code,),
        ).fetchall()
    return {coerce_date(row[0]) for row in rows}


def load_prices(stock_code: str) -> pd.DataFrame:
    code = str(stock_code).zfill(6)
    with connect() as conn:
        df = _read_sql(
            """
            SELECT stock_code, date, close, volume, trading_value, ma3
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


def upsert_stock(stock_code: str, stock_name: str, market: str) -> None:
    with connect() as conn:
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


def upsert_prices(rows: pd.DataFrame) -> int:
    """새 (종목, 날짜)만 삽입한다. 이미 있는 행의 close/거래대금은 바꾸지 않는다."""
    if rows is None or rows.empty:
        return 0
    payload = rows.copy()
    payload["stock_code"] = payload["stock_code"].astype(str).str.zfill(6)
    payload["date"] = payload["date"].map(_as_iso)
    records = payload[
        ["stock_code", "date", "close", "volume", "trading_value"]
    ].itertuples(index=False, name=None)
    payload_rows = list(records)
    with connect() as conn:
        if isinstance(conn, PgConnection):
            before = _count_rows(conn, "daily_prices")
            conn.executemany(
                """
                INSERT OR IGNORE INTO daily_prices (stock_code, date, close, volume, trading_value, ma3)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                payload_rows,
            )
            return _count_rows(conn, "daily_prices") - before
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO daily_prices (stock_code, date, close, volume, trading_value, ma3)
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            payload_rows,
        )
        return conn.total_changes - before


def save_ma3(stock_code: str, values: pd.DataFrame) -> int:
    code = str(stock_code).zfill(6)
    records = [
        (None if pd.isna(ma3) else float(ma3), code, _as_iso(day))
        for day, ma3 in zip(values["date"], values["ma3"])
    ]
    with connect() as conn:
        conn.executemany(
            """
            UPDATE daily_prices
            SET ma3 = ?
            WHERE stock_code = ? AND date = ?
            """,
            records,
        )
    return len(records)


def listed_stock_codes() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM daily_prices ORDER BY stock_code"
        ).fetchall()
    return [row[0] for row in rows]


def delete_from(stock_code: str, start: date) -> int:
    """검증용: start 이후 행 삭제."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM daily_prices WHERE stock_code = ? AND date >= ?",
            (str(stock_code).zfill(6), start.isoformat()),
        )
        return cur.rowcount


def last_signal(
    user_id: str,
    stock_code: str,
    signal_type: str,
    signal_level: int,
) -> dict | None:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT user_id, stock_code, signal_date, signal_type, signal_level,
                   ma3, reference_price, threshold, sms_sent
            FROM signal_history
            WHERE user_id = ? AND stock_code = ? AND signal_type = ? AND signal_level = ?
            ORDER BY signal_date DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id, str(stock_code).zfill(6), signal_type, int(signal_level)),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["signal_date"] = coerce_date(data["signal_date"])
    data["sms_sent"] = bool(data["sms_sent"])
    data["ma3"] = _plain(data["ma3"])
    data["reference_price"] = _plain(data["reference_price"])
    data["threshold"] = _plain(data["threshold"])
    return data


def insert_signal(user_id: str, signal: dict) -> bool:
    """새 신호면 True. 같은 날 동일 조건이면 False."""
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO signal_history (
              user_id, stock_code, signal_date, signal_type, signal_level,
              ma3, reference_price, threshold, sms_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                user_id,
                str(signal["stock_code"]).zfill(6),
                _as_iso(signal["signal_date"]),
                signal["signal_type"],
                int(signal["signal_level"]),
                float(signal["ma3"]),
                float(signal["reference_price"]),
                float(signal["threshold"]),
            ),
        )
        return cur.rowcount == 1


def mark_sms_sent(
    user_id: str,
    stock_code: str,
    signal_date: date,
    signal_type: str,
    signal_level: int,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE signal_history
            SET sms_sent = 1
            WHERE user_id = ? AND stock_code = ? AND signal_date = ?
              AND signal_type = ? AND signal_level = ?
            """,
            (
                user_id,
                str(stock_code).zfill(6),
                signal_date.isoformat(),
                signal_type,
                int(signal_level),
            ),
        )


def delete_signals(stock_code: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM signal_history WHERE stock_code = ?",
            (str(stock_code).zfill(6),),
        )


def get_stock(stock_code: str) -> dict | None:
    code = str(stock_code).zfill(6)
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT stock_code, stock_name, market FROM stocks WHERE stock_code = ?",
            (code,),
        ).fetchone()
    return dict(row) if row else None


def save_top20(df: pd.DataFrame) -> None:
    if df is None or df.empty:
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
        for row in df.itertuples(index=False)
    ]
    with connect() as conn:
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


def load_latest_top20() -> dict | None:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        meta = conn.execute(
            """
            SELECT selection_date, window_start, trading_days
            FROM top20_history
            ORDER BY selection_date DESC
            LIMIT 1
            """
        ).fetchone()
        if meta is None:
            return None
        rows = conn.execute(
            """
            SELECT rank, stock_code, stock_name, market, avg_trading_value
            FROM top20_history
            WHERE selection_date = ?
            ORDER BY rank
            """,
            (meta["selection_date"],),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["rank"] = int(item["rank"])
        item["avg_trading_value"] = _plain(item["avg_trading_value"])
        items.append(item)
    window_start = meta["window_start"]
    trading_days = meta["trading_days"]
    return {
        "selection_date": date_str(meta["selection_date"]),
        "window_start": date_str(window_start) if window_start is not None else None,
        "trading_days": int(trading_days) if trading_days is not None else 0,
        "items": items,
    }


def list_user_ids() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT user_id FROM user_settings
            UNION
            SELECT user_id FROM user_watchlist
            ORDER BY 1
            """
        ).fetchall()
    ids = [str(row[0]) for row in rows]
    return ids or ["local"]


def get_settings(user_id: str) -> dict:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT user_id, phone_number, x1, x2, y1, y2, buy_alert, sell_alert
            FROM user_settings WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            conn.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
            row = conn.execute(
                """
                SELECT user_id, phone_number, x1, x2, y1, y2, buy_alert, sell_alert
                FROM user_settings WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
    data = dict(row)
    data["buy_alert"] = bool(data["buy_alert"])
    data["sell_alert"] = bool(data["sell_alert"])
    for key in ("x1", "x2", "y1", "y2"):
        data[key] = _plain(data[key])
    return data


def save_settings(user_id: str, payload: dict) -> dict:
    current = get_settings(user_id)
    merged = {**current, **{k: v for k, v in payload.items() if v is not None}}
    if isinstance(merged.get("phone_number"), str):
        raw = merged["phone_number"].strip() or None
        if raw:
            from sms import normalize_phone

            merged["phone_number"] = normalize_phone(raw)
        else:
            merged["phone_number"] = None
    if float(merged["x2"]) <= float(merged["x1"]) or float(merged["y2"]) <= float(merged["y1"]):
        raise ValueError("x2 must be greater than x1, and y2 greater than y1")
    with connect() as conn:
        conn.execute(
            """
            UPDATE user_settings
            SET phone_number = ?, x1 = ?, x2 = ?, y1 = ?, y2 = ?,
                buy_alert = ?, sell_alert = ?
            WHERE user_id = ?
            """,
            (
                merged.get("phone_number"),
                float(merged["x1"]),
                float(merged["x2"]),
                float(merged["y1"]),
                float(merged["y2"]),
                1 if merged["buy_alert"] else 0,
                1 if merged["sell_alert"] else 0,
                user_id,
            ),
        )
    return get_settings(user_id)


def list_watchlist(user_id: str) -> list[dict]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT w.stock_code, COALESCE(s.stock_name, w.stock_code) AS stock_name,
                   COALESCE(s.market, '') AS market, w.alert_enabled
            FROM user_watchlist w
            LEFT JOIN stocks s ON s.stock_code = w.stock_code
            WHERE w.user_id = ?
            ORDER BY w.created_at
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "market": row["market"],
            "alert_enabled": bool(row["alert_enabled"]),
        }
        for row in rows
    ]


def add_watchlist(user_id: str, stock_code: str, alert_enabled: bool = True) -> dict:
    code = str(stock_code).zfill(6)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO user_watchlist (user_id, stock_code, alert_enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, stock_code) DO UPDATE SET
              alert_enabled = excluded.alert_enabled
            """,
            (user_id, code, 1 if alert_enabled else 0),
        )
    items = [row for row in list_watchlist(user_id) if row["stock_code"] == code]
    return items[0]


def remove_watchlist(user_id: str, stock_code: str) -> bool:
    code = str(stock_code).zfill(6)
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM user_watchlist WHERE user_id = ? AND stock_code = ?",
            (user_id, code),
        )
        return cur.rowcount > 0


def list_signals(user_id: str, stock_code: str | None = None) -> list[dict]:
    sql = """
        SELECT h.user_id, h.stock_code, COALESCE(s.stock_name, h.stock_code) AS stock_name,
               h.signal_date, h.signal_type, h.signal_level, h.ma3,
               h.reference_price, h.threshold, h.sms_sent
        FROM signal_history h
        LEFT JOIN stocks s ON s.stock_code = h.stock_code
        WHERE h.user_id = ?
    """
    params: list = [user_id]
    if stock_code:
        sql += " AND h.stock_code = ?"
        params.append(str(stock_code).zfill(6))
    sql += " ORDER BY h.signal_date DESC, h.created_at DESC, h.id DESC"
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["sms_sent"] = bool(item["sms_sent"])
        item["signal_date"] = date_str(item["signal_date"])
        item["ma3"] = _plain(item["ma3"])
        item["reference_price"] = _plain(item["reference_price"])
        item["threshold"] = _plain(item["threshold"])
        out.append(item)
    return out


def _as_iso(value) -> str:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]
