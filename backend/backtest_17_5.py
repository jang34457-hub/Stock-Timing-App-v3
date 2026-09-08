"""STEP 17-5: MA3 Timing vs Buy & Hold (동일 종목·동일 시점).

production prices.db / 전략 코드는 사용·변경하지 않는다.
"""

from __future__ import annotations

import csv
import json
import statistics
from datetime import date
from pathlib import Path

import pandas as pd

import backtest_db
from backtest import detect_signals_asof

INITIAL = 1_000_000.0
N_STOCKS = 10
ILLUSTRATIVE_BUY_FEE = 0.00015
ILLUSTRATIVE_SELL_FEE = 0.00195

RESULT_JSON = Path(__file__).resolve().parent / "data" / "backtest_result_17_5.json"
RESULT_CSV = Path(__file__).resolve().parent / "data" / "backtest_result_17_5.csv"
TRADES_CSV = Path(__file__).resolve().parent / "data" / "backtest_trades_17_5.csv"
EQUITY_CSV = Path(__file__).resolve().parent / "data" / "backtest_equity_17_5.csv"


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _median(vals: list[float]) -> float | None:
    return float(statistics.median(vals)) if vals else None


def max_drawdown(equity: list[float]) -> float:
    peak = 0.0
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd * 100.0


def next_trading_day(dates: list[date], day: date) -> date | None:
    try:
        i = dates.index(day)
    except ValueError:
        return None
    if i + 1 >= len(dates):
        return None
    return dates[i + 1]


def apply_buy(cash: float, price: float, fee: float) -> tuple[float, float]:
    shares = (cash * (1.0 - fee)) / price
    return 0.0, shares


def apply_sell(shares: float, price: float, fee: float) -> tuple[float, float]:
    return shares * price * (1.0 - fee), 0.0


def buy_and_hold(
    dates: list[date],
    closes: dict[date, float],
    start: date,
    end: date,
    *,
    initial: float = INITIAL,
    buy_fee: float = 0.0,
) -> dict:
    px0 = float(closes[start])
    _cash, shares = apply_buy(initial, px0, buy_fee)
    curve = []
    for d in dates:
        if d < start or d > end:
            continue
        curve.append({"date": d, "equity": shares * float(closes[d]), "cash": 0.0, "shares": shares})
    final_eq = shares * float(closes[end])
    return {
        "shares": shares,
        "cash": 0.0,
        "stock_value": final_eq,
        "final": final_eq,
        "profit": final_eq - initial,
        "return_pct": (final_eq / initial - 1.0) * 100.0,
        "trades": 1,
        "sells": 0,
        "buys": 1,
        "mdd": max_drawdown([r["equity"] for r in curve]),
        "equity": curve,
    }


