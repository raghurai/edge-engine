"""Is the bad-news bounce real? Split by half-year and by stock, with t-stats."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from edge.data import alpaca, news as news_data
from edge.signals import news as news_signal, price

watchlist = ["NVDA", "TSLA", "AAPL", "AMD", "PLTR", "META", "AMZN", "MSFT",
             "SOXL", "TQQQ", "COIN", "MSTR", "GME", "AMC", "SMCI", "INTC"]
H = 5

bars = alpaca.history(watchlist + [price.BENCHMARK], days=365 * 2)
close = bars["close"].unstack(level="symbol")
idx = pd.DatetimeIndex(close.index.tz_convert("UTC").normalize())
close.index = idx
scored = news_signal.score_headlines(news_data.headlines(watchlist, days=365 * 2))

day = (scored["time"].dt.tz_convert("UTC") - pd.Timedelta(hours=20)).dt.normalize() + pd.Timedelta(days=1)
pos = idx.searchsorted(pd.DatetimeIndex(day.dt.tz_convert("UTC")))
s = scored[pos < len(idx)].copy()
s["date"] = idx[pos[pos < len(idx)]]
daily = s.groupby(["date", "symbol"])["score"].agg(mean="mean", n="size")

fwd = ((close.shift(-H) / close - 1) * 100)
spy = fwd[price.BENCHMARK]
ex = fwd.drop(columns=price.BENCHMARK).sub(spy, axis=0).stack()
ex.index.names = ["date", "symbol"]
d = daily.join(ex.rename("excess"), how="inner").dropna()
d["bad"] = d["mean"] < -0.3

def report(g, label):
    bad, rest = g.loc[g["bad"], "excess"], g.loc[~g["bad"], "excess"]
    if len(bad) < 5 or len(rest) < 5:
        return
    t =(bad.mean() - rest.mean()) / np.sqrt(bad.var() / len(bad) + rest.var() / len(rest))
    print(f"{label:>14}  bad n={len(bad):4d} {bad.mean():+.2f}%   rest n={len(rest):4d} {rest.mean():+.2f}%   diff {bad.mean()-rest.mean():+.2f}  t={t:+.2f}")

print(f"forward {H}d excess return after bad-news days (mean FinBERT < -0.3)\n")
report(d, "all")
half = d.index.get_level_values("date").tz_localize(None).to_period("Q")
for q, g in d.groupby(half):
    report(g, str(q))
print()
for sym, g in d.groupby(level="symbol"):
    report(g, sym)
print("\nnon-overlapping check: only every 5th trading day")
sub = d[d.index.get_level_values("date").isin(idx[::H])]
report(sub, "every 5th")
