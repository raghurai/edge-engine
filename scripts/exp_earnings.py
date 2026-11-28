"""Experiment: post-earnings-announcement drift.

Hypothesis: after a large earnings surprise, the stock keeps drifting in the
surprise's direction for the next 20-60 trading days (excess over SPY).

Earnings dates and EPS surprises come from Yahoo (free, no key) for the most
liquid names; prices from the Alpaca cache.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import yfinance as yf

from edge.data import wide

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
CACHE = wide.CACHE / f"earnings_{N}.parquet"

top = wide.most_liquid(1000)
bars = wide.daily_bars(top, years=4)
close = bars["close"].unstack(level="symbol")
close.index = close.index.tz_convert("UTC").normalize().tz_localize(None)
dates = close.index

if CACHE.exists():
    ev = pd.read_parquet(CACHE)
else:
    rows = []
    for k, sym in enumerate(top[:N]):
        try:
            ed = yf.Ticker(sym).get_earnings_dates(limit=20)
            if ed is None or ed.empty:
                continue
            ed = ed.dropna(subset=["Surprise(%)"])
            for t, row in ed.iterrows():
                rows.append({"symbol": sym, "date": pd.Timestamp(t).tz_localize(None).normalize(),
                             "surprise": float(row["Surprise(%)"])})
        except Exception as e:
            pass
        if k % 50 == 0:
            print(f"  earnings {k}/{N}  events so far {len(rows)}")
        time.sleep(0.3)
    ev = pd.DataFrame(rows)
    ev.to_parquet(CACHE)

ev = ev[(ev["date"] > dates[0] + pd.Timedelta(days=5)) & (ev["date"] < dates[-1] - pd.Timedelta(days=90))]
print(f"events: {len(ev)}  symbols: {ev['symbol'].nunique()}  from {ev['date'].min():%Y-%m} to {ev['date'].max():%Y-%m}\n")

spy = close["SPY"] if "SPY" in close else None

def excess(sym, d0, start, end):
    """Excess return (%) over SPY from `start` to `end` trading days after the first post-earnings close."""
    pos = dates.searchsorted(d0)
    if pos + 1 + end >= len(dates) or sym not in close:
        return np.nan
    p = close[sym]
    a, b = p.iloc[pos + 1 + start], p.iloc[pos + 1 + end]
    s = spy.iloc[pos + 1 + end] / spy.iloc[pos + 1 + start] - 1
    return (b / a - 1 - s) * 100 if a > 0 else np.nan

for lo, hi in [(0, 1), (1, 20), (1, 40), (1, 60)]:
    ev[f"ex_{lo}_{hi}"] = [excess(s, d, lo, hi) for s, d in zip(ev["symbol"], ev["date"])]
ev["day0"] = [excess(s, d, -1, 0) for s, d in zip(ev["symbol"], ev["date"])]   # announcement-day reaction

ev = ev.dropna()
ev["bucket"] = pd.qcut(ev["surprise"], 5, labels=["big miss", "miss", "inline", "beat", "big beat"], duplicates="drop")
pd.set_option("display.float_format", "{:.2f}".format)
print("Mean excess return (%) after the announcement, by EPS-surprise quintile:")
print(ev.groupby("bucket", observed=True)[["day0", "ex_1_20", "ex_1_40", "ex_1_60"]].mean().round(2).to_string())
print("\nSame, by the stock's own day-0 reaction (the market's verdict, not the EPS number):")
ev["react"] = pd.qcut(ev["day0"], 5, labels=["crushed", "down", "flat", "up", "soared"])
g = ev.groupby("react", observed=True)[["day0", "ex_1_20", "ex_1_40", "ex_1_60"]]
print(g.mean().round(2).to_string())
top_, bot = ev[ev["react"] == "soared"]["ex_1_40"], ev[ev["react"] == "crushed"]["ex_1_40"]
t = (top_.mean() - bot.mean()) / np.sqrt(top_.var() / len(top_) + bot.var() / len(bot))
print(f"\nDrift spread (soared minus crushed, 40d): {top_.mean() - bot.mean():+.2f}%   t={t:+.2f}   n={len(top_)}+{len(bot)}")

print("\nBig beat (top 20% surprise) vs everything else:")
cut = ev["surprise"].quantile(0.8)
bb, rest = ev[ev["surprise"] >= cut], ev[ev["surprise"] < cut]
for h in ("ex_1_20", "ex_1_40", "ex_1_60"):
    t = (bb[h].mean() - rest[h].mean()) / np.sqrt(bb[h].var() / len(bb) + rest[h].var() / len(rest))
    print(f"  {h}: big-beat {bb[h].mean():+.2f}%  rest {rest[h].mean():+.2f}%  diff {bb[h].mean() - rest[h].mean():+.2f}  t={t:+.2f}  n={len(bb)}")
print("\nBig-beat drift by year (mean excess %, n):")
print(bb.groupby(bb["date"].dt.year)[["ex_1_20", "ex_1_40", "ex_1_60"]].agg(["mean", "size"]).round(2).to_string())
print("\nBig beat AND the market liked it (day0 > +2%):")
bl = bb[bb["day0"] > 2]
print(f"  n={len(bl)}  20d {bl['ex_1_20'].mean():+.2f}%  40d {bl['ex_1_40'].mean():+.2f}%  60d {bl['ex_1_60'].mean():+.2f}%  hit rate 40d {(bl['ex_1_40'] > 0).mean():.0%}")
