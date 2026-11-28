"""Runs the self-improving engine.

  python scripts/learn.py              # live: predict every minute, learn as outcomes arrive
  python scripts/learn.py --replay 5   # replay the last 5 trading days minute by minute
  python scripts/learn.py --reset      # forget everything and start fresh

Writes docs/learning.json for the dashboard after every step.
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from edge.data import alpaca
from edge.engine import online

SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META", "MSFT", "AMZN", "PLTR",
           "GOOGL", "NFLX", "AVGO", "INTC", "COIN", "MSTR", "SMCI", "JPM",
           "XOM", "LLY", "BA", "CAT", "QQQ", "IWM", "SOXL", "TQQQ"]
OUT = Path(__file__).resolve().parents[1] / "docs" / "learning.json"
args = sys.argv[1:]
REPLAY_DAYS = int(args[args.index("--replay") + 1]) if "--replay" in args else 0


def session_bars(days: int) -> dict[str, pd.DataFrame]:
    """1-minute bars per symbol, regular session only, oldest first."""
    raw = alpaca.history(SYMBOLS, days=days, timeframe="1min")
    out = {}
    for sym in SYMBOLS:
        if sym not in raw.index.get_level_values("symbol"):
            continue
        b = raw.xs(sym, level="symbol").sort_index()
        et = b.index.tz_convert("America/New_York")
        b = b[(et.time >= pd.Timestamp("09:30").time()) & (et.time <= pd.Timestamp("16:00").time())]
        out[sym] = b
    return out


def step(learner: online.Learner, bars: dict[str, pd.DataFrame]) -> list[dict]:
    """One minute of work: learn from anything resolved, then predict on the newest bar."""
    learner.resolve({s: b["close"] for s, b in bars.items()})
    suggestions = []
    for sym, b in bars.items():
        f = online.features_at(b)
        if f is None:
            continue
        pr = learner.predict(sym, b.index[-1], float(b["close"].iloc[-1]), f)
        if pr:
            suggestions.append({"symbol": sym, "p_up": round(pr["p_up"], 3), "price": pr["price"],
                                "t": pr["t"], "target_t": pr["target_t"],
                                "drivers": {k: round(float(f[k]), 2) for k in ("mom15", "vol_surge", "vwap_dist")}})
    suggestions.sort(key=lambda s: -abs(s["p_up"] - 0.5))
    return suggestions


def write(learner, suggestions, mode):
    s = learner.summary(suggestions)
    s["mode"] = mode
    OUT.write_text(json.dumps(s))


def replay(learner: online.Learner, days: int):
    bars = session_bars(days + 3)
    times = sorted(set().union(*[set(b.index) for b in bars.values()]))
    times = [t for t in times if t >= times[-1] - pd.Timedelta(days=days)]
    print(f"Replaying {len(times)} minutes across {len(bars)} symbols")
    last_write = 0
    for i, t in enumerate(times):
        window = {s: b[b.index <= t].tail(120) for s, b in bars.items()}
        window = {s: w for s, w in window.items() if len(w) and w.index[-1] == t}
        sug = step(learner, window)
        if i - last_write >= 15 or i == len(times) - 1:
            write(learner, sug, f"replay {t.tz_convert('America/New_York'):%b %d %H:%M}")
            last_write = i
            acc = learner.accuracy(500)
            print(f"{t.tz_convert('America/New_York'):%m-%d %H:%M}  resolved {len(learner.resolved):5d}  "
                  f"acc(500) {acc if acc is None else f'{acc:.1%}'}  weights {learner.weights()}")
    learner.save()


def live(learner: online.Learner):
    print("Live: predicting every minute, learning as outcomes arrive. Ctrl+C to stop.")
    while True:
        try:
            bars = {s: b.tail(120) for s, b in session_bars(3).items()}
            sug = step(learner, bars)
            write(learner, sug, "live")
            learner.save()
            acc = learner.accuracy(500)
            print(f"{datetime.now():%H:%M:%S}  pending {len(learner.pending):3d}  resolved {len(learner.resolved):5d}  "
                  f"acc(500) {acc if acc is None else f'{acc:.1%}'}")
        except Exception as e:
            print("step error:", e)
        time.sleep(60)


if __name__ == "__main__":
    learner = online.Learner() if "--reset" in args else online.Learner.load()
    if REPLAY_DAYS:
        replay(learner, REPLAY_DAYS)
    else:
        live(learner)
