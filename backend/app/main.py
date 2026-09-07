"""FastAPI 애플리케이션.

개발계획서 §9(STEP 9) 의 엔드포인트를 제공한다. 로컬에서는 SQLite +
샘플 시드로 완전히 동작하며, 프론트엔드(Expo)가 이 API 를 소비한다.
"""

from __future__ import annotations

from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import db_cursor
from .engine.signals import (
    calculate_buy_levels,
    calculate_sell_levels,
    find_3month_ma_high,
    find_3month_ma_low,
)
from .schemas import (
    ChartPoint,
    SettingsPayload,
    Signal,
    StockDetail,
    Top20Item,
    WatchlistCreate,
    WatchlistItem,
)
from .seed import DEMO_USER_ID, seed

app = FastAPI(
    title="Stock Timing API",
    version="0.1.0",
    description="KOSPI/KOSDAQ TOP20 · MA3 매수/매도 신호 API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    # 최초 기동 시 스키마 생성 + 샘플 데이터(비어있을 때만) 적재.
    seed(force=False)


@app.get("/health")
def health() -> dict:
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM stocks")
        stock_count = cur.fetchone()["c"]
    return {
        "status": "ok",
        "storage": "sqlite" if not settings.supabase_db_url else "postgres",
        "sms_enabled": settings.sms_enabled,
        "stock_count": stock_count,
    }


@app.get("/top20", response_model=List[Top20Item])
def get_top20() -> List[Top20Item]:
    with db_cursor() as cur:
        cur.execute("SELECT MAX(selection_date) AS d FROM top20_history")
        row = cur.fetchone()
        latest = row["d"] if row else None
        if not latest:
            return []
        cur.execute(
            "SELECT t.rank, t.stock_code, s.stock_name, s.market,"
            "       t.avg_trading_value"
            " FROM top20_history t JOIN stocks s USING (stock_code)"
            " WHERE t.selection_date = ? ORDER BY t.rank",
            (latest,),
        )
        return [Top20Item(**dict(r)) for r in cur.fetchall()]


def _load_ma3_series(cur, stock_code: str):
    cur.execute(
        "SELECT date, close, ma3 FROM daily_prices"
        " WHERE stock_code = ? ORDER BY date",
        (stock_code,),
    )
    rows = cur.fetchall()
    dates = [r["date"] for r in rows]
    closes = [r["close"] for r in rows]
    ma3 = [r["ma3"] for r in rows]
    return rows, dates, closes, ma3


@app.get("/stocks/{stock_code}", response_model=StockDetail)
def get_stock(stock_code: str) -> StockDetail:
    with db_cursor() as cur:
        cur.execute(
            "SELECT stock_code, stock_name, market FROM stocks"
            " WHERE stock_code = ?",
            (stock_code,),
        )
        stock = cur.fetchone()
        if not stock:
            raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")

        rows, _dates, _closes, ma3 = _load_ma3_series(cur, stock_code)
        # 신호 계산은 최근 3개월(약 65 거래일)만 사용. §10, §20.
        ma3_3m = [v for v in ma3[-65:]]
        high = find_3month_ma_high(ma3_3m)
        low = find_3month_ma_low(ma3_3m)

        return StockDetail(
            stock_code=stock["stock_code"],
            stock_name=stock["stock_name"],
            market=stock["market"],
            latest_close=rows[-1]["close"] if rows else None,
            latest_ma3=ma3[-1] if ma3 else None,
            ma3_high_3m=high,
            ma3_low_3m=low,
            sell_levels=calculate_sell_levels(
                high, settings.default_x1, settings.default_x2
            )
            if high is not None
            else [],
            buy_levels=calculate_buy_levels(
                low, settings.default_y1, settings.default_y2
            )
            if low is not None
            else [],
        )


@app.get("/stocks/{stock_code}/chart", response_model=List[ChartPoint])
def get_chart(stock_code: str) -> List[ChartPoint]:
    # 차트는 최근 6개월 전체를 표시. §10.
    with db_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM stocks WHERE stock_code = ?", (stock_code,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
        cur.execute(
            "SELECT date, close, ma3 FROM daily_prices"
            " WHERE stock_code = ? ORDER BY date",
            (stock_code,),
        )
        return [ChartPoint(**dict(r)) for r in cur.fetchall()]


@app.get("/signals", response_model=List[Signal])
def get_signals() -> List[Signal]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT h.stock_code, s.stock_name, h.signal_date, h.signal_type,"
            "       h.signal_level, h.ma3, h.reference_price, h.sms_sent"
            " FROM signal_history h JOIN stocks s USING (stock_code)"
            " WHERE h.user_id = ? ORDER BY h.signal_date DESC, h.stock_code",
            (DEMO_USER_ID,),
        )
        return [
            Signal(**{**dict(r), "sms_sent": bool(r["sms_sent"])})
            for r in cur.fetchall()
        ]


@app.get("/watchlist", response_model=List[WatchlistItem])
def get_watchlist() -> List[WatchlistItem]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT w.stock_code, s.stock_name, w.alert_enabled"
            " FROM user_watchlist w JOIN stocks s USING (stock_code)"
            " WHERE w.user_id = ? ORDER BY w.stock_code",
            (DEMO_USER_ID,),
        )
        return [
            WatchlistItem(**{**dict(r), "alert_enabled": bool(r["alert_enabled"])})
            for r in cur.fetchall()
        ]


@app.post("/watchlist", response_model=WatchlistItem)
def add_watchlist(payload: WatchlistCreate) -> WatchlistItem:
    with db_cursor() as cur:
        cur.execute(
            "SELECT stock_name FROM stocks WHERE stock_code = ?",
            (payload.stock_code,),
        )
        stock = cur.fetchone()
        if not stock:
            raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
        cur.execute(
            "INSERT INTO user_watchlist (user_id, stock_code, alert_enabled)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT (user_id, stock_code)"
            " DO UPDATE SET alert_enabled = excluded.alert_enabled",
            (DEMO_USER_ID, payload.stock_code, int(payload.alert_enabled)),
        )
        return WatchlistItem(
            stock_code=payload.stock_code,
            stock_name=stock["stock_name"],
            alert_enabled=payload.alert_enabled,
        )


@app.put("/settings", response_model=SettingsPayload)
def update_settings(payload: SettingsPayload) -> SettingsPayload:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO user_settings"
            " (user_id, phone_number, x1, x2, y1, y2, buy_alert, sell_alert)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id) DO UPDATE SET"
            "   phone_number = excluded.phone_number,"
            "   x1 = excluded.x1, x2 = excluded.x2,"
            "   y1 = excluded.y1, y2 = excluded.y2,"
            "   buy_alert = excluded.buy_alert,"
            "   sell_alert = excluded.sell_alert",
            (
                DEMO_USER_ID,
                payload.phone_number,
                payload.x1,
                payload.x2,
                payload.y1,
                payload.y2,
                int(payload.buy_alert),
                int(payload.sell_alert),
            ),
        )
    return payload
