"""Fill production TOP20 from backtest_prices.db when the live snapshot has fewer than 20 names."""

from __future__ import annotations

import sqlite3

import pandas as pd

import backtest_db
from ma3 import calculate_ma3
from prices_db import load_latest_top20, save_top20, upsert_prices, upsert_stock
from top20 import TOP_N


def snapshot_is_complete(cached: dict | None, n: int = TOP_N) -> bool:
    return bool(cached) and len((cached or {}).get("items") or []) >= n


def seed_top20_from_backtest() -> dict | None:
    path = backtest_db.DB_PATH
    if not path.is_file():
        return None
    src = sqlite3.connect(path)
    src.row_factory = sqlite3.Row
    try:
        latest = src.execute("SELECT MAX(selection_date) FROM top20_history").fetchone()[0]
        if not latest:
            return None
        rows = src.execute(
            """
            SELECT selection_date, stock_code, stock_name, market, rank,
                   avg_trading_value, window_start, trading_days
            FROM top20_history
            WHERE selection_date = ?
            ORDER BY rank
            """,
            (latest,),
        ).fetchall()
        if len(rows) < TOP_N:
            return None
        df = pd.DataFrame([dict(row) for row in rows])
        from prices_db import connect

        with connect() as conn:
            conn.execute("DELETE FROM top20_history WHERE selection_date = ?", (str(latest)[:10],))
        for row in rows:
            upsert_stock(row["stock_code"], row["stock_name"], row["market"])
        save_top20(df)
        for row in rows:
            code = str(row["stock_code"]).zfill(6)
            prices = pd.read_sql_query(
                """
                SELECT stock_code, date, close, volume,
                       COALESCE(trading_value, 0) AS trading_value
                FROM daily_prices
                WHERE stock_code = ?
                ORDER BY date
                """,
                src,
                params=(code,),
            )
            if prices.empty:
                continue
            upsert_prices(prices)
            try:
                calculate_ma3(code)
            except ValueError:
                pass
        return load_latest_top20()
    finally:
        src.close()
