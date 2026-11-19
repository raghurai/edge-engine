"""Lab bench for the brain: try a training variant, print its out-of-sample record.

    python scripts/brain_lab.py <target> [horizons...]
    targets: rank | mean | odds | winner

Same walk-forward and the same scoring as brain_train.py, one line per test so
variants can be compared quickly. Nothing here is saved; the winner goes into
brain_train.py.
"""
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_train as BT  # noqa: E402
from edge.brain import dataset as D  # noqa: E402


def make_label(df, h, group, target):
    y = df[f"y_{h}"]
    if target == "rank":
        return df.groupby(group)[f"y_{h}"].rank(pct=True) - 0.5, "reg"
    if target == "mean":
        c = y.clip(-0.5, 1.0)
        return c - c.groupby(df[group]).transform("mean"), "reg"
    if target == "odds":
        return (y > 0).astype(float).where(y.notna()), "bin"
    if target == "up":            # absolute: did it finish above where it was bought
        a = df[f"a_{h}"]
        return (a > 0).astype(float).where(a.notna()), "bin"
    if target == "winner":
        r = df.groupby(group)[f"y_{h}"].rank(pct=True)
        return (r > 0.8).astype(float).where(y.notna()), "bin"
    raise SystemExit(target)


def walk(df, feats, h, params, group, target, years=None):
    y, kind = make_label(df, h, group, target)
    p = dict(params)
    if kind == "bin":
        p["objective"] = "binary"; p.pop("alpha", None)
    elif target == "mean":
        p["objective"] = "regression"; p.pop("alpha", None)
    pred = pd.Series(np.nan, index=df.index)
    gap = pd.Timedelta(days=int(h * 1.5) + 3)
    for yr in (years or BT.TEST_YEARS):
        lo, hi = pd.Timestamp(yr, 1, 1), pd.Timestamp(yr + 1, 1, 1)
        tr = (df["date"] < lo - gap) & y.notna()
        te = (df["date"] >= lo) & (df["date"] < hi)
        if tr.sum() < 5000 or te.sum() == 0:
            continue
        M = lgb.LGBMClassifier if kind == "bin" else lgb.LGBMRegressor
        m = M(**p).fit(df.loc[tr, feats], y[tr])
        pred[te] = m.predict_proba(df.loc[te, feats])[:, 1] if kind == "bin" else m.predict(df.loc[te, feats])
    return pred


def line_weekly(tag, s):
    L = s["liquid"]
    print(f"{tag:<26} IC {s['ic']:+.3f} t{s['ic_t']:>5} | top10% vs univ {s['top_decile_vs_universe']:+.2f} t{s['top_decile_t']:>5} hit {s['top_decile_hit']} | "
          f"top20 net {s['top20_net_vs_spy']:+.2f} t{s['top20_t']:>5} | LIQ top20 net {L['top20_net_vs_spy']:+.2f} t{L['top20_t']:>5} hit {L['top20_hit']} yrs+ {L['years_positive']} {list(L['by_year'].values())}", flush=True)


def line_event(tag, s):
    L = s["liquid"]
    print(f"{tag:<26} quint {s['quintiles_vs_spy']} | top10% net {s['top_decile_net']:+.2f} t{s['top_decile_t']:>5} hit {s['top_decile_hit']} | "
          f"LIQ top10% net {L['top_decile_net']:+.2f} t{L['top_decile_t']:>5} hit {L['top_decile_hit']} {list(L['top_decile_by_year'].values())} | rule {s['rule_net']:+.2f} LIQ {L['rule_net']:+.2f}", flush=True)


def load():
    if "--rebuild" in sys.argv:
        sys.argv.remove("--rebuild")
        t = time.time(); D.build_tables(); print(f"tables rebuilt {time.time() - t:.0f}s", flush=True)
    tdir = D.RESEARCH / "tables"
    W, E = pd.read_parquet(tdir / "weekly.parquet"), pd.read_parquet(tdir / "events.parquet")
    feats = [f for f in json.loads((tdir / "features.json").read_text()) if f not in BT.REGIME]
    E = E.assign(ym=E["date"].dt.to_period("Q").astype(str))
    return W, E, feats


if __name__ == "__main__":
    W, E, feats = load()
    targets = sys.argv[1].split(",")
    hs = [int(x) for x in sys.argv[2:]] or [21]
    t0 = time.time()
    for target in targets:
        for h in hs:
            if h in (5, 21, 126):
                s, _ = BT.eval_weekly(W, walk(W, feats, h, BT.PARAMS, "date", target), h)
                line_weekly(f"xs_{h} {target}", s)
            if h in (5, 40):
                s, _ = BT.eval_events(E, walk(E, feats, h, BT.EVENT_PARAMS, "ym", target), h)
                line_event(f"event_{h} {target}", s)
    print(f"{time.time() - t0:.0f}s")
