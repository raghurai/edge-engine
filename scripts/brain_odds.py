"""Experiment: are the ODDS OF A GAIN predictable and calibrated out of sample?

For each horizon: a walk-forward classifier of "finished above the entry price",
then, among the names it likes most, how often they really gained, what they
made, and how that compares with SPY bought on the same days.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_lab as LAB  # noqa: E402
import brain_train as BT  # noqa: E402

W, E, feats = LAB.load()
t0 = time.time()
for h in [int(x) for x in sys.argv[1:]] or [21, 126, 252]:
    pred = LAB.walk(W, feats, h, BT.PARAMS, "date", "up")
    d = W.assign(p=pred).dropna(subset=["p", f"a_{h}"])
    d["spy"] = d[f"a_{h}"] - d[f"y_{h}"]
    step = max(1, int(np.ceil(h / 5)))
    keep = np.sort(d["date"].unique())[::step]
    d = d[d["date"].isin(keep)]
    print(f"\n=== {h} days · {d['date'].nunique()} independent start dates · {len(d):,} stock-periods")
    for name, u in (("all tradable", d), ("liquid >= $20M/day", d[d["dollar_vol"] >= BT.LIQUID])):
        r = u.groupby("date")["p"].rank(pct=True)
        rows = []
        for lab, g in (("top 20 names", u.loc[u.groupby("date")["p"].rank(ascending=False) <= 20]), ("top 5%", u[r > 0.95]), ("top 10%", u[r > 0.9]),
                       ("middle", u[(r > 0.45) & (r <= 0.55)]), ("bottom 10%", u[r <= 0.1]), ("everything", u)):
            a = g[f"a_{h}"]
            per = g.groupby("date")[f"a_{h}"].mean()
            rows.append({"bucket": lab, "gained %": round((a > 0).mean() * 100, 1), "avg %": round(a.mean() * 100, 2), "median %": round(a.median() * 100, 2),
                         "worst 10% <": round(a.quantile(0.1) * 100, 1), "beat SPY %": round((g[f"y_{h}"] > 0).mean() * 100, 1),
                         "avg vs SPY": round(g[f"y_{h}"].mean() * 100, 2), "dates basket up %": round((per > 0).mean() * 100, 1),
                         "said odds": round(g["p"].mean() * 100, 1)})
        spy = u.groupby("date")["spy"].first()
        print(f"-- {name}   SPY itself: gained {((spy > 0).mean() * 100):.1f}% of start dates, avg {spy.mean() * 100:+.2f}%")
        print(pd.DataFrame(rows).to_string(index=False))
        top = u[r > 0.9]
        yr = top.groupby(top["date"].dt.year).agg(gained=(f"a_{h}", lambda s: round((s > 0).mean() * 100, 1)), avg=(f"a_{h}", lambda s: round(s.mean() * 100, 2)),
                                                  vs_spy=(f"y_{h}", lambda s: round(s.mean() * 100, 2)))
        print("   top 10% by year:", {int(k): tuple(v) for k, v in zip(yr.index, yr.to_numpy())})
        reg = top.groupby("uptrend").agg(gained=(f"a_{h}", lambda s: round((s > 0).mean() * 100, 1)), avg=(f"a_{h}", lambda s: round(s.mean() * 100, 2)), n=("p", "size"))
        print("   top 10% by market state (SPY above its 200-day?):", {bool(k): tuple(v) for k, v in zip(reg.index, reg.to_numpy())})
    # Reliability: when the model said X%, how often did it happen
    b = pd.cut(d["p"], [0, .3, .4, .45, .5, .55, .6, .65, .7, .8, 1.0])
    rel = d.groupby(b, observed=True).agg(said=("p", "mean"), happened=(f"a_{h}", lambda s: (s > 0).mean()), n=("p", "size"))
    print("   reliability (said -> happened):", [(round(a * 100), round(c * 100), int(n)) for a, c, n in rel.to_numpy()])
print(f"{time.time() - t0:.0f}s")