def timing_strategy(
    prices: pd.DataFrame,
    start: date,
    end: date,
    *,
    initial: float = INITIAL,
    buy_fee: float = 0.0,
    sell_fee: float = 0.0,
    stock_code: str = "",
) -> dict:
    rows = prices.sort_values("date")
    dates = [d for d in rows["date"].tolist() if start <= d <= end]
    closes = {row.date: float(row.close) for row in rows.itertuples() if start <= row.date <= end}
    cash, shares = apply_buy(initial, closes[start], buy_fee)
    holding = True
    pending: dict | None = None
    trades: list[dict] = []
    skipped: list[dict] = []
    curve: list[dict] = []
    sells = 0
    buys = 1

    for d in dates:
        px = closes[d]
        if pending is not None and pending["execution_date"] == d:
            if pending["action"] == "SELL" and holding and shares > 0:
                cash, shares = apply_sell(shares, px, sell_fee)
                holding = False
                sells += 1
                trades.append(_trade_row(stock_code, pending, px, 0.0, cash, cash))
            elif pending["action"] == "BUY" and (not holding) and cash > 0:
                cash, shares = apply_buy(cash, px, buy_fee)
                holding = True
                buys += 1
                trades.append(_trade_row(stock_code, pending, px, shares, cash, shares * px))
            pending = None

        equity = cash + shares * px
        curve.append(
            {
                "date": d,
                "equity": equity,
                "cash": cash,
                "shares": shares,
                "state": "HOLDING" if holding else "CASH",
            }
        )

        if pending is not None:
            continue
        sigs = detect_signals_asof(prices, d, stock_code=stock_code)
        actionable = None
        for sig in sigs:
            if sig["signal_type"] == "sell" and holding:
                actionable = sig
                break
            if sig["signal_type"] == "buy" and not holding:
                actionable = sig
                break
        if actionable is None:
            continue
        nxt = next_trading_day(dates, d)
        payload = {
            "signal_date": d,
            "signal": f"{actionable['signal_type'].upper()}{actionable['signal_level']}",
            "action": "SELL" if actionable["signal_type"] == "sell" else "BUY",
            "level": actionable["signal_level"],
        }
        if nxt is None:
            skipped.append({**payload, "reason": "no_next_trading_day"})
            continue
        pending = {**payload, "execution_date": nxt}

    final = cash + shares * closes[end]
    return {
        "shares": shares,
        "cash": cash,
        "stock_value": shares * closes[end],
        "final": final,
        "profit": final - initial,
        "return_pct": (final / initial - 1.0) * 100.0,
        "trades": sells + buys,
        "sells": sells,
        "buys": buys,
        "mdd": max_drawdown([r["equity"] for r in curve]),
        "equity": curve,
        "trade_log": trades,
        "skipped": skipped,
        "cycles": _cycles(trades, dates, closes, end),
        "holding": holding,
    }


def _trade_row(code, pending, px, shares, cash, portfolio) -> dict:
    return {
        "stock": code,
        "signal_date": pending["signal_date"],
        "signal": pending["signal"],
        "execution_date": pending["execution_date"],
        "price": px,
        "shares": shares,
        "cash_after": cash,
        "portfolio_value": portfolio,
        "action": pending["action"],
        "level": pending["level"],
    }


def _cycles(trades: list[dict], dates: list[date], closes: dict[date, float], end: date) -> list[dict]:
    out: list[dict] = []
    sell = None
    for t in trades:
        if t["action"] == "SELL":
            sell = t
            continue
        if t["action"] == "BUY" and sell is not None:
            lo, hi = sell["execution_date"], t["execution_date"]
            window = [closes[d] for d in dates if lo < d <= hi]
            min_px = min(window) if window else None
            sell_px = float(sell["price"])
            buy_px = float(t["price"])
            success = buy_px < sell_px
            out.append(
                {
                    "sell_date": _iso(lo),
                    "sell_price": sell_px,
                    "min_after_sell": min_px,
                    "buy_date": _iso(hi),
                    "buy_price": buy_px,
                    "reentry_cheaper": success,
                    "share_change_pct": ((sell_px / buy_px) - 1.0) * 100.0 if buy_px else None,
                    "outcome": "avoided_drop_reentered_lower" if success else "reentered_higher_or_equal",
                }
            )
            sell = None
    if sell is not None:
        lo = sell["execution_date"]
        window = [closes[d] for d in dates if lo < d <= end]
        out.append(
            {
                "sell_date": _iso(lo),
                "sell_price": float(sell["price"]),
                "min_after_sell": min(window) if window else None,
                "buy_date": None,
                "buy_price": None,
                "reentry_cheaper": None,
                "share_change_pct": None,
                "outcome": "no_reentry_ended_in_cash",
            }
        )
    return out


def _iso(value) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def load_stock_meta(path: Path | None = None) -> dict[str, dict]:
    with backtest_db.connect(path) as conn:
        rows = conn.execute("SELECT stock_code, stock_name, market FROM stocks").fetchall()
    return {r[0]: {"stock_name": r[1], "market": r[2]} for r in rows}


