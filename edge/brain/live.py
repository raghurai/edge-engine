"""Live scorer: the trained brain applied to today's market.

Same feature code as training, on split-adjusted consolidated bars through the last
completed session (a decision is made after a close and traded at the next open,
exactly as in the walk-forward). Every score is translated through the model card's
out-of-sample calibration table, so "odds" and "expected" are what that bucket of
predictions actually did on years the model never saw, not what the model hopes.
"""
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from edge.brain import features as F

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models" / "brain"
CACHE = ROOT / "data" / "brain"
INDEX_FUNDS = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow 30", "IWM": "Russell 2000"}
EV_COLS = ["symbol", "date", "session", "estimate", "eps", "surprise", "n_estimates"]

# feature -> (what it means when it argues for the name being high, when low)
WORDS = {
    "ret_1": ("up yesterday", "down yesterday"), "ret_5": ("up on the week", "down on the week"), "ret_10": ("up over two weeks", "down over two weeks"),
    "ret_21": ("up on the month", "down on the month"), "ret_63": ("up on the quarter", "down on the quarter"), "ret_126": ("up over six months", "down over six months"),
    "mom_231_21": ("strong 12-month momentum", "weak 12-month momentum"), "hi_231": ("near its 52-week high", "far below its 52-week high"),
    "lo_231": ("well off its 52-week low", "near its 52-week low"), "hi_63": ("near its 3-month high", "far below its 3-month high"),
    "vol_21": ("volatile lately", "calm lately"), "vol_63": ("volatile", "low volatility"), "vol_ratio": ("volatility picking up", "volatility fading"),
    "max_21": ("had a big up day", "no spike days"), "min_21": ("no crash days", "had a big down day"), "skew_63": ("lottery-like returns", "steady returns"),
    "gap": ("gapped up", "gapped down"), "intraday": ("bought during the session", "sold during the session"), "clv": ("closed at its high", "closed at its low"),
    "clv_5": ("closing strong all week", "closing weak all week"), "range_21": ("wide daily ranges", "tight daily ranges"),
    "dv_63": ("heavily traded", "thinly traded"), "dv_ratio_1": ("volume surge", "quiet volume"), "dv_ratio_5": ("volume building", "volume drying up"),
    "amihud_63": ("illiquid", "liquid"), "beta_126": ("high beta", "low beta"), "idio_126": ("stock-specific swings", "moves with the market"),
    "ret_21_vol": ("strong month for its volatility", "weak month for its volatility"), "ret_5_vol": ("strong week for its volatility", "weak week for its volatility"),
    "overnight_21": ("bought overnight", "sold overnight"), "overnight_63": ("bought overnight all quarter", "sold overnight all quarter"),
    "intraday_21": ("bought intraday", "sold intraday"), "intraday_63": ("bought intraday all quarter", "sold intraday all quarter"),
    "seasonal_12m": ("strong this month last year", "weak this month last year"),
    "ind_ret_21": ("industry up on the month", "industry down on the month"), "ind_ret_126": ("industry strong for six months", "industry weak for six months"),
    "rel_ind_21": ("leading its industry", "lagging its industry"), "rel_ind_5": ("beat its industry this week", "lagged its industry this week"),
    "rel_ind_126": ("six-month industry leader", "six-month industry laggard"), "ind_surp": ("peers beating earnings", "peers missing earnings"),
    "ind_react": ("peers rewarded on earnings", "peers punished on earnings"), "ind_breadth": ("industry in uptrends", "industry in downtrends"),
    "si_dtc": ("heavily shorted", "lightly shorted"), "si_chg": ("short interest rising", "short interest falling"), "si_dtc_3m": ("shorts building for months", "shorts covering for months"),
    "e_days_since": ("reported a while ago", "just reported"), "e_days_to_next": ("no report soon", "reports soon"), "e_surp": ("beat estimates", "missed estimates"),
    "e_react": ("rallied on earnings", "fell on earnings"), "e_react_vol": ("heavy volume on earnings", "light volume on earnings"),
    "e_react_clv": ("closed strong on earnings day", "closed weak on earnings day"), "e_prev_surp": ("beat the quarter before", "missed the quarter before"),
    "e_beats4": ("a run of beats", "a run of misses"), "e_n_est": ("widely covered", "thinly covered"), "e_est_small": ("near-zero estimates", "real earnings"),
    "e_post_drift": ("up since its report", "down since its report"), "e_sue": ("big surprise for this name", "big miss for this name"),
    "e_eps_yoy": ("earnings growing", "earnings shrinking"), "e_eps_accel": ("growth accelerating", "growth slowing"),
}
RAW_MID = {"e_days_since": 30, "e_days_to_next": 15, "e_beats4": 2, "e_n_est": 5, "e_est_small": 0.5, "e_react_vol": 2}


def load() -> tuple[dict, dict]:
    card = json.loads((MODELS / "card.json").read_text())
    boosters = {name: lgb.Booster(model_file=str(MODELS / f"{name}.txt")) for name in card["models"] if (MODELS / f"{name}.txt").exists()}
    return card, boosters


def _why(booster, X: pd.DataFrame, feats: list[str], k_for=3, k_against=2) -> tuple[list, list]:
    """Plain-language drivers per name, from the model's own per-feature contributions."""
    contrib = booster.predict(X, pred_contrib=True)[:, :-1]
    vals = X.to_numpy()
    fors, againsts = [], []
    for i in range(len(X)):
        order = np.argsort(-contrib[i])
        def say(j):
            v = vals[i, j]
            if v != v or feats[j] not in WORDS:
                return None
            return WORDS[feats[j]][0 if v > RAW_MID.get(feats[j], 0.0) else 1]
        f = [s for s in (say(j) for j in order[:6] if contrib[i, j] > 0) if s][:k_for]
        a = [s for s in (say(j) for j in order[::-1][:6] if contrib[i, j] < 0) if s][:k_against]
        fors.append(f); againsts.append(a)
    return fors, againsts


