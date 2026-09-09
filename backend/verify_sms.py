"""SMS 테스트 모드: 실제 발송 없이 로그·API를 확인한다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["SMS_PROVIDER"] = "test"
os.environ["APP_ENV"] = "development"
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["CATCHUP_ON_START"] = "0"
os.environ["CATCHUP_ON_READ"] = "0"
os.environ.pop("API_SECRET_KEY", None)

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import app
from notify import notify_signals
from prices_db import add_watchlist, upsert_stock
from sms import LOG_PATH, format_signal_sms, send_sms

os.environ["APP_ENV"] = "development"
os.environ.pop("API_SECRET_KEY", None)

USER = {"X-User-Id": "sms-test-empty"}
USER_OK = {"X-User-Id": "sms-test"}


def _ok(label: str, detail: str) -> None:
    print(f"[OK] {label}: {detail}")


def main() -> None:
    client = TestClient(app)

    missing = client.post("/sms/test", headers=USER, json={})
    assert missing.status_code == 400, missing.text
    _ok("POST /sms/test 번호 없음", missing.json()["detail"])

    bad = client.post("/sms/test", headers=USER, json={"phone_number": "123"})
    assert bad.status_code == 400, bad.text
    _ok("POST /sms/test 잘못된 번호", bad.json()["detail"])

    saved = client.put(
        "/settings",
        headers=USER_OK,
        json={"phone_number": "01012345678"},
    )
    assert saved.status_code == 200, saved.text

    ping = client.post("/sms/test", headers=USER_OK, json={})
    assert ping.status_code == 200, ping.text
    body = ping.json()
    assert body["ok"] is True
    assert body["status"] == "test"
    assert body["to"] == "01012345678"
    assert "테스트 SMS" in body["text"]
    _ok("POST /sms/test", f"{body['to']} {body['message_id']}")

    log_line = LOG_PATH.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "01012345678" in log_line
    _ok("sms_log.jsonl", log_line[:120])

    direct = send_sms("010-8765-4321", "[Stock Timing] 직접 호출 테스트", test=True)
    assert direct["to"] == "01087654321"
    _ok("send_sms()", direct["message_id"])

    upsert_stock("005930", "삼성전자", "KOSPI")
    add_watchlist("sms-test", "005930", True)
    signal = {
        "stock_code": "005930",
        "signal_type": "buy",
        "signal_level": 1,
        "ma3": 258500,
    }
    text = format_signal_sms("삼성전자", signal)
    assert "삼성전자" in text and "매수1" in text
    delivered = notify_signals(
        "sms-test",
        [{**signal, "signal_date": "2026-09-07", "reference_price": 1, "threshold": 1}],
    )
    assert len(delivered) == 1
    assert delivered[0]["sms"]["status"] == "test"
    _ok("notify_signals(관심종목)", delivered[0]["sms"]["text"])

    skipped = notify_signals(
        "sms-test",
        [
            {
                "stock_code": "000660",
                "signal_type": "buy",
                "signal_level": 1,
                "ma3": 1,
                "signal_date": "2026-09-07",
                "reference_price": 1,
                "threshold": 1,
            }
        ],
    )
    assert skipped == []
    _ok("관심종목 아니면 SMS 생략", "000660 skipped")


if __name__ == "__main__":
    main()