def select_ten_stocks(path: Path | None = None) -> dict:
    coverage = backtest_db.inspect_coverage(path)
    start, end = coverage["data_start"], coverage["data_end"]
    if not start or not end:
        raise RuntimeError("empty backtest DB")
    complete = [s for s in coverage["stocks"] if s["start"] == start and s["end"] == end]
    top20 = set(backtest_db.load_all_top20_codes(path))
    meta = load_stock_meta(path)
    ranked = []
    for s in complete:
        code = s["stock_code"]
        if top20 and code not in top20:
            continue
        df = backtest_db.load_prices(code, path)
        avg_tv = float(pd.to_numeric(df["trading_value"], errors="coerce").mean() or 0.0)
        info = meta.get(code, {"stock_name": code, "market": "KOSPI"})
        name = info["stock_name"]
        if str(name).endswith("우"):
            continue
        ranked.append(
            {
                "stock_code": code,
                "stock_name": name,
                "market": info["market"],
                "avg_trading_value": avg_tv,
                "rows": s["rows"],
                "start": s["start"],
                "end": s["end"],
            }
        )
    ranked.sort(key=lambda r: (-r["avg_trading_value"], r["stock_code"]))
    kospi = [r for r in ranked if r["market"] == "KOSPI"]
    kosdaq = [r for r in ranked if r["market"] == "KOSDAQ"]
    picked: list[dict] = []
    for row in kospi[:7] + kosdaq[:3]:
        if row not in picked:
            picked.append(row)
    for row in ranked:
        if len(picked) >= N_STOCKS:
            break
        if row not in picked:
            picked.append(row)
    picked = picked[:N_STOCKS]
    if len(picked) < N_STOCKS:
        raise RuntimeError(f"need {N_STOCKS} stocks, got {len(picked)}")
    if {p["start"] for p in picked} != {start} or {p["end"] for p in picked} != {end}:
        raise RuntimeError("selected stocks do not share start/end")
    reason = (
        "전체 기간 시세가 있는 historical TOP20 중 우선주(우) 제외, "
        "평균 거래대금 상위 KOSPI 최대 7 + KOSDAQ 최대 3, 부족분은 거래대금 순 보충. "
        "동일 발행사 우선주를 빼 삼성전자 계열에 결과가 치우치지 않게 했다."
    )
    for p in picked:
        p["reason"] = reason
    return {
        "start": start,
        "end": end,
        "calendar_stocks": len(complete),
        "top20_unique": len(top20),
        "stocks": picked,
        "selection_rule": reason,
    }


def compare_stock(code: str, prices: pd.DataFrame, start: date, end: date, fees: tuple[float, float]) -> dict:
    buy_fee, sell_fee = fees
    dates = list(prices.sort_values("date")["date"])
    closes = {row.date: float(row.close) for row in prices.itertuples()}
    bh = buy_and_hold(dates, closes, start, end, buy_fee=buy_fee)
    tm = timing_strategy(prices, start, end, buy_fee=buy_fee, sell_fee=sell_fee, stock_code=code)
    adv = tm["final"] - bh["final"]
    adv_pct = (tm["final"] / bh["final"] - 1.0) * 100.0 if bh["final"] else None
    tf, bf = round(tm["final"], 2), round(bh["final"], 2)
    if tf > bf:
        winner = "MA3_TIMING"
    elif tf < bf:
        winner = "BUY_AND_HOLD"
    else:
        winner = "TIE"
    return {
        "stock_code": code,
        "timing": tm,
        "bh": bh,
        "advantage": adv,
        "advantage_pct": adv_pct,
        "mdd_improvement": bh["mdd"] - tm["mdd"],
        "winner": winner,
    }


def run_backtest_17_5(*, db_path: Path | None = None, save: bool = True) -> dict:
    path = db_path or backtest_db.DB_PATH
    prod = Path(__file__).resolve().parent / "data" / "prices.db"
    if path.resolve() == prod.resolve():
        raise RuntimeError("production prices.db must not be used")
    selection = select_ten_stocks(path)
    start = date.fromisoformat(selection["start"])
    end = date.fromisoformat(selection["end"])
    zero = _run_universe(path, selection["stocks"], start, end, (0.0, 0.0))
    cost = _run_universe(
        path, selection["stocks"], start, end, (ILLUSTRATIVE_BUY_FEE, ILLUSTRATIVE_SELL_FEE)
    )
    result = {
        "metadata": {
            "db": str(path),
            "production_db_used": False,
            "start": selection["start"],
            "end": selection["end"],
            "initial": INITIAL,
            "x1": 10,
            "x2": 20,
            "y1": 10,
            "y2": 20,
            "execution": "next_trading_day_close",
            "selection": selection,
            "cost_note": "A=0. B=매수 0.015% + 매도 0.195% 예시(코드베이스에 수수료 정의 없음).",
        },
        "cost_0": zero,
        "cost_illustrative": cost,
    }
    _print_report(result)
    if save:
        save_results(result)
    return result


