"""관심종목 신호 SMS."""

from __future__ import annotations

from prices_db import get_settings, get_stock, list_watchlist
from sms import format_signal_sms, send_sms

_sms_errors: list[dict] = []


def take_sms_errors() -> list[dict]:
    """잡 로그용으로 수집한 SMS 실패를 꺼낸다."""
    out = list(_sms_errors)
    _sms_errors.clear()
    return out


def notify_signals(user_id: str, signals: list[dict]) -> list[dict]:
    if not signals:
        return []
    settings = get_settings(user_id)
    phone = settings.get("phone_number")
    if not phone:
        return []
    watched = {
        item["stock_code"]
        for item in list_watchlist(user_id)
        if item.get("alert_enabled")
    }
    sent: list[dict] = []
    for signal in signals:
        if signal["stock_code"] not in watched:
            continue
        if signal["signal_type"] == "buy" and not settings.get("buy_alert"):
            continue
        if signal["signal_type"] == "sell" and not settings.get("sell_alert"):
            continue
        meta = get_stock(signal["stock_code"]) or {}
        name = meta.get("stock_name") or signal["stock_code"]
        try:
            result = send_sms(phone, format_signal_sms(name, signal))
        except Exception as exc:
            _sms_errors.append(
                {
                    "job": "daily",
                    "stage": "sms",
                    "stock_code": signal["stock_code"],
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            continue
        sent.append({**signal, "sms": result})
    return sent
