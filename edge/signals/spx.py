"""SPX desk: what the index option market is pricing, where positioning sits, and
three tiers of SPX structures built from it.

Everything here comes from the chain in edge/data/spxchain.py (spot, IV, OI, delta) and the
index's own history. Nothing is claimed as proven unless the evidence block says
what was measured; the only measured SPX edge used is the variance risk premium
(implied vol above realised vol, measured here from VIX vs forward realised vol),
which the Will tier harvests with defined-risk iron condors, never naked.
"""
import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from edge.data import spxchain

CACHE = Path(__file__).resolve().parents[2] / "data" / "spx"
WING = 50            # points between short and long strike on each side of a condor
SHORT_DELTA = 0.16   # ~1 sigma: the classic 84%-OTM short strike


def _num(v, d=2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, d)


def _leg(r: pd.Series, side: int) -> dict:
    return {"contract": r["contract"], "side": side, "type": r["type"], "strike": _num(r["strike"], 0), "expiry": r["expiry"],
            "bid": _num(r["bid"]), "ask": _num(r["ask"]), "mid": _num(r["mid"]), "delta": _num(r["delta"], 2), "iv": _num(r["iv"], 3)}


def _nearest(df: pd.DataFrame, col: str, target: float):
    if df.empty:
        return None
    return df.iloc[(df[col] - target).abs().argsort().iloc[0]]


def pick_expiries(ch: pd.DataFrame) -> dict:
    """next = first session that still expires, week = this week's Friday (next week's once
    we're inside 1 day), month = the monthly ~1 month out, quarter = ~3 months out."""
    live = ch[(ch["dte"] >= 0) & ch["mid"].notna()]
    by_exp = live.groupby("expiry").agg(dte=("dte", "first"), oi=("oi", "sum"), root=("root", "first")).sort_index()
    out = {}
    out["next"] = by_exp.index[0]
    fridays = [e for e, r in by_exp.iterrows() if date.fromisoformat(e).weekday() == 4 and r["dte"] >= 1]
    out["week"] = fridays[0] if fridays else by_exp.index[min(1, len(by_exp) - 1)]
    m = by_exp[(by_exp["dte"] >= 18) & (by_exp["dte"] <= 50)]
    out["month"] = (m[m["root"] == "SPX"].index[0] if (m["root"] == "SPX").any() else m["oi"].idxmax()) if len(m) else None
    q = by_exp[(by_exp["dte"] >= 60) & (by_exp["dte"] <= 120)]
    out["quarter"] = q["oi"].idxmax() if len(q) else None
    leap = by_exp[by_exp["dte"] >= 300]
    out["leap"] = leap["oi"].idxmax() if len(leap) else None
    return out


def expected_move(ch: pd.DataFrame, expiry: str, spot: float, label: str) -> dict | None:
    """ATM straddle = the market's own one-standard-deviation-ish range to expiry."""
    e = ch[(ch["expiry"] == expiry) & ch["mid"].notna()]
    if e.empty:
        return None
    k = e.iloc[(e["strike"] - spot).abs().argsort().iloc[0]]["strike"]
    c, p = e[(e["strike"] == k) & (e["type"] == "call")], e[(e["strike"] == k) & (e["type"] == "put")]
    if c.empty or p.empty:
        return None
    straddle = float(c["mid"].iloc[0] + p["mid"].iloc[0])
    # Straddle overstates sigma slightly; 0.85x is the usual conversion. Delta-16 strikes shown too.
    move = straddle * 0.85
    up16 = _nearest(e[(e["type"] == "call") & e["delta"].notna()], "delta", SHORT_DELTA)
    dn16 = _nearest(e[(e["type"] == "put") & e["delta"].notna()], "delta", -SHORT_DELTA)
    return {"label": label, "expiry": expiry, "dte": int(e["dte"].iloc[0]), "atm": _num(k, 0), "straddle": _num(straddle),
            "move": _num(move, 0), "move_pct": _num(move / spot * 100), "lo": _num(spot - move, 0), "hi": _num(spot + move, 0),
            "d16_lo": _num(dn16["strike"], 0) if dn16 is not None else None, "d16_hi": _num(up16["strike"], 0) if up16 is not None else None,
            "iv": _num(float(c["iv"].iloc[0]) * 100, 1) if pd.notna(c["iv"].iloc[0]) else None}


