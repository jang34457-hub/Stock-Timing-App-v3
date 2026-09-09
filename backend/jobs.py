"""장 마감 후 일일 파이프라인 · 주 첫 거래일 TOP20 재선정."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from incremental import (
    PRICE_LOOKBACK_MONTHS,
    clear_listing_cache,
    fetch_missing_prices,
    get_last_date,
    get_missing_dates,
    save_prices,
    update_ma3,
)
from market_data import is_trading_day, latest_trading_day, subtract_months, trading_days
from notify import take_sms_errors
from prices_db import get_settings, list_user_ids, load_latest_top20, save_top20
from signals import signals_for_sms
from top20 import first_trading_day_of_week, is_first_trading_day_of_week, select_top20

KST = ZoneInfo("Asia/Seoul")
LOG_PATH = Path(__file__).resolve().parent / "data" / "job_log.jsonl"

_lock = threading.Lock()
_last_run: dict | None = None


def today_kst(now: datetime | None = None) -> date:
    return (now or datetime.now(KST)).date()


def last_run() -> dict | None:
    return _last_run


def data_is_stale(session: date | None = None) -> bool:
    """TOP20 시세가 최근 거래일보다 뒤처졌거나, 이번 주 선정이 없으면 True."""
    end = session or latest_trading_day()
    cached = load_latest_top20()
    items = (cached or {}).get("items") or []
    if len(items) < 20:
        return True
    if _as_date(cached.get("selection_date")) < first_trading_day_of_week(end):
        return True
    for item in items:
        last = get_last_date(item["stock_code"])
        if last is None or last < end:
            return True
    return False


def catch_up_if_stale(*, wait: bool = True) -> dict:
    """날짜가 바뀐 뒤 접속하면 빠진 장 마감 데이터를 이어서 채운다.

    스케줄러가 16시에 못 돌았거나 서버가 꺼져 있어도, 최신 거래일까지 증분 업데이트한다.
    """
    session = latest_trading_day()
    if not data_is_stale(session):
        return {
            "ok": True,
            "job": "catch_up",
            "status": "skipped",
            "skipped": "up_to_date",
            "session": session.isoformat(),
        }
    acquired = _lock.acquire(timeout=600) if wait else _lock.acquire(blocking=False)
    if not acquired:
        return {
            "ok": False,
            "job": "catch_up",
            "status": "skipped",
            "skipped": "already_running",
            "session": session.isoformat(),
        }
    try:
        session = latest_trading_day()
        if not data_is_stale(session):
            return {
                "ok": True,
                "job": "catch_up",
                "status": "skipped",
                "skipped": "up_to_date",
                "session": session.isoformat(),
            }
        result = _run_daily_job(session, force=True)
        result["job"] = "catch_up"
        _store(result)
        return result
    finally:
        _lock.release()


def run_daily_job(as_of: date | None = None, *, force: bool = False, wait: bool = False) -> dict:
    """매일: 장 마감 → TOP20 확인 → 결측만 수집 → MA3 → 신호 → 관심종목 SMS.

    해당 주의 첫 거래일이면 TOP20을 다시 선정한다. 휴장·주말은 건너뛴다.
    """
    if not (_lock.acquire(timeout=600) if wait else _lock.acquire(blocking=False)):
        return {"ok": False, "job": "daily", "status": "skipped", "skipped": "already_running"}
    try:
        result = _run_daily_job(as_of, force=force)
        _store(result)
        return result
    finally:
        _lock.release()


def _run_daily_job(as_of: date | None, *, force: bool) -> dict:
    today = as_of or today_kst()
    started = datetime.now(KST).isoformat()
    errors: list[dict] = []
    clear_listing_cache()

    if not force and not is_trading_day(today):
        return _finish(
            {
                "ok": True,
                "job": "daily",
                "status": "skipped",
                "skipped": "holiday_or_weekend",
                "today": today.isoformat(),
                "top20": "skipped",
                "prices": "skipped",
                "signals": "skipped",
                "sms": "skipped",
                "error_count": 0,
                "errors": [],
                "started_at": started,
            }
        )

    try:
        session = latest_trading_day(today)
        first_session = is_first_trading_day_of_week(session)
    except Exception as exc:
        errors.append(_error("calendar", exc))
        return _finish(
            {
                "ok": False,
                "job": "daily",
                "status": "error",
                "today": today.isoformat(),
                "top20": "skipped",
                "prices": "skipped",
                "signals": "skipped",
                "sms": "skipped",
                "error_count": len(errors),
                "errors": errors,
                "started_at": started,
            }
        )

    top20_step = None
    try:
        top20_step = _ensure_top20(session, first_session=first_session)
        top20_status = "success"
    except Exception as exc:
        errors.append(_error("top20", exc))
        return _finish(
            {
                "ok": False,
                "job": "daily",
                "status": "error",
                "today": today.isoformat(),
                "session": session.isoformat(),
                "holiday": False,
                "first_trading_day_of_week": first_session,
                "top20": "error",
                "prices": "skipped",
                "signals": "skipped",
                "sms": "skipped",
                "error_count": len(errors),
                "errors": errors,
                "started_at": started,
            }
        )

    codes = [item["stock_code"] for item in top20_step["items"]]
    prices_step = _update_prices(codes, session, errors)
    prices_status = _stage_status(prices_step.get("failed", 0), prices_step.get("stocks", 0))

    users = list_user_ids()
    signals_step = _scan_signals(codes, session, users, errors)
    signals_status = _stage_status(signals_step.get("failed", 0), len(codes) * max(len(users), 1))
    sms_failed = sum(1 for err in errors if err.get("stage") == "sms")
    sms_status = "error" if sms_failed and signals_step.get("sms_sent", 0) == 0 else (
        "partial" if sms_failed else "success"
    )

    overall = _overall_status([top20_status, prices_status, signals_status, sms_status])
    return _finish(
        {
            "ok": overall != "error",
            "job": "daily",
            "status": overall,
            "today": today.isoformat(),
            "session": session.isoformat(),
            "holiday": False,
            "first_trading_day_of_week": first_session,
            "top20": top20_step,
            "top20_status": top20_status,
            "prices": prices_step,
            "prices_status": prices_status,
            "signals": signals_step,
            "signals_status": signals_status,
            "sms": sms_status,
            "error_count": len(errors),
            "errors": errors,
            "started_at": started,
        }
    )


def _ensure_top20(session: date, *, first_session: bool) -> dict:
    cached = load_latest_top20()
    week_start = first_trading_day_of_week(session)
    if (
        cached is None
        or _as_date(cached.get("selection_date")) < week_start
        or len(cached.get("items") or []) < 20
    ):
        snapshot = select_top20(as_of=session)
        save_top20(snapshot)
        saved = load_latest_top20() or {"items": []}
        had_snapshot = cached is not None
        return {
            "action": "reselected" if first_session or had_snapshot else "seeded",
            "selection_date": saved.get("selection_date"),
            "count": len(saved.get("items", [])),
            "items": saved.get("items", []),
        }
    return {
        "action": "kept",
        "selection_date": cached.get("selection_date"),
        "count": len(cached.get("items", [])),
        "items": cached.get("items", []),
    }


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _update_prices(codes: list[str], session: date, errors: list[dict] | None = None) -> dict:
    if errors is None:
        errors = []
    stocks: list[dict] = []
    fetched_rows = 0
    saved_rows = 0
    failed = 0
    try:
        calendar = trading_days(subtract_months(session, PRICE_LOOKBACK_MONTHS), session)
    except Exception as exc:
        errors.append(_error("price_update", exc))
        return {
            "stocks": len(codes),
            "failed": len(codes),
            "fetched_rows": 0,
            "saved_rows": 0,
            "items": [{"stock_code": code, "status": "error"} for code in codes],
        }
    for code in codes:
        try:
            missing = get_missing_dates(code, session, calendar=calendar)
            fetched = 0
            saved = 0
            if missing:
                rows = fetch_missing_prices(
                    code, session, calendar=calendar, missing=missing
                )
                fetched = 0 if rows is None else len(rows)
                saved = save_prices()
            ma3_rows = update_ma3(code)
            fetched_rows += fetched
            saved_rows += saved
            stocks.append(
                {
                    "stock_code": code,
                    "status": "success",
                    "missing_days": len(missing),
                    "fetched": fetched,
                    "saved": saved,
                    "ma3_rows": ma3_rows,
                }
            )
        except Exception as exc:
            failed += 1
            errors.append(_error("price_update", exc, stock_code=code))
            stocks.append({"stock_code": code, "status": "error"})
    return {
        "stocks": len(stocks),
        "failed": failed,
        "fetched_rows": fetched_rows,
        "saved_rows": saved_rows,
        "items": stocks,
    }


def _scan_signals(
    codes: list[str],
    session: date,
    users: list[str],
    errors: list[dict] | None = None,
) -> dict:
    if errors is None:
        errors = []
    recorded: list[dict] = []
    sms_sent = 0
    failed = 0
    for user_id in users:
        try:
            settings = get_settings(user_id)
        except Exception as exc:
            failed += 1
            errors.append(_error("signals", exc))
            continue
        for code in codes:
            try:
                batch = signals_for_sms(
                    code,
                    session,
                    user_id=user_id,
                    x1=float(settings["x1"]),
                    x2=float(settings["x2"]),
                    y1=float(settings["y1"]),
                    y2=float(settings["y2"]),
                )
            except Exception as exc:
                failed += 1
                errors.append(_error("signals", exc, stock_code=code))
                continue
            for item in take_sms_errors():
                errors.append(item)
            for item in batch:
                recorded.append(
                    {
                        "user_id": user_id,
                        "stock_code": item["stock_code"],
                        "signal_type": item["signal_type"],
                        "signal_level": item["signal_level"],
                        "sms_sent": item.get("sms_sent", False),
                    }
                )
                if item.get("sms_sent"):
                    sms_sent += 1
    return {
        "users": len(users),
        "failed": failed,
        "new_signals": len(recorded),
        "sms_sent": sms_sent,
        "items": recorded,
    }


def _error(stage: str, exc: BaseException, stock_code: str | None = None) -> dict:
    item = {
        "job": "daily",
        "stage": stage,
        "status": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    if stock_code:
        item["stock_code"] = stock_code
    return item


def _stage_status(failed: int, total: int) -> str:
    if total <= 0:
        return "success"
    if failed <= 0:
        return "success"
    if failed >= total:
        return "error"
    return "partial"


def _overall_status(parts: list[str]) -> str:
    if any(part == "error" for part in parts if part != "skipped"):
        if any(part in {"success", "partial"} for part in parts):
            return "partial"
        return "error"
    if any(part == "partial" for part in parts):
        return "partial"
    return "success"


def _finish(result: dict) -> dict:
    result["finished_at"] = datetime.now(KST).isoformat()
    return result


def _store(result: dict) -> None:
    global _last_run
    _last_run = result
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        result.setdefault("errors", []).append(_error("job_log", exc))
        result["error_count"] = len(result["errors"])
        result["status"] = "partial" if result.get("status") == "success" else result.get("status", "error")
        try:
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
