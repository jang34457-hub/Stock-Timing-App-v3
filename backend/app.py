"""모바일 앱과 통신하는 FastAPI."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from jobs import last_run, run_daily_job
from market_data import fetch_listing_on, latest_trading_day
from prices_db import (
    add_watchlist,
    remove_watchlist,
    get_settings,
    get_stock,
    list_signals,
    list_watchlist,
    load_latest_top20,
    load_prices,
    save_settings,
    save_top20,
    upsert_stock,
)
from signals import (
    calculate_buy_levels,
    calculate_sell_levels,
    detect_chart_signals,
    find_3month_ma_high,
    find_3month_ma_low,
)
from sms import send_sms
from scheduler import scheduler_status, start_scheduler, stop_scheduler
from seed_top20 import seed_top20_from_backtest, snapshot_is_complete
from top20 import select_top20

ENV_PATH = Path(__file__).resolve().parent / ".env"
_OPEN_PATHS = {"/", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/health"}


def _load_dotenv(path: Path = ENV_PATH) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _app_env() -> str:
    return os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower() or "development"


def _api_secret_key() -> str:
    return os.getenv("API_SECRET_KEY", "").strip()


def _is_production() -> bool:
    return _app_env() in {"production", "prod"}


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [part.strip() for part in raw.split(",") if part.strip()] or ["*"]


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401)


def _check_api_key(provided: str | None) -> JSONResponse | None:
    secret = _api_secret_key()
    if not secret:
        if _is_production():
            return _unauthorized("API_SECRET_KEY is not configured")
        return None
    if not provided or not hmac.compare_digest(provided, secret):
        return _unauthorized("Invalid or missing API key")
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Stock Timing API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
        return await call_next(request)
    rejected = _check_api_key(request.headers.get("x-api-key"))
    if rejected is not None:
        return rejected
    return await call_next(request)

UserId = Annotated[str, Header(alias="X-User-Id")]


class WatchlistIn(BaseModel):
    stock_code: str
    alert_enabled: bool = True


class SmsTestIn(BaseModel):
    phone_number: str | None = None
    text: str | None = None
    live: bool = False


class SettingsIn(BaseModel):
    phone_number: str | None = None
    x1: float | None = Field(default=None, gt=0)
    x2: float | None = Field(default=None, gt=0)
    y1: float | None = Field(default=None, gt=0)
    y2: float | None = Field(default=None, gt=0)
    buy_alert: bool | None = None
    sell_alert: bool | None = None


@app.get("/", response_class=HTMLResponse)
def get_root() -> str:
    return """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"/><title>Stock Timing API</title></head>
