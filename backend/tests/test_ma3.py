"""MA3 엔진 테스트 — 손으로 계산한 값과 일치하는지 확인(개발계획서 STEP 6)."""

from app.engine.ma3 import calculate_ma3


def test_first_two_days_are_none():
    result = calculate_ma3([100.0, 110.0])
    assert result == [None, None]


def test_ma3_matches_hand_calculation():
    closes = [90.0, 100.0, 110.0, 120.0, 130.0]
    result = calculate_ma3(closes)
    # 손 계산: (90+100+110)/3=100, (100+110+120)/3=110, (110+120+130)/3=120
    assert result == [None, None, 100.0, 110.0, 120.0]


def test_ma3_rounds_to_four_decimals():
    result = calculate_ma3([100.0, 100.0, 101.0])
    assert result[2] == round(301.0 / 3.0, 4)


def test_empty_input():
    assert calculate_ma3([]) == []
