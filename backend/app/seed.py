"""데모용 샘플 데이터 생성.

외부 주식 API 없이도 엔드투엔드 흐름(가격 -> MA3 -> 신호 -> TOP20)을
확인할 수 있도록 결정론적(deterministic) 시세를 생성한다.
실제 운영에서는 이 모듈 대신 증분 업데이트 수집기가 데이터를 채운다.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import List

from .config import settings
from .db import db_cursor, init_db
from .engine.ma3 import calculate_ma3
from .engine.signals import detect_signals
from .engine.top20 import select_top20

DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"
TRADING_DAYS = 130  # 약 6개월치 거래일

# (code, name, market, base_price, amplitude_pct, avg_trading_value_billion)
SAMPLE_STOCKS = [
    ("005930", "삼성전자", "KOSPI", 70000, 0.18, 1200),
    ("000660", "SK하이닉스", "KOSPI", 130000, 0.22, 900),
    ("035420", "NAVER", "KOSPI", 210000, 0.16, 500),
    ("051910", "LG화학", "KOSPI", 420000, 0.20, 450),
    ("247540", "에코프로비엠", "KOSDAQ", 250000, 0.28, 700),
    ("091990", "셀트리온헬스케어", "KOSDAQ", 68000, 0.24, 300),
    ("035720", "카카오", "KOSPI", 48000, 0.19, 400),
    ("068270", "셀트리온", "KOSPI", 175000, 0.17, 350),
]

# 관심종목(★)으로 등록해 SMS 대상이 되는 종목.
DEMO_WATCHLIST = ["005930", "000660", "247540"]


def _trading_dates(n: int) -> List[str]:
    """오늘 기준 과거 n 거래일(주말 제외)을 오래된 순으로 반환."""
    dates: List[str] = []
    cursor = date.today()
    while len(dates) < n:
        if cursor.weekday() < 5:  # 0=월 ~ 4=금
            dates.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(dates))


def _price_series(base: float, amplitude_pct: float, n: int) -> List[float]:
    """사인파 기반 결정론적 종가 시계열(신호가 발생하도록 진폭을 준다)."""
    closes: List[float] = []
    for i in range(n):
        # 두 번의 큰 파동 + 약한 추세.
        wave = math.sin(i / 10.0) * amplitude_pct
        trend = (i / n) * 0.05
        closes.append(round(base * (1 + wave + trend), 2))
    return closes


def seed(force: bool = False) -> None:
    """샘플 데이터 적재. 이미 존재하면(비강제) 건너뛴다(idempotent)."""
    init_db()

    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM stocks")
        if cur.fetchone()["c"] > 0 and not force:
            return

    dates = _trading_dates(TRADING_DAYS)
    selection_date = dates[-1]
    per_stock_tv: dict[str, List[float]] = {}

    with db_cursor() as cur:
        for table in (
            "signal_history",
            "user_watchlist",
            "user_settings",
            "top20_history",
            "daily_prices",
            "stocks",
        ):
            cur.execute(f"DELETE FROM {table}")

        for code, name, market, base, amp, tv_bil in SAMPLE_STOCKS:
            cur.execute(
                "INSERT INTO stocks (stock_code, stock_name, market, is_active)"
                " VALUES (?, ?, ?, 1)",
                (code, name, market),
            )

            closes = _price_series(base, amp, TRADING_DAYS)
            ma3 = calculate_ma3(closes)
            trading_values: List[float] = []
            for i, d in enumerate(dates):
                # 거래대금(원): 종목별 평균 규모(십억) 를 반영해 랭킹이 갈리게 한다.
                tv = tv_bil * 1e8 * (1 + 0.1 * math.sin(i / 7.0))
                trading_values.append(tv)
                cur.execute(
                    "INSERT INTO daily_prices"
                    " (stock_code, date, close, volume, trading_value, ma3)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        code,
                        d,
                        closes[i],
                        int(tv / max(closes[i], 1)),
                        round(tv, 2),
                        ma3[i],
                    ),
                )
            per_stock_tv[code] = trading_values

            # 관심종목이면 신호 이력 생성(SMS 대상). §16~§17.
            if code in DEMO_WATCHLIST:
                events = detect_signals(
                    dates,
                    ma3,
                    settings.default_x1,
                    settings.default_x2,
                    settings.default_y1,
                    settings.default_y2,
                )
                for ev in events:
                    cur.execute(
                        "INSERT OR IGNORE INTO signal_history"
                        " (user_id, stock_code, signal_date, signal_type,"
                        "  signal_level, ma3, reference_price, sms_sent)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                        (
                            DEMO_USER_ID,
                            code,
                            ev.date,
                            ev.signal_type,
                            ev.signal_level,
                            ev.ma3,
                            ev.reference_price,
                        ),
                    )

        # TOP20 선정 및 기록.
        for code, avg_tv, rank in select_top20(per_stock_tv):
            cur.execute(
                "INSERT INTO top20_history"
                " (selection_date, stock_code, rank, avg_trading_value)"
                " VALUES (?, ?, ?, ?)",
                (selection_date, code, rank, avg_tv),
            )

        # 데모 사용자 설정 + 관심종목.
        cur.execute(
            "INSERT INTO user_settings"
            " (user_id, phone_number, x1, x2, y1, y2, buy_alert, sell_alert)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
            (
                DEMO_USER_ID,
                "010-0000-0000",
                settings.default_x1,
                settings.default_x2,
                settings.default_y1,
                settings.default_y2,
            ),
        )
        for code in DEMO_WATCHLIST:
            cur.execute(
                "INSERT INTO user_watchlist (user_id, stock_code, alert_enabled)"
                " VALUES (?, ?, 1)",
                (DEMO_USER_ID, code),
            )


if __name__ == "__main__":
    seed(force=True)
    print("Seed complete:", settings.sqlite_path)