def walls(ch: pd.DataFrame, spot: float, days: int = 7) -> dict:
    """Where open interest piles up this week: call walls above, put walls below, the
    put/call ratio, max pain for the next session, and the strike with the most gamma."""
    w = ch[(ch["dte"] >= 0) & (ch["dte"] <= days) & (ch["oi"] > 0)]
    if w.empty:
        return {}
    calls = w[(w["type"] == "call") & (w["strike"] >= spot)].groupby("strike")["oi"].sum().sort_values(ascending=False)
    puts = w[(w["type"] == "put") & (w["strike"] <= spot)].groupby("strike")["oi"].sum().sort_values(ascending=False)
    nxt = w[w["expiry"] == w["expiry"].min()]
    strikes = np.sort(nxt["strike"].unique())
    pain = None
    if len(strikes):
        c, p = nxt[nxt["type"] == "call"], nxt[nxt["type"] == "put"]
        loss = [(np.clip(s - c["strike"], 0, None) * c["oi"]).sum() + (np.clip(p["strike"] - s, 0, None) * p["oi"]).sum() for s in strikes]
        pain = float(strikes[int(np.argmin(loss))])
    g = w.assign(g=w["gamma"].fillna(0) * w["oi"]).groupby("strike")["g"].sum()
    return {
        "through": w["expiry"].max(), "next_expiry": nxt["expiry"].iloc[0],
        "calls": [{"strike": _num(k, 0), "oi": int(v)} for k, v in calls.head(4).items()],
        "puts": [{"strike": _num(k, 0), "oi": int(v)} for k, v in puts.head(4).items()],
        "pc_ratio": _num(w[w["type"] == "put"]["oi"].sum() / max(w[w["type"] == "call"]["oi"].sum(), 1)),
        "max_pain": _num(pain, 0), "gamma_strike": _num(g.idxmax(), 0) if len(g) else None,
        "total_oi": int(w["oi"].sum()), "volume": int(w["volume"].sum()),
    }


def history(max_age_h: float = 6) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "index_history.parquet"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return pd.read_parquet(path)
    h = yf.download(["^GSPC", "^VIX"], start="2012-01-01", progress=False, auto_adjust=True)["Close"]
    h.columns = ["SPX", "VIX"] if list(h.columns) == ["^GSPC", "^VIX"] else [c.strip("^").replace("GSPC", "SPX") for c in h.columns]
    h = h.dropna()
    h.to_parquet(path)
    return h


def vrp_evidence(h: pd.DataFrame) -> dict:
    """Variance risk premium, measured: VIX (30-day implied) vs the realised vol of the
    next 21 sessions, every day since 2012. The gap is what option sellers are paid."""
    r = np.log(h["SPX"]).diff()
    fwd = r[::-1].rolling(21).std()[::-1].shift(-1) * np.sqrt(252) * 100   # realised vol over the NEXT 21 days
    df = pd.DataFrame({"vix": h["VIX"], "realised": fwd}).dropna()
    gap = df["vix"] - df["realised"]
    yr = df.index[-252:]
    return {"since": str(df.index[0].date()), "days": int(len(df)), "share": _num((gap > 0).mean() * 100, 0),
            "gap": _num(gap.mean(), 1), "gap_1y": _num(gap.loc[yr].mean(), 1), "share_1y": _num((gap.loc[yr] > 0).mean() * 100, 0),
            "worst": _num(gap.min(), 1)}


def lean(h: pd.DataFrame, spot: float) -> dict:
    """Unproven directional lean from the tape: price vs 20/50-day averages and the week."""
    s = h["SPX"]
    sma20, sma50 = float(s.tail(20).mean()), float(s.tail(50).mean())
    ret5 = (spot / float(s.iloc[-6]) - 1) * 100 if len(s) > 6 else 0.0
    votes = [("above the 20-day average" if spot > sma20 else "below the 20-day average", 1 if spot > sma20 else -1),
             ("20-day above 50-day" if sma20 > sma50 else "20-day below 50-day", 1 if sma20 > sma50 else -1),
             (f"{ret5:+.1f}% on the week", 1 if ret5 > 0 else -1 if ret5 < 0 else 0)]
    score = sum(v for _, v in votes)
    return {"dir": "up" if score > 0 else "down" if score < 0 else "flat", "score": int(score), "reasons": [t for t, _ in votes],
            "sma20": _num(sma20, 0), "sma50": _num(sma50, 0), "ret5": _num(ret5, 1), "vix": _num(h["VIX"].iloc[-1], 1)}


