"""The engine's own paper portfolio. It trades its WILL tier with no human in the loop.

Rules (fixed, so the record is honest):
  - starts with $100,000 cash
  - buys every name that reaches WILL, at that name's entry close, 5% of equity per position
  - holds HOLD_DAYS trading days, sells at the close that day
  - one position per name at a time, no leverage, cash can run out (then it waits)
State lives in docs/engine_portfolio.json and is updated on every export.
"""
import json
from pathlib import Path

import pandas as pd

from edge.signals.earnings import HOLD_DAYS

START_CASH = 100_000.0
POSITION_PCT = 0.05
PATH = Path(__file__).resolve().parents[2] / "docs" / "engine_portfolio.json"


def load() -> dict:
    if PATH.exists():
        return json.loads(PATH.read_text())
    return {"started": None, "cash": START_CASH, "open": [], "closed": [], "curve": [], "spy_start": None}


def update(state: dict, will_rows: list[dict], close: pd.DataFrame, spy: pd.Series) -> dict:
    dates = close.index
    today = dates[-1]
    if state["started"] is None:
        # Goes live at the current session if the market is open today, else the next one.
        # No inherited positions, no backfilled entries from before the start.
        now_et = pd.Timestamp.now(tz="America/New_York")
        is_today_session = today.normalize() == now_et.normalize().tz_localize(None) and now_et.weekday() < 5
        start = today if is_today_session else today + pd.tseries.offsets.BDay(1)
        state["started"] = start.strftime("%Y-%m-%d")
        state["spy_start"] = None          # set on the first session at or after the start
    if state["spy_start"] is None and today >= pd.Timestamp(state["started"]):
        state["spy_start"] = float(spy.iloc[-1])
    live = today >= pd.Timestamp(state["started"])

    # 1. Exits: anything held HOLD_DAYS trading days sells at that day's close.
    still = []
    for p in state["open"]:
        ei = dates.searchsorted(pd.Timestamp(p["entry_date"]))
        xi = ei + HOLD_DAYS
        if xi < len(dates) and p["symbol"] in close and pd.notna(close[p["symbol"]].iloc[xi]):
            px = float(close[p["symbol"]].iloc[xi])
            proceeds = px * p["shares"]
            state["cash"] += proceeds
            bench = (float(spy.iloc[xi]) / float(spy.iloc[ei]) - 1) * 100
            state["closed"].insert(0, {**p, "exit_date": dates[xi].strftime("%Y-%m-%d"), "exit": round(px, 2),
                                        "pnl": round(proceeds - p["cost"], 2), "ret_pct": round((px / p["entry"] - 1) * 100, 2),
                                        "excess_pct": round((px / p["entry"] - 1) * 100 - bench, 2)})
        else:
            still.append(p)
    state["open"] = still

    # 2. Entries: every WILL name with a real entry date that we don't already hold.
    held = {p["symbol"] for p in state["open"]}
    equity = state["cash"] + sum(p["shares"] * float(close[p["symbol"]].iloc[-1]) for p in state["open"] if p["symbol"] in close)
    for w in sorted(will_rows, key=lambda r: -r.get("expected_40d", 0)):
        sym, entry_date, entry = w["symbol"], w.get("entry_date"), w.get("entry")
        if not live or not entry_date or not entry or sym in held or w.get("status") == "pending":
            continue
        if pd.Timestamp(entry_date) < pd.Timestamp(state["started"]):
            continue   # signal predates the engine's start: not its trade
        if any(c["symbol"] == sym and c["entry_date"] == entry_date for c in state["closed"]):
            continue
        budget = min(equity * POSITION_PCT, state["cash"])
        shares = int(budget // entry)
        if shares < 1:
            continue
        cost = shares * entry
        state["cash"] -= cost
        held.add(sym)
        state["open"].append({"symbol": sym, "entry_date": entry_date, "entry": entry, "shares": shares, "cost": round(cost, 2),
                              "expected_40d": w.get("expected_40d"), "why": w.get("for", [])[:3]})

    # 3. Mark and record today's equity point.
    for p in state["open"]:
        px = float(close[p["symbol"]].iloc[-1]) if p["symbol"] in close and pd.notna(close[p["symbol"]].iloc[-1]) else p["entry"]
        ei = dates.searchsorted(pd.Timestamp(p["entry_date"]))
        p.update(price=round(px, 2), value=round(px * p["shares"], 2), pnl=round((px - p["entry"]) * p["shares"], 2),
                 ret_pct=round((px / p["entry"] - 1) * 100, 2), days_left=int(max(0, HOLD_DAYS - (len(dates) - 1 - ei))))
    equity = state["cash"] + sum(p["value"] for p in state["open"])
    spy_ret = (float(spy.iloc[-1]) / state["spy_start"] - 1) * 100 if state["spy_start"] else 0.0
    point = {"date": today.strftime("%Y-%m-%d"), "equity": round(equity, 2), "spy_pct": round(spy_ret, 2)}
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
    }
    PATH.write_text(json.dumps(state, indent=1))
    return state
