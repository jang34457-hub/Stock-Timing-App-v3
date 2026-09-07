"""3일 이동평균선(MA3) 계산 엔진.

개발계획서 §11: MA3 = (오늘 종가 + 전일 종가 + 전전일 종가) / 3.
주가 계산은 LLM이 아니라 순수 파이썬 코드로 수행한다(§4, §24).
"""

from __future__ import annotations

from typing import List, Optional


def calculate_ma3(closes: List[float]) -> List[Optional[float]]:
    """종가 리스트(과거->최신)를 받아 각 날짜의 MA3를 반환한다.

    앞의 두 날짜는 3개 값이 모이지 않아 ``None`` 이다.
    """
    result: List[Optional[float]] = []
    for i in range(len(closes)):
        if i < 2:
            result.append(None)
        else:
            window = closes[i - 2 : i + 1]
            result.append(round(sum(window) / 3.0, 4))
    return result
