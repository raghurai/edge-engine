"""Company-level data beyond price: fundamentals, analysts, positioning, insiders.

All from Yahoo (free, no key), cached per symbol in data/fund/. Each getter
returns a dated table so experiments can use only what was known at the time.
"""
import logging
import os
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

logging.getLogger("yfinance").setLevel(logging.CRITICAL)   # ETFs have no fundamentals; don't shout about it

CACHE = Path(__file__).resolve().parents[2] / "data" / "fund"
# Each run may fetch at most this many NEW symbols from Yahoo, so a run always finishes
# in minutes and the cache fills up across runs instead of needing one giant build.
BUDGET = {"left": int(os.getenv("MAX_NEW_FUND", "300")), "skipped": 0}


def _cached(symbol: str, name: str, build, max_age_h: float = 48) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{symbol}_{name}.parquet"
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return pd.read_parquet(p)
    if p.exists():
        stale = pd.read_parquet(p)          # refresh if budget allows, else use what we have
        if BUDGET["left"] <= 0:
            return stale
    elif BUDGET["left"] <= 0:
        BUDGET["skipped"] += 1
        return pd.DataFrame()
    if name == "quarterly":                 # count once per symbol (quarterly is fetched first)
        BUDGET["left"] -= 1
    try:
        df = build()
    except Exception:
        df = pd.DataFrame()
    if df is None:
        df = pd.DataFrame()
    df.to_parquet(p)
    return df


def quarterly(symbol: str) -> pd.DataFrame:
    """One row per fiscal quarter: revenue, gross/operating margin, net income, op cash flow, debt.
    Indexed by quarter end date (point-in-time: use rows with date < event)."""
    def build():
        t = yf.Ticker(symbol)
        inc, cf, bs = t.quarterly_income_stmt, t.quarterly_cashflow, t.quarterly_balance_sheet
        rows = {}
        for col in inc.columns:
            g = lambda frame, k: float(frame.loc[k, col]) if k in frame.index and pd.notna(frame.loc[k, col]) else None
            rev = g(inc, "Total Revenue")
            rows[pd.Timestamp(col)] = {
                "revenue": rev,
                "gross_margin": (g(inc, "Gross Profit") / rev) if rev and g(inc, "Gross Profit") else None,
                "op_margin": (g(inc, "Operating Income") / rev) if rev and g(inc, "Operating Income") else None,
                "net_income": g(inc, "Net Income"),
                "op_cash_flow": g(cf, "Operating Cash Flow") if col in cf.columns else None,
                "total_debt": g(bs, "Total Debt") if col in bs.columns else None,
                "cash": g(bs, "Cash And Cash Equivalents") if col in bs.columns else None,
            }
        df = pd.DataFrame(rows).T.sort_index()
        df.index.name = "quarter"
        return df.reset_index()
    return _cached(symbol, "quarterly", build)


def analyst_actions(symbol: str) -> pd.DataFrame:
    """Dated upgrades/downgrades: firm, from-grade, to-grade, action."""
    def build():
        df = yf.Ticker(symbol).upgrades_downgrades
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.reset_index().rename(columns={"GradeDate": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        return df[["date", "Firm", "ToGrade", "FromGrade", "Action"]].rename(columns=str.lower)
    return _cached(symbol, "analysts", build)


def insiders(symbol: str) -> pd.DataFrame:
    """Dated insider transactions: shares, value, buy/sell text."""
    def build():
        df = yf.Ticker(symbol).insider_transactions
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={"Start Date": "date", "Shares": "shares", "Value": "value", "Text": "text"})
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df["buy"] = df["text"].astype(str).str.contains("Purchase|Buy", case=False)
        return df[["date", "shares", "value", "text", "buy"]]
    return _cached(symbol, "insiders", build)


def snapshot(symbol: str) -> dict:
    """Current-only fields (not point-in-time): short % float, institutional %, target price, PE, beta, cap."""
    def build():
        i = yf.Ticker(symbol).info or {}
        keys = {"shortPercentOfFloat": "short_pct_float", "heldPercentInstitutions": "inst_pct",
                "targetMeanPrice": "target_price", "currentPrice": "price", "trailingPE": "pe", "forwardPE": "fwd_pe",
                "beta": "beta", "marketCap": "market_cap", "revenueGrowth": "rev_growth", "earningsGrowth": "eps_growth",
                "recommendationMean": "rec_mean", "numberOfAnalystOpinions": "n_analysts"}
        return pd.DataFrame([{v: i.get(k) for k, v in keys.items()}])
    df = _cached(symbol, "snapshot", build, max_age_h=24)
    return df.iloc[0].to_dict() if len(df) else {}


def features_at(symbol: str, when: pd.Timestamp, q: pd.DataFrame | None = None,
                a: pd.DataFrame | None = None, ins: pd.DataFrame | None = None) -> dict:
    """Point-in-time company features as of `when` (only data dated before it)."""
    q = quarterly(symbol) if q is None else q
    a = analyst_actions(symbol) if a is None else a
    ins = insiders(symbol) if ins is None else ins
    f = {}
    if len(q):
        past = q[q["quarter"] < when].tail(5)
        if len(past) >= 5 and past["revenue"].notna().all():
            r = past["revenue"].values
            f["rev_yoy"] = r[-1] / r[0] - 1 if r[0] else None             # vs same quarter last year
            f["rev_qoq"] = r[-1] / r[-2] - 1 if r[-2] else None
            f["rev_accel"] = (r[-1] / r[-2] - r[-2] / r[-3]) if r[-2] and r[-3] else None
        if len(past) >= 2:
            f["gm_change"] = (past["gross_margin"].iloc[-1] or 0) - (past["gross_margin"].iloc[-2] or 0) if past["gross_margin"].notna().iloc[-2:].all() else None
            f["opm_change"] = (past["op_margin"].iloc[-1] or 0) - (past["op_margin"].iloc[-2] or 0) if past["op_margin"].notna().iloc[-2:].all() else None
            f["op_margin"] = past["op_margin"].iloc[-1]
    if len(a):
        w = a[(a["date"] < when) & (a["date"] >= when - pd.Timedelta(days=90))]
        f["upgrades_90d"] = int((w["action"] == "up").sum()); f["downgrades_90d"] = int((w["action"] == "down").sum())
        f["net_rating_90d"] = f["upgrades_90d"] - f["downgrades_90d"]
    if len(ins):
        w = ins[(ins["date"] < when) & (ins["date"] >= when - pd.Timedelta(days=180))]
        f["insider_buys_180d"] = int(w["buy"].sum()); f["insider_sells_180d"] = int((~w["buy"]).sum())
        f["insider_net_value_180d"] = float((w["value"].fillna(0) * w["buy"].map({True: 1, False: -1})).sum())
    return f


def after_event(symbol: str, when: pd.Timestamp, days: int = 10, a: pd.DataFrame | None = None) -> dict:
    """Analyst reaction in the days after an event: the slow buyer's paper trail."""
    a = analyst_actions(symbol) if a is None else a
    if not len(a):
        return {}
    w = a[(a["date"] >= when) & (a["date"] <= when + pd.Timedelta(days=days))]
    return {"upgrades_after": int((w["action"] == "up").sum()), "downgrades_after": int((w["action"] == "down").sum())}
