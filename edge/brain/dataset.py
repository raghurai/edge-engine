"""Research panel and training tables for the brain.

panel()   -> wide matrices for every company that ever reported earnings (listed or
             delisted), 2016 to today, from the SIP-adjusted chunks in data/research/bars.
tables()  -> two training tables built with the shared feature library:
               weekly : every tradable name, sampled every 5th trading day
               events : every earnings report, on the day the market reacted
             each row carrying its features and forward excess returns (5/21/40/126 days).
"""
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from edge.brain import features as F

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "data" / "research"
HORIZONS = (5, 21, 40, 126, 252)
FIELDS = ("open", "high", "low", "close", "volume")


def events() -> pd.DataFrame:
    from edge.data import nasdaq_cal
    path = RESEARCH / "events.parquet"
    if path.exists():
        return pd.read_parquet(path)
    from datetime import date
    ev = nasdaq_cal.events(back_days=(date.today() - date(2017, 1, 2)).days, ahead_days=0, max_age_h=24 * 365)
    return ev


def panel(symbols: set | None = None, min_rows: int = 260, cache: bool = True) -> dict:
    cdir = RESEARCH / "panel"
    if cache and (cdir / "close.parquet").exists():
        return {f: pd.read_parquet(cdir / f"{f}.parquet") for f in FIELDS}
    files = sorted(glob.glob(str(RESEARCH / "bars" / "chunk_*.parquet")))
    parts = {f: [] for f in FIELDS}
    for fp in files:
        d = pd.read_parquet(fp)
        if d.empty:
            continue
        keep = d["symbol"].isin(symbols) if symbols is not None else pd.Series(True, index=d.index)
        n = d.groupby("symbol")["close"].transform("size")
        d = d[keep & (n >= min_rows)]
        if d.empty:
            continue
        for f in FIELDS:
            parts[f].append(d.pivot(index="date", columns="symbol", values=f).astype("float32"))
    P = {f: pd.concat(parts[f], axis=1).sort_index() for f in FIELDS}
    dates = P["close"][F.BENCH].dropna().index            # the market's own calendar
    P = {f: P[f].reindex(dates) for f in FIELDS}
    if cache:
        cdir.mkdir(parents=True, exist_ok=True)
        for f in FIELDS:
            P[f].to_parquet(cdir / f"{f}.parquet")
    return P


def industries() -> pd.Series | None:
    """symbol -> industry (Nasdaq's listing directory). Delisted names have none."""
    path = RESEARCH / "sectors.parquet"
    if not path.exists():
        return None
    s = pd.read_parquet(path)
    s = s[s["industry"].astype(str).str.len() > 0]
    return s.drop_duplicates("symbol").set_index("symbol")["industry"]


def short_interest() -> pd.DataFrame | None:
    """Every FINRA short-interest file, long: symbol, settle, short, adv, chg."""
    files = sorted(glob.glob(str(RESEARCH / "finra" / "shrt*.csv")))
    if not files:
        return None
    parts = []
    want = ["symbolCode", "currentShortPositionQuantity", "averageDailyVolumeQuantity", "changePercent", "settlementDate"]
    for fp in files:
        # Quotes are not escaped consistently in issuer names, so read them as plain characters.
        d = pd.read_csv(fp, sep="|", quoting=3, dtype=str, on_bad_lines="skip", encoding_errors="ignore")
        d.columns = [c.strip('"') for c in d.columns]
        if not set(want) <= set(d.columns):
            continue
        parts.append(d[want].apply(lambda c: c.str.strip('"')))
    d = pd.concat(parts)
    d.columns = ["symbol", "short", "adv", "chg", "settle"]
    d["settle"] = pd.to_datetime(d["settle"])
    for c in ("short", "adv", "chg"):
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("float32")
    return d.dropna(subset=["symbol"]).drop_duplicates(["symbol", "settle"])


def tables(P: dict, ev: pd.DataFrame, sample_every: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    C = P["close"]
    dates, cols = C.index, C.columns
    mask = F.universe_mask(P)
    EV = F.event_panel(P, ev)
    Y, A = F.forward_returns(P, HORIZONS)
    up = F.regime(P).to_numpy()
    dv = (C * P["volume"]).rolling(63, min_periods=40).median().to_numpy(dtype="float32")

    rows = np.arange(F.MIN_HISTORY, len(dates), sample_every)
    wk = np.zeros_like(mask); wk[rows] = True
    tw, jw = np.nonzero(mask & wk)
    hist = np.zeros_like(mask); hist[F.MIN_HISTORY:] = True
    te, je = np.nonzero(EV["is_reaction"] & mask & hist)

    def frame(t, j):
        d = {"date": dates[t], "symbol": cols[j], "dollar_vol": dv[t, j], "uptrend": up[t]}
        for h in HORIZONS:
            d[f"y_{h}"] = Y[h][t, j]
            d[f"a_{h}"] = A[h][t, j]
        return d
    W, E = frame(tw, jw), frame(te, je)
    names = []
    for name, M in F.iter_features(P, EV, mask, industry=industries(), SI=F.short_interest_panel(P, short_interest())):
        W[name] = M[tw, jw]; E[name] = M[te, je]; names.append(name)
    W, E = pd.DataFrame(W), pd.DataFrame(E)
    W.attrs["features"] = E.attrs["features"] = names
    return W, E


def build_tables() -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Build both training tables from the cached panel and save them with their feature list."""
    import json
    ev = events()
    P = panel(symbols=set(ev["symbol"]) | {F.BENCH})
    W, E = tables(P, ev)
    feats = W.attrs["features"]
    tdir = RESEARCH / "tables"
    tdir.mkdir(parents=True, exist_ok=True)
    W.to_parquet(tdir / "weekly.parquet"); E.to_parquet(tdir / "events.parquet")
    (tdir / "features.json").write_text(json.dumps(feats))
    return W, E, feats
