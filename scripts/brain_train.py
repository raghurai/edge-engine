"""Train and honestly validate the brain.

    python scripts/brain_train.py            # full: build tables, walk-forward, fit final models
    python scripts/brain_train.py --quick    # two test years only, for a fast look

Walk-forward: to predict year Y the model is fitted only on data that ended before Y
(minus a gap as long as the holding period, so no label overlaps the test year).
Every number reported, and every number the site quotes, is from those out-of-sample
predictions. Outputs:
    models/brain/<name>.txt   LightGBM models fitted on all data (what the live scorer loads)
    models/brain/card.json    out-of-sample record, calibration tables, feature importances
"""
import json
import os
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge.brain import dataset as D  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "models" / "brain"
QUICK = "--quick" in sys.argv
TEST_YEARS = [2024, 2026] if QUICK else list(range(2020, 2027))
PARAMS = dict(objective="huber", alpha=0.25, learning_rate=0.05, n_estimators=300, num_leaves=63, min_child_samples=400,
              colsample_bytree=0.6, subsample=0.6, subsample_freq=1, reg_lambda=20.0, max_bin=63, n_jobs=os.cpu_count() or 4, verbose=-1)
EVENT_PARAMS = {**PARAMS, "num_leaves": 15, "min_child_samples": 150, "n_estimators": 250, "learning_rate": 0.03}
BINS = 20
REGIME = ("mkt_ret_21", "mkt_ret_63", "mkt_vol_21", "breadth_50", "xs_disp_21")   # same for every name on a day: cannot rank stocks
LIQUID = 2e7                                                                     # $20M a day: where options are tradable


def cost(dv: np.ndarray) -> np.ndarray:
    """Round-trip trading cost by liquidity (spread + slippage), as a fraction."""
    return np.where(dv >= 5e7, 0.0006, np.where(dv >= 1e7, 0.0015, 0.004))


def rank_label(df: pd.DataFrame, col: str, by) -> pd.Series:
    return df.groupby(by)[col].rank(pct=True) - 0.5


def walk_forward(df, feats, h, params, group):
    """Out-of-sample predictions for every test year."""
    y = rank_label(df, f"y_{h}", group)
    pred = pd.Series(np.nan, index=df.index)
    gap = pd.Timedelta(days=int(h * 1.5) + 3)
    for yr in TEST_YEARS:
        lo, hi = pd.Timestamp(yr, 1, 1), pd.Timestamp(yr + 1, 1, 1)
        tr = (df["date"] < lo - gap) & y.notna()
        te = (df["date"] >= lo) & (df["date"] < hi)
        if tr.sum() < 5000 or te.sum() == 0:
            continue
        m = lgb.LGBMRegressor(**params).fit(df.loc[tr, feats], y[tr])
        pred[te] = m.predict(df.loc[te, feats])
    return pred


def tstat(x):
    x = np.asarray(x, dtype=float); x = x[~np.isnan(x)]
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else 0.0


