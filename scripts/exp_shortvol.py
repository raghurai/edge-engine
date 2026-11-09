"""Experiment: short-volume pressure (squeeze fuel, free version).

FINRA publishes daily short-sale volume per symbol. Hypothesis: when a stock's
short-volume share spikes well above its own norm AND the stock closes up on
high volume, shorts are trapped and the stock outperforms over the next 5-20 days.

Data: https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt (public).
"""
import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import requests

from edge.data import wide

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 400
CACHE = wide.CACHE / "finra_shortvol.parquet"

top = wide.most_liquid(1000)
bars = wide.daily_bars(top, years=4)
close = bars["close"].unstack(level="symbol"); vol = bars["volume"].unstack(level="symbol")
for df in (close, vol):
    df.index = df.index.tz_convert("UTC").normalize().tz_localize(None)
dates = close.index

have = pd.read_parquet(CACHE) if CACHE.exists() else pd.DataFrame(columns=["date", "symbol", "short", "total"])
got = set(have["date"].astype(str)) if len(have) else set()
rows, missing = [], 0
want = [d for d in dates[-DAYS:] if str(d.date()) not in got]
for k, d in enumerate(want):
    url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d:%Y%m%d}.txt"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            missing += 1; continue
        t = pd.read_csv(io.StringIO(r.text), sep="|").dropna(subset=["Symbol"])
        t = t[t["Symbol"].isin(top)]
        rows.append(pd.DataFrame({"date": d, "symbol": t["Symbol"].values,
                                  "short": t["ShortVolume"].values, "total": t["TotalVolume"].values}))
    except Exception:
        missing += 1
    if k % 50 == 0:
        print(f"  finra {k}/{len(want)}")
if rows:
    have = pd.concat([have, *rows], ignore_index=True)
    have.to_parquet(CACHE)
sv = have.copy(); sv["date"] = pd.to_datetime(sv["date"])
print(f"short-volume days: {sv['date'].nunique()}  (missing {missing})  rows {len(sv)}\n")

ratio = sv.pivot(index="date", columns="symbol", values="short").div(sv.pivot(index="date", columns="symbol", values="total")).reindex(dates)
# How unusual today's short share is vs the stock's own last 60 days
z = (ratio - ratio.rolling(60, min_periods=30).mean()) / ratio.rolling(60, min_periods=30).std()
ret1 = close.pct_change()
volz = (np.log1p(vol) - np.log1p(vol).rolling(20).mean()) / np.log1p(vol).rolling(20).std()
spy = close["SPY"]

out = []
for h in (5, 10, 20):
    fwd = (close.shift(-h) / close - 1).sub(spy.shift(-h) / spy - 1, axis=0) * 100
    d = pd.concat({"z": z.stack(), "ret1": ret1.stack() * 100, "volz": volz.stack(), "fwd": fwd.stack()}, axis=1).dropna()
    trap = d[(d["z"] > 2) & (d["ret1"] > 2) & (d["volz"] > 1)]        # shorts piled in, stock ripped on volume
    calm = d[(d["z"].abs() < 0.5)]
    hi = d[d["z"] > 2]
    t = (trap["fwd"].mean() - calm["fwd"].mean()) / np.sqrt(trap["fwd"].var() / len(trap) + calm["fwd"].var() / len(calm))
    out.append({"horizon": f"{h}d", "trap_n": len(trap), "trap_excess": trap["fwd"].mean(),
                "high_short_only": hi["fwd"].mean(), "baseline": calm["fwd"].mean(), "t_trap_vs_base": t})
pd.set_option("display.float_format", "{:.2f}".format)
print("Excess return over SPY (%), by setup:")
print(pd.DataFrame(out).set_index("horizon").to_string())
print("\nBy short-share z-score decile, 10-day excess return:")
fwd10 = (close.shift(-10) / close - 1).sub(spy.shift(-10) / spy - 1, axis=0) * 100
d = pd.concat({"z": z.stack(), "fwd": fwd10.stack()}, axis=1).dropna()
print(d.groupby(pd.qcut(d["z"], 10, labels=False, duplicates="drop"))["fwd"].agg(["mean", "size"]).round(2).to_string())
