"""TOP20 엔진 테스트 — 3개월 일평균 거래대금 정렬(개발계획서 §5)."""

from app.engine.top20 import average_trading_value, select_top20


def test_average_trading_value():
    assert average_trading_value([100.0, 200.0, 300.0]) == 200.0
    assert average_trading_value([]) == 0.0


def test_select_top20_orders_by_average_desc():
    data = {
        "A": [100.0, 100.0],   # avg 100
        "B": [300.0, 300.0],   # avg 300
        "C": [200.0, 200.0],   # avg 200
    }
    result = select_top20(data, limit=2)
    assert [code for code, _avg, _rank in result] == ["B", "C"]
    assert result[0] == ("B", 300.0, 1)
    assert result[1] == ("C", 200.0, 2)
