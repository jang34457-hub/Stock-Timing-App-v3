"""API 통합 테스트 — 격리된 임시 SQLite 로 엔드투엔드 흐름 확인."""

import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 테스트마다 격리된 SQLite 파일 사용.
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))

    import app.config as config

    importlib.reload(config)
    import app.db as db

    importlib.reload(db)
    import app.seed as seed

    importlib.reload(seed)
    import app.main as main

    importlib.reload(main)

    seed.seed(force=True)
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["stock_count"] == 8


def test_top20_returns_ranked_list(client):
    r = client.get("/top20")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 8
    ranks = [i["rank"] for i in items]
    assert ranks == sorted(ranks)
    avgs = [i["avg_trading_value"] for i in items]
    assert avgs == sorted(avgs, reverse=True)


def test_chart_has_six_months(client):
    r = client.get("/stocks/005930/chart")
    assert r.status_code == 200
    points = r.json()
    assert len(points) == 130
    # 앞 두 날은 MA3 없음, 이후는 존재
    assert points[0]["ma3"] is None
    assert points[-1]["ma3"] is not None


def test_stock_detail_has_levels(client):
    r = client.get("/stocks/005930")
    assert r.status_code == 200
    detail = r.json()
    assert detail["ma3_high_3m"] is not None
    assert len(detail["sell_levels"]) == 2
    assert len(detail["buy_levels"]) == 2


def test_signals_present_for_watchlist(client):
    r = client.get("/signals")
    assert r.status_code == 200
    signals = r.json()
    assert len(signals) > 0
    assert {s["signal_type"] for s in signals} <= {"BUY", "SELL"}


def test_unknown_stock_returns_404(client):
    assert client.get("/stocks/999999").status_code == 404


def test_add_and_update_watchlist(client):
    r = client.post("/watchlist", json={"stock_code": "035420", "alert_enabled": True})
    assert r.status_code == 200
    assert r.json()["stock_code"] == "035420"
    codes = [w["stock_code"] for w in client.get("/watchlist").json()]
    assert "035420" in codes


def test_update_settings(client):
    payload = {
        "phone_number": "010-1234-5678",
        "x1": 8, "x2": 15, "y1": 8, "y2": 15,
        "buy_alert": True, "sell_alert": False,
    }
    r = client.put("/settings", json=payload)
    assert r.status_code == 200
    assert r.json()["x1"] == 8
