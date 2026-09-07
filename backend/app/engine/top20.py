"""TOP20 선정 엔진.

개발계획서 §5: 최근 3개월 일평균 거래대금 기준으로 상위 20종목 선정.
    일평균 거래대금 = 최근 3개월 거래대금 합계 / 실제 거래일 수.
"""

from __future__ import annotations

from typing import Dict, List, Tuple


def average_trading_value(trading_values: List[float]) -> float:
    """거래대금 리스트의 평균(실제 거래일 수 기준)."""
    if not trading_values:
        return 0.0
    return sum(trading_values) / len(trading_values)


def select_top20(
    per_stock_trading_values: Dict[str, List[float]],
    limit: int = 20,
) -> List[Tuple[str, float, int]]:
    """종목별 3개월 거래대금 → (stock_code, 평균거래대금, rank) 상위 목록.

    반환은 평균 거래대금 내림차순으로 정렬되며 rank 는 1부터 시작한다.
    """
    ranked = sorted(
        (
            (code, average_trading_value(values))
            for code, values in per_stock_trading_values.items()
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return [
        (code, round(avg, 2), rank)
        for rank, (code, avg) in enumerate(ranked[:limit], start=1)
    ]