def _structure(name, kind, legs, spot, note="", **extra) -> dict:
    net = sum(-l["side"] * l["mid"] for l in legs)        # + credit received, - debit paid
    out = {"name": name, "kind": kind, "expiry": legs[0]["expiry"], "legs": legs, "spot": _num(spot), "note": note, **extra}
    out["net"] = _num(net)
    return out


def condor(ch: pd.DataFrame, expiry: str, spot: float) -> dict | None:
    e = ch[(ch["expiry"] == expiry) & ch["mid"].notna() & ch["delta"].notna() & (ch["ask"] > 0)]
    sp = _nearest(e[e["type"] == "put"], "delta", -SHORT_DELTA)
    sc = _nearest(e[e["type"] == "call"], "delta", SHORT_DELTA)
    if sp is None or sc is None:
        return None
    lp = e[(e["type"] == "put") & (e["strike"] == sp["strike"] - WING)]
    lc = e[(e["type"] == "call") & (e["strike"] == sc["strike"] + WING)]
    if lp.empty or lc.empty:
        return None
    legs = [_leg(lp.iloc[0], 1), _leg(sp, -1), _leg(sc, -1), _leg(lc.iloc[0], 1)]
    credit = sum(-l["side"] * l["mid"] for l in legs)
    if credit <= 0:
        return None
    pop = 1 - (abs(float(sp["delta"])) + float(sc["delta"]))
    return _structure(f"SPX {sp['strike']:.0f}/{sp['strike'] - WING:.0f}P · {sc['strike']:.0f}/{sc['strike'] + WING:.0f}C", "Iron condor", legs, spot,
                      note="sell both 1-sigma strikes, buy the wings 50 points further out; keep the credit if SPX stays inside",
                      credit=_num(credit), max_loss=_num(WING - credit), max_gain=_num(credit), ror=_num(credit / (WING - credit) * 100, 0),
                      pop=_num(pop * 100, 0), lo=_num(sp["strike"] - credit, 0), hi=_num(sc["strike"] + credit, 0),
                      lo_pct=_num((sp["strike"] / spot - 1) * 100, 1), hi_pct=_num((sc["strike"] / spot - 1) * 100, 1), dte=int(sp["dte"]))


def debit_spread(ch: pd.DataFrame, expiry: str, spot: float, direction: str, em: float | None) -> dict | None:
    kind = "call" if direction == "up" else "put"
    e = ch[(ch["expiry"] == expiry) & (ch["type"] == kind) & ch["mid"].notna() & (ch["ask"] > 0) & ch["delta"].notna()]
    if e.empty:
        return None
    long = _nearest(e, "delta", 0.5 if kind == "call" else -0.5)
    target = long["strike"] + (em or spot * 0.01) * (1 if kind == "call" else -1)
    short = _nearest(e[(e["strike"] > long["strike"]) if kind == "call" else (e["strike"] < long["strike"])], "strike", target)
    if long is None or short is None:
        return None
    legs = [_leg(long, 1), _leg(short, -1)]
    debit = long["mid"] - short["mid"]
    width = abs(short["strike"] - long["strike"])
    if debit <= 0 or width <= debit:
        return None
    be = long["strike"] + debit * (1 if kind == "call" else -1)
    return _structure(f"SPX {long['strike']:.0f}/{short['strike']:.0f} {kind.title()} spread", f"{kind.title()} debit spread", legs, spot,
                      note=f"buy the at-the-money {kind}, sell one expected move further; pays {width / debit:.1f}x if SPX gets there",
                      debit=_num(debit), max_loss=_num(debit), max_gain=_num(width - debit), ror=_num((width - debit) / debit * 100, 0),
                      breakeven=_num(be, 0), needs_pct=_num((be / spot - 1) * 100, 1), dte=int(long["dte"]))


