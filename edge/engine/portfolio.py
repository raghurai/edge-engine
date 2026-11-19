"""The engine's own paper portfolio. It trades the brain's monthly picks with no human in the loop.

Rules (fixed, so the record is honest):
  - starts with $100,000 cash; no leverage; one position per name
  - BUYS the brain's highest-ranked monthly picks it does not hold, 5% of equity each,
    at most MAX_BUYS_PER_DAY new names a session so entries (and therefore exits) stay staggered
  - SELLS a brain position after HOLD trading days (what the walk-forward tested), or earlier
    if the brain drops it onto the avoid list (bottom tenth) after at least MIN_HOLD days
  - LEGACY positions, bought before 2026-11-19 under the earnings-drift rule the research
    overturned, are sold as soon as the brain ranks them in its bottom half, or at HOLD
    trading days, whichever comes first
  - trades fill at the latest price of a session that follows the scoring close
State lives in docs/engine_portfolio.json and is updated on every export.
"""
import json
from pathlib import Path

import pandas as pd

START_CASH = 100_000.0
POSITION_PCT = 0.05
HOLD = 21
MIN_HOLD = 3
MAX_POSITIONS = 20
MAX_BUYS_PER_DAY = 3
PATH = Path(__file__).resolve().parents[2] / "docs" / "engine_portfolio.json"


def load() -> dict:
    if PATH.exists():
        return json.loads(PATH.read_text())
    return {"started": None, "cash": START_CASH, "open": [], "closed": [], "curve": [], "spy_start": None}


def update(state: dict, picks: list[dict], close: pd.DataFrame, spy: pd.Series, ranks: dict | None = None, can_trade: bool = True) -> dict:
    """picks: the brain's monthly list, best first (symbol, price, exp, for). ranks: symbol -> the
    brain's current monthly percentile (0..1) for every name it scored. can_trade: a session has
    opened since the scoring close, so today's prices are a fair fill."""
    ranks = ranks or {}
    dates = close.index
    today = dates[-1]
    today_s = today.strftime("%Y-%m-%d")
    if state["started"] is None:
        now_et = pd.Timestamp.now(tz="America/New_York")
        is_today_session = today.normalize() == now_et.normalize().tz_localize(None) and now_et.weekday() < 5
        start = today if is_today_session else today + pd.tseries.offsets.BDay(1)
        state["started"] = start.strftime("%Y-%m-%d")
        state["spy_start"] = None
    if state["spy_start"] is None and today >= pd.Timestamp(state["started"]):
        state["spy_start"] = float(spy.iloc[-1])
    live = today >= pd.Timestamp(state["started"])

    def px_now(sym):
        return float(close[sym].iloc[-1]) if sym in close and pd.notna(close[sym].iloc[-1]) else None

    def held_days(p):
        return len(dates) - 1 - dates.searchsorted(pd.Timestamp(p["entry_date"]))

    # 1. Exits.
    still = []
    for p in state["open"]:
        sym, px, days = p["symbol"], px_now(p["symbol"]), held_days(p)
        legacy = p.get("source") is None or "brain" not in p.get("source", "")
        rank = ranks.get(sym)
        reason = None
        if days >= HOLD:
            reason = f"held {HOLD} days"
        elif legacy and rank is not None and rank < 0.5:
            reason = "legacy position the brain ranks in its bottom half"
        elif not legacy and rank is not None and rank <= 0.10 and days >= MIN_HOLD:
            reason = "dropped onto the avoid list"
        if reason and px is not None and live and can_trade:
            ei = dates.searchsorted(pd.Timestamp(p["entry_date"]))
            proceeds = px * p["shares"]
            state["cash"] += proceeds
            bench = (float(spy.iloc[-1]) / float(spy.iloc[min(ei, len(spy) - 1)]) - 1) * 100
            state["closed"].insert(0, {**p, "exit_date": today_s, "exit": round(px, 2), "pnl": round(proceeds - p["cost"], 2),
                                        "ret_pct": round((px / p["entry"] - 1) * 100, 2),
                                        "excess_pct": round((px / p["entry"] - 1) * 100 - bench, 2), "reason": reason})
        else:
            still.append(p)
    state["open"] = still

    # 2. Entries: best-ranked picks not held, a few a day.
    held = {p["symbol"] for p in state["open"]}
    sold_today = {c["symbol"] for c in state["closed"] if c.get("exit_date") == today_s}
    bought_today = sum(1 for p in state["open"] if p["entry_date"] == today_s)
    equity = state["cash"] + sum(p["shares"] * (px_now(p["symbol"]) or p["entry"]) for p in state["open"])
    for w in picks:
        if not (live and can_trade) or bought_today >= MAX_BUYS_PER_DAY or len(held) >= MAX_POSITIONS:
            break
        sym, entry = w["symbol"], px_now(w["symbol"])
        if sym in held or sym in sold_today or not entry or entry <= 0:
            continue
        budget = min(equity * POSITION_PCT, state["cash"])
        shares = int(budget // entry)
        if shares < 1:
            continue
        cost = shares * entry
        state["cash"] -= cost
        held.add(sym); bought_today += 1
        state["open"].append({"symbol": sym, "entry_date": today_s, "entry": round(entry, 2), "shares": shares, "cost": round(cost, 2),
                              "expected_40d": w.get("exp"), "why": (w.get("for") or [])[:3], "hold": HOLD, "source": "brain · monthly model"})

    # 3. Mark and record today's equity point.
    for p in state["open"]:
        px = px_now(p["symbol"]) or p["entry"]
        p.update(price=round(px, 2), value=round(px * p["shares"], 2), pnl=round((px - p["entry"]) * p["shares"], 2),
                 ret_pct=round((px / p["entry"] - 1) * 100, 2), days_left=int(max(0, HOLD - held_days(p))),
                 rank=None if ranks.get(p["symbol"]) is None else round(float(ranks[p["symbol"]]) * 100))
    equity = state["cash"] + sum(p["value"] for p in state["open"])
    spy_ret = (float(spy.iloc[-1]) / state["spy_start"] - 1) * 100 if state["spy_start"] else 0.0
    point = {"date": today_s, "equity": round(equity, 2), "spy_pct": round(spy_ret, 2)}
    if state["curve"] and state["curve"][-1]["date"] == point["date"]:
        state["curve"][-1] = point
    else:
        state["curve"].append(point)

    closed = state["closed"]
    prev = state["curve"][-2]["equity"] if len(state["curve"]) >= 2 else START_CASH
    state["summary"] = {
        "live": live, "equity": round(equity, 2), "cash": round(state["cash"], 2),
        "total_pnl": round(equity - START_CASH, 2), "day_pnl": round(equity - prev, 2),
        "return_pct": round((equity / START_CASH - 1) * 100, 2), "spy_pct": round(spy_ret, 2),
        "open_pnl": round(sum(p["pnl"] for p in state["open"]), 2),
        "realized": round(sum(c["pnl"] for c in closed), 2),
        "n_open": len(state["open"]), "n_closed": len(closed),
        "hit_rate": round(sum(c["pnl"] > 0 for c in closed) / len(closed), 3) if closed else None,
        "avg_excess": round(sum(c["excess_pct"] for c in closed) / len(closed), 2) if closed else None,
        "trades_today": sum(1 for p in state["open"] if p["entry_date"] == today_s) + sum(1 for c in closed if c.get("exit_date") == today_s),
    }
    PATH.write_text(json.dumps(state, indent=1))
    return state