def _run_universe(path, stocks, start, end, fees) -> dict:
    rows = []
    trades = []
    equity_rows = []
    skipped = []
    cycles = []
    for spec in stocks:
        code = spec["stock_code"]
        prices = backtest_db.load_prices(code, path)
        cmp_ = compare_stock(code, prices, start, end, fees)
        rows.append({**cmp_, "stock_name": spec["stock_name"], "market": spec["market"]})
        trades.extend(cmp_["timing"]["trade_log"])
        skipped.extend({"stock": code, **s} for s in cmp_["timing"]["skipped"])
        cycles.extend({"stock": code, **c} for c in cmp_["timing"]["cycles"])
        bh_eq = {r["date"]: r["equity"] for r in cmp_["bh"]["equity"]}
        for r in cmp_["timing"]["equity"]:
            equity_rows.append(
                {
                    "stock": code,
                    "date": r["date"],
                    "timing_equity": r["equity"],
                    "bh_equity": bh_eq.get(r["date"]),
                    "state": r["state"],
                }
            )
    timing_rets = [r["timing"]["return_pct"] for r in rows]
    bh_rets = [r["bh"]["return_pct"] for r in rows]
    advs = [r["advantage_pct"] for r in rows if r["advantage_pct"] is not None]
    winners = [r["winner"] for r in rows]
    return {
        "per_stock": rows,
        "trades": trades,
        "skipped": skipped,
        "cycles": cycles,
        "equity": equity_rows,
        "summary": {
            "timing_wins": winners.count("MA3_TIMING"),
            "bh_wins": winners.count("BUY_AND_HOLD"),
            "ties": winners.count("TIE"),
            "win_rate": winners.count("MA3_TIMING") / len(rows) if rows else None,
            "timing_avg_return": _mean(timing_rets),
            "bh_avg_return": _mean(bh_rets),
            "timing_median_return": _median(timing_rets),
            "bh_median_return": _median(bh_rets),
            "timing_avg_mdd": _mean([r["timing"]["mdd"] for r in rows]),
            "bh_avg_mdd": _mean([r["bh"]["mdd"] for r in rows]),
            "advantage_avg": _mean(advs),
            "advantage_median": _median(advs),
            "total_sells": sum(r["timing"]["sells"] for r in rows),
            "total_buys": sum(r["timing"]["buys"] for r in rows),
            "cycle_count": len(cycles),
            "successful_cycles": sum(1 for c in cycles if c.get("outcome") == "avoided_drop_reentered_lower"),
            "failed_cycles": sum(1 for c in cycles if c.get("outcome") == "reentered_higher_or_equal"),
            "open_cash_cycles": sum(1 for c in cycles if c.get("outcome") == "no_reentry_ended_in_cash"),
        },
    }


def save_results(result: dict) -> None:
    RESULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULT_JSON.write_text(json.dumps(_jsonable(result), ensure_ascii=False, indent=2), encoding="utf-8")
    z = result["cost_0"]
    with RESULT_CSV.open("w", encoding="utf-8", newline="") as fh:
        fields = [
            "stock_code",
            "stock_name",
            "timing_final",
            "bh_final",
            "advantage",
            "timing_return",
            "bh_return",
            "winner",
            "timing_mdd",
            "bh_mdd",
            "timing_sells",
            "timing_buys",
        ]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in z["per_stock"]:
            w.writerow(
                {
                    "stock_code": r["stock_code"],
                    "stock_name": r["stock_name"],
                    "timing_final": r["timing"]["final"],
                    "bh_final": r["bh"]["final"],
                    "advantage": r["advantage"],
                    "timing_return": r["timing"]["return_pct"],
                    "bh_return": r["bh"]["return_pct"],
                    "winner": r["winner"],
                    "timing_mdd": r["timing"]["mdd"],
                    "bh_mdd": r["bh"]["mdd"],
                    "timing_sells": r["timing"]["sells"],
                    "timing_buys": r["timing"]["buys"],
                }
            )
    with TRADES_CSV.open("w", encoding="utf-8", newline="") as fh:
        fields = [
            "stock",
            "signal_date",
            "signal",
            "execution_date",
            "price",
            "shares",
            "cash_after",
            "portfolio_value",
        ]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for t in z["trades"]:
            w.writerow({k: _csv(t.get(k)) for k in fields})
    with EQUITY_CSV.open("w", encoding="utf-8", newline="") as fh:
        fields = ["stock", "date", "timing_equity", "bh_equity", "state"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in z["equity"]:
            w.writerow({k: _csv(r.get(k)) for k in fields})
    print(f"저장: {RESULT_JSON}")
    print(f"저장: {RESULT_CSV}")
    print(f"저장: {TRADES_CSV}")
    print(f"저장: {EQUITY_CSV}")


def _print_report(result: dict) -> None:
    m = result["metadata"]
    z = result["cost_0"]
    s = z["summary"]
    print("## 1. 데이터 기간")
    print(f"{m['start']} ~ {m['end']}")
    print("## 2. 선정된 10개 종목과 선정 이유")
    print(m["selection"]["selection_rule"])
    for st in m["selection"]["stocks"]:
        print(f"  {st['stock_code']} {st['stock_name']} {st['market']} avgTV={st['avg_trading_value']:.0f}")
    print("## 3. 투자 조건")
    print(f"종목당 {INITIAL:,.0f}원, 시작일 종가 전액 매수, fractional share, 체결=D+1 종가, X/Y=10/20")
    print("## 4-6. 종목별 결과 (비용 0)")
    print("| 종목 | Timing 최종자산 | B&H 최종자산 | 차이 | Timing 수익률 | B&H 수익률 | 승자 | Timing MDD | B&H MDD |")
    print("| -- | ----------: | -------: | -: | ---------: | ------: | -- | ---------: | ------: |")
    for r in z["per_stock"]:
        print(
            f"| {r['stock_code']} | {r['timing']['final']:,.0f} | {r['bh']['final']:,.0f} | "
            f"{r['advantage']:,.0f} | {r['timing']['return_pct']:.2f}% | {r['bh']['return_pct']:.2f}% | "
            f"{r['winner']} | {r['timing']['mdd']:.2f}% | {r['bh']['mdd']:.2f}% |"
        )
    print("## 7. 전체 요약")
    print(f"Timing 승 {s['timing_wins']} / B&H 승 {s['bh_wins']} / 무승부 {s['ties']}")
    print(f"Timing 평균 수익률 {s['timing_avg_return']:.3f}%  중앙값 {s['timing_median_return']:.3f}%")
    print(f"B&H 평균 수익률 {s['bh_avg_return']:.3f}%  중앙값 {s['bh_median_return']:.3f}%")
    print(f"Timing 평균 MDD {s['timing_avg_mdd']:.3f}%  B&H 평균 MDD {s['bh_avg_mdd']:.3f}%")
    print(f"Timing advantage 평균 {s['advantage_avg']:.3f}%  중앙값 {s['advantage_median']:.3f}%")
    print("## 8. 거래 분석")
    print(f"총 SELL {s['total_sells']} 총 BUY {s['total_buys']} (초기 매수 포함)")
    print(
        f"사이클 {s['cycle_count']} / 낮은 재진입 {s['successful_cycles']} / "
        f"높은 재진입 {s['failed_cycles']} / 미재진입 {s['open_cash_cycles']}"
    )
    print("## 9. 거래비용 0")
    print(f"승률 {s['win_rate']}")
    c = result["cost_illustrative"]["summary"]
    print("## 10. 거래비용 예시 적용")
    print(f"Timing 승 {c['timing_wins']} / B&H 승 {c['bh_wins']} / advantage 평균 {c['advantage_avg']}")


def _csv(value):
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
