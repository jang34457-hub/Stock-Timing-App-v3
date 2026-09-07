"""SQLite 저장소 계층.

로컬/Cloud Agent 개발에서 외부 의존성 없이 동작하도록 stdlib sqlite3 사용.
운영에서 Supabase(PostgreSQL)로 옮길 때는 이 모듈만 교체하면 된다.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    stock_code TEXT PRIMARY KEY,
    stock_name TEXT NOT NULL,
    market     TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS daily_prices (
    stock_code    TEXT NOT NULL,
    date          TEXT NOT NULL,
    close         REAL NOT NULL,
    volume        INTEGER,
    trading_value REAL,
    ma3           REAL,
    PRIMARY KEY (stock_code, date)
);

CREATE TABLE IF NOT EXISTS top20_history (
    selection_date    TEXT NOT NULL,
    stock_code        TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    avg_trading_value REAL NOT NULL,
    PRIMARY KEY (selection_date, stock_code)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id      TEXT PRIMARY KEY,
    phone_number TEXT,
    x1 REAL NOT NULL DEFAULT 10,
    x2 REAL NOT NULL DEFAULT 20,
    y1 REAL NOT NULL DEFAULT 10,
    y2 REAL NOT NULL DEFAULT 20,
    buy_alert  INTEGER NOT NULL DEFAULT 1,
    sell_alert INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_watchlist (
    user_id       TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    alert_enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS signal_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    signal_date     TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    signal_level    INTEGER NOT NULL,
    ma3             REAL NOT NULL,
    reference_price REAL NOT NULL,
    sms_sent        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, stock_code, signal_date, signal_type, signal_level)
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_connection()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_cursor() as cur:
        cur.executescript(SCHEMA)
