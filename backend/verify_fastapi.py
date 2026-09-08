"""STEP 9: FastAPI 엔드포인트가 응답하는지 확인."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ["STA_FORCE_SQLITE"] = "1"
os.environ["APP_ENV"] = "development"
os.environ.pop("API_SECRET_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prices_db

prices_db.DB_PATH = Path(tempfile.mkdtemp()) / "prices.db"

from app import app
from prices_db import save_top20, upsert_prices, upsert_stock

os.environ["APP_ENV"] = "development"
os.environ.pop("API_SECRET_KEY", None)

USER = {"X-User-Id": "api-test"}


def _ok(label: str, detail: str) -> None:
    print(f"[OK] {label}: {detail}")


def main() -> None:
    upsert_stock("005930", "삼성전자", "KOSPI")
    days = [date(2026, 9, 1) + pd.Timedelta(days=i) for i in range(7)]
    upsert_prices(
        pd.DataFrame(
            {
                "stock_code": ["005930"] * 7,
                "date": days,
                "close": [260000 + i * 1000 for i in range(7)],
                "volume": [1] * 7,
                "trading_value": [1.0] * 7,
            }
        )
    )
    save_top20(
        pd.DataFrame(
            [
                {
                    "rank": 1,
                    "selection_date": date(2026, 9, 7),
                    "window_start": date(2026, 6, 7),
                    "trading_days": 63,
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "market": "KOSPI",
                    "avg_trading_value": 1.0,
                }
            ]
        )
    )

    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200, health.text
    assert health.json()["ok"] is True
    _ok("GET /health", health.json()["service"])

    top = client.get("/top20")
    assert top.status_code == 200, top.text
    items = top.json()["items"]
    assert items, top.text
    _ok("GET /top20", f"{len(items)}종목")

    stock = client.get("/stocks/005930", headers=USER)
    assert stock.status_code == 200, stock.text
    body = stock.json()
    assert body["stock_name"] == "삼성전자"
    assert body["close"] > 0
    _ok("GET /stocks/{code}", f"{body['date']} 종가 {body['close']:,.0f} MA3 {body['ma3']}")

    chart = client.get("/stocks/005930/chart", headers=USER)
    assert chart.status_code == 200, chart.text
    assert len(chart.json()["points"]) >= 3
    assert "close" in chart.json()
    assert "recent_signals" in top.json()
    _ok("GET /stocks/{code}/chart", f"{len(chart.json()['points'])}봉")

    added = client.post(
        "/watchlist",
        headers=USER,
        json={"stock_code": "005930", "alert_enabled": True},
    )
    assert added.status_code == 200, added.text
    watch = client.get("/watchlist", headers=USER)
    assert watch.status_code == 200
    assert any(item["stock_code"] == "005930" for item in watch.json()["items"])
    _ok("POST/GET /watchlist", str(watch.json()["items"]))

    settings = client.put(
        "/settings",
        headers=USER,
        json={"phone_number": "01012345678", "x1": 12, "x2": 22, "y1": 8, "y2": 18},
    )
    assert settings.status_code == 200, settings.text
    assert settings.json()["x1"] == 12
    _ok("PUT /settings", str(settings.json()))

    signals = client.get("/signals", headers=USER)
    assert signals.status_code == 200, signals.text
    _ok("GET /signals", f"{len(signals.json()['items'])}건")
    print("FastAPI 엔드포인트 검증을 통과했습니다.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise
