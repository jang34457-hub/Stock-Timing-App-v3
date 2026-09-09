"""STEP 1~15 통합 테스트. 프로덕션 코드·실거래소·실 SMS·기존 DB를 건드리지 않는다."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ["SMS_PROVIDER"] = "test"
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["CATCHUP_ON_START"] = "0"
os.environ["CATCHUP_ON_READ"] = "0"
os.environ["APP_ENV"] = "development"
os.environ.pop("API_SECRET_KEY", None)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

import prices_db

TEST_ROOT = Path(tempfile.mkdtemp(prefix="sta-integration-"))
prices_db.DB_PATH = TEST_ROOT / "prices.db"

import incremental
import jobs
import sms
from fastapi.testclient import TestClient
from incremental import (
    PRICE_LOOKBACK_MONTHS,
    fetch_missing_prices,
    get_missing_dates,
    save_prices,
    update_ma3,
)
from jobs import _ensure_top20, run_daily_job
from ma3 import calculate_ma3, ma3_from_closes
from notify import notify_signals
from prices_db import (
    add_watchlist,
    connect,
    get_settings,
    insert_signal,
    last_signal,
    list_signals,
    list_watchlist,
    load_latest_top20,
    load_prices,
    save_ma3,
    save_settings,
    save_top20,
    stored_dates,
    upsert_prices,
    upsert_stock,
)
from signals import (
    _cross_down,
    _cross_up,
    calculate_buy_levels,
    calculate_sell_levels,
    detect_chart_signals,
    detect_signal,
    find_3month_ma_high,
    find_3month_ma_low,
    is_duplicate_condition,
    record_for_sms,
    signals_for_sms,
)
from top20 import LOOKBACK_MONTHS, select_top20

import app as app_module
from app import app

jobs.LOG_PATH = TEST_ROOT / "job_log.jsonl"
sms.LOG_PATH = TEST_ROOT / "sms_log.jsonl"

MONDAY = date(2026, 9, 7)
TUESDAY = date(2026, 9, 8)
PREV_MON = date(2026, 8, 31)
USER = "local"

RESULTS: list[tuple[str, str, str]] = []
NOTES: dict[str, list[str]] = defaultdict(list)
SPAN_OBSERVE: dict[str, object] = {}
API_COUNTS: dict[str, dict[str, int]] = {}


def _ok(item: str, note: str = "") -> None:
    RESULTS.append((item, "PASS", note))
    print(f"[PASS] {item}" + (f": {note}" if note else ""))


def _fail(item: str, note: str) -> None:
    RESULTS.append((item, "FAIL", note))
    print(f"[FAIL] {item}: {note}")


def _clear_db() -> None:
    with connect() as conn:
        for table in (
            "signal_history",
            "user_watchlist",
            "user_settings",
            "top20_history",
            "daily_prices",
            "stocks",
        ):
            conn.execute(f"DELETE FROM {table}")
    incremental._pending_prices = None


def _seed_prices(code: str, days: list[date], closes: list[float], name: str = "테스트", market: str = "KOSPI") -> None:
    code = str(code).zfill(6)
    upsert_stock(code, name, market)
    rows = pd.DataFrame(
        {
            "stock_code": code,
            "date": days,
            "close": closes,
            "volume": [100] * len(days),
            "trading_value": [1_000] * len(days),
        }
    )
    upsert_prices(rows)
    save_ma3(code, pd.DataFrame({"date": days, "ma3": ma3_from_closes(closes)}))


def _fake_top20_df(session: date, codes: list[tuple[str, str, str]] | None = None) -> pd.DataFrame:
    codes = codes or [("005930", "삼성전자", "KOSPI"), ("247540", "에코프로비엠", "KOSDAQ")]
    rows = []
    for i, (code, name, market) in enumerate(codes, start=1):
        rows.append(
            {
                "rank": i,
                "selection_date": session,
                "window_start": date(2026, 6, 7),
                "trading_days": 63,
                "stock_code": code,
                "stock_name": name,
                "market": market,
                "avg_trading_value": float(1_000_000_000 * (21 - i)),
                "total_trading_value": float(1_000_000_000 * (21 - i) * 63),
                "days_present": 63,
            }
        )
    return pd.DataFrame(rows)


def _listing_frame(day: date, stocks: list[dict]) -> pd.DataFrame:
    rows = []
    for s in stocks:
        rows.append(
            {
                "date": pd.Timestamp(day),
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "market": s["market"],
                "close": s.get("close", 1),
                "volume": s.get("volume", 1),
                "trading_value": s["trading_value"],
            }
        )
    return pd.DataFrame(rows)


def test_01_top20() -> None:
    _clear_db()
    session = MONDAY
    days = [session - timedelta(days=i) for i in range(4, -1, -1)]
    stocks = []
    for i in range(1, 26):
        market = "KOSPI" if i % 2 else "KOSDAQ"
        stocks.append(
            {
                "stock_code": f"{i:06d}",
                "stock_name": f"종목{i}",
                "market": market,
                "trading_value": (26 - i) * 1_000_000,
            }
        )

    frames = []
    for day in days:
        frames.append(_listing_frame(day, stocks))
    history = pd.concat(frames, ignore_index=True)

    with (
        patch("top20.latest_trading_day", return_value=session),
        patch("top20.subtract_months", return_value=days[0]),
        patch("top20.fetch_trading_value_history", return_value=history) as fetch_hist,
    ):
        top = select_top20(as_of=session)

    assert fetch_hist.called
    assert len(top) == 20
    assert list(top["rank"]) == list(range(1, 21))
    assert set(top["market"]).issuperset({"KOSPI", "KOSDAQ"})
    session_count = history["date"].nunique()
    assert int(top.iloc[0]["trading_days"]) == session_count == 5
    expected_avg = (25 * 1_000_000 * 5) / 5
    assert abs(float(top.iloc[0]["avg_trading_value"]) - expected_avg) < 1
    assert top.iloc[0]["stock_code"] == "000001"
    assert list(top["avg_trading_value"]) == sorted(top["avg_trading_value"], reverse=True)
    assert all(top["selection_date"] == session)
    save_top20(top)
    saved = load_latest_top20()
    assert saved is not None
    assert saved["selection_date"] == session.isoformat()
    assert [x["rank"] for x in saved["items"]] == list(range(1, 21))
    NOTES["TOP20"].append("select_top20 분모=구간 unique 거래일, verify_top20와 동일 정렬 규칙")
    _ok("TOP20", "KOSPI+KOSDAQ, 분모 5일, rank 1~20 저장")


def test_02_weekly() -> None:
    _clear_db()

    def run_case(session: date, week_start: date, first: bool) -> dict:
        with (
            patch("jobs.first_trading_day_of_week", return_value=week_start),
            patch("jobs.is_first_trading_day_of_week", return_value=first),
            patch("jobs.select_top20", side_effect=lambda as_of=None, n=20, **k: _fake_top20_df(as_of or session)),
        ):
            return _ensure_top20(session, first_session=first)

    _clear_db()
    out = run_case(MONDAY, MONDAY, True)
    assert out["action"] in {"reselected", "seeded"}
    assert load_latest_top20()["selection_date"] == MONDAY.isoformat()

    _clear_db()
    save_top20(_fake_top20_df(PREV_MON))
    out = run_case(TUESDAY, MONDAY, False)
    assert out["action"] == "reselected"
    assert load_latest_top20()["selection_date"] == TUESDAY.isoformat()

    _clear_db()
    save_top20(_fake_top20_df(PREV_MON))
    out = run_case(TUESDAY, TUESDAY, True)
    assert out["action"] == "reselected"
    assert load_latest_top20()["selection_date"] == TUESDAY.isoformat()

    _clear_db()
    save_top20(_fake_top20_df(MONDAY))
    with (
        patch("jobs.first_trading_day_of_week", return_value=MONDAY),
        patch("jobs.select_top20") as sel,
    ):
        out = _ensure_top20(TUESDAY, first_session=False)
    assert out["action"] == "kept"
    sel.assert_not_called()
    assert load_latest_top20()["selection_date"] == MONDAY.isoformat()

    _clear_db()
    save_top20(_fake_top20_df(PREV_MON))
    out = run_case(TUESDAY, MONDAY, False)
    assert out["action"] == "reselected"

    _clear_db()
    save_top20(_fake_top20_df(MONDAY))
    out = run_case(TUESDAY, MONDAY, False)
    assert out["action"] == "kept"
    _ok("주간 재선정", "①~⑥ assertion 통과")


def test_03_six_month_new() -> None:
    _clear_db()
    code = "099991"
    end = MONDAY
    start = end.replace(month=3) if end.month > 6 else date(2025, 9, 7)
    calendar = [end - timedelta(days=7 * i) for i in range(12, -1, -1)]
    calendar = [d for d in calendar if start <= d <= end]
    if len(calendar) < 3:
        calendar = [end - timedelta(days=i) for i in range(9, -1, -1)]

    def fake_prices(stock_code, start_s, end_s):
        rows = []
        for i, day in enumerate(calendar):
            rows.append(
                {
                    "date": day,
                    "open": 10,
                    "high": 10,
                    "low": 10,
                    "close": 100 + i,
                    "volume": 10,
                }
            )
        return pd.DataFrame(rows)

    with (
        patch("incremental.latest_trading_day", return_value=end),
        patch("incremental.subtract_months", return_value=calendar[0]) as sub,
        patch("incremental.trading_days", return_value=calendar),
        patch("incremental.fetch_prices", side_effect=fake_prices),
        patch("incremental.fetch_listing_on", side_effect=RuntimeError("no listing")),
    ):
        missing = get_missing_dates(code, end)
        assert missing == calendar
        fetched = fetch_missing_prices(code, end)
        saved = save_prices(fetched)
        sub.assert_called()
        assert sub.call_args.args[1] == PRICE_LOOKBACK_MONTHS == 6

    have = stored_dates(code)
    assert have == set(calendar)
    assert saved == len(calendar)
    assert len(have) == len(calendar)
    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT date) FROM daily_prices WHERE stock_code=?",
            (code,),
        ).fetchone()
    assert n[0] == n[1] == len(calendar)
    assert len(calendar) >= 3
    update_ma3(code)
    _ok("6개월 데이터", f"신규 {code} {len(calendar)}거래일, 중복 없음")


def test_04_incremental() -> None:
    _clear_db()
    code = "099992"
    days = [date(2026, 9, d) for d in (1, 2, 3, 4, 5, 6, 7)]
    stored = [date(2026, 9, d) for d in (1, 2, 3, 5, 6, 7)]
    upsert_stock(code, "증분", "KOSPI")
    upsert_prices(
        pd.DataFrame(
            {
                "stock_code": code,
                "date": stored,
                "close": [10] * len(stored),
                "volume": [1] * len(stored),
                "trading_value": [100.0] * len(stored),
            }
        )
    )
    originals = load_prices(code).copy()

    requested: list[tuple[str, str]] = []

    def fake_prices(stock_code, start_s, end_s):
        requested.append((start_s, end_s))
        out = []
        cur = date.fromisoformat(start_s)
        last = date.fromisoformat(end_s)
        while cur <= last:
            out.append({"date": cur, "open": 1, "high": 1, "low": 1, "close": 99, "volume": 7})
            cur += timedelta(days=1)
        return pd.DataFrame(out)

    with (
        patch("incremental.latest_trading_day", return_value=days[-1]),
        patch("incremental.subtract_months", return_value=days[0]),
        patch("incremental.trading_days", return_value=days),
        patch("incremental.fetch_prices", side_effect=fake_prices),
        patch("incremental.fetch_listing_on", side_effect=RuntimeError("skip")),
    ):
        missing = get_missing_dates(code, days[-1])
        fetched = fetch_missing_prices(code, days[-1])
        save_prices(fetched)

    assert missing == [date(2026, 9, 4)]
    SPAN_OBSERVE["single_gap_request"] = requested
    SPAN_OBSERVE["single_gap_missing"] = [d.isoformat() for d in missing]
    after = load_prices(code)
    assert stored_dates(code) == set(days)
    before_map = {r.date: (r.close, r.trading_value) for r in originals.itertuples()}
    for row in after.itertuples():
        if row.date in before_map and row.date != date(2026, 9, 4):
            assert (row.close, row.trading_value) == before_map[row.date]

    _clear_db()
    upsert_stock(code, "증분", "KOSPI")
    two_holes_stored = [date(2026, 9, d) for d in (1, 2, 3, 5, 7)]
    upsert_prices(
        pd.DataFrame(
            {
                "stock_code": code,
                "date": two_holes_stored,
                "close": [10] * 5,
                "volume": [1] * 5,
                "trading_value": [100.0] * 5,
            }
        )
    )
    requested2: list[tuple[str, str]] = []

    def fake_prices2(stock_code, start_s, end_s):
        requested2.append((start_s, end_s))
        out = []
        cur = date.fromisoformat(start_s)
        last = date.fromisoformat(end_s)
        while cur <= last:
            out.append({"date": cur, "open": 1, "high": 1, "low": 1, "close": 50, "volume": 1})
            cur += timedelta(days=1)
        return pd.DataFrame(out)

    with (
        patch("incremental.latest_trading_day", return_value=days[-1]),
        patch("incremental.subtract_months", return_value=days[0]),
        patch("incremental.trading_days", return_value=days),
        patch("incremental.fetch_prices", side_effect=fake_prices2),
        patch("incremental.fetch_listing_on", side_effect=RuntimeError("skip")),
    ):
        missing2 = get_missing_dates(code, days[-1])
        fetched2 = fetch_missing_prices(code, days[-1])
        save_prices(fetched2)

    SPAN_OBSERVE["span_gap_request"] = requested2
    SPAN_OBSERVE["span_gap_missing"] = [d.isoformat() for d in missing2]
    SPAN_OBSERVE["span_gap_saved_from_fetch"] = [d.isoformat() for d in fetched2["date"].tolist()]
    assert missing2 == [date(2026, 9, 4), date(2026, 9, 6)]
    assert requested2 == [("2026-09-04", "2026-09-04"), ("2026-09-06", "2026-09-06")]
    NOTES["증분 업데이트"].append(
        "결측 4,6은 거래일 연속 구간별로 4~4, 6~6 두 번 요청하고 결측만 저장"
    )
    with connect() as conn:
        dup = conn.execute(
            "SELECT date, COUNT(*) c FROM daily_prices WHERE stock_code=? GROUP BY date HAVING c>1",
            (code,),
        ).fetchall()
    assert dup == []
    _ok("증분 업데이트", f"요청={requested2} 결측={missing2} 저장필터={list(fetched2['date'])}")


def test_05_ma3() -> None:
    _clear_db()
    closes = [100.0, 110.0, 120.0, 90.0, 80.0, 100.0]
    got = ma3_from_closes(closes)
    assert got[0] is None and got[1] is None
    assert got[2] == round((100 + 110 + 120) / 3, 2)
    assert got[3] == round((110 + 120 + 90) / 3, 2)
    assert got[4] == round((120 + 90 + 80) / 3, 2)
    assert got[5] == round((90 + 80 + 100) / 3, 2)
    days = [MONDAY + timedelta(days=i) for i in range(6)]
    _seed_prices("TEST05", days, closes)
    df = calculate_ma3("TEST05")
    got_db = [None if pd.isna(v) else float(v) for v in df["ma3"]]
    assert got_db == got
    _ok("MA3", str(got))


def test_06_hl() -> None:
    _clear_db()
    code = "TEST06"
    old_days = [date(2026, 3, 10), date(2026, 3, 11), date(2026, 4, 1)]
    new_days = [date(2026, 7, 1), date(2026, 8, 3), date(2026, 9, 7)]
    days = old_days + new_days
    closes = [10.0, 10.0, 9_000.0, 100.0, 300.0, 200.0]
    upsert_stock(code, "HL", "KOSPI")
    upsert_prices(
        pd.DataFrame(
            {
                "stock_code": code,
                "date": days,
                "close": closes,
                "volume": [1] * 6,
                "trading_value": [1] * 6,
            }
        )
    )
    save_ma3(
        code,
        pd.DataFrame(
            {
                "date": days,
                "ma3": [None, None, 9_000.0, 100.0, 300.0, 200.0],
            }
        )
    )
    with patch("signals.latest_trading_day", return_value=MONDAY):
        high = find_3month_ma_high(code, MONDAY)
        low = find_3month_ma_low(code, MONDAY)
        markers = detect_chart_signals(code, MONDAY)
    assert high["value"] == 300.0
    assert low["value"] == 100.0
    assert high["date"] >= date(2026, 6, 7)
    chart_days = set(load_prices(code)["date"])
    assert date(2026, 4, 1) in chart_days
    assert all(m["signal_date"] >= date(2026, 6, 7) for m in markers)
    _ok("3개월 H/L", f"H={high['value']} L={low['value']} 차트일수={len(chart_days)}")


def test_07_levels() -> None:
    sell = calculate_sell_levels(100_000, 10, 20)
    buy = calculate_buy_levels(50_000, 10, 20)
    assert sell["sell1"] == 90_000
    assert sell["sell2"] == 80_000
    assert buy["buy1"] == 55_000
    assert buy["buy2"] == 60_000
    _ok("매수/매도 기준", "H*0.9/0.8, L*1.1/1.2")


def test_08_cross() -> None:
    assert _cross_down(101, 100, 100) is True
    assert _cross_down(101, 99, 100) is True
    assert _cross_down(100, 99, 100) is False
    assert _cross_up(99, 100, 100) is True
    assert _cross_up(99, 101, 100) is True
    assert _cross_up(100, 101, 100) is False
    _ok("Cross", "경계 = 포함, 전일=기준은 신규 cross 아님")


def test_09_duplicate() -> None:
    _clear_db()
    code = "TEST09"
    days = [
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
        date(2026, 9, 4),
        date(2026, 9, 7),
    ]
    ma3s = [105.0, 105.0, 105.0, 95.0, 94.0, 101.0, 99.0]
    upsert_stock(code, "중복", "KOSPI")
    upsert_prices(
        pd.DataFrame(
            {
                "stock_code": code,
                "date": days,
                "close": ma3s,
                "volume": [1] * len(days),
                "trading_value": [1] * len(days),
            }
        )
    )
    save_ma3(code, pd.DataFrame({"date": days, "ma3": ma3s}))
    save_settings(USER, {"phone_number": "01011112222"})
    add_watchlist(USER, code, True)
    x1 = 100.0 * (1.0 - 100.0 / 105.0)

    first = signals_for_sms(code, date(2026, 9, 2), user_id=USER, x1=x1)
    assert [(s["signal_type"], s["signal_level"]) for s in first] == [("sell", 1)]
    again = signals_for_sms(code, date(2026, 9, 2), user_id=USER, x1=x1)
    assert again == []

    mid = {
        "stock_code": code,
        "signal_date": date(2026, 9, 3),
        "signal_type": "sell",
        "signal_level": 1,
        "ma3": 94.0,
        "reference_price": 105.0,
        "threshold": 100.0,
    }
    assert is_duplicate_condition(mid, user_id=USER) is True
    assert record_for_sms([mid], user_id=USER) == []

    second = signals_for_sms(code, date(2026, 9, 7), user_id=USER, x1=x1)
    assert [(s["signal_type"], s["signal_level"]) for s in second] == [("sell", 1)]
    hist = [s for s in list_signals(USER, code) if s["signal_type"] == "sell" and s["signal_level"] == 1]
    assert len(hist) == 2
    _ok("중복 방지", "같은 날 재실행 0건, episode 2회")


def test_10_watchlist() -> None:
    _clear_db()
    a, b = "000101", "000102"
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 9, 7)]
    ma3s = [100.0, 100.0, 89.0]
    for code, name in ((a, "관심"), (b, "비관심")):
        upsert_stock(code, name, "KOSPI")
        upsert_prices(
            pd.DataFrame(
                {
                    "stock_code": code,
                    "date": days,
                    "close": ma3s,
                    "volume": [1, 1, 1],
                    "trading_value": [1, 1, 1],
                }
            )
        )
        save_ma3(code, pd.DataFrame({"date": days, "ma3": ma3s}))
    save_settings(USER, {"phone_number": "01033334444", "buy_alert": True, "sell_alert": True})
    add_watchlist(USER, a, True)

    out_a = signals_for_sms(a, MONDAY, user_id=USER)
    out_b = signals_for_sms(b, MONDAY, user_id=USER)
    hist_a = list_signals(USER, a)
    hist_b = list_signals(USER, b)
    assert hist_a and hist_b
    assert out_a and out_a[0]["sms_sent"] is True
    assert out_b and out_b[0]["sms_sent"] is False
    NOTES["관심종목"].append(
        "현재 설계: TOP20(테스트에선 두 종목) 신호를 signal_history에 모두 기록하고, SMS만 관심종목 필터"
    )
    NOTES["관심종목"].append(
        "record_for_sms는 비관심 종목에도 notify_signals를 호출하며, notify 내부에서 SMS를 건너뜀"
    )
    _ok("관심종목", f"A sms_sent={out_a[0]['sms_sent']} B sms_sent={out_b[0]['sms_sent']} history A/B 모두 존재")


def test_11_settings() -> None:
    _clear_db()
    saved = save_settings(
        USER,
        {
            "phone_number": "01055556666",
            "x1": 10,
            "x2": 20,
            "y1": 10,
            "y2": 20,
            "buy_alert": True,
            "sell_alert": True,
        },
    )
    got = get_settings(USER)
    assert got["phone_number"] == "01055556666"
    assert got["x1"] == 10 and got["x2"] == 20
    upsert_stock("005930", "삼성전자", "KOSPI")
    add_watchlist(USER, "005930", True)
    sig = {
        "stock_code": "005930",
        "signal_type": "buy",
        "signal_level": 1,
        "ma3": 1,
        "signal_date": MONDAY,
        "reference_price": 1,
        "threshold": 1,
    }
    assert notify_signals(USER, [sig])
    save_settings(USER, {"buy_alert": False})
    assert notify_signals(USER, [sig]) == []
    save_settings(USER, {"buy_alert": True, "sell_alert": False})
    sell = {**sig, "signal_type": "sell"}
    assert notify_signals(USER, [sell]) == []
    save_settings("nophone", {"buy_alert": True, "sell_alert": True, "phone_number": None})
    add_watchlist("nophone", "005930", True)
    save_settings("nophone", {"phone_number": None})
    empty = get_settings("nophone")
    with connect() as conn:
        conn.execute("UPDATE user_settings SET phone_number=NULL WHERE user_id=?", ("nophone",))
    assert notify_signals("nophone", [sig]) == []
    _ok("사용자 설정", "읽기·매수/매도 OFF·번호 없음 필터")


def test_12_sms() -> None:
    _clear_db()
    upsert_stock("001111", "관심", "KOSPI")
    upsert_stock("002222", "비관심", "KOSPI")
    save_settings(USER, {"phone_number": "01077778888", "buy_alert": True, "sell_alert": True})
    add_watchlist(USER, "001111", True)
    calls: list[tuple] = []

    def spy(to, text, **kwargs):
        calls.append((to, text, kwargs))
        return {"status": "test", "to": to, "text": text, "provider": "test"}

    watch = {
        "stock_code": "001111",
        "signal_type": "buy",
        "signal_level": 1,
        "ma3": 10,
        "signal_date": MONDAY,
        "reference_price": 1,
        "threshold": 1,
    }
    other = {**watch, "stock_code": "002222"}
    with patch("notify.send_sms", side_effect=spy):
        assert notify_signals(USER, [watch])
        n_after_watch = len(calls)
        assert notify_signals(USER, [other]) == []
        assert len(calls) == n_after_watch
        save_settings(USER, {"buy_alert": False})
        assert notify_signals(USER, [watch]) == []
        save_settings(USER, {"buy_alert": True, "sell_alert": False})
        sell = {**watch, "signal_type": "sell"}
        assert notify_signals(USER, [sell]) == []
    _ok("SMS 필터", "test 모드, 관심+알림+번호만 send_sms")


def test_13_fastapi() -> None:
    _clear_db()
    upsert_stock("005930", "삼성전자", "KOSPI")
    days = [MONDAY - timedelta(days=i) for i in range(5, -1, -1)]
    _seed_prices("005930", days, [100, 110, 120, 130, 140, 150], "삼성전자", "KOSPI")
    save_top20(_fake_top20_df(MONDAY, [("005930", "삼성전자", "KOSPI")]))
    client = TestClient(app_module.app)
    assert client.get("/top20").status_code == 200
    assert client.get("/stocks/005930", headers={"X-User-Id": USER}).status_code == 200
    chart = client.get("/stocks/005930/chart", headers={"X-User-Id": USER})
    assert chart.status_code == 200
    assert client.get("/watchlist", headers={"X-User-Id": USER}).status_code == 200
    posted = client.post(
        "/watchlist",
        headers={"X-User-Id": USER},
        json={"stock_code": "005930", "alert_enabled": True},
    )
    assert posted.status_code == 200
    deleted = client.delete("/watchlist/005930", headers={"X-User-Id": USER})
    assert deleted.status_code == 200
    assert client.get("/signals", headers={"X-User-Id": USER}).status_code == 200
    assert client.get("/settings", headers={"X-User-Id": USER}).status_code == 200
    put = client.put(
        "/settings",
        headers={"X-User-Id": USER},
        json={"phone_number": "01099990000", "x1": 10, "x2": 20, "y1": 10, "y2": 20},
    )
    assert put.status_code == 200
    os.environ["API_SECRET_KEY"] = "secret-key"
    try:
        assert client.post("/jobs/run").status_code == 401
        assert client.post("/jobs/run", headers={"X-API-Key": "wrong"}).status_code == 401
        with patch("app.run_daily_job", return_value={"ok": True}):
            ok = client.post("/jobs/run", headers={"X-API-Key": "secret-key"})
        assert ok.status_code == 200
        assert client.post("/sms/test", json={"phone_number": "01012345678"}).status_code == 401
        sms_ok = client.post(
            "/sms/test",
            headers={"X-API-Key": "secret-key", "X-User-Id": USER},
            json={"phone_number": "01012345678"},
        )
        assert sms_ok.status_code == 200
        assert sms_ok.json()["status"] == "test"
    finally:
        os.environ.pop("API_SECRET_KEY", None)
    _ok("FastAPI", "목록 API 200, 보호 엔드포인트 401/200")
    _ok("인증", "키 없음·오키 401, 정상 키 200")


def test_14_chart() -> None:
    _clear_db()
    code = "005930"
    old = [date(2026, 3, 10) + timedelta(days=i) for i in range(3)]
    new = [date(2026, 7, 1), date(2026, 8, 3), date(2026, 8, 4), date(2026, 9, 7)]
    days = old + new
    closes = [50.0, 50.0, 8_000.0, 100.0, 100.0, 100.0, 89.0]
    _seed_prices(code, days, closes, "삼성전자")
    save_settings(USER, {"x1": 10, "x2": 20, "y1": 10, "y2": 20})
    client = TestClient(app_module.app)
    with patch("signals.latest_trading_day", return_value=MONDAY):
        res = client.get(f"/stocks/{code}/chart", headers={"X-User-Id": USER})
    assert res.status_code == 200
    body = res.json()
    point_dates = {p["date"] for p in body["points"]}
    assert "2026-03-10" in point_dates
    assert "2026-09-07" in point_dates
    high = find_3month_ma_high(code, MONDAY)
    low = find_3month_ma_low(code, MONDAY)
    assert high["date"] >= date(2026, 6, 7)
    assert body["ma_high"] == high["value"]
    assert body["ma_low"] == low["value"]
    for m in body["markers"]:
        assert m["signal_date"] >= "2026-06-07"
    _ok("차트", f"points={len(body['points'])} markers={len(body['markers'])} H/L=3개월")


def test_15_daily_job() -> None:
    _clear_db()
    code = "005930"
    days = [date(2026, 8, 3), date(2026, 8, 4), MONDAY]
    _seed_prices(code, days, [100, 100, 89], "삼성전자")
    save_settings(USER, {"phone_number": "01012121212"})
    add_watchlist(USER, code, True)
    save_top20(_fake_top20_df(PREV_MON, [(code, "삼성전자", "KOSPI")]))

    with (
        patch("jobs.is_trading_day", return_value=True),
        patch("jobs.latest_trading_day", return_value=MONDAY),
        patch("jobs.is_first_trading_day_of_week", return_value=True),
        patch("jobs.first_trading_day_of_week", return_value=MONDAY),
        patch("jobs.select_top20", return_value=_fake_top20_df(MONDAY, [(code, "삼성전자", "KOSPI")])),
        patch("jobs.trading_days", return_value=[MONDAY]),
        patch("jobs.subtract_months", return_value=MONDAY),
        patch("jobs.get_missing_dates", return_value=[]),
        patch("jobs.fetch_missing_prices") as fetch,
        patch("jobs.save_prices", return_value=0),
        patch("jobs.update_ma3", return_value=3),
    ):
        result = run_daily_job(MONDAY, force=True)
    assert result["ok"] is True
    assert result["top20"]["action"] == "reselected"
    fetch.assert_not_called()
    assert jobs.LOG_PATH.is_file()
    last = json.loads(jobs.LOG_PATH.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["ok"] is True
    _ok("일일 잡", f"action={result['top20']['action']} signals={result['signals']}")


def test_16_failures() -> None:
    _clear_db()
    save_top20(_fake_top20_df(MONDAY, [("005930", "삼성전자", "KOSPI")]))

    with (
        patch("jobs.is_trading_day", return_value=True),
        patch("jobs.latest_trading_day", return_value=MONDAY),
        patch("jobs.is_first_trading_day_of_week", return_value=False),
        patch("jobs.first_trading_day_of_week", return_value=MONDAY),
        patch("jobs.trading_days", return_value=[MONDAY]),
        patch("jobs.subtract_months", return_value=MONDAY),
        patch("jobs.get_missing_dates", return_value=[MONDAY]),
        patch("jobs.fetch_missing_prices", side_effect=RuntimeError("price API down")),
        patch("jobs.update_ma3", return_value=0),
        patch("jobs.list_user_ids", return_value=["local"]),
        patch("jobs.get_settings", return_value={"x1": 10, "x2": 20, "y1": 10, "y2": 20}),
        patch("jobs.signals_for_sms", return_value=[]),
    ):
        priced = run_daily_job(MONDAY, force=True)
    assert priced["status"] == "partial"
    assert any("price API down" in e.get("message", "") for e in priced["errors"])
    log = jobs.LOG_PATH.read_text(encoding="utf-8")
    assert "price API down" in log

    with (
        patch("jobs.is_trading_day", return_value=True),
        patch("jobs.latest_trading_day", return_value=TUESDAY),
        patch("jobs.is_first_trading_day_of_week", return_value=False),
        patch("jobs.first_trading_day_of_week", return_value=MONDAY),
        patch("jobs.load_latest_top20", return_value=None),
        patch("jobs.select_top20", side_effect=RuntimeError("top20 failed")),
        patch("jobs._update_prices") as prices,
        patch("jobs._scan_signals") as signals,
    ):
        top = run_daily_job(TUESDAY, force=True)
    assert top["ok"] is False and top["status"] == "error"
    assert top["prices"] == "skipped"
    prices.assert_not_called()
    signals.assert_not_called()
    assert "top20 failed" in jobs.LOG_PATH.read_text(encoding="utf-8")

    upsert_stock("001111", "A", "KOSPI")
    upsert_stock("002222", "B", "KOSPI")
    save_settings(USER, {"phone_number": "01000001111"})
    add_watchlist(USER, "001111", True)
    add_watchlist(USER, "002222", True)
    n = {"i": 0}

    def flaky(to, text, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("sms fail")
        return {"status": "test", "to": to, "text": text, "provider": "test"}

    sigs = [
        {
            "stock_code": "001111",
            "signal_type": "buy",
            "signal_level": 1,
            "ma3": 1,
            "signal_date": MONDAY,
            "reference_price": 1,
            "threshold": 1,
        },
        {
            "stock_code": "002222",
            "signal_type": "buy",
            "signal_level": 1,
            "ma3": 1,
            "signal_date": MONDAY,
            "reference_price": 1,
            "threshold": 1,
        },
    ]
    with patch("notify.send_sms", side_effect=flaky):
        delivered = notify_signals(USER, sigs)
    assert len(delivered) == 1
    _ok("오류 처리", "가격 partial+로그, TOP20 중단+로그, SMS 다음 종목 계속")


def test_17_api_counts() -> None:
    counts = defaultdict(int)
    session = MONDAY
    cal = [session - timedelta(days=i) for i in range(4, -1, -1)]

    def td(start, end):
        counts["trading_days"] += 1
        return [d for d in cal if start <= d <= end] or cal[-1:]

    def listing(day):
        counts["fetch_listing_on"] += 1
        return _listing_frame(
            day,
            [
                {
                    "stock_code": f"{i:06d}",
                    "stock_name": f"S{i}",
                    "market": "KOSPI" if i < 15 else "KOSDAQ",
                    "trading_value": (30 - i) * 1_000,
                }
                for i in range(1, 25)
            ],
        )

    def ltd(as_of=None):
        counts["latest_trading_day"] += 1
        return session

    with (
        patch("top20.latest_trading_day", side_effect=ltd),
        patch("top20.trading_days", side_effect=td),
        patch("market_data.trading_days", side_effect=td),
        patch("top20.fetch_trading_value_history", wraps=None) as hist,
    ):
        def fake_hist(start, end, progress=None):
            counts["TOP20_history"] += 1
            days = td(start, end)
            return pd.concat([listing(d) for d in days], ignore_index=True)

        hist.side_effect = fake_hist
        with patch("top20.subtract_months", return_value=cal[0]):
            select_top20(as_of=session)
    API_COUNTS["top20_select"] = dict(counts)

    counts2 = defaultdict(int)
    incremental.clear_listing_cache()
    code = "005930"
    _clear_db()
    upsert_stock(code, "삼성전자", "KOSPI")
    have = cal[:-1]
    upsert_prices(
        pd.DataFrame(
            {
                "stock_code": code,
                "date": have,
                "close": [1] * len(have),
                "volume": [1] * len(have),
                "trading_value": [1] * len(have),
            }
        )
    )

    def td2(start, end):
        counts2["trading_days"] += 1
        return [d for d in cal if start <= d <= end]

    def prices(stock_code, start_s, end_s):
        counts2["price_api"] += 1
        return pd.DataFrame(
            [{"date": cal[-1], "open": 1, "high": 1, "low": 1, "close": 2, "volume": 1}]
        )

    def listing2(day):
        counts2["fetch_listing_on"] += 1
        raise RuntimeError("x")

    with (
        patch("incremental.latest_trading_day", return_value=session),
        patch("incremental.subtract_months", return_value=cal[0]),
        patch("incremental.trading_days", side_effect=td2),
        patch("incremental.fetch_prices", side_effect=prices),
        patch("incremental.fetch_listing_on", side_effect=listing2),
    ):
        fetch_missing_prices(code, session)
        save_prices()
        fetch_missing_prices(code, session)
    API_COUNTS["daily_update"] = dict(counts2)

    counts3 = defaultdict(int)
    incremental.clear_listing_cache()
    _clear_db()

    def td3(start, end):
        counts3["trading_days"] += 1
        return cal

    def prices3(stock_code, start_s, end_s):
        counts3["price_api"] += 1
        return pd.DataFrame(
            [
                {"date": d, "open": 1, "high": 1, "low": 1, "close": 10 + i, "volume": 1}
                for i, d in enumerate(cal)
            ]
        )

    def listing3(day):
        counts3["fetch_listing_on"] += 1
        raise RuntimeError("x")

    with (
        patch("incremental.latest_trading_day", return_value=session),
        patch("incremental.subtract_months", return_value=cal[0]),
        patch("incremental.trading_days", side_effect=td3),
        patch("incremental.fetch_prices", side_effect=prices3),
        patch("incremental.fetch_listing_on", side_effect=listing3),
    ):
        fetch_missing_prices("099993", session)
        save_prices()
        fetch_missing_prices("099993", session)
    API_COUNTS["new_stock"] = dict(counts3)
    API_COUNTS["rerun_after_fill"] = {
        "price_api_second_call": counts3["price_api"] - 1,
        "trading_days": counts3["trading_days"],
        "fetch_listing_on": counts3["fetch_listing_on"],
    }
    NOTES["API 효율성"].append(
        f"TOP20 select: {API_COUNTS['top20_select']}; "
        f"일반 증분: {API_COUNTS['daily_update']}; "
        f"신규: {API_COUNTS['new_stock']}; "
        f"재실행: {API_COUNTS['rerun_after_fill']}"
    )
    NOTES["API 효율성"].append(
        "listing은 날짜당 1회 캐시. 일일 잡은 거래일 캘린더를 종목 간에 재사용."
    )
    incremental.clear_listing_cache()
    if counts3["fetch_listing_on"] > len(cal):
        _fail(
            "API 효율성",
            f"listing={counts3['fetch_listing_on']} unique_days={len(cal)}",
        )
    else:
        _ok("API 효율성", str(dict(API_COUNTS)))


def main() -> int:
    tests = [
        ("TOP20", test_01_top20),
        ("주간 재선정", test_02_weekly),
        ("6개월 데이터", test_03_six_month_new),
        ("증분 업데이트", test_04_incremental),
        ("MA3", test_05_ma3),
        ("3개월 H/L", test_06_hl),
        ("매수/매도 기준", test_07_levels),
        ("Cross", test_08_cross),
        ("중복 방지", test_09_duplicate),
        ("관심종목", test_10_watchlist),
        ("사용자 설정", test_11_settings),
        ("SMS 필터", test_12_sms),
        ("FastAPI", test_13_fastapi),
        ("차트", test_14_chart),
        ("일일 잡", test_15_daily_job),
        ("오류 처리", test_16_failures),
        ("API 효율성", test_17_api_counts),
    ]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            _fail(name, f"{exc}")
            traceback.print_exc()
    print("\n=== 통합 테스트 요약 ===")
    print("| 항목 | 결과 | 핵심 문제 |")
    print("| --- | --- | --- |")
    seen = set()
    rows = []
    for item, status, note in RESULTS:
        if item in seen and item != "인증":
            continue
        seen.add(item)
        rows.append((item, status, note))
        print(f"| {item} | {status} | {note} |")
    print("\n증분 관찰:", json.dumps(SPAN_OBSERVE, ensure_ascii=False, default=str))
    print("API 카운트:", json.dumps(API_COUNTS, ensure_ascii=False, default=str))
    for k, v in NOTES.items():
        print(f"NOTE[{k}]: {v}")
    failed = any(s == "FAIL" for _, s, _ in RESULTS)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print(f"테스트 DB 삭제: {TEST_ROOT}")
    raise SystemExit(code)
