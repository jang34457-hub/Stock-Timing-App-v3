"""STEP 18-3: production SMS path — phone, test/live, BUY/SELL, dedupe, failure log, retry.

Does not send a real Solapi message unless STA_LIVE_SMS=1 and credentials exist.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SMS_PROVIDER", "test")
os.environ["APP_ENV"] = "development"
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["CATCHUP_ON_START"] = "0"
os.environ["CATCHUP_ON_READ"] = "0"
os.environ["SMS_RETRY_MAX"] = "3"
os.environ["SMS_RETRY_BACKOFF_SEC"] = "0"
os.environ.pop("API_SECRET_KEY", None)
os.environ["STA_FORCE_SQLITE"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sms as sms_mod
from fastapi.testclient import TestClient

from app import app
from notify import notify_signals, take_sms_errors
from prices_db import add_watchlist, delete_signals, last_signal, save_settings, upsert_stock
from signals import is_duplicate_condition, record_for_sms
from sms import format_signal_sms, normalize_phone, send_sms


def _ok(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    extra = f" ({detail})" if detail else ""
    print(f"  [{status}] {name}{extra}")
    if not cond:
        raise SystemExit(1)


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _log_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def main() -> None:
    print("STEP 18-3 verify_sms_ops")
    tmp = Path(tempfile.mkdtemp()) / "sms_log.jsonl"
    sms_mod.LOG_PATH = tmp

    _ok("phone 010-1234-5678", normalize_phone("010-1234-5678") == "01012345678")
    _ok("phone +82", normalize_phone("+82 10-9876-5432") == "01098765432")
    try:
        normalize_phone("123")
        _ok("invalid phone rejected", False)
    except ValueError:
        _ok("invalid phone rejected", True)

    saved = save_settings("sms-ops", {"phone_number": "010-2222-3333"})
    _ok("settings store digits only", saved["phone_number"] == "01022223333")

    client = TestClient(app)
    ping = client.post(
        "/sms/test",
        headers={"X-User-Id": "sms-ops"},
        json={},
    )
    _ok("test SMS uses settings phone", ping.status_code == 200 and ping.json()["status"] == "test")
    _ok("test SMS not live by default", ping.json()["provider"] == "test")

    buy_text = format_signal_sms(
        "삼성전자", {"signal_type": "buy", "signal_level": 1, "ma3": 258500}
    )
    sell_text = format_signal_sms(
        "삼성전자", {"signal_type": "SELL", "signal_level": 2, "ma3": 90000}
    )
    _ok("BUY 문구", "매수1" in buy_text)
    _ok("SELL 문구", "매도2" in sell_text)

    upsert_stock("005930", "삼성전자", "KOSPI")
    add_watchlist("sms-ops", "005930", True)
    buy_sig = {
        "stock_code": "005930",
        "signal_type": "buy",
        "signal_level": 1,
        "ma3": 258500,
        "signal_date": date(2026, 9, 7),
        "reference_price": 1,
        "threshold": 1,
    }
    sell_sig = {**buy_sig, "signal_type": "sell", "signal_level": 2, "ma3": 90000}
    sent_buy = notify_signals("sms-ops", [buy_sig])
    sent_sell = notify_signals("sms-ops", [sell_sig])
    _ok("BUY 신호 SMS", bool(sent_buy) and "매수1" in sent_buy[0]["sms"]["text"])
    _ok("SELL 신호 SMS", bool(sent_sell) and "매도2" in sent_sell[0]["sms"]["text"])

    delete_signals("009999")
    upsert_stock("009999", "재시도", "KOSPI")
    add_watchlist("sms-retry", "009999", True)
    save_settings("sms-retry", {"phone_number": "01044445555"})
    fail_sig = {
        "stock_code": "009999",
        "signal_type": "buy",
        "signal_level": 1,
        "ma3": 10,
        "signal_date": date(2026, 9, 7),
        "reference_price": 1,
        "threshold": 1,
    }
    n = {"i": 0}

    def fail_once(to, text, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("solapi down")
        return {"status": "test", "to": to, "text": text, "provider": "test"}

    take_sms_errors()
    with patch("notify.send_sms", side_effect=fail_once):
        first = record_for_sms([fail_sig], user_id="sms-retry")
        errs = take_sms_errors()
        _ok("SMS 실패해도 history 기록", bool(first) and first[0]["sms_sent"] is False)
        _ok("실패 로그(notify)", len(errs) == 1 and errs[0]["stage"] == "sms")
        last = last_signal("sms-retry", "009999", "buy", 1)
        _ok("sms_sent=0 이면 중복 아님", last is not None and last["sms_sent"] is False)
        _ok(
            "is_duplicate 실패 건은 재시도 허용",
            is_duplicate_condition(fail_sig, user_id="sms-retry") is False,
        )
        second = record_for_sms([fail_sig], user_id="sms-retry")
        last2 = last_signal("sms-retry", "009999", "buy", 1)
        _ok("재시도 후 발송", bool(second) and second[0]["sms_sent"] is True)
        _ok("재시도 후 sms_sent=1", bool(last2) and last2["sms_sent"] is True)
        third = record_for_sms([fail_sig], user_id="sms-retry")
        _ok("성공 후 중복 차단", third == [])

    os.environ["SMS_PROVIDER"] = "solapi"
    os.environ["SOLAPI_API_KEY"] = "key"
    os.environ["SOLAPI_API_SECRET"] = "secret"
    os.environ["SOLAPI_SENDER"] = "01000000000"
    calls: list[int] = []

    def flaky_http(*_a, **_k):
        calls.append(1)
        if len(calls) < 3:
            return _Resp(503, {"error": "unavailable"})
        return _Resp(200, {"messageId": "mid-ok"})

    with patch("sms.requests.post", side_effect=flaky_http):
        out = send_sms("01066667777", "retry-ok", test=False)
    _ok("5xx 재시도 후 성공", out["status"] == "sent" and out["attempts"] == 3 and len(calls) == 3)

    calls.clear()

    def hard_400(*_a, **_k):
        calls.append(1)
        return _Resp(400, {"error": "bad"})

    try:
        with patch("sms.requests.post", side_effect=hard_400):
            send_sms("01066667777", "no-retry", test=False)
        _ok("4xx 재시도 안 함", False)
    except RuntimeError:
        _ok("4xx 재시도 안 함", len(calls) == 1)

    rows = _log_rows(tmp)
    error_rows = [r for r in rows if r.get("status") == "error"]
    _ok("sms_log.jsonl 실패 기록", len(error_rows) >= 2)

    live = os.getenv("STA_LIVE_SMS", "").strip().lower() in {"1", "true", "yes"}
    creds = all(
        os.getenv(k, "").strip()
        for k in ("SOLAPI_API_KEY", "SOLAPI_API_SECRET", "SOLAPI_SENDER")
    )
    # dummy key/secret from this test are not real — require explicit live + non-dummy
    dummy = os.getenv("SOLAPI_API_KEY", "") == "key"
    if live and creds and not dummy:
        live_body = client.post(
            "/sms/test",
            headers={"X-User-Id": "sms-ops"},
            json={"live": True, "text": "[Stock Timing] STEP 18-3 live test"},
        )
        _ok("live test SMS", live_body.status_code == 200 and live_body.json().get("status") in {"sent", "test"})
    else:
        _ok("live Solapi skipped (set STA_LIVE_SMS=1 with real keys to send)", True)

    os.environ["SMS_PROVIDER"] = "test"
    delete_signals("009999")
    print("All STEP 18-3 checks passed.")


if __name__ == "__main__":
    main()