def single(ch: pd.DataFrame, expiry: str, spot: float, kind: str, delta_lo: float, delta_hi: float, max_ask: float, name: str, label: str) -> dict | None:
    e = ch[(ch["expiry"] == expiry) & (ch["type"] == kind) & ch["mid"].notna() & (ch["ask"] > 0) & (ch["ask"] <= max_ask) & ch["delta"].notna()]
    e = e[e["delta"].abs().between(delta_lo, delta_hi)]
    if e.empty:
        return None
    r = e.iloc[(e["delta"].abs() - (delta_lo + delta_hi) / 2).abs().argsort().iloc[0]]
    be = r["strike"] + r["ask"] * (1 if kind == "call" else -1)
    legs = [_leg(r, 1)]
    return _structure(f"SPX ${r['strike']:.0f} {kind.title()}", name, legs, spot, note=label, debit=_num(r["ask"]), max_loss=_num(r["ask"]),
                      breakeven=_num(be, 0), needs_pct=_num((be / spot - 1) * 100, 1), dte=int(r["dte"]), delta=_num(r["delta"]))


def build() -> dict:
    header, ch = spxchain.index_chain()
    spot = float(header["spot"])
    ex = pick_expiries(ch)
    h = history()
    L = lean(h, spot)
    moves = [m for m in (expected_move(ch, ex[k], spot, lab) for k, lab in (("next", "next session"), ("week", "this week"), ("month", "month"), ("quarter", "quarter")) if ex.get(k)) if m]
    em = {m["label"]: m["move"] for m in moves}
    other = "down" if L["dir"] == "up" else "up"

    will = {"weekly": [c for c in [condor(ch, ex["week"], spot)] if c],
            "month": [c for c in [condor(ch, ex["month"], spot)] if ex.get("month") and c], "years": []}
    maybe = {"weekly": [], "month": [], "years": []}
    if L["dir"] != "flat":
        maybe["weekly"] = [s for s in [debit_spread(ch, ex["week"], spot, L["dir"], em.get("this week"))] if s]
        if ex.get("month"):
            maybe["month"] = [s for s in [debit_spread(ch, ex["month"], spot, L["dir"], em.get("month"))] if s]
        if ex.get("leap"):
            kind = "call" if L["dir"] == "up" else "put"
            maybe["years"] = [s for s in [single(ch, ex["leap"], spot, kind, 0.40, 0.55, 1e9, f"LEAP {kind}", "near the money with a year to run; the slow trade on the lean")] if s]
    lotto = {"weekly": [], "month": [], "years": []}
    for d in ([L["dir"], other] if L["dir"] != "flat" else ["up", "down"]):
        kind = "call" if d == "up" else "put"
        for exp_key, tag in (("next", "next session"), ("week", "this week")):
            s = single(ch, ex[exp_key], spot, kind, 0.03, 0.10, 3.0, f"Lotto {kind}", "")
            if s:
                s["note"] = f"{tag} · about {abs(s['delta']) * 100:.0f}% chance of finishing in the money"
                lotto["weekly"].append(s)
        if ex.get("month"):
            s = single(ch, ex["month"], spot, kind, 0.03, 0.08, 12.0, f"Lotto {kind}", "")
            if s:
                s["note"] = f"about {abs(s['delta']) * 100:.0f}% chance of finishing in the money"
                lotto["month"].append(s)
        if ex.get("leap"):
            s = single(ch, ex["leap"], spot, kind, 0.05, 0.12, 60.0, f"LEAP lotto {kind}", "")
            if s:
                s["note"] = f"about {abs(s['delta']) * 100:.0f}% chance of finishing in the money · a year for the thesis to play"
                lotto["years"].append(s)

    marks = {}
    for tier in (will, maybe, lotto):
        for rows in tier.values():
            for s in rows:
                for l in s["legs"]:
                    marks[l["contract"]] = {"bid": l["bid"], "ask": l["ask"], "mid": l["mid"], "spot": _num(spot), "dte": s["dte"]}
    return {
        **{k: _num(v) for k, v in header.items() if k not in ("symbol", "as_of")}, "as_of": header["as_of"],
        "vix": L["vix"], "expiries": ex, "expected": moves, "walls": walls(ch, spot), "lean": L,
        "evidence": vrp_evidence(h), "tiers": {"will": will, "maybe": maybe, "lotto": lotto}, "marks": marks,
        "contracts": int(len(ch)),
    }
