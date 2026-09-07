"""신호 엔진 테스트 — 기준선 계산과 돌파 판정(개발계획서 §12~§15)."""

from app.engine.signals import (
    calculate_buy_levels,
    calculate_sell_levels,
    detect_signals,
    find_3month_ma_high,
    find_3month_ma_low,
    is_buy_breakout,
    is_sell_breakout,
)


def test_high_low_ignore_none():
    series = [None, None, 100.0, 50.0, 75.0]
    assert find_3month_ma_high(series) == 100.0
    assert find_3month_ma_low(series) == 50.0


def test_sell_levels_example():
    # §13 예시: MA3 최고점 100,000 → 90,000 / 80,000
    assert calculate_sell_levels(100000, 10, 20) == [90000.0, 80000.0]


def test_buy_levels_example():
    # §14 예시: MA3 최저점 50,000 → 55,000 / 60,000
    assert calculate_buy_levels(50000, 10, 20) == [55000.0, 60000.0]


def test_sell_breakout_only_on_crossing():
    # 기준 90,000 을 위에서 아래로 통과하는 순간만 True
    assert is_sell_breakout(91000, 89000, 90000) is True
    # 이미 아래에 있으면 매일 반복되지 않음
    assert is_sell_breakout(89000, 88000, 90000) is False


def test_buy_breakout_only_on_crossing():
    assert is_buy_breakout(54000, 56000, 55000) is True
    assert is_buy_breakout(56000, 57000, 55000) is False


def test_detect_signals_finds_expected_events():
    dates = ["d0", "d1", "d2", "d3", "d4", "d5", "d6"]
    # MA3 최저=100, 최고=140 → 매수1=110, 매도1=126
    ma3 = [None, None, 100.0, 112.0, 141.0, 124.0, 100.0]
    events = detect_signals(dates, ma3, x1=10, x2=20, y1=10, y2=20)
    types = {(e.date, e.signal_type, e.signal_level) for e in events}
    # d3: 100->112 매수1(110) 돌파, d5: 141->124 매도1(126) 돌파
    assert ("d3", "BUY", 1) in types
    assert ("d5", "SELL", 1) in types
