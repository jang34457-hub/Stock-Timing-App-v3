"""STEP 17-3: backtest_prices.db 로 신호 성과를 측정한다.

production prices.db / 전략 코드는 사용·변경하지 않는다.
"""

from __future__ import annotations

import csv
import json
import statistics
from datetime import date
from pathlib import Path

import backtest_db
from backtest import (
    HORIZONS,
    _warmup_start,
    attach_returns,
    build_weekly_top20,
    first_trading_day_in_week,
    future_returns,
    load_local_listing,
    local_listing_dates,
    run_signal_backtest,
)
from prepare_backtest_data import unique_top20_codes

RESULT_JSON = Path(__file__).resolve().parent / "data" / "backtest_result_17_3.json"
RESULT_CSV = Path(__file__).resolve().parent / "data" / "backtest_signals_17_3.csv"


def sample_note(signal_count: int) -> str:
    if signal_count < 10:
        return "LOW SAMPLE"
    if signal_count < 30:
        return "LIMITED SAMPLE"
    return "OK"


def is_win(signal_type: str, ret: float) -> bool:
    if signal_type == "buy":
        return ret > 0
    return ret < 0


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def horizon_block(kind: str, rows: list[dict], n: int) -> dict:
    key = f"return_{n}d"
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    wins = sum(1 for v in vals if is_win(kind, v))
    return {
        "valid_count": len(vals),
        "avg_return": _mean(vals),
        "median_return": _median(vals),
        "win_rate": (100.0 * wins / len(vals)) if vals else None,
        "excluded": len(rows) - len(vals),
    }


def group_stats(kind: str, rows: list[dict]) -> dict:
    n = len(rows)
    out = {
        "signal_count": n,
        "sample_note": sample_note(n),
    }
    for h in HORIZONS:
        out[f"{h}d"] = horizon_block(kind, rows, h)
    return out


def by_signal_level(signals: list[dict]) -> dict:
    def pick(kind: str | None, level: int | None) -> list[dict]:
        out = signals
        if kind is not None:
            out = [s for s in out if s["signal_type"] == kind]
        if level is not None:
            out = [s for s in out if int(s["signal_level"]) == level]
        return out

    return {
        "BUY": {
            "LEVEL 1": group_stats("buy", pick("buy", 1)),
            "LEVEL 2": group_stats("buy", pick("buy", 2)),
            "ALL": group_stats("buy", pick("buy", None)),
        },
        "SELL": {
            "LEVEL 1": group_stats("sell", pick("sell", 1)),
            "LEVEL 2": group_stats("sell", pick("sell", 2)),
            "ALL": group_stats("sell", pick("sell", None)),
        },
    }


def _avg_ret(rows: list[dict], kind: str, n: int) -> float | None:
    vals = [
        float(s[f"return_{n}d"])
        for s in rows
        if s["signal_type"] == kind and s.get(f"return_{n}d") is not None
    ]
    return _mean(vals)


def by_stock(signals: list[dict]) -> list[dict]:
    codes = sorted({s["stock_code"] for s in signals})
    out = []
    for code in codes:
        rows = [s for s in signals if s["stock_code"] == code]
        buys = [s for s in rows if s["signal_type"] == "buy"]
        sells = [s for s in rows if s["signal_type"] == "sell"]
        out.append(
            {
                "stock_code": code,
                "signal_count": len(rows),
                "buy_count": len(buys),
                "sell_count": len(sells),
                "buy_5d_avg": _avg_ret(rows, "buy", 5),
                "sell_5d_avg": _avg_ret(rows, "sell", 5),
                "buy_10d_avg": _avg_ret(rows, "buy", 10),
                "sell_10d_avg": _avg_ret(rows, "sell", 10),
                "buy_20d_avg": _avg_ret(rows, "buy", 20),
                "sell_20d_avg": _avg_ret(rows, "sell", 20),
                "sample_note": sample_note(len(rows)),
            }
        )
    return out


