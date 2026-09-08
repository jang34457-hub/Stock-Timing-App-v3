"""STEP 17-4: 같은 날 TOP20 대비 excess return.

production 전략/파라미터는 변경하지 않는다. 17-3 신호 이벤트는 CSV에서 재사용한다.
"""

from __future__ import annotations

import csv
import json
import random
import statistics
from datetime import date
from pathlib import Path

from backtest import (
    HORIZONS,
    _warmup_start,
    build_weekly_top20,
    first_trading_day_in_week,
    future_returns,
    load_local_listing,
    local_listing_dates,
)
from backtest_17_3 import RESULT_CSV as SIGNALS_17_3, inspect_and_load, sample_note
from prepare_backtest_data import unique_top20_codes

RESULT_JSON = Path(__file__).resolve().parent / "data" / "backtest_result_17_4.json"
RESULT_CSV = Path(__file__).resolve().parent / "data" / "backtest_signals_17_4.csv"


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def is_excess_win(signal_type: str, excess: float) -> bool:
    if signal_type == "buy":
        return excess > 0
    return excess < 0


def pre_return(
    dates: list[date],
    closes: dict[date, float],
    signal_date: date,
    lookback: int,
) -> float | None:
    """P(D) / P(D-lookback) - 1. 과거만 사용."""
    try:
        i = dates.index(signal_date)
    except ValueError:
        return None
    if i < lookback:
        return None
    prev = float(closes[dates[i - lookback]])
    today = float(closes[signal_date])
    if prev == 0:
        return None
    return (today / prev - 1.0) * 100.0


def series(prices) -> tuple[list[date], dict[date, float]]:
    rows = prices.sort_values("date")
    dates = list(rows["date"])
    closes = {row.date: float(row.close) for row in rows.itertuples()}
    return dates, closes


def same_date_top20_baseline(
    day: date,
    weekly: dict[date, list[str]],
    calendar: list[date],
    prices_by_code: dict,
) -> dict:
    """날짜 D의 당시 TOP20 종목에 대해 신호와 같은 전방 수익률 공식 사용."""
    out = {f"avg_{n}d": None for n in HORIZONS}
    out.update({f"median_{n}d": None for n in HORIZONS})
    out["n_stocks"] = 0
    out["n_valid"] = {n: 0 for n in HORIZONS}
    try:
        week = first_trading_day_in_week(day, calendar)
    except ValueError:
        return out
    universe = [str(c).zfill(6) for c in (weekly.get(week) or [])]
    buckets = {n: [] for n in HORIZONS}
    used = 0
    for code in universe:
        df = prices_by_code.get(code)
        if df is None or df.empty:
            continue
        dates, closes = series(df)
        got = future_returns(dates, closes, day)
        used += 1
        for n in HORIZONS:
            val = got.get(f"return_{n}d")
            if val is not None:
                buckets[n].append(float(val))
    out["n_stocks"] = used
    for n in HORIZONS:
        out[f"avg_{n}d"] = _mean(buckets[n])
        out[f"median_{n}d"] = _median(buckets[n])
        out["n_valid"][n] = len(buckets[n])
    return out


def excess_return(signal_ret: float | None, baseline: float | None) -> float | None:
    if signal_ret is None or baseline is None:
        return None
    return float(signal_ret) - float(baseline)


def load_signals_17_3(path: Path | None = None) -> list[dict]:
    path = path or SIGNALS_17_3
    if not path.exists():
        raise FileNotFoundError(f"STEP 17-3 signals not found: {path}")
    rows: list[dict] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            rows.append(_parse_signal(raw))
    return rows


def _parse_signal(raw: dict) -> dict:
    def num(key: str):
        val = raw.get(key, "")
        if val is None or val == "":
            return None
        return float(val)

    return {
        "signal_date": date.fromisoformat(raw["signal_date"]),
        "stock_code": str(raw["stock_code"]).zfill(6),
        "signal_type": raw["signal_type"],
        "signal_level": int(raw["signal_level"]),
        "ma3": num("ma3"),
        "reference_high": num("reference_high"),
        "reference_low": num("reference_low"),
        "threshold": num("threshold"),
        "next_close": num("next_close"),
        "return_5d": num("return_5d"),
        "return_10d": num("return_10d"),
        "return_20d": num("return_20d"),
    }