def score(long: pd.DataFrame, events: pd.DataFrame, industry: pd.Series | None, si: pd.DataFrame | None) -> dict:
    card, boosters = load()
    feats = card["features"]
    n = long.groupby("symbol")["close"].transform("size")
    P = F.wide(long[n >= 130])
    dates = P["close"][F.BENCH].dropna().index
    P = {k: v.reindex(dates) for k, v in P.items()}
    C = P["close"]
    cols = C.columns
    mask = F.universe_mask(P)
    EV = F.event_panel(P, events)
    last = {name: mat[-1] for name, mat in F.iter_features(P, EV, mask, industry=industry, SI=F.short_interest_panel(P, si)) if name in feats}
    j = np.nonzero(mask[-1])[0]
    X = pd.DataFrame({f: last[f][j] for f in feats})
    R = C.pct_change(fill_method=None)
    xs = pd.DataFrame({
        "symbol": cols[j], "close": C.iloc[-1].to_numpy()[j], "vol": (R.iloc[-63:].std() * np.sqrt(252)).to_numpy()[j],
        "dollar_vol": (C * P["volume"]).iloc[-63:].median().to_numpy()[j],
        "days_to_next": EV["days_to_next"][-1, j], "days_since": EV["days_since"][-1, j], "surprise": EV["surp"][-1, j],
        "sue": EV["sue"][-1, j], "react": EV["react"][-1, j] * 100, "si_dtc": (si.sort_values("settle").groupby("symbol").tail(1).assign(
            dtc=lambda d: d["short"] / d["adv"].where(d["adv"] > 0)).set_index("symbol")["dtc"].reindex(cols[j]).to_numpy() if si is not None and len(si) else np.nan),
    })
    for name, b in boosters.items():
        h = name.split("_")[1]
        pred = b.predict(X)
        pct = pd.Series(pred).rank(pct=True).to_numpy()
        cal = pd.DataFrame(card["models"][name]["calibration"]).set_index("bin")
        bins = len(cal)
        c = cal.reindex(np.clip((pct * 0.9999 * bins).astype(int), 0, bins - 1)).reset_index(drop=True)
        xs[f"pct_{h}"] = pct
        xs[f"exp_{h}"], xs[f"beat_{h}"] = c["mean"].to_numpy() * 100, c["hit"].to_numpy() * 100
        xs[f"abs_{h}"], xs[f"odds_{h}"] = c["abs_mean"].to_numpy() * 100, c["abs_hit"].to_numpy() * 100
        for q in ("p05", "p10", "p25", "p50", "p75", "p90", "p95"):
            xs[f"{q}_{h}"] = c[q].to_numpy() * 100
        f, a = _why(b, X, feats)
        xs[f"for_{h}"], xs[f"against_{h}"] = [json.dumps(x) for x in f], [json.dumps(x) for x in a]
    # Where each index fund stands against its own 200-day average: its odds differ by state.
    state = {}
    for sym in INDEX_FUNDS:
        if sym in C:
            px = C[sym].dropna()
            state[sym] = bool(px.iloc[-1] > px.iloc[-200:].mean()) if len(px) >= 200 else True
    return {"xs": xs, "asof": dates[-1], "tradable": int(mask[-1].sum()), "index_state": state}


def run(calendar: pd.DataFrame, tradable: list[str]) -> dict | None:
    """Score the market, reusing today's result until a new session completes or new
    earnings numbers arrive. Returns None if the models are not there."""
    from edge.data import finra, wide
    if not (MODELS / "card.json").exists():
        return None
    card = json.loads((MODELS / "card.json").read_text())
    hist = pd.read_parquet(MODELS / "events_history.parquet")
    ev = pd.concat([hist, calendar[[c for c in EV_COLS if c in calendar]]]).drop_duplicates(["symbol", "date"], keep="last")
    symbols = sorted((set(ev["symbol"]) & set(tradable)) | set(INDEX_FUNDS))
    long = wide.adjusted_bars(symbols)
    asof = long["date"].max()
    recent = ev[(ev["date"] >= asof - pd.Timedelta(days=7)) & ev["surprise"].notna()]
    key = f"{asof.date()}|{len(recent)}|{card['trained_at']}"
    CACHE.mkdir(parents=True, exist_ok=True)
    meta, table = CACHE / "scores.json", CACHE / "scores.parquet"
    if meta.exists() and table.exists():
        m = json.loads(meta.read_text())
        if m.get("key") == key:
            return {"xs": pd.read_parquet(table), "asof": pd.Timestamp(m["asof"]), "tradable": m["tradable"], "index_state": m["index_state"], "card": card}
    sectors = pd.read_parquet(MODELS / "sectors.parquet") if (MODELS / "sectors.parquet").exists() else None
    industry = sectors[sectors["industry"].astype(str).str.len() > 0].drop_duplicates("symbol").set_index("symbol")["industry"] if sectors is not None else None
    try:
        si = finra.recent()
    except Exception as e:
        print("short interest unavailable:", str(e)[:80]); si = None
    out = score(long, ev, industry, si)
    if sectors is not None:
        out["xs"] = out["xs"].merge(sectors.drop_duplicates("symbol")[["symbol", "sector"]], on="symbol", how="left")
    out["xs"].to_parquet(table)
    meta.write_text(json.dumps({"key": key, "asof": str(out["asof"].date()), "tradable": out["tradable"], "index_state": out["index_state"]}))
    out["card"] = card
    return out