def by_month(signals: list[dict]) -> list[dict]:
    months = sorted({_ym(s["signal_date"]) for s in signals})
    out = []
    for month in months:
        rows = [s for s in signals if _ym(s["signal_date"]) == month]
        buys = [s for s in rows if s["signal_type"] == "buy"]
        sells = [s for s in rows if s["signal_type"] == "sell"]
        out.append(
            {
                "month": month,
                "signal_count": len(rows),
                "buy_count": len(buys),
                "sell_count": len(sells),
                "buy_5d_avg": _avg_ret(rows, "buy", 5),
                "sell_5d_avg": _avg_ret(rows, "sell", 5),
                "sample_note": sample_note(len(rows)),
            }
        )
    return out


def _ym(value) -> str:
    if isinstance(value, date):
        return value.isoformat()[:7]
    return str(value)[:7]


def buy_and_hold(
    prices_by_code: dict[str, pd.DataFrame],
    codes: list[str],
    start: date,
    end: date,
) -> dict:
    rets: list[float] = []
    used = 0
    skipped = 0
    for code in codes:
        df = prices_by_code.get(code)
        if df is None or df.empty:
            skipped += 1
            continue
        by_day = {row.date: float(row.close) for row in df.itertuples()}
        if start not in by_day or end not in by_day or by_day[start] == 0:
            skipped += 1
            continue
        rets.append((by_day[end] / by_day[start] - 1.0) * 100.0)
        used += 1
    return {
        "description": "평가 시작일 종가 대비 종료일 종가. 단순 보유이며 신호 전략이 아니다.",
        "stocks_used": used,
        "stocks_skipped": skipped,
        "avg_return": _mean(rets),
        "median_return": _median(rets),
    }


def unconditional_forward(
    prices_by_code: dict[str, pd.DataFrame],
    calendar: list[date],
    weekly: dict[date, list[str]],
    start: date,
    end: date,
) -> dict:
    """TOP20 편입일 전부에 대한 5/10/20일 전방 수익률. 신호일만의 단순 비교용."""
    buckets = {n: [] for n in HORIZONS}
    n_obs = 0
    for day in calendar:
        if day < start or day > end:
            continue
        try:
            week = first_trading_day_in_week(day, calendar)
        except ValueError:
            continue
        universe = weekly.get(week) or []
        for code in universe:
            df = prices_by_code.get(str(code).zfill(6))
            if df is None or df.empty:
                continue
            dates = list(df.sort_values("date")["date"])
            closes = {row.date: float(row.close) for row in df.itertuples()}
            got = future_returns(dates, closes, day)
            if got["next_close"] is None:
                continue
            n_obs += 1
            for n in HORIZONS:
                val = got.get(f"return_{n}d")
                if val is not None:
                    buckets[n].append(float(val))
    return {
        "description": "해당일 TOP20 종목의 신호 없는 전방 수익률 평균. 무작위 타이밍에 가깝다.",
        "observations": n_obs,
        **{
            f"{n}d_avg": _mean(buckets[n])
            for n in HORIZONS
        },
    }


def inspect_and_load(db_path: Path | None = None) -> dict:
    path = db_path or backtest_db.DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"backtest DB not found: {path}")
    coverage = backtest_db.inspect_coverage(path)
    prices = backtest_db.load_all_prices(path)
    return {"path": str(path), "coverage": coverage, "prices": prices}