def signal_key(row: dict) -> tuple:
    day = row["signal_date"]
    if isinstance(day, date):
        day_s = day.isoformat()
    else:
        day_s = str(day)[:10]
    return (day_s, str(row["stock_code"]).zfill(6), row["signal_type"], int(row["signal_level"]))


def attach_context(
    signals: list[dict],
    prices_by_code: dict,
    weekly: dict[date, list[str]],
    calendar: list[date],
) -> list[dict]:
    baseline_cache: dict[date, dict] = {}
    out = []
    for sig in signals:
        day = sig["signal_date"]
        if day not in baseline_cache:
            baseline_cache[day] = same_date_top20_baseline(day, weekly, calendar, prices_by_code)
        base = baseline_cache[day]
        code = str(sig["stock_code"]).zfill(6)
        df = prices_by_code.get(code)
        pre5 = pre10 = None
        if df is not None and not df.empty:
            dates, closes = series(df)
            pre5 = pre_return(dates, closes, day, 5)
            pre10 = pre_return(dates, closes, day, 10)
        row = dict(sig)
        for n in HORIZONS:
            bavg = base.get(f"avg_{n}d")
            row[f"baseline_{n}d"] = bavg
            row[f"baseline_median_{n}d"] = base.get(f"median_{n}d")
            row[f"excess_{n}d"] = excess_return(row.get(f"return_{n}d"), bavg)
        row["pre_5d_return"] = pre5
        row["pre_10d_return"] = pre10
        row["post_5d"] = row.get("return_5d")
        row["post_10d"] = row.get("return_10d")
        row["post_20d"] = row.get("return_20d")
        row["top20_n"] = base["n_stocks"]
        out.append(row)
    return out


def excess_block(kind: str, rows: list[dict], n: int) -> dict:
    key = f"excess_{n}d"
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    wins = sum(1 for v in vals if is_excess_win(kind, v))
    return {
        "n": len(vals),
        "excluded": len(rows) - len(vals),
        "avg_excess": _mean(vals),
        "median_excess": _median(vals),
        "excess_win_rate": (100.0 * wins / len(vals)) if vals else None,
        "avg_signal": _mean([float(r[f"return_{n}d"]) for r in rows if r.get(f"return_{n}d") is not None]),
        "avg_baseline": _mean([float(r[f"baseline_{n}d"]) for r in rows if r.get(f"baseline_{n}d") is not None]),
        "median_baseline": _median(
            [float(r[f"baseline_median_{n}d"]) for r in rows if r.get(f"baseline_median_{n}d") is not None]
        ),
    }


def pre_post_block(kind: str, rows: list[dict]) -> dict:
    def avg(key: str) -> float | None:
        return _mean([float(r[key]) for r in rows if r.get(key) is not None])

    def med(key: str) -> float | None:
        return _median([float(r[key]) for r in rows if r.get(key) is not None])

    return {
        "pre_5d_avg": avg("pre_5d_return"),
        "pre_5d_median": med("pre_5d_return"),
        "pre_10d_avg": avg("pre_10d_return"),
        "pre_10d_median": med("pre_10d_return"),
        "post_5d_avg": avg("post_5d"),
        "post_10d_avg": avg("post_10d"),
        "post_20d_avg": avg("post_20d"),
    }


def group_excess(kind: str, rows: list[dict]) -> dict:
    return {
        "signal_count": len(rows),
        "sample_note": sample_note(len(rows)),
        "pre_post": pre_post_block(kind, rows),
        **{f"{n}d": excess_block(kind, rows, n) for n in HORIZONS},
    }


def by_level_excess(signals: list[dict]) -> dict:
    def pick(kind: str | None, level: int | None) -> list[dict]:
        out = signals
        if kind is not None:
            out = [s for s in out if s["signal_type"] == kind]
        if level is not None:
            out = [s for s in out if int(s["signal_level"]) == level]
        return out

    return {
        "BUY": {
            "LEVEL 1": group_excess("buy", pick("buy", 1)),
            "LEVEL 2": group_excess("buy", pick("buy", 2)),
            "ALL": group_excess("buy", pick("buy", None)),
        },
        "SELL": {
            "LEVEL 1": group_excess("sell", pick("sell", 1)),
            "LEVEL 2": group_excess("sell", pick("sell", 2)),
            "ALL": group_excess("sell", pick("sell", None)),
        },
    }


