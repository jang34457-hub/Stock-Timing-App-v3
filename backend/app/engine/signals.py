"""매수·매도 신호 엔진.

개발계획서 §12~§15 기준:
- 최고점/최저점 = 최근 3개월 MA3 중 최고/최저값.
- 매도 기준 = H * (1 - X/100), 매수 기준 = L * (1 + Y/100).
- 신호는 기준선을 "돌파"하는 순간만 발생한다(매일 반복 금지).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SignalEvent:
    date: str
    signal_type: str  # "BUY" | "SELL"
    signal_level: int  # 1 | 2
    ma3: float
    reference_price: float


def find_3month_ma_high(ma3_series: List[Optional[float]]) -> Optional[float]:
    values = [v for v in ma3_series if v is not None]
    return max(values) if values else None


def find_3month_ma_low(ma3_series: List[Optional[float]]) -> Optional[float]:
    values = [v for v in ma3_series if v is not None]
    return min(values) if values else None


def calculate_sell_levels(high: float, x1: float, x2: float) -> List[float]:
    """매도 기준가격 [1차, 2차]. 개발계획서 §13."""
    return [
        round(high * (1 - x1 / 100.0), 2),
        round(high * (1 - x2 / 100.0), 2),
    ]


def calculate_buy_levels(low: float, y1: float, y2: float) -> List[float]:
    """매수 기준가격 [1차, 2차]. 개발계획서 §14."""
    return [
        round(low * (1 + y1 / 100.0), 2),
        round(low * (1 + y2 / 100.0), 2),
    ]


def is_sell_breakout(prev_ma3: float, today_ma3: float, reference: float) -> bool:
    """매도 돌파: 전일 MA3 > 기준 AND 오늘 MA3 <= 기준. §15."""
    return prev_ma3 > reference and today_ma3 <= reference


def is_buy_breakout(prev_ma3: float, today_ma3: float, reference: float) -> bool:
    """매수 돌파: 전일 MA3 < 기준 AND 오늘 MA3 >= 기준. §15."""
    return prev_ma3 < reference and today_ma3 >= reference


def detect_signals(
    dates: List[str],
    ma3_series: List[Optional[float]],
    x1: float = 10,
    x2: float = 20,
    y1: float = 10,
    y2: float = 20,
) -> List[SignalEvent]:
    """MA3 시계열을 스캔하여 돌파 신호 이벤트 목록을 반환한다.

    기준선은 최근 3개월 MA3 최고/최저값으로 고정하고, 시계열 전체에서
    기준선을 처음 돌파하는 순간만 이벤트로 기록한다.
    """
    high = find_3month_ma_high(ma3_series)
    low = find_3month_ma_low(ma3_series)
    if high is None or low is None:
        return []

    sell_levels = calculate_sell_levels(high, x1, x2)
    buy_levels = calculate_buy_levels(low, y1, y2)

    events: List[SignalEvent] = []
    for i in range(1, len(ma3_series)):
        prev, today = ma3_series[i - 1], ma3_series[i]
        if prev is None or today is None:
            continue

        for level_idx, ref in enumerate(sell_levels, start=1):
            if is_sell_breakout(prev, today, ref):
                events.append(
                    SignalEvent(dates[i], "SELL", level_idx, today, ref)
                )
        for level_idx, ref in enumerate(buy_levels, start=1):
            if is_buy_breakout(prev, today, ref):
                events.append(
                    SignalEvent(dates[i], "BUY", level_idx, today, ref)
                )
    return events
