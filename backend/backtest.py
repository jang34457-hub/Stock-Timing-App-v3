"""신호 성과 백테스트 (STEP 17-1).

production 전략 로직은 변경하지 않는다. MA3·기준선·cross는 기존 함수를 재사용하고,
날짜 D의 신호는 D 이하 데이터만 사용한다.
"""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ma3 import ma3_from_closes
from market_data import LISTING_CACHE_DIR, _normalize_cached_listing, subtract_months
from prices_db import listed_stock_codes, load_prices
from signals import (
    DEFAULT_X1,
    DEFAULT_X2,
    DEFAULT_Y1,
    DEFAULT_Y2,
    LOOKBACK_MONTHS,
    _cross_down,
    _cross_up,
    calculate_buy_levels,
    calculate_sell_levels,
)
from top20 import TOP_N

HORIZONS = (5, 10, 20)
RESULT_JSON = Path(__file__).resolve().parent / "data" / "backtest_result.json"
RESULT_CSV = Path(__file__).resolve().parent / "data" / "backtest_result.csv"


def first_trading_day_in_week(day: date, calendar: list[date]) -> date:
    """그 주 월요일부터 캘린더에 있는 첫 거래일. production first_trading_day_of_week와 동일 규칙."""
    monday = day - timedelta(days=day.weekday())
    week_end = monday + timedelta(days=6)
    days = [d for d in calendar if monday <= d <= week_end]
    if not days:
        raise ValueError(f"no trading days in week of {day}")
    return days[0]


def prices_with_ma3(prices: pd.DataFrame) -> pd.DataFrame:
    """종가 시계열로 MA3를 다시 계산한다. 앞 이틀은 None."""
    rows = prices.sort_values("date").reset_index(drop=True).copy()
    rows["ma3"] = ma3_from_closes(rows["close"])
    return rows