def date_aggregated(kind: str, rows: list[dict], key: str) -> dict:
    """종목 이벤트 평균 vs 날짜별 먼저 평균 후 날짜 평균."""
    event_vals = [float(r[key]) for r in rows if r.get(key) is not None and r["signal_type"] == kind]
    by_day: dict[date, list[float]] = {}
    for r in rows:
        if r["signal_type"] != kind or r.get(key) is None:
            continue
        by_day.setdefault(r["signal_date"], []).append(float(r[key]))
    day_means = [_mean(v) for v in by_day.values() if _mean(v) is not None]
    day_means_f = [float(v) for v in day_means if v is not None]
    return {
        "event_n": len(event_vals),
        "event_avg": _mean(event_vals),
        "event_median": _median(event_vals),
        "date_n": len(day_means_f),
        "date_avg": _mean(day_means_f),
        "date_median": _median(day_means_f),
        "note": "같은 날 여러 종목 신호는 독립 표본이 아니다.",
    }


def bootstrap_date_mean_ci(
    kind: str,
    rows: list[dict],
    key: str,
    *,
    n_boot: int = 1000,
    seed: int = 17,
) -> dict | None:
    by_day: dict[date, list[float]] = {}
    for r in rows:
        if r["signal_type"] != kind or r.get(key) is None:
            continue
        by_day.setdefault(r["signal_date"], []).append(float(r[key]))
    days = list(by_day)
    if len(days) < 5:
        return None
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(n_boot):
        sample = [days[rng.randrange(len(days))] for _ in days]
        vals = []
        for d in sample:
            m = _mean(by_day[d])
            if m is not None:
                vals.append(m)
        if vals:
            means.append(sum(vals) / len(vals))
    if not means:
        return None
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return {
        "n_dates": len(days),
        "n_boot": n_boot,
        "mean": _mean(means),
        "ci95": [lo, hi],
        "note": "signal-date 재표본. 확정적 검정이 아니다.",
    }


def eval_calendar_and_weekly(prices_by_code: dict, coverage: dict) -> tuple[list[date], dict[date, list[str]], list[dict]]:
    data_start = date.fromisoformat(coverage["data_start"])
    data_end = date.fromisoformat(coverage["data_end"])
    listing_cal = [d for d in local_listing_dates() if data_start <= d <= data_end]
    price_cal = sorted({d for df in prices_by_code.values() for d in df["date"].tolist()})
    calendar = listing_cal if listing_cal else price_cal
    bt_start = _warmup_start(calendar, data_start)
    if bt_start is None:
        raise RuntimeError("not enough warm-up")
    listing_cache = {}
    for day in calendar:
        loaded = load_local_listing(day)
        if loaded is not None:
            listing_cache[day] = loaded
    work_cal = sorted(listing_cache) or calendar
    first_needed = first_trading_day_in_week(bt_start, work_cal)
    weekly, snapshots = build_weekly_top20(
        work_cal,
        listing_cache=listing_cache,
        min_selection=first_needed,
        max_selection=data_end,
    )
    return work_cal, weekly, snapshots


def run_analysis_17_4(*, db_path: Path | None = None, save: bool = True) -> dict:
    loaded = inspect_and_load(db_path)
    coverage = loaded["coverage"]
    prices_by_code = loaded["prices"]
    signals = load_signals_17_3()
    calendar, weekly, snapshots = eval_calendar_and_weekly(prices_by_code, coverage)
    enriched = attach_context(signals, prices_by_code, weekly, calendar)
    if len(enriched) != len(signals):
        raise RuntimeError("signal count changed while attaching context")
    levels = by_level_excess(enriched)
    date_agg = {}
    boot = {}
    for kind in ("buy", "sell"):
        date_agg[kind] = {f"{n}d": date_aggregated(kind, enriched, f"excess_{n}d") for n in HORIZONS}
        boot[kind] = {f"{n}d": bootstrap_date_mean_ci(kind, enriched, f"excess_{n}d") for n in HORIZONS}

    missing = {
        f"return_{n}d": sum(1 for s in enriched if s.get(f"return_{n}d") is None) for n in HORIZONS
    }
    missing.update(
        {f"excess_{n}d": sum(1 for s in enriched if s.get(f"excess_{n}d") is None) for n in HORIZONS}
    )
    missing.update(
        {
            "pre_5d": sum(1 for s in enriched if s.get("pre_5d_return") is None),
            "pre_10d": sum(1 for s in enriched if s.get("pre_10d_return") is None),
        }
    )

    result = {
        "metadata": {
            "db": loaded["path"],
            "signals_source": str(SIGNALS_17_3),
            "data_start": coverage["data_start"],
            "data_end": coverage["data_end"],
            "price_rows": coverage["price_rows"],
            "stock_count": coverage["stock_count"],
            "signal_count_17_3": len(signals),
            "signal_count": len(enriched),
            "unique_top20": len(unique_top20_codes(weekly)),
            "notes": [
                "표본은 날짜·종목 클러스터가 있어 독립이 아니다.",
                "excess = 신호 종목 전방수익률 - 같은 날 TOP20 평균 전방수익률.",
                "SELL excess WIN = 같은 날 TOP20보다 더 하락.",
            ],
        },
        "summary": {
            "buy": len([s for s in enriched if s["signal_type"] == "buy"]),
            "sell": len([s for s in enriched if s["signal_type"] == "sell"]),
            "BUY": levels["BUY"]["ALL"],
            "SELL": levels["SELL"]["ALL"],
        },
        "by_signal_level": levels,
        "date_aggregation": date_agg,
        "bootstrap_date": boot,
        "data_quality": {
            "missing": missing,
            "top20_weeks": len(weekly),
            "snapshots": snapshots,
        },
        "signals": enriched,
    }
    _print_run(result)
    if save:
        save_results(result)
    return result


