"""매수·매도 신호 엔진.

최고/최저점은 최근 3개월 MA3이며, 신호는 기준선을 처음 돌파한 날에만 발생한다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from market_data import latest_trading_day, subtract_months
from prices_db import (
    insert_signal,
    last_signal,
    load_prices,
    mark_sms_sent,
)

LOOKBACK_MONTHS = 3
DEFAULT_X1 = 10.0
DEFAULT_X2 = 20.0
DEFAULT_Y1 = 10.0
DEFAULT_Y2 = 20.0
PRICE_DECIMALS = 2
DEFAULT_USER_ID = "local"


def find_3month_ma_high(stock_code: str, as_of: date | None = None) -> dict:
    """최근 3개월 MA3 최고값 H."""
    window = _ma3_window(stock_code, as_of)
    idx = window["ma3"].idxmax()
    row = window.loc[idx]
    return {
        "stock_code": str(stock_code).zfill(6),
        "value": float(row["ma3"]),
        "date": row["date"],
        "window_start": window["date"].min(),
        "window_end": window["date"].max(),
    }


def find_3month_ma_low(stock_code: str, as_of: date | None = None) -> dict:
    """최근 3개월 MA3 최저값 L."""
    window = _ma3_window(stock_code, as_of)
    idx = window["ma3"].idxmin()
    row = window.loc[idx]
    return {
        "stock_code": str(stock_code).zfill(6),
        "value": float(row["ma3"]),
        "date": row["date"],
        "window_start": window["date"].min(),
        "window_end": window["date"].max(),
    }


def calculate_sell_levels(
    high: float,
    x1: float = DEFAULT_X1,
    x2: float = DEFAULT_X2,
) -> dict:
    """매도1 = H*(1-X1/100), 매도2 = H*(1-X2/100)."""
    h = float(high)
    return {
        "high": round(h, PRICE_DECIMALS),
        "x1": float(x1),
        "x2": float(x2),
        "sell1": round(h * (1 - float(x1) / 100), PRICE_DECIMALS),
        "sell2": round(h * (1 - float(x2) / 100), PRICE_DECIMALS),
    }


def calculate_buy_levels(
    low: float,
    y1: float = DEFAULT_Y1,
    y2: float = DEFAULT_Y2,
) -> dict:
    """매수1 = L*(1+Y1/100), 매수2 = L*(1+Y2/100)."""
    low_v = float(low)
    return {
        "low": round(low_v, PRICE_DECIMALS),
        "y1": float(y1),
        "y2": float(y2),
        "buy1": round(low_v * (1 + float(y1) / 100), PRICE_DECIMALS),
        "buy2": round(low_v * (1 + float(y2) / 100), PRICE_DECIMALS),
    }


def detect_signal(
    stock_code: str,
    as_of: date | None = None,
    *,
    x1: float = DEFAULT_X1,
    x2: float = DEFAULT_X2,
    y1: float = DEFAULT_Y1,
    y2: float = DEFAULT_Y2,
) -> list[dict]:
    """오늘 MA3가 기준선을 돌파하면 신호를 반환한다. 이미 아래에 있으면 반복하지 않는다."""
    code = str(stock_code).zfill(6)
    window = _ma3_window(code, as_of)
    if len(window) < 2:
        return []

    today_row = window.iloc[-1]
    prev_row = window.iloc[-2]
    today_ma3 = float(today_row["ma3"])
    prev_ma3 = float(prev_row["ma3"])
    signal_date = today_row["date"]

    high = find_3month_ma_high(code, as_of)
    low = find_3month_ma_low(code, as_of)
    sell = calculate_sell_levels(high["value"], x1, x2)
    buy = calculate_buy_levels(low["value"], y1, y2)

    signals: list[dict] = []
    if _cross_down(prev_ma3, today_ma3, sell["sell1"]):
        signals.append(
            _signal_row(code, signal_date, "sell", 1, today_ma3, high["value"], sell["sell1"])
        )
    if _cross_down(prev_ma3, today_ma3, sell["sell2"]):
        signals.append(
            _signal_row(code, signal_date, "sell", 2, today_ma3, high["value"], sell["sell2"])
        )
    if _cross_up(prev_ma3, today_ma3, buy["buy1"]):
        signals.append(
            _signal_row(code, signal_date, "buy", 1, today_ma3, low["value"], buy["buy1"])
        )
    if _cross_up(prev_ma3, today_ma3, buy["buy2"]):
        signals.append(
            _signal_row(code, signal_date, "buy", 2, today_ma3, low["value"], buy["buy2"])
        )
    return signals


def detect_chart_signals(
    stock_code: str,
    as_of: date | None = None,
    *,
    x1: float = DEFAULT_X1,
    x2: float = DEFAULT_X2,
    y1: float = DEFAULT_Y1,
    y2: float = DEFAULT_Y2,
) -> list[dict]:
    """최근 3개월 창에서 돌파만 모아 차트 마커로 쓴다. 6개월 시세는 그대로 그린다."""
    code = str(stock_code).zfill(6)
    end = as_of or latest_trading_day()
    analysis_start = subtract_months(end, LOOKBACK_MONTHS)
    prices = load_prices(code)
    rows = prices.dropna(subset=["ma3"]).sort_values("date").reset_index(drop=True)
    if len(rows) < 2:
        return []

    found: list[dict] = []
    for i in range(1, len(rows)):
        day = rows.iloc[i]["date"]
        if day < analysis_start or day > end:
            continue
        window_start = subtract_months(day, LOOKBACK_MONTHS)
        window = rows[(rows["date"] >= window_start) & (rows["date"] <= day)]
        high = float(window["ma3"].max())
        low = float(window["ma3"].min())
        sell = calculate_sell_levels(high, x1, x2)
        buy = calculate_buy_levels(low, y1, y2)
        prev_ma3 = float(rows.iloc[i - 1]["ma3"])
        today_ma3 = float(rows.iloc[i]["ma3"])
        if _cross_down(prev_ma3, today_ma3, sell["sell1"]):
            found.append(_signal_row(code, day, "sell", 1, today_ma3, high, sell["sell1"]))
        if _cross_down(prev_ma3, today_ma3, sell["sell2"]):
            found.append(_signal_row(code, day, "sell", 2, today_ma3, high, sell["sell2"]))
        if _cross_up(prev_ma3, today_ma3, buy["buy1"]):
            found.append(_signal_row(code, day, "buy", 1, today_ma3, low, buy["buy1"]))
        if _cross_up(prev_ma3, today_ma3, buy["buy2"]):
            found.append(_signal_row(code, day, "buy", 2, today_ma3, low, buy["buy2"]))
    return found


def signals_for_sms(
    stock_code: str,
    as_of: date | None = None,
    *,
    user_id: str = DEFAULT_USER_ID,
    x1: float = DEFAULT_X1,
    x2: float = DEFAULT_X2,
    y1: float = DEFAULT_Y1,
    y2: float = DEFAULT_Y2,
) -> list[dict]:
    """돌파 신호 중 signal_history에 없는 것만 SMS 대상으로 남긴다."""
    return record_for_sms(detect_signal(stock_code, as_of, x1=x1, x2=x2, y1=y1, y2=y2), user_id=user_id)


def record_for_sms(signals: list[dict], user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """같은 날 재실행·같은 에피소드 반복을 걸러 새로 기록된 신호만 반환한다."""
    to_send: list[dict] = []
    for signal in signals:
        if is_duplicate_condition(signal, user_id=user_id):
            continue
        inserted = insert_signal(user_id, signal)
        last = last_signal(
            user_id,
            signal["stock_code"],
            signal["signal_type"],
            int(signal["signal_level"]),
        )
        retry_unsent = bool(last) and not last.get("sms_sent")
        if not inserted and not retry_unsent:
            continue
        from notify import notify_signals

        delivered = notify_signals(user_id, [signal])
        sent = bool(delivered)
        if sent:
            mark_sms_sent(
                user_id,
                signal["stock_code"],
                signal["signal_date"],
                signal["signal_type"],
                signal["signal_level"],
            )
        if inserted or sent:
            to_send.append({**signal, "sms_sent": sent, "user_id": user_id})
    return to_send


def is_duplicate_condition(signal: dict, user_id: str = DEFAULT_USER_ID) -> bool:
    """동일 종목·유형·단계가 아직 해소되지 않았으면 True (매일 SMS 금지)."""
    last = last_signal(
        user_id,
        signal["stock_code"],
        signal["signal_type"],
        int(signal["signal_level"]),
    )
    if last is None:
        return False
    if not last.get("sms_sent"):
        return False
    if last["signal_date"] == signal["signal_date"]:
        return True
    return not _episode_cleared(signal["stock_code"], last, signal["signal_date"])


def _episode_cleared(stock_code: str, last: dict, until: date) -> bool:
    """직전 신호 이후, 오늘 전에 기준선을 반대로 다시 넘었으면 에피소드 종료."""
    prices = load_prices(stock_code)
    if prices.empty:
        return False
    between = prices[
        (prices["date"] > last["signal_date"]) & (prices["date"] < until)
    ].dropna(subset=["ma3"])
    if between.empty:
        return False
    threshold = float(last["threshold"])
    if last["signal_type"] == "sell":
        return bool((between["ma3"] > threshold).any())
    return bool((between["ma3"] < threshold).any())


def _ma3_window(stock_code: str, as_of: date | None = None) -> pd.DataFrame:
    end = as_of or latest_trading_day()
    start = subtract_months(end, LOOKBACK_MONTHS)
    prices = load_prices(stock_code)
    if prices.empty:
        raise ValueError(f"no daily prices for {str(stock_code).zfill(6)}")
    window = prices.dropna(subset=["ma3"]).copy()
    window = window[(window["date"] >= start) & (window["date"] <= end)]
    window = window.sort_values("date").reset_index(drop=True)
    if window.empty:
        raise ValueError(f"no MA3 in 3-month window for {str(stock_code).zfill(6)}")
    return window


def _cross_down(prev_ma3: float, today_ma3: float, level: float) -> bool:
    return prev_ma3 > level and today_ma3 <= level


def _cross_up(prev_ma3: float, today_ma3: float, level: float) -> bool:
    return prev_ma3 < level and today_ma3 >= level


def _signal_row(
    stock_code: str,
    signal_date: date,
    signal_type: str,
    signal_level: int,
    ma3: float,
    reference_price: float,
    threshold: float,
) -> dict:
    return {
        "stock_code": stock_code,
        "signal_date": signal_date,
        "signal_type": signal_type,
        "signal_level": signal_level,
        "ma3": ma3,
        "reference_price": reference_price,
        "threshold": threshold,
    }
