"""Which company features sharpen the earnings-drift signal?

For every earnings event we already have outcomes for, attach point-in-time
company features (revenue growth, margins, analyst actions, insider trades) and
ask: among big beats, does the feature separate the winners from the losers?
Also: does the analysts' reaction in the 10 days AFTER the beat predict the rest
of the drift (the slow buyer leaving a paper trail)?
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from edge.data import fundamentals as fund, wide

ev = pd.read_parquet(wide.CACHE / "earnings_300.parquet")
top = wide.most_liquid(1000)
bars = wide.daily_bars(top, years=4)
close = bars["close"].unstack(level="symbol")
close.index = close.index.tz_convert("UTC").normalize().tz_localize(None)
dates = close.index; spy = close["SPY"]
ev = ev[(ev["date"] > dates[0] + pd.Timedelta(days=5)) & (ev["date"] < dates[-1] - pd.Timedelta(days=65))].copy()

def excess(sym, d0, start, end):
    pos = dates.searchsorted(d0)
    if pos + 1 + end >= len(dates) or sym not in close or pos == 0:
        return np.nan
    p = close[sym]; a, b = p.iloc[pos + 1 + start], p.iloc[pos + 1 + end]
    s = spy.iloc[pos + 1 + end] / spy.iloc[pos + 1 + start] - 1
    return (b / a - 1 - s) * 100 if a > 0 else np.nan

ev["day0"] = [excess(s, d, -1, 0) for s, d in zip(ev["symbol"], ev["date"])]
ev["ex40"] = [excess(s, d, 1, 40) for s, d in zip(ev["symbol"], ev["date"])]
ev["ex_10_40"] = [excess(s, d, 10, 40) for s, d in zip(ev["symbol"], ev["date"])]   # drift AFTER the analyst window
ev = ev.dropna(subset=["day0", "ex40"])

rows, n = [], ev["symbol"].nunique()
for k, (sym, g) in enumerate(ev.groupby("symbol")):
    q, a, ins = fund.quarterly(sym), fund.analyst_actions(sym), fund.insiders(sym)
    for _, e in g.iterrows():
        f = fund.features_at(sym, e["date"], q, a, ins)
        f.update(fund.after_event(sym, e["date"], a=a))
        rows.append({**e.to_dict(), **f})
    if k % 25 == 0:
        print(f"  features {k}/{n}", flush=True)
df = pd.DataFrame(rows)
df.to_parquet(wide.CACHE / "earnings_features.parquet")

cut = df["surprise"].quantile(0.8)
bb = df[(df["surprise"] >= cut) & (df["day0"] > 2)].copy()
print(f"\nevents {len(df)}   big beats with positive reaction {len(bb)}   base 40d excess {bb['ex40'].mean():+.2f}%\n")

def split(frame, col, target="ex40", label=None):
    s = frame.dropna(subset=[col])
    if len(s) < 60:
        return None
    med = s[col].median()
    hi, lo = s[s[col] > med][target], s[s[col] <= med][target]
    if len(hi) < 20 or len(lo) < 20:
        return None
    t = (hi.mean() - lo.mean()) / np.sqrt(hi.var() / len(hi) + lo.var() / len(lo))
    return {"feature": label or col, "n": len(s), "above_median": hi.mean(), "below_median": lo.mean(), "diff": hi.mean() - lo.mean(), "t": t}

pd.set_option("display.float_format", "{:.2f}".format)
feats = ["rev_yoy", "rev_qoq", "rev_accel", "gm_change", "opm_change", "op_margin",
         "net_rating_90d", "upgrades_90d", "downgrades_90d", "insider_buys_180d", "insider_sells_180d", "insider_net_value_180d",
         "surprise", "day0"]
out = [r for r in (split(bb, c) for c in feats) if r]
print("Among big beats: 40d excess when the feature is above vs below its median")
print(pd.DataFrame(out).set_index("feature").sort_values("t", key=abs, ascending=False).to_string())

print("\nAnalyst reaction in the 10 days after the beat vs the drift from day 10 to 40:")
for col in ("upgrades_after", "downgrades_after"):
    s = bb.dropna(subset=[col])
    for v, g in s.groupby(s[col] > 0):
        print(f"  {col} {'>0' if v else '=0'}: n={len(g):4d}  day10-40 excess {g['ex_10_40'].mean():+.2f}%")

print("\nSame splits across ALL events (does the feature predict drift regardless of the beat?):")
out = [r for r in (split(df, c) for c in feats if c not in ("surprise", "day0")) if r]
print(pd.DataFrame(out).set_index("feature").sort_values("t", key=abs, ascending=False).to_string())