def _print_run(result: dict) -> None:
    m = result["metadata"]
    s = result["summary"]
    print("=== STEP 17-4 excess vs same-date TOP20 ===")
    print(f"데이터 기간: {m['data_start']} ~ {m['data_end']}")
    print(f"signal count: {m['signal_count']} (17-3 {m['signal_count_17_3']})")
    print(f"BUY {s['buy']} / SELL {s['sell']}")
    for side in ("BUY", "SELL"):
        print(side, s[side]["sample_note"])
        pp = s[side]["pre_post"]
        print(f"  pre5 avg={pp['pre_5d_avg']} med={pp['pre_5d_median']}  pre10 avg={pp['pre_10d_avg']}")
        print(f"  post5 avg={pp['post_5d_avg']} post10={pp['post_10d_avg']} post20={pp['post_20d_avg']}")
        for n in HORIZONS:
            b = s[side][f"{n}d"]
            print(
                f"  {n}D n={b['n']} signal={b['avg_signal']} baseline={b['avg_baseline']} "
                f"excess avg={b['avg_excess']} med={b['median_excess']} win={b['excess_win_rate']}"
            )
    print("Level별 excess:")
    for side in ("BUY", "SELL"):
        for label, stats in result["by_signal_level"][side].items():
            if label == "ALL":
                continue
            print(f"  {side} {label} n={stats['signal_count']}")
            for n in HORIZONS:
                b = stats[f"{n}d"]
                print(f"    {n}D excess avg={b['avg_excess']} med={b['median_excess']} win={b['excess_win_rate']}")
    print("signal-date aggregation (excess):")
    for kind in ("buy", "sell"):
        for n in HORIZONS:
            a = result["date_aggregation"][kind][f"{n}d"]
            print(
                f"  {kind} {n}D event_avg={a['event_avg']} date_avg={a['date_avg']} "
                f"dates={a['date_n']} events={a['event_n']}"
            )
    print("bootstrap 95% CI (date resample, excess mean):")
    for kind in ("buy", "sell"):
        for n in HORIZONS:
            b = result["bootstrap_date"][kind][f"{n}d"]
            print(f"  {kind} {n}D {b}")
    print("데이터 부족:", result["data_quality"]["missing"])


def save_results(result: dict) -> None:
    RESULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload["signals"] = [_json_row(s) for s in result["signals"]]
    RESULT_JSON.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "signal_date",
        "stock_code",
        "signal_type",
        "signal_level",
        "ma3",
        "threshold",
        "next_close",
        "return_5d",
        "return_10d",
        "return_20d",
        "baseline_5d",
        "baseline_10d",
        "baseline_20d",
        "excess_5d",
        "excess_10d",
        "excess_20d",
        "pre_5d_return",
        "pre_10d_return",
    ]
    with RESULT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in result["signals"]:
            writer.writerow({k: _csv(row.get(k)) for k in fields})
    print(f"저장: {RESULT_JSON}")
    print(f"저장: {RESULT_CSV}")


def _json_row(row: dict) -> dict:
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