def eval_weekly(df, pred, h, top_n=20):
    """Per rebalance date: rank IC, top-decile and top-N excess return vs the tradable universe and vs SPY."""
    d = df.assign(pred=pred).dropna(subset=["pred", f"y_{h}"])
    d["net"] = d[f"y_{h}"] - cost(d["dollar_vol"].to_numpy())
    rows = []
    for date, g in d.groupby("date"):
        if len(g) < 200:
            continue
        r = g["pred"].rank(pct=True)
        top, bot = g[r > 0.9], g[r <= 0.1]
        best = g.nlargest(top_n, "pred")
        liq = g[g["dollar_vol"] >= LIQUID]
        lbest = liq.nlargest(top_n, "pred")
        rows.append({"date": date, "ic": g["pred"].corr(g[f"y_{h}"], method="spearman"), "univ": g[f"y_{h}"].mean(),
                     "liq_univ": liq[f"y_{h}"].mean(), "liq_best": lbest[f"y_{h}"].mean(), "liq_best_net": lbest["net"].mean(),
                     "liq_best_hit": (lbest[f"y_{h}"] > 0).mean(), "liq_ic": liq["pred"].corr(liq[f"y_{h}"], method="spearman"),
                     "liq_top": liq[liq["pred"].rank(pct=True) > 0.9][f"y_{h}"].mean(),
                     "top": top[f"y_{h}"].mean(), "top_net": top["net"].mean(), "bot": bot[f"y_{h}"].mean(),
                     "best": best[f"y_{h}"].mean(), "best_net": best["net"].mean(), "best_hit": (best[f"y_{h}"] > 0).mean(),
                     "top_hit": (top[f"y_{h}"] > 0).mean()})
    R = pd.DataFrame(rows).set_index("date")
    step = max(1, int(np.ceil(h / 5)))            # non-overlapping holding periods for the t-stats
    S = R.iloc[::step]
    by_year = S.groupby(S.index.year).agg(ic=("ic", "mean"), top=("top", "mean"), univ=("univ", "mean"), best=("best", "mean"), n=("ic", "size"),
                                          liq_best=("liq_best_net", "mean"), liq_univ=("liq_univ", "mean"))
    summary = {
        "horizon": h, "dates": int(len(R)), "independent_periods": int(len(S)),
        "ic": round(float(R["ic"].mean()), 4), "ic_t": round(tstat(S["ic"]), 1),
        "top_decile_vs_spy": round(float(S["top"].mean()) * 100, 2), "top_decile_vs_universe": round(float((S["top"] - S["univ"]).mean()) * 100, 2),
        "top_decile_t": round(tstat(S["top"] - S["univ"]), 1), "top_decile_net_vs_spy": round(float(S["top_net"].mean()) * 100, 2),
        "top_decile_net_t": round(tstat(S["top_net"]), 1), "top_decile_hit": round(float(S["top_hit"].mean()) * 100, 1),
        f"top{top_n}_vs_spy": round(float(S["best"].mean()) * 100, 2), f"top{top_n}_net_vs_spy": round(float(S["best_net"].mean()) * 100, 2),
        f"top{top_n}_t": round(tstat(S["best_net"]), 1), f"top{top_n}_hit": round(float(S["best_hit"].mean()) * 100, 1),
        "bottom_decile_vs_spy": round(float(S["bot"].mean()) * 100, 2), "universe_vs_spy": round(float(S["univ"].mean()) * 100, 2),
        "liquid": {"ic": round(float(R["liq_ic"].mean()), 4), "universe_vs_spy": round(float(S["liq_univ"].mean()) * 100, 2),
                   "top_decile_vs_spy": round(float(S["liq_top"].mean()) * 100, 2),
                   f"top{top_n}_net_vs_spy": round(float(S["liq_best_net"].mean()) * 100, 2), f"top{top_n}_t": round(tstat(S["liq_best_net"]), 1),
                   f"top{top_n}_hit": round(float(S["liq_best_hit"].mean()) * 100, 1),
                   f"top{top_n}_vs_liquid_universe_t": round(tstat(S["liq_best"] - S["liq_univ"]), 1),
                   "years_positive": int((by_year["liq_best"] > 0).sum()),
                   "by_year": {int(k): round(float(v) * 100, 2) for k, v in by_year["liq_best"].items()}},
        "years_positive": int(((by_year["top"] - by_year["univ"]) > 0).sum()), "years": int(len(by_year)),
        "by_year": {int(k): {"ic": round(float(v["ic"]), 3), "top_vs_universe": round(float(v["top"] - v["univ"]) * 100, 2),
                             "top20_vs_spy": round(float(v["best"]) * 100, 2), "n": int(v["n"])} for k, v in by_year.iterrows()},
    }
    return summary, d


def calibration(d, h, by_date=True):
    """Prediction bucket -> what actually happened out of sample, for names liquid enough to
    trade. Both versus SPY and in absolute terms; the live 'expected' and 'odds' come from here,
    and the absolute quantiles are the scenario distribution the contract picker prices against."""
    q = d.groupby("date")["pred"].rank(pct=True) if by_date else d["pred"].rank(pct=True)
    d = d.assign(bin=np.minimum((q * BINS).astype(int), BINS - 1))
    L = d[d["dollar_vol"] >= LIQUID] if "dollar_vol" in d else d
    out = []
    for i, g in L.groupby("bin"):
        y, ab = g[f"y_{h}"], g[f"a_{h}"] if f"a_{h}" in g else g[f"y_{h}"]
        row = {"bin": int(i), "n": int(len(g)), "mean": float(y.mean()), "hit": float((y > 0).mean()), "abs_mean": float(ab.mean()),
               "abs_hit": float((ab > 0).mean())}
        for name, qq in (("p05", .05), ("p10", .10), ("p25", .25), ("p50", .50), ("p75", .75), ("p90", .90), ("p95", .95)):
            row[name] = float(ab.quantile(qq))
        out.append({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()})
    return out


