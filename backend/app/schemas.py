"""API 응답/요청 스키마 (Pydantic)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Top20Item(BaseModel):
    rank: int
    stock_code: str
    stock_name: str
    market: str
    avg_trading_value: float


class ChartPoint(BaseModel):
    date: str
    close: float
    ma3: Optional[float] = None


class StockDetail(BaseModel):
    stock_code: str
    stock_name: str
    market: str
    latest_close: Optional[float] = None
    latest_ma3: Optional[float] = None
    ma3_high_3m: Optional[float] = None
    ma3_low_3m: Optional[float] = None
    sell_levels: List[float] = []
    buy_levels: List[float] = []


class Signal(BaseModel):
    stock_code: str
    stock_name: str
    signal_date: str
    signal_type: str
    signal_level: int
    ma3: float
    reference_price: float
    sms_sent: bool


class WatchlistItem(BaseModel):
    stock_code: str
    stock_name: str
    alert_enabled: bool


class WatchlistCreate(BaseModel):
    stock_code: str
    alert_enabled: bool = True


class SettingsPayload(BaseModel):
    phone_number: Optional[str] = None
    x1: float
    x2: float
    y1: float
    y2: float
    buy_alert: bool
    sell_alert: bool