def frame_as_of(prices: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """D 이후 행을 제거한 뒤 MA3를 재계산한다 (look-ahead 방지)."""
    rows = prices[prices["date"] <= as_of].sort_values("date").reset_index(drop=True)
    if rows.empty:
        return rows
    return prices_with_ma3(rows)


def rolling_high_low(prices: pd.DataFrame, as_of: date, months: int = LOOKBACK_MONTHS) -> dict | None:
    """D까지의 최근 3개월 MA3 최고/최저. D 이후 값은 쓰지 않는다."""
    rows = frame_as_of(prices, as_of)
    start = subtract_months(as_of, months)
    window = rows.dropna(subset=["ma3"])
    window = window[(window["date"] >= start) & (window["date"] <= as_of)]
    if window.empty:
        return None
    high_row = window.loc[window["ma3"].idxmax()]
    low_row = window.loc[window["ma3"].idxmin()]
    return {
        "high": float(high_row["ma3"]),
        "high_date": high_row["date"],
        "low": float(low_row["ma3"]),
        "low_date": low_row["date"],
        "window_start": window["date"].min(),
        "window_end": window["date"].max(),
    }


def detect_signals_asof(
    prices: pd.DataFrame,
    as_of: date,
    *,
    stock_code: str = "",
    x1: float = DEFAULT_X1,
    x2: float = DEFAULT_X2,
    y1: float = DEFAULT_Y1,
    y2: float = DEFAULT_Y2,
    months: int = LOOKBACK_MONTHS,
) -> list[dict]:
    """날짜 as_of의 cross 신호. production detect_signal과 같은 기준선·equality 규칙."""
    rows = frame_as_of(prices, as_of)
    if as_of not in set(rows["date"].tolist()):
        return []
    hl = rolling_high_low(prices, as_of, months=months)
    if hl is None:
        return []
    start = subtract_months(as_of, months)
    window = rows.dropna(subset=["ma3"])
    window = window[(window["date"] >= start) & (window["date"] <= as_of)]
    if len(window) < 2:
        return []
    today_row = window.iloc[-1]
    if today_row["date"] != as_of:
        return []
    prev_ma3 = float(window.iloc[-2]["ma3"])
    today_ma3 = float(today_row["ma3"])
    sell = calculate_sell_levels(hl["high"], x1, x2)
    buy = calculate_buy_levels(hl["low"], y1, y2)
    code = str(stock_code).zfill(6) if stock_code else ""
    out: list[dict] = []
    checks = (
        ("sell", 1, sell["sell1"], _cross_down, hl["high"]),
        ("sell", 2, sell["sell2"], _cross_down, hl["high"]),
        ("buy", 1, buy["buy1"], _cross_up, hl["low"]),
        ("buy", 2, buy["buy2"], _cross_up, hl["low"]),
    )
    for kind, level, threshold, cross, _ref in checks:
        if cross(prev_ma3, today_ma3, threshold):
            out.append(
                {
                    "stock_code": code,
                    "signal_date": as_of,
                    "signal_type": kind,
                    "signal_level": level,
                    "ma3": today_ma3,
                    "reference_high": hl["high"],
                    "reference_low": hl["low"],
                    "threshold": threshold,
                }
            )
    return out


def future_returns(
    dates: list[date],
    closes: dict[date, float],
    signal_date: date,
    horizons: tuple[int, ...] = HORIZONS,
) -> dict:
    """기준가 = 신호 다음 거래일 종가. N일 수익률은 P(D+1+N) / P(D+1) - 1."""
    result: dict = {"next_close": None}
    for n in horizons:
        result[f"return_{n}d"] = None
    try:
        i = dates.index(signal_date)
    except ValueError:
        return result
    if i + 1 >= len(dates):
        return result
    ref_day = dates[i + 1]
    ref = float(closes[ref_day])
    if ref == 0:
        return result
    result["next_close"] = ref
    for n in horizons:
        j = i + 1 + n
        if j >= len(dates):
            continue
        result[f"return_{n}d"] = (float(closes[dates[j]]) / ref - 1.0) * 100.0
    return result


def attach_returns(signal: dict, prices: pd.DataFrame) -> dict:
    rows = prices.sort_values("date")
    dates = list(rows["date"])
    closes = {row.date: float(row.close) for row in rows.itertuples()}
    return {**signal, **future_returns(dates, closes, signal["signal_date"])}


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _win_rate(kind: str, returns: list[float]) -> float | None:
    if not returns:
        return None
    if kind == "buy":
        wins = sum(1 for r in returns if r > 0)
    else:
        wins = sum(1 for r in returns if r < 0)
    return 100.0 * wins / len(returns)


def _bucket_stats(kind: str, rows: list[dict]) -> dict:
    stats: dict = {"signals": len(rows)}
    for n in HORIZONS:
        key = f"return_{n}d"
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        stats[f"{n}d_avg_return"] = _mean(vals)
        stats[f"{n}d_win_rate"] = _win_rate(kind, vals)
        stats[f"{n}d_excluded"] = len(rows) - len(vals)
    return stats


def summarize_signals(signals: list[dict]) -> dict:
    def pick(kind: str | None, level: int | None) -> list[dict]:
        out = signals
        if kind is not None:
            out = [s for s in out if s["signal_type"] == kind]
        if level is not None:
            out = [s for s in out if int(s["signal_level"]) == level]
        return out

    return {
        "BUY": {
            "Level 1": _bucket_stats("buy", pick("buy", 1)),
            "Level 2": _bucket_stats("buy", pick("buy", 2)),
            "ALL": _bucket_stats("buy", pick("buy", None)),
        },
        "SELL": {
            "Level 1": _bucket_stats("sell", pick("sell", 1)),
            "Level 2": _bucket_stats("sell", pick("sell", 2)),
            "ALL": _bucket_stats("sell", pick("sell", None)),
        },
    }


def local_listing_dates() -> list[date]:
    if not LISTING_CACHE_DIR.exists():
        return []
    days = []
    for path in LISTING_CACHE_DIR.glob("*.csv"):
        try:
            days.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(days)


def load_local_listing(day: date) -> pd.DataFrame | None:
    """로컬 krx_listing CSV만 읽는다. 없으면 None (네트워크 호출 없음)."""
    path = LISTING_CACHE_DIR / f"{day.isoformat()}.csv"
    if not path.exists():
        return None
    raw = pd.read_csv(path, dtype={"Code": str, "MarketId": str})
    return _normalize_cached_listing(raw, day)


def rank_top20_from_history(
    history: pd.DataFrame,
    *,
    end: date,
    start: date,
    n: int = TOP_N,
) -> pd.DataFrame:
    """production select_top20과 같은 분모(구간 unique 거래일) 순위."""
    if history.empty:
        raise RuntimeError("no daily trading-value snapshots in range")
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame = frame[(frame["date"] >= start) & (frame["date"] <= end)]
    if frame.empty:
        raise RuntimeError("no daily trading-value snapshots in range")
    session_count = int(frame["date"].nunique())
    ranked = (
        frame.dropna(subset=["trading_value"])
        .groupby("stock_code", as_index=False)
        .agg(
            stock_name=("stock_name", "last"),
            market=("market", "last"),
            total_trading_value=("trading_value", "sum"),
            days_present=("date", "nunique"),
        )
    )
    ranked["avg_trading_value"] = ranked["total_trading_value"] / session_count
    ranked = ranked.sort_values(
        ["avg_trading_value", "stock_code"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    top = ranked.head(n).copy()
    top.insert(0, "rank", range(1, len(top) + 1))
    top.insert(1, "selection_date", end)
    top.insert(2, "window_start", start)
    top.insert(3, "trading_days", session_count)
    return top[
        [
            "rank",
            "selection_date",
            "window_start",
            "trading_days",
            "stock_code",
            "stock_name",
            "market",
            "avg_trading_value",
            "total_trading_value",
            "days_present",
        ]
    ]


def historical_top20(
    as_of: date,
    *,
    n: int = TOP_N,
    listing_cache: dict[date, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """as_of 이하 listing만으로 TOP20. as_of 이후 거래대금은 사용하지 않는다."""
    start = subtract_months(as_of, LOOKBACK_MONTHS)
    frames: list[pd.DataFrame] = []
    cache = listing_cache if listing_cache is not None else {}
    candidates = set(cache)
    candidates.update(local_listing_dates())
    for day in sorted(candidates):
        if day < start or day > as_of:
            continue
        if day not in cache:
            loaded = load_local_listing(day)
            if loaded is None:
                continue
            cache[day] = loaded
        frames.append(cache[day])
    if not frames:
        raise RuntimeError(f"no local listings for TOP20 window ending {as_of}")
    return rank_top20_from_history(pd.concat(frames, ignore_index=True), end=as_of, start=start, n=n)


def inspect_price_coverage(codes: list[str] | None = None) -> dict:
    codes = codes or listed_stock_codes()
    per: list[dict] = []
    all_dates: list[date] = []
    for code in codes:
        df = load_prices(code)
        if df.empty:
            per.append(
                {
                    "stock_code": code,
                    "start": None,
                    "end": None,
                    "rows": 0,
                    "reason": "no_prices",
                }
            )
            continue
        days = sorted(df["date"].tolist())
        all_dates.extend(days)
        per.append(
            {
                "stock_code": code,
                "start": days[0].isoformat(),
                "end": days[-1].isoformat(),
                "rows": len(days),
                "reason": None,
            }
        )
    listing_days = local_listing_dates()
    data_start = min(all_dates) if all_dates else None
    data_end = max(all_dates) if all_dates else None
    return {
        "data_start": data_start.isoformat() if data_start else None,
        "data_end": data_end.isoformat() if data_end else None,
        "stock_count": len([p for p in per if p["rows"] > 0]),
        "listing_days": len(listing_days),
        "listing_start": listing_days[0].isoformat() if listing_days else None,
        "listing_end": listing_days[-1].isoformat() if listing_days else None,
        "stocks": per,
    }


def _warmup_start(calendar: list[date], data_start: date) -> date | None:
    """3개월 rolling + MA3 2일 warm-up이 가능한 첫 평가일."""
    for day in calendar:
        if subtract_months(day, LOOKBACK_MONTHS) >= data_start and calendar.index(day) >= 2:
            return day
    return None


def run_signal_backtest(
    prices_by_code: dict[str, pd.DataFrame],
    calendar: list[date],
    weekly_top20: dict[date, list[str]],
    *,
    start: date | None = None,
    end: date | None = None,
    x1: float = DEFAULT_X1,
    x2: float = DEFAULT_X2,
    y1: float = DEFAULT_Y1,
    y2: float = DEFAULT_Y2,
) -> dict:
    """캘린더를 하루씩 전진하며 TOP20 종목의 신호를 모은다."""
    calendar = sorted(calendar)
    if not calendar:
        raise RuntimeError("empty trading calendar")
    eval_start = start or calendar[0]
    eval_end = end or calendar[-1]
    excluded_stocks: dict[str, str] = {}
    signals: list[dict] = []
    days_used = 0
    for day in calendar:
        if day < eval_start or day > eval_end:
            continue
        try:
            week = first_trading_day_in_week(day, calendar)
        except ValueError:
            continue
        universe = weekly_top20.get(week)
        if not universe:
            continue
        days_used += 1
        for code in universe:
            code = str(code).zfill(6)
            prices = prices_by_code.get(code)
            if prices is None or prices.empty:
                excluded_stocks.setdefault(code, "no_price_data")
                continue
            try:
                found = detect_signals_asof(
                    prices, day, stock_code=code, x1=x1, x2=x2, y1=y1, y2=y2
                )
            except (ValueError, KeyError) as exc:
                excluded_stocks.setdefault(code, str(exc))
                continue
            for sig in found:
                signals.append(attach_returns(sig, prices))

    quality = _quality_report(signals)
    return {
        "backtest_start": eval_start.isoformat(),
        "backtest_end": eval_end.isoformat(),
        "trading_days_evaluated": days_used,
        "universe_weeks": len(weekly_top20),
        "prices_stock_count": len(prices_by_code),
        "excluded_stocks": excluded_stocks,
        "signals": signals,
        "summary": summarize_signals(signals),
        "quality": quality,
    }


def _quality_report(signals: list[dict]) -> dict:
    buy = [s for s in signals if s["signal_type"] == "buy"]
    sell = [s for s in signals if s["signal_type"] == "sell"]
    by_level = {
        "buy_1": sum(1 for s in buy if s["signal_level"] == 1),
        "buy_2": sum(1 for s in buy if s["signal_level"] == 2),
        "sell_1": sum(1 for s in sell if s["signal_level"] == 1),
        "sell_2": sum(1 for s in sell if s["signal_level"] == 2),
    }
    missing_next = sum(1 for s in signals if s.get("next_close") is None)
    excluded = {f"{n}d": sum(1 for s in signals if s.get(f"return_{n}d") is None) for n in HORIZONS}
    return {
        "total_signals": len(signals),
        "buy_signals": len(buy),
        "sell_signals": len(sell),
        "by_level": by_level,
        "missing_next_close": missing_next,
        "excluded_returns": excluded,
    }


def build_weekly_top20(
    calendar: list[date],
    *,
    n: int = TOP_N,
    listing_cache: dict[date, pd.DataFrame] | None = None,
    min_selection: date | None = None,
    max_selection: date | None = None,
) -> tuple[dict[date, list[str]], list[dict]]:
    cache = listing_cache if listing_cache is not None else {}
    weekly: dict[date, list[str]] = {}
    snapshots: list[dict] = []
    for day in calendar:
        if day != first_trading_day_in_week(day, calendar):
            continue
        if min_selection is not None and day < min_selection:
            continue
        if max_selection is not None and day > max_selection:
            continue
        df = historical_top20(day, n=n, listing_cache=cache)
        weekly[day] = [str(c).zfill(6) for c in df["stock_code"].tolist()]
        snapshots.append(
            {
                "selection_date": day.isoformat(),
                "window_start": df["window_start"].iloc[0].isoformat()
                if hasattr(df["window_start"].iloc[0], "isoformat")
                else str(df["window_start"].iloc[0]),
                "listing_sessions": int(df["trading_days"].iloc[0]),
                "stocks": weekly[day],
            }
        )
    return weekly, snapshots


def run_backtest_from_db(*, save: bool = True) -> dict:
    coverage = inspect_price_coverage()
    print("=== 데이터 coverage ===")
    print(f"전체 데이터 시작일: {coverage['data_start']}")
    print(f"전체 데이터 종료일: {coverage['data_end']}")
    print(f"사용 가능한 종목 수: {coverage['stock_count']}")
    print(f"listing 캐시: {coverage['listing_start']} ~ {coverage['listing_end']} ({coverage['listing_days']}일)")
    for row in coverage["stocks"]:
        if row["rows"] == 0:
            print(f"  제외 {row['stock_code']}: {row['reason']}")
        else:
            print(f"  {row['stock_code']}: {row['start']} ~ {row['end']} ({row['rows']}일)")

    listing_cal = local_listing_dates()
    codes = [p["stock_code"] for p in coverage["stocks"] if p["rows"] > 0]
    prices_by_code = {code: load_prices(code) for code in codes}
    price_dates = sorted({d for df in prices_by_code.values() for d in df["date"].tolist()})
    calendar = listing_cal if listing_cal else price_dates
    if not calendar:
        raise RuntimeError("no historical calendar (prices or listings)")

    data_start = date.fromisoformat(coverage["data_start"]) if coverage["data_start"] else calendar[0]
    data_end = date.fromisoformat(coverage["data_end"]) if coverage["data_end"] else calendar[-1]
    bt_start = _warmup_start(calendar, data_start)
    if bt_start is None:
        raise RuntimeError("not enough warm-up data for 3-month window + MA3")
    bt_end = data_end
    eval_cal = [d for d in calendar if bt_start <= d <= bt_end]
    print(f"백테스트 실제 시작일: {bt_start}")
    print(f"백테스트 실제 종료일: {bt_end}")
    print(f"거래일 수(평가 캘린더): {len(eval_cal)}")
    print("데이터가 부족해서 제외된 기간: 3개월 rolling + MA3 2일 warm-up 이전")

    listing_cache: dict[date, pd.DataFrame] = {}
    first_needed = first_trading_day_in_week(bt_start, calendar)
    weekly, snapshots = build_weekly_top20(
        calendar,
        listing_cache=listing_cache,
        min_selection=first_needed,
        max_selection=bt_end,
    )
    print("=== 주간 TOP20 (historical) ===")
    for snap in snapshots:
        print(
            f"  {snap['selection_date']} sessions={snap['listing_sessions']} "
            f"{','.join(snap['stocks'][:5])}..."
        )

    result = run_signal_backtest(
        prices_by_code,
        calendar,
        weekly,
        start=bt_start,
        end=bt_end,
    )
    result["coverage"] = coverage
    result["top20_history"] = snapshots
    result["notes"] = [
        "기간은 로컬 daily_prices + krx_listing 캐시로 결정했다. 1~2년이 없으면 맞추지 않았다.",
        "TOP20은 as_of 당일 이하 listing만 사용한다.",
        "가격이 없는 TOP20 종목은 신호 계산에서 제외한다.",
    ]
    _print_quality(result)
    if save:
        save_backtest_result(result)
        print(f"저장: {RESULT_JSON}")
        print(f"저장: {RESULT_CSV}")
    return result


def _print_quality(result: dict) -> None:
    q = result["quality"]
    print("=== 데이터 품질 ===")
    print(f"백테스트 기간: {result['backtest_start']} ~ {result['backtest_end']}")
    print(f"총 신호 수: {q['total_signals']}")
    print(f"BUY 신호 수: {q['buy_signals']}")
    print(f"SELL 신호 수: {q['sell_signals']}")
    print(f"Level별: {q['by_level']}")
    print(f"미래 데이터 부족 제외: {q['excluded_returns']} (next_close 없음 {q['missing_next_close']})")
    print(f"종목별 데이터 부족: {result['excluded_stocks']}")
    print("=== 성과 ===")
    for side in ("BUY", "SELL"):
        print(side)
        for label, stats in result["summary"][side].items():
            print(f"  {label}: {stats}")


def save_backtest_result(result: dict, json_path: Path | None = None, csv_path: Path | None = None) -> None:
    json_path = json_path or RESULT_JSON
    csv_path = csv_path or RESULT_CSV
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _jsonable(result)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "signal_date",
        "stock_code",
        "signal_type",
        "signal_level",
        "ma3",
        "reference_high",
        "reference_low",
        "threshold",
        "next_close",
        "return_5d",
        "return_10d",
        "return_20d",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in result.get("signals", []):
            writer.writerow({k: _csv_cell(row.get(k)) for k in fields})


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return value


def _jsonable(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and value != value:
        return None
    return value


if __name__ == "__main__":
    run_backtest_from_db()