def eval_events(E, pred, h=40):
    d = E.assign(pred=pred).dropna(subset=["pred", f"y_{h}"])
    d["net"] = d[f"y_{h}"] - cost(d["dollar_vol"].to_numpy())
    d["q"] = d.groupby(d["date"].dt.year)["pred"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
    quint = d.groupby("q")[f"y_{h}"].agg(["mean", "size"])
    top = d[d["q"] == 4]
    monthly = top.groupby(top["date"].dt.to_period("M"))["net"].mean()
    # The old hand rule on the same out-of-sample events: top-20% surprise and a +2% reaction.
    bar = d.groupby(d["date"].dt.year)["e_surp"].transform(lambda s: s.quantile(0.8))
    rule = d[(d["e_surp"] >= bar) & (d["e_react"] > 0.02)]
    rule_m = rule.groupby(rule["date"].dt.to_period("M"))["net"].mean()
    # Model's top decile, to match the rule's selectivity.
    d["dec"] = d.groupby(d["date"].dt.year)["pred"].transform(lambda s: s.rank(pct=True))
    best = d[d["dec"] > 0.9]
    best_m = best.groupby(best["date"].dt.to_period("M"))["net"].mean()
    by_year = top.groupby(top["date"].dt.year)["net"].agg(["mean", "size"])
    L = d[d["dollar_vol"] >= LIQUID]
    Lbest, Lrule = L[L["dec"] > 0.9], rule[rule["dollar_vol"] >= LIQUID]
    mt = lambda x: tstat(x.groupby(x["date"].dt.to_period("M"))["net"].mean())
    yr = lambda x: {int(k): round(float(v) * 100, 2) for k, v in x.groupby(x["date"].dt.year)["net"].mean().items()}
    liquid = {"events": int(len(L)), "all_vs_spy": round(float(L[f"y_{h}"].mean()) * 100, 2),
              "top_decile_net": round(float(Lbest["net"].mean()) * 100, 2), "top_decile_hit": round(float((Lbest[f"y_{h}"] > 0).mean()) * 100, 1),
              "top_decile_t": round(mt(Lbest), 1), "top_decile_n": int(len(Lbest)), "top_decile_by_year": yr(Lbest),
              "rule_net": round(float(Lrule["net"].mean()) * 100, 2), "rule_hit": round(float((Lrule[f"y_{h}"] > 0).mean()) * 100, 1),
              "rule_t": round(mt(Lrule), 1), "rule_n": int(len(Lrule)), "rule_by_year": yr(Lrule)}
    return {
        "liquid": liquid, "rule_by_year": yr(rule), "top_decile_by_year": yr(best),
        "horizon": h, "events": int(len(d)),
        "quintiles_vs_spy": [round(float(x) * 100, 2) for x in quint["mean"]],
        "top_quintile_net": round(float(top["net"].mean()) * 100, 2), "top_quintile_hit": round(float((top[f"y_{h}"] > 0).mean()) * 100, 1),
        "top_quintile_t": round(tstat(monthly), 1), "top_quintile_n": int(len(top)),
        "top_decile_net": round(float(best["net"].mean()) * 100, 2), "top_decile_hit": round(float((best[f"y_{h}"] > 0).mean()) * 100, 1),
        "top_decile_t": round(tstat(best_m), 1), "top_decile_n": int(len(best)),
        "rule_net": round(float(rule["net"].mean()) * 100, 2), "rule_hit": round(float((rule[f"y_{h}"] > 0).mean()) * 100, 1),
        "rule_t": round(tstat(rule_m), 1), "rule_n": int(len(rule)),
        "all_events_vs_spy": round(float(d[f"y_{h}"].mean()) * 100, 2),
        "years_positive": int((by_year["mean"] > 0).sum()), "years": int(len(by_year)),
        "by_year": {int(k): {"top_quintile_net": round(float(v["mean"]) * 100, 2), "n": int(v["size"])} for k, v in by_year.iterrows()},
    }, d


def importances(model, feats, k=12):
    g = model.booster_.feature_importance(importance_type="gain")
    order = np.argsort(-g)[:k]
    return [{"feature": feats[i], "share": round(float(g[i] / g.sum()), 3)} for i in order]


def index_odds():
    """How often the index itself gained over each horizon, from its whole listed history,
    and split by market state (above / below its 200-day average). This is the bar."""
    import yfinance as yf
    out = {}
    for sym in ("SPY", "QQQ", "DIA", "IWM"):
        try:
            px = yf.download(sym, start="1993-01-01", progress=False, auto_adjust=True)["Close"].squeeze().dropna()
        except Exception as e:
            print("index history failed", sym, str(e)[:80]); continue
        up = px > px.rolling(200).mean()
        rec = {"since": str(px.index[0].date())}
        for h in (5, 21, 126, 252):
            r = (px.shift(-h) / px - 1).dropna()
            st = up.reindex(r.index)
            stat = lambda x: {"hit": round(float((x > 0).mean()) * 100, 1), "mean": round(float(x.mean()) * 100, 2),
                              "p10": round(float(x.quantile(.1)) * 100, 1), "p90": round(float(x.quantile(.9)) * 100, 1), "n": int(len(x))}
            rec[str(h)] = {**stat(r), "uptrend": stat(r[st]), "downtrend": stat(r[~st]), "since_2020": stat(r[r.index >= "2020-01-01"])}
        out[sym] = rec
    return out


def earnings_moves(P, ev):
    """Each name's own history of earnings-day moves: what its options have to beat."""
    from edge.brain import features as F
    R = P["close"].pct_change(fill_method=None)
    e = F.reaction_index(P, ev.dropna(subset=["surprise"]))
    e = e[~e["pending"]]
    r = R.to_numpy()[e["ri"].to_numpy(), e["j"].to_numpy()]
    e = e.assign(move=np.abs(r)).dropna(subset=["move"]).sort_values(["symbol", "ri"])
    last = e.groupby("symbol").tail(12)
    g = last.groupby("symbol")["move"]
    return pd.DataFrame({"n": g.size(), "avg_move": g.mean(), "median_move": g.median(), "max_move": g.max(),
                         "big_share": g.apply(lambda x: float((x > 0.10).mean()))}).reset_index()


def main():
    t0 = time.time()
    tdir = D.RESEARCH / "tables"
    ev = D.events()
    if (tdir / "weekly.parquet").exists() and "--rebuild" not in sys.argv:
        W, E = pd.read_parquet(tdir / "weekly.parquet"), pd.read_parquet(tdir / "events.parquet")
        feats = json.loads((tdir / "features.json").read_text())
    else:
        W, E, feats = D.build_tables()
    all_feats, feats = feats, [f for f in feats if f not in REGIME]
    print("weekly", W.shape, "events", E.shape, "features", len(feats), f"{time.time() - t0:.0f}s", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    card = {"trained_at": pd.Timestamp.utcnow().isoformat(), "features": feats, "all_features": all_feats, "test_years": TEST_YEARS,
            "rows": {"weekly": int(len(W)), "events": int(len(E))}, "span": [str(W["date"].min().date()), str(W["date"].max().date())],
            "symbols": int(W["symbol"].nunique()), "liquid_floor": LIQUID, "models": {}}

    for h in (5, 21):
        pred = walk_forward(W, feats, h, PARAMS, "date")
        s, d = eval_weekly(W, pred, h)
        s["calibration"] = calibration(d, h)
        # Head-to-head on odds of a gain: the model's favourite liquid names against SPY bought the same days.
        step = max(1, int(np.ceil(h / 5)))
        dd = d[d["date"].isin(np.sort(d["date"].unique())[::step]) & (d["dollar_vol"] >= LIQUID)]
        top = dd[dd.groupby("date")["pred"].rank(ascending=False) <= 20]
        bot = dd[dd.groupby("date")["pred"].rank(pct=True) <= 0.10]
        spy = (dd[f"a_{h}"] - dd[f"y_{h}"]).groupby(dd["date"]).first()
        s["head_to_head"] = {"picks_gained": round(float((top[f"a_{h}"] > 0).mean()) * 100, 1), "picks_avg": round(float(top[f"a_{h}"].mean()) * 100, 2),
                             "spy_gained": round(float((spy > 0).mean()) * 100, 1), "spy_avg": round(float(spy.mean()) * 100, 2),
                             "avoid_gained": round(float((bot[f"a_{h}"] > 0).mean()) * 100, 1), "avoid_avg": round(float(bot[f"a_{h}"].mean()) * 100, 2),
                             "avoid_vs_spy": round(float(bot[f"y_{h}"].mean()) * 100, 2), "periods": int(len(spy))}
        ok = W[f"y_{h}"].notna()
        final = lgb.LGBMRegressor(**PARAMS).fit(W.loc[ok, feats], rank_label(W, f"y_{h}", "date")[ok])
        final.booster_.save_model(str(OUT / f"xs_{h}.txt.new"))      # staged; swapped in only if the checks below pass
        s["importance"] = importances(final, feats)
        card["models"][f"xs_{h}"] = s
        print(f"xs_{h}", json.dumps({k: v for k, v in s.items() if k not in ("calibration", "importance", "by_year")}), f"{time.time() - t0:.0f}s", flush=True)

    # The record of the rule the site used to call proven, on every report in the whole market.
    for h in (40,):
        d = E.dropna(subset=[f"y_{h}"])
        d = d[d["date"] >= "2020-01-01"]
        bar = d.groupby(d["date"].dt.year)["e_surp"].transform(lambda x: x.quantile(0.8))
        rule = d[(d["e_surp"] >= bar) & (d["e_react"] > 0.02)]
        net = rule[f"y_{h}"] - cost(rule["dollar_vol"].to_numpy())
        m = net.groupby(rule["date"].dt.to_period("M")).mean()
        card["earnings_drift"] = {"trades": int(len(rule)), "net_vs_spy": round(float(net.mean()) * 100, 2), "t": round(tstat(m), 1),
                                  "hit": round(float((rule[f"y_{h}"] > 0).mean()) * 100, 1), "all_reports_vs_spy": round(float(d[f"y_{h}"].mean()) * 100, 2),
                                  "by_year": {int(k): round(float(v) * 100, 2) for k, v in net.groupby(rule["date"].dt.year).mean().items()}}
    card["index"] = index_odds()

    # Guard rails before anything replaces the model in service.
    name = "card_quick.json" if QUICK else "card.json"
    prev = json.loads((OUT / "card.json").read_text()) if (OUT / "card.json").exists() else None
    new_ic = card["models"]["xs_21"]["ic"]
    problems = []
    if new_ic <= 0:
        problems.append("the monthly model has no out-of-sample rank skill")
    if prev and new_ic < 0.5 * prev["models"]["xs_21"]["ic"]:
        problems.append(f"monthly rank skill fell from {prev['models']['xs_21']['ic']} to {new_ic}")
    if prev and card["rows"]["weekly"] < 0.9 * prev["rows"]["weekly"]:
        problems.append(f"training set shrank from {prev['rows']['weekly']:,} to {card['rows']['weekly']:,} rows")
    if not card["index"].get("SPY"):
        problems.append("index history missing")
    if problems:
        for f in OUT.glob("*.new"):
            f.unlink()
        raise SystemExit("refusing to publish this retrain: " + "; ".join(problems))
    if not QUICK:
        for f in OUT.glob("*.txt.new"):
            os.replace(f, f.with_suffix(""))
        # A running record of every retrain, so the site can show whether the brain is getting better.
        hist_path = OUT / "history.json"
        hist = json.loads(hist_path.read_text()) if hist_path.exists() else []
        m = card["models"]["xs_21"]
        hist.append({"trained_at": card["trained_at"][:10], "through": card["span"][1], "rows": card["rows"]["weekly"], "ic": m["ic"],
                     "top20_net_vs_spy": m["liquid"]["top20_net_vs_spy"], "top20_t": m["liquid"]["top20_t"], "avoid_vs_spy": m["head_to_head"]["avoid_vs_spy"]})
        hist_path.write_text(json.dumps(hist[-104:], indent=1))
        card["history"] = hist[-12:]

    # What the live scorer needs besides the models.
    keep = ev[["symbol", "date", "session", "estimate", "eps", "surprise", "n_estimates"]]
    keep.to_parquet(OUT / "events_history.parquet")
    if (D.RESEARCH / "sectors.parquet").exists():
        pd.read_parquet(D.RESEARCH / "sectors.parquet")[["symbol", "sector", "industry"]].to_parquet(OUT / "sectors.parquet")
    try:
        P = D.panel(symbols=set(ev["symbol"]) | {"SPY"})
        earnings_moves(P, ev).to_parquet(OUT / "earnings_moves.parquet")
    except Exception as e:
        print("earnings moves failed:", str(e)[:120])

    (OUT / name).write_text(json.dumps(card, indent=1))
    print("wrote", OUT / name, f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
