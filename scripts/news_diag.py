"""Diagnose the news signal: does headline sentiment predict next-day returns at all?

Uses the cached headlines and FinBERT scores. Prints score distribution,
correlation with forward returns at 1/5/21 days, and returns by sentiment bucket.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from edge.data import alpaca, news as news_data
from edge.signals import news as news_signal, price

watchlist = ["NVDA", "TSLA", "AAPL", "AMD", "PLTR", "META", "AMZN", "MSFT",
             "SOXL", "TQQQ", "COIN", "MSTR", "GME", "AMC", "SMCI", "INTC"]
years = 2

bars = alpaca.history(watchlist + [price.BENCHMARK], days=365 * years)
close = bars["close"].unstack(level="symbol")
dates = close.index
scored = news_signal.score_headlines(news_data.headlines(watchlist, days=365 * years))

print(f"headlines: {len(scored)}   score mean {scored['score'].mean():.3f}   "
      f"|score|>0.5: {(scored['score'].abs() > 0.5).mean():.1%}   neg: {(scored['score'] < -0.3).mean():.1%}   pos: {(scored['score'] > 0.3).mean():.1%}")

# Map each headline to the next trading date (after-close news counts tomorrow).
s = scored.copy()
day = (s["time"].dt.tz_convert("UTC") - pd.Timedelta(hours=20)).dt.normalize() + pd.Timedelta(days=1)
idx = pd.DatetimeIndex(dates.tz_convert("UTC").normalize())
pos = idx.searchsorted(pd.DatetimeIndex(day.dt.tz_convert("UTC")))
s = s[pos < len(idx)].copy()
s["date"] = idx[pos[pos < len(idx)]]

daily = s.groupby(["date", "symbol"]).agg(net=("score", "sum"), mean=("score", "mean"), n=("score", "size"))
daily["neg_n"] = s[s["score"] < -0.3].groupby(["date", "symbol"]).size().reindex(daily.index).fillna(0)
daily["pos_n"] = s[s["score"] > 0.3].groupby(["date", "symbol"]).size().reindex(daily.index).fillna(0)

close.index = idx
for h in (1, 5, 21):
    fwd = ((close.shift(-h) / close - 1) * 100).stack()
    fwd.index.names = ["date", "symbol"]
    d = daily.join(fwd.rename("fwd"), how="inner").dropna()
    # Excess over SPY so a market-wide move doesn't masquerade as a news effect.
    spy = ((close[price.BENCHMARK].shift(-h) / close[price.BENCHMARK] - 1) * 100)
    d["excess"] = d["fwd"] - spy.reindex(d.index.get_level_values("date")).values
    print(f"\n--- forward {h}d  (n={len(d)}) ---")
    for col in ("net", "mean", "neg_n", "pos_n"):
        print(f"  corr {col:>6} vs excess: {d[col].corr(d['excess']):+.4f}   spearman {d[col].corr(d['excess'], method='spearman'):+.4f}")
    bucket = pd.qcut(d["mean"], 5, duplicates="drop", labels=False)
    print(d.groupby(bucket)["excess"].agg(["mean", "size"]).rename_axis("mean-sentiment quintile").round(3).to_string())
    # Separate good vs bad news days
    print("  bad-news days (mean<-0.3):", round(d.loc[d['mean'] < -0.3, 'excess'].mean(), 3),
          " good-news days (mean>0.3):", round(d.loc[d['mean'] > 0.3, 'excess'].mean(), 3),
          " neutral:", round(d.loc[d['mean'].abs() <= 0.3, 'excess'].mean(), 3))