<body style="font-family:sans-serif;max-width:40rem;margin:2rem;line-height:1.5">
<h1>Stock Timing API</h1>
<p>여기는 <strong>서버 API</strong>입니다. 앱 화면이 아닙니다.</p>
<p>PC에서 앱을 보려면 브라우저에 이것을 여세요:</p>
<p><a href="http://127.0.0.1:8081">http://127.0.0.1:8081</a></p>
<p>API 확인: <a href="/health">/health</a> · <a href="/docs">/docs</a> · <a href="/top20">/top20</a></p>
</body></html>
"""


@app.get("/health")
def get_health() -> dict:
    return {"ok": True, "service": "stock-timing", "env": _app_env()}


@app.get("/top20")
def get_top20(refresh: bool = Query(default=False), x_user_id: UserId = "local") -> dict:
    cached = load_latest_top20()
    if snapshot_is_complete(cached) and not refresh:
        return _enrich_top20(cached, x_user_id)
    seeded = None if refresh else seed_top20_from_backtest()
    if snapshot_is_complete(seeded):
        return _enrich_top20(seeded, x_user_id)
    snapshot = select_top20()
    save_top20(snapshot)
    saved = load_latest_top20()
    if saved is None:
        raise HTTPException(500, "failed to store TOP20")
    return _enrich_top20(saved, x_user_id)


@app.get("/stocks/{code}")
def get_stock_detail(code: str, x_user_id: UserId = "local") -> dict:
    code = code.zfill(6)
    meta = _ensure_stock(code)
    prices = load_prices(code)
    if prices.empty:
        raise HTTPException(404, f"no price data for {code}")
    settings = get_settings(x_user_id)
    last = prices.iloc[-1]
    high = find_3month_ma_high(code)
    low = find_3month_ma_low(code)
    sell = calculate_sell_levels(high["value"], settings["x1"], settings["x2"])
    buy = calculate_buy_levels(low["value"], settings["y1"], settings["y2"])
    return {
        **meta,
        "date": _iso(last["date"]),
        "close": _num(last["close"]),
        "volume": int(last["volume"]),
        "ma3": _num(last["ma3"]) if pd.notna(last["ma3"]) else None,
        "ma_high": high["value"],
        "ma_low": low["value"],
        "sell1": sell["sell1"],
        "sell2": sell["sell2"],
        "buy1": buy["buy1"],
        "buy2": buy["buy2"],
    }


@app.get("/stocks/{code}/chart")
def get_stock_chart(code: str, x_user_id: UserId = "local") -> dict:
    code = code.zfill(6)
    meta = _ensure_stock(code)
    prices = load_prices(code)
    if prices.empty:
        raise HTTPException(404, f"no chart data for {code}")
    settings = get_settings(x_user_id)
    high = find_3month_ma_high(code)
    low = find_3month_ma_low(code)
    sell = calculate_sell_levels(high["value"], settings["x1"], settings["x2"])
    buy = calculate_buy_levels(low["value"], settings["y1"], settings["y2"])
    points = []
    for row in prices.itertuples(index=False):
        points.append(
            {
                "date": _iso(row.date),
                "close": _num(row.close),
                "volume": int(row.volume),
                "ma3": _num(row.ma3) if pd.notna(row.ma3) else None,
            }
        )
    last = prices.iloc[-1]
    return {
        **meta,
        "date": _iso(last["date"]),
        "close": _num(last["close"]),
        "volume": int(last["volume"]),
        "ma3": _num(last["ma3"]) if pd.notna(last["ma3"]) else None,
        "points": points,
        "ma_high": high["value"],
        "ma_low": low["value"],
        "sell1": sell["sell1"],
        "sell2": sell["sell2"],
        "buy1": buy["buy1"],
        "buy2": buy["buy2"],
        "signals": list_signals(x_user_id, code),
        "markers": [
            {**item, "signal_date": _iso(item["signal_date"])}
            for item in detect_chart_signals(
                code,
                x1=settings["x1"],
                x2=settings["x2"],
                y1=settings["y1"],
                y2=settings["y2"],
            )
        ],
    }


@app.get("/watchlist")
def get_watchlist(x_user_id: UserId = "local") -> dict:
    return {"items": list_watchlist(x_user_id)}


@app.post("/watchlist")
def post_watchlist(body: WatchlistIn, x_user_id: UserId = "local") -> dict:
    code = body.stock_code.zfill(6)
    _ensure_stock(code)
    item = add_watchlist(x_user_id, code, body.alert_enabled)
    return item


@app.delete("/watchlist/{code}")
def delete_watchlist(code: str, x_user_id: UserId = "local") -> dict:
    removed = remove_watchlist(x_user_id, code.zfill(6))
    if not removed:
        raise HTTPException(404, f"{code} is not in watchlist")
    return {"ok": True, "stock_code": code.zfill(6)}


@app.get("/signals")
def get_signals(x_user_id: UserId = "local", code: str | None = None) -> dict:
    return {"items": list_signals(x_user_id, code)}


@app.put("/settings")
def put_settings(body: SettingsIn, x_user_id: UserId = "local") -> dict:
    try:
        return save_settings(x_user_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/settings")
def get_user_settings(x_user_id: UserId = "local") -> dict:
    return get_settings(x_user_id)


@app.post("/sms/test")
def post_sms_test(body: SmsTestIn | None = None, x_user_id: UserId = "local") -> dict:
    payload = body.model_dump() if body else {}
    settings = get_settings(x_user_id)
    phone = payload.get("phone_number") or settings.get("phone_number")
    if not phone:
        raise HTTPException(400, "전화번호가 없습니다. 설정에서 먼저 저장하세요.")
    text = payload.get("text") or "[Stock Timing] 테스트 SMS입니다. 이 번호로 매수·매도 알림을 보냅니다."
    live = bool(payload.get("live"))
    try:
        result = send_sms(phone, text, test=not live)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "live": live, **result}


@app.get("/jobs/status")
def get_job_status() -> dict:
    return {**scheduler_status(), "last_run": last_run()}


@app.post("/jobs/run")
def post_job_run(force: bool = Query(default=False)) -> dict:
    return run_daily_job(force=force)


def _ensure_stock(code: str) -> dict:
    meta = get_stock(code)
    if meta:
        return meta
    try:
        snap = fetch_listing_on(latest_trading_day())
        hit = snap.loc[snap["stock_code"] == code]
        if not hit.empty:
            name = str(hit.iloc[0]["stock_name"])
            market = str(hit.iloc[0]["market"])
            upsert_stock(code, name, market)
            return {"stock_code": code, "stock_name": name, "market": market}
    except Exception:
        pass
    upsert_stock(code, code, "KOSPI")
    return {"stock_code": code, "stock_name": code, "market": "KOSPI"}


def _last_quote(code: str) -> dict:
    prices = load_prices(code)
    if prices.empty:
        return {"date": None, "close": None, "ma3": None}
    last = prices.iloc[-1]
    return {
        "date": _iso(last["date"]),
        "close": _num(last["close"]),
        "ma3": _num(last["ma3"]) if pd.notna(last["ma3"]) else None,
    }


def _enrich_top20(saved: dict, user_id: str) -> dict:
    recent = list_signals(user_id)
    latest_by_code: dict[str, dict] = {}
    for item in recent:
        code = str(item.get("stock_code", "")).zfill(6)
        if code not in latest_by_code:
            latest_by_code[code] = item
    items = []
    for raw in saved.get("items", []):
        code = str(raw.get("stock_code", "")).zfill(6)
        items.append({**raw, **_last_quote(code), "last_signal": latest_by_code.get(code)})
    return {**saved, "items": items, "recent_signals": recent[:8]}


def _iso(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _num(value) -> float:
    return round(float(value), 2)
