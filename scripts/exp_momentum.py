"""Experiment: cross-sectional momentum, done the way the literature does it.

Hypothesis: ranking ~1000 liquid stocks by their 12-month return (skipping the
most recent month) and holding the top decile vs the bottom decile for one month
earns a positive spread, after costs.

Walk-forward by construction: each month's ranking uses only past prices.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from edge.data import wide

COST_PCT = 0.10   # round-trip cost per rebalance, in percent (spread + slippage on liquid names)

top = wide.most_liquid(1000)
bars = wide.daily_bars(top, years=4)
close = bars["close"].unstack(level="symbol")
close.index = close.index.tz_convert("UTC").normalize()
# Month-end prices
me = close.resample("ME").last().dropna(how="all")

results = []
for i in range(12, len(me) - 1):
    # signal: return from month t-12 to t-1 (skip the last month)
    sig = me.iloc[i - 1] / me.iloc[i - 12] - 1
    fwd = me.iloc[i + 1] / me.iloc[i] - 1
    ok = sig.notna() & fwd.notna() & (me.iloc[i] > 2)
    sig, fwd = sig[ok], fwd[ok]
    if len(sig) < 200:
        continue
    dec = pd.qcut(sig.rank(method="first"), 10, labels=False)
    by = fwd.groupby(dec).mean() * 100
    results.append({"month": me.index[i + 1].strftime("%Y-%m"), "n": len(sig),
                    "top": by[9], "bottom": by[0], "spread": by[9] - by[0], "all": fwd.mean() * 100,
                    **{f"d{k}": by[k] for k in range(10)}})
r = pd.DataFrame(results).set_index("month")

pd.set_option("display.float_format", "{:.2f}".format)
print(f"months: {len(r)}   names/month: {int(r['n'].mean())}\n")
print("Average next-month return by momentum decile (d0 = losers, d9 = winners), %:")
print(r[[f"d{k}" for k in range(10)]].mean().round(2).to_string())
spread = r["spread"] - COST_PCT * 2
t = spread.mean() / (spread.std() / np.sqrt(len(spread)))
print(f"\nTop-minus-bottom spread after costs: {spread.mean():+.2f}%/month   t={t:+.2f}   "
      f"positive months {(spread > 0).mean():.0%}")
print(f"Top decile vs all stocks: {(r['top'] - r['all']).mean():+.2f}%/month   "
      f"t={((r['top'] - r['all']).mean() / ((r['top'] - r['all']).std() / np.sqrt(len(r)))):+.2f}")
print("\nBy year:")
print(r.groupby(r.index.str[:4])[["top", "bottom", "spread", "all"]].mean().round(2).to_string())