def run_backtest_17_3(*, db_path: Path | None = None, save: bool = True) -> dict:
    loaded = inspect_and_load(db_path)
    coverage = loaded["coverage"]
    prices_by_code = loaded["prices"]
    if coverage["price_rows"] == 0:
        raise RuntimeError("backtest_prices.db is empty")

    data_start = date.fromisoformat(coverage["data_start"])
    data_end = date.fromisoformat(coverage["data_end"])
    listing_cal = [d for d in local_listing_dates() if data_start <= d <= data_end]
    price_cal = sorted({d for df in prices_by_code.values() for d in df["date"].tolist()})
    calendar = listing_cal if listing_cal else price_cal
    bt_start = _warmup_start(calendar, data_start)
    if bt_start is None:
        raise RuntimeError("not enough warm-up for 3-month MA3 window")
    bt_end = data_end

    listing_cache: dict[date, pd.DataFrame] = {}
    for day in calendar:
        loaded_listing = load_local_listing(day)
        if loaded_listing is not None:
            listing_cache[day] = loaded_listing
    work_cal = sorted(listing_cache) or calendar
    first_needed = first_trading_day_in_week(bt_start, work_cal)
    weekly, snapshots = build_weekly_top20(
        work_cal,
        listing_cache=listing_cache,
        min_selection=first_needed,
        max_selection=bt_end,
    )
    unique_top = unique_top20_codes(weekly)

    db_codes = [s["stock_code"] for s in coverage["stocks"]]
    not_in_eval = [c for c in db_codes if c not in set(unique_top)]
    thin: list[str] = []
    usable: list[str] = []
    for code in unique_top:
        df = prices_by_code.get(code)
        if df is None or df.empty or len(df) < 3:
            thin.append(code)
        else:
            usable.append(code)

    raw = run_signal_backtest(
        prices_by_code,
        work_cal,
        weekly,
        start=bt_start,
        end=bt_end,
    )
    signals = raw["signals"]
    levels = by_signal_level(signals)
    stocks = by_stock(signals)
    months = by_month(signals)
    hold = buy_and_hold(prices_by_code, unique_top, bt_start, bt_end)
    baseline = unconditional_forward(prices_by_code, work_cal, weekly, bt_start, bt_end)
    signal_stocks = sorted({s["stock_code"] for s in signals})
    excluded_future = {
        f"{n}d": sum(1 for s in signals if s.get(f"return_{n}d") is None) for n in HORIZONS
    }
    low = [k for side, groups in levels.items() for k, g in groups.items() if g["sample_note"] == "LOW SAMPLE"]
    limited = [
        k for side, groups in levels.items() for k, g in groups.items() if g["sample_note"] == "LIMITED SAMPLE"
    ]

    result = {
        "metadata": {
            "db": loaded["path"],
            "production_db_used": False,
            "data_start": coverage["data_start"],
            "data_end": coverage["data_end"],
            "backtest_start": bt_start.isoformat(),
            "backtest_end": bt_end.isoformat(),
            "trading_days_calendar": len(work_cal),
            "trading_days_evaluated": raw["trading_days_evaluated"],
            "price_rows": coverage["price_rows"],
            "db_stock_count": coverage["stock_count"],
            "historical_top20_unique": len(unique_top),
            "historical_top20_codes": unique_top,
            "db_stocks_not_in_eval_top20": not_in_eval,
            "actual_backtest_stocks": usable,
            "actual_backtest_stock_count": len(usable),
            "signal_stocks": signal_stocks,
            "signal_stock_count": len(signal_stocks),
            "top20_insufficient_prices": thin,
            "top20_selections": len(weekly),
            "notes": [
                "기간은 DB에서 읽었다. 47/124를 하드코딩하지 않았다.",
                "표본이 작은 그룹은 LOW SAMPLE / LIMITED SAMPLE 이며 전략 증거로 해석하지 않는다.",
                "월별 구간은 짧아서 성과 증거가 아니다.",
            ],
        },
        "summary": {
            "total_signals": len(signals),
            "buy_signals": sum(1 for s in signals if s["signal_type"] == "buy"),
            "sell_signals": sum(1 for s in signals if s["signal_type"] == "sell"),
            "buy_1": sum(1 for s in signals if s["signal_type"] == "buy" and s["signal_level"] == 1),
            "buy_2": sum(1 for s in signals if s["signal_type"] == "buy" and s["signal_level"] == 2),
            "sell_1": sum(1 for s in signals if s["signal_type"] == "sell" and s["signal_level"] == 1),
            "sell_2": sum(1 for s in signals if s["signal_type"] == "sell" and s["signal_level"] == 2),
            "benchmark": {"buy_and_hold": hold, "unconditional_forward": baseline},
        },
        "by_signal_level": levels,
        "by_stock": stocks,
        "by_month": months,
        "data_quality": {
            "coverage": coverage,
            "excluded_returns": excluded_future,
            "missing_next_close": sum(1 for s in signals if s.get("next_close") is None),
            "excluded_stocks_from_job": raw.get("excluded_stocks", {}),
            "low_sample_groups": low,
            "limited_sample_groups": limited,
            "top20_history": snapshots,
        },
        "signals": signals,
    }
    _print_run(result)
    if save:
        save_results(result)
    return result


