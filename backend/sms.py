"""SMS 발송.

SMS_PROVIDER=test 이면 실제 망으로 보내지 않고 로그 파일에 남긴다.
SMS_PROVIDER=solapi 이면 SOLAPI_API_KEY / SOLAPI_API_SECRET / SOLAPI_SENDER 로 발송한다.

일시 오류는 SMS_RETRY_MAX 만큼 재시도하고, 실패는 sms_log.jsonl 에 남긴다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

LOG_PATH = Path(__file__).resolve().parent / "data" / "sms_log.jsonl"
SOLAPI_URL = "https://api.solapi.com/messages/v4/send"
ENV_PATH = Path(__file__).resolve().parent / ".env"
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env(ENV_PATH)


def retry_max() -> int:
    try:
        return max(1, int(os.getenv("SMS_RETRY_MAX", "3")))
    except ValueError:
        return 3


def retry_backoff_sec() -> float:
    try:
        return max(0.0, float(os.getenv("SMS_RETRY_BACKOFF_SEC", "1")))
    except ValueError:
        return 1.0


def normalize_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    if not re.fullmatch(r"01[016789]\d{7,8}", digits):
        raise ValueError("올바른 휴대폰 번호가 아닙니다.")
    return digits


def send_sms(to: str, text: str, *, test: bool | None = None) -> dict:
    """테스트 모드면 로그에만 기록하고, solapi면 실제 발송한다."""
    to_norm = normalize_phone(to)
    provider = os.getenv("SMS_PROVIDER", "test").strip().lower()
    if test is True:
        provider = "test"
    payload = {
        "to": to_norm,
        "text": text,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
    }
    if provider == "test":
        payload["status"] = "test"
        payload["message_id"] = f"test-{uuid.uuid4().hex[:12]}"
        payload["attempts"] = 1
        _append_log(payload)
        return payload
    if provider == "solapi":
        try:
            live = _send_solapi(to_norm, text)
            payload.update(live)
            _append_log(payload)
            return payload
        except Exception as exc:
            payload["status"] = "error"
            payload["error_type"] = type(exc).__name__
            payload["message"] = str(exc)
            if "attempts" not in payload:
                payload["attempts"] = retry_max()
            _append_log(payload)
            raise
    raise ValueError(f"unsupported SMS_PROVIDER: {provider}")


def format_signal_sms(stock_name: str, signal: dict) -> str:
    kind_raw = str(signal.get("signal_type", "")).lower()
    kind = "매수" if kind_raw == "buy" else "매도"
    ma3 = f"{float(signal['ma3']):,.0f}"
    return f"[Stock Timing] {stock_name} {kind}{signal['signal_level']} 신호 (MA3 {ma3})"


def _send_solapi(to: str, text: str) -> dict:
    api_key = os.getenv("SOLAPI_API_KEY", "").strip()
    api_secret = os.getenv("SOLAPI_API_SECRET", "").strip()
    sender = os.getenv("SOLAPI_SENDER", "").strip()
    if not api_key or not api_secret or not sender:
        raise RuntimeError("SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_SENDER를 .env에 넣어야 합니다.")
    attempts = retry_max()
    backoff = retry_backoff_sec()
    last_error = "Solapi error"
    for attempt in range(1, attempts + 1):
        try:
            response = _solapi_post(to, text, api_key, api_secret, sender)
        except requests.RequestException as exc:
            last_error = f"Solapi network error: {exc}"
            _log_attempt_failure(to, text, attempt, attempts, last_error, http_status=None)
            if attempt >= attempts:
                raise RuntimeError(last_error) from exc
            time.sleep(backoff * attempt)
            continue
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}
        if response.status_code < 400:
            return {
                "status": "sent",
                "message_id": str(data.get("messageId") or data.get("groupId") or ""),
                "provider_response": data,
                "attempts": attempt,
            }
        last_error = f"Solapi error {response.status_code}: {data}"
        _log_attempt_failure(
            to, text, attempt, attempts, last_error, http_status=response.status_code
        )
        if response.status_code not in TRANSIENT_HTTP or attempt >= attempts:
            raise RuntimeError(last_error)
        time.sleep(backoff * attempt)
    raise RuntimeError(last_error)


def _solapi_post(to: str, text: str, api_key: str, api_secret: str, sender: str):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    salt = uuid.uuid4().hex
    signature = hmac.new(
        api_secret.encode("utf-8"),
        f"{date}{salt}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Authorization": (
            f"HMAC-SHA256 apiKey={api_key}, date={date}, salt={salt}, signature={signature}"
        ),
        "Content-Type": "application/json",
    }
    body = {
        "message": {
            "to": to,
            "from": re.sub(r"\D", "", sender),
            "text": text,
        }
    }
    return requests.post(SOLAPI_URL, headers=headers, json=body, timeout=20)


def _log_attempt_failure(
    to: str,
    text: str,
    attempt: int,
    attempts: int,
    message: str,
    *,
    http_status: int | None,
) -> None:
    _append_log(
        {
            "to": to,
            "text": text,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "provider": "solapi",
            "status": "error",
            "attempt": attempt,
            "attempts": attempts,
            "http_status": http_status,
            "message": message,
            "retry": attempt < attempts and (http_status is None or http_status in TRANSIENT_HTTP),
        }
    )


def _append_log(payload: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
