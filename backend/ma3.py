"""MA3 엔진.

MA3 = (오늘 종가 + 전일 종가 + 전전일 종가) / 3
거래일 기준이며, 종가가 3개 미만인 날은 None.
"""

from __future__ import annotations

import pandas as pd

from prices_db import load_prices, save_ma3

MA3_WINDOW = 3
MA3_DECIMALS = 2


def ma3_from_closes(closes: list[float] | pd.Series) -> list[float | None]:
    """종가 리스트에 대한 MA3. 앞 이틀은 None."""
    values = [float(v) for v in closes]
    out: list[float | None] = []
    for i, today in enumerate(values):
        if i < MA3_WINDOW - 1:
            out.append(None)
            continue
        total = today + values[i - 1] + values[i - 2]
        out.append(round(total / MA3_WINDOW, MA3_DECIMALS))
    return out


def calculate_ma3(stock_code: str) -> pd.DataFrame:
    """저장된 일별 종가로 MA3를 계산하고 DB에 반영한다."""
    code = str(stock_code).zfill(6)
    prices = load_prices(code)
    if prices.empty:
        raise ValueError(f"no daily prices for {code}")

    prices = prices.sort_values("date").reset_index(drop=True)
    prices["ma3"] = ma3_from_closes(prices["close"])
    save_ma3(code, prices[["date", "ma3"]])
    return prices[["date", "close", "ma3"]]