def _print_run(result: dict) -> None:
    m = result["metadata"]
    s = result["summary"]
    print("=== STEP 17-3 백테스트 ===")
    print(f"데이터 기간: {m['data_start']} ~ {m['data_end']}")
    print(f"평가 기간: {m['backtest_start']} ~ {m['backtest_end']}")
    print(f"거래일 수(캘린더): {m['trading_days_calendar']} / 평가 {m['trading_days_evaluated']}")
    print(f"DB 종목 수: {m['db_stock_count']}")
    print(f"historical TOP20 unique stocks (평가 구간): {m['historical_top20_unique']}")
    print(f"actual backtest stocks: {m['actual_backtest_stock_count']}")
    print(f"price rows: {m['price_rows']}")
    print(f"signal stocks: {m['signal_stock_count']}")
    print(f"signal count: {s['total_signals']}")
    print(f"TOP20 but insufficient prices: {m['top20_insufficient_prices'] or '(none)'}")
    print(
        "DB에 있으나 평가 구간 TOP20 밖(워밍업 주에만 등장 등): "
        f"{m.get('db_stocks_not_in_eval_top20') or '(none)'}"
    )
    print(f"BUY {s['buy_signals']} (L1={s['buy_1']} L2={s['buy_2']})  SELL {s['sell_signals']} (L1={s['sell_1']} L2={s['sell_2']})")
    for side in ("BUY", "SELL"):
        print(side)
        for label, stats in result["by_signal_level"][side].items():
            print(f"  {label} [{stats['sample_note']}] n={stats['signal_count']}")
            for h in HORIZONS:
                b = stats[f"{h}d"]
                print(
                    f"    {h}D valid={b['valid_count']} avg={b['avg_return']} "
                    f"median={b['median_return']} win={b['win_rate']}"
                )
    print("종목별 (sample_note 포함):")
    for row in result["by_stock"]:
        print(
            f"  {row['stock_code']} n={row['signal_count']} [{row['sample_note']}] "
            f"BUY5={row['buy_5d_avg']} SELL5={row['sell_5d_avg']}"
        )
    print("월별 (짧은 기간, 성과 증거 아님):")
    for row in result["by_month"]:
        print(
            f"  {row['month']} n={row['signal_count']} [{row['sample_note']}] "
            f"BUY5={row['buy_5d_avg']} SELL5={row['sell_5d_avg']}"
        )
    print("benchmark buy&hold:", result["summary"]["benchmark"]["buy_and_hold"])
    print("benchmark unconditional:", result["summary"]["benchmark"]["unconditional_forward"])
    print("제외된 미래 수익률:", result["data_quality"]["excluded_returns"])


def save_results(result: dict) -> None:
    RESULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload["signals"] = [_row_json(s) for s in result["signals"]]
    RESULT_JSON.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
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
    with RESULT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in result["signals"]:
            writer.writerow({k: _csv(row.get(k)) for k in fields})
    print(f"저장: {RESULT_JSON}")
    print(f"저장: {RESULT_CSV}")


def _row_json(row: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()}


def _csv(value) -> str:
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
    return value
