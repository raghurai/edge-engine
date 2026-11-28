"""Contract brain: pick the option that best expresses a forecast, by expected value.

The old picker took "a strike near the money, about N days out". This one prices
every listed contract against the engine's own forecast for the stock:

  1. implied vol for each contract, solved from its mid (the free feed has none)
  2. a fat-tailed distribution of where the stock is when the trade is closed,
     centred on the forecast and as wide as the stock's realised / implied vol
  3. each contract revalued in every scenario (time decay and the spread included)
  4. the winner maximises expected log growth of a fixed slice of capital, which
     rewards expected value and punishes lottery-like variance; a contract whose
     expected value is negative is reported as such, never dressed up

Outputs per contract: expected return on premium, probability of profit, the stock
move it needs, and the vol it is priced at next to the vol the stock has realised.
"""
import math

import numpy as np
import pandas as pd
from scipy.stats import norm, t as student_t

RATE = 0.04
SLICE = 0.25                 # fraction of the sleeve assumed on one contract, for the growth score
MAX_SPREAD = 0.35            # (ask - bid) / mid: wider than this is not a tradable quote
_Q = (np.arange(1, 400) / 400.0)   # scenario grid: 399 equally likely quantiles


def bs(S, K, T, vol, kind):
    """Black-Scholes value. Vectorised; T in years. At T<=0 it is intrinsic value."""
    S, K, T, vol = np.broadcast_arrays(np.asarray(S, float), np.asarray(K, float), np.asarray(T, float), np.asarray(vol, float))
    call = np.asarray(kind) == "call"
    intrinsic = np.where(call, np.maximum(S - K, 0), np.maximum(K - S, 0))
    ok = (T > 1e-6) & (vol > 1e-6)
    Ts, vs = np.where(ok, T, 1.0), np.where(ok, vol, 1.0)
    d1 = (np.log(S / K) + (RATE + 0.5 * vs ** 2) * Ts) / (vs * np.sqrt(Ts))
    d2 = d1 - vs * np.sqrt(Ts)
    c = S * norm.cdf(d1) - K * np.exp(-RATE * Ts) * norm.cdf(d2)
    p = K * np.exp(-RATE * Ts) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return np.where(ok, np.where(call, c, p), intrinsic)


def delta(S, K, T, vol, kind):
    d1 = (np.log(S / K) + (RATE + 0.5 * vol ** 2) * T) / (vol * np.sqrt(T))
    return np.where(np.asarray(kind) == "call", norm.cdf(d1), norm.cdf(d1) - 1)


def implied_vol(price, S, K, T, kind, lo=0.03, hi=5.0):
    """Bisection on vol, vectorised. NaN where the price is outside the no-arbitrage range."""
    price, K, T = np.asarray(price, float), np.asarray(K, float), np.asarray(T, float)
    a, b = np.full_like(price, lo), np.full_like(price, hi)
    bad = (price <= bs(S, K, T, lo, kind)) | (price >= bs(S, K, T, hi, kind)) | (T <= 0)
    for _ in range(40):
        m = (a + b) / 2
        up = bs(S, K, T, m, kind) < price
        a, b = np.where(up, m, a), np.where(up, b, m)
    return np.where(bad, np.nan, (a + b) / 2)


def scenarios(spot: float, exp_ret: float, sigma_h: float) -> np.ndarray:
    """Stock prices at exit: Student-t(4) log returns (fat tails), scaled to sigma_h and
    shifted so the mean simple return equals exp_ret."""
    z = student_t.ppf(_Q, df=4) / math.sqrt(2.0)        # t(4) has variance 2
    r = np.exp(z * sigma_h)
    r = r * (1 + exp_ret) / r.mean()
    return spot * r


def evaluate(ch: pd.DataFrame, spot: float, kind: str, exp_ret: float, hold_days: int, realised_vol: float,
             dte: tuple[int, int] | None = None, moneyness: tuple[float, float] = (-12.0, 30.0), max_ask: float | None = None) -> pd.DataFrame:
    """Score every candidate contract of one type. exp_ret is the forecast simple return of the
    stock over hold_days trading days; realised_vol is its annualised daily vol."""
    if ch is None or ch.empty or not spot or spot <= 0:
        return pd.DataFrame()
    cal_hold = int(round(hold_days * 365 / 252))
    lo, hi = dte or (cal_hold + 10, cal_hold + 150)
    sign = 1 if kind == "call" else -1
    c = ch[(ch["type"] == kind) & ch["dte"].between(lo, hi) & (ch["bid"] > 0) & (ch["ask"] > 0) & ch["mid"].notna()].copy()
    c = c[(c["otm_pct"] * sign).between(*moneyness)]
    if max_ask is not None:
        c = c[c["ask"] <= max_ask]
    if c.empty:
        return c
    c["spread"] = (c["ask"] - c["bid"]) / c["mid"]
    c = c[(c["spread"] <= MAX_SPREAD) & (c["mid"] >= 0.05)]
    if c.empty:
        return c
    T = c["dte"].to_numpy() / 365.0
    K = c["strike"].to_numpy()
    c["iv"] = implied_vol(c["mid"].to_numpy(), spot, K, T, kind)
    c = c[c["iv"].notna()]
    if c.empty:
        return c
    T, K, iv = c["dte"].to_numpy() / 365.0, c["strike"].to_numpy(), c["iv"].to_numpy()
    atm_iv = float(c.iloc[(c["otm_pct"].abs()).argsort().iloc[0]]["iv"])
    # Width of the forecast: the stock's own realised vol, blended with what options imply.
    vol = 0.5 * realised_vol + 0.5 * atm_iv if realised_vol and realised_vol == realised_vol else atm_iv
    S_T = scenarios(spot, exp_ret, vol * math.sqrt(hold_days / 252.0))           # (scenarios,)
    T_left = np.maximum(T - cal_hold / 365.0, 0.0)
    # Exit value: same implied vol less a 5% haircut, sold half a spread below mid.
    val = bs(S_T[:, None], K[None, :], T_left[None, :], (iv * 0.95)[None, :], kind)
    half = ((c["ask"] - c["bid"]) / 2).to_numpy()
    exit_px = np.maximum(val - half[None, :], 0.0)
    ask = c["ask"].to_numpy()
    R = exit_px / ask[None, :] - 1                                               # return on premium per scenario
    c["ev"] = R.mean(axis=0)
    c["p_profit"] = (R > 0).mean(axis=0)
    c["p_double"] = (R >= 1).mean(axis=0)
    c["growth"] = np.log1p(SLICE * np.maximum(R, -1.0)).mean(axis=0)
    c["breakeven"] = K + sign * ask
    c["breakeven_move_pct"] = (c["breakeven"] / spot - 1) * 100
    c["delta"] = delta(spot, K, T, iv, kind)
    c["atm_iv"] = atm_iv
    c["vol_used"] = vol
    c["spot"] = spot
    return c.sort_values("growth", ascending=False)


def best(ch, spot, kind, exp_ret, hold_days, realised_vol, **kw) -> dict | None:
    """The contract with the highest expected growth, as a JSON-ready dict."""
    c = evaluate(ch, spot, kind, exp_ret, hold_days, realised_vol, **kw)
    if c is None or c.empty:
        return None
    r = c.iloc[0]
    out = {}
    for k in ("contract", "underlying", "type", "expiry", "dte", "strike", "bid", "ask", "mid", "last", "otm_pct", "spot", "breakeven_move_pct"):
        v = r.get(k)
        if hasattr(v, "item"):
            v = v.item()
        out[k] = None if isinstance(v, float) and v != v else (round(v, 2) if isinstance(v, float) else v)
    out.update({
        "ev_pct": round(float(r["ev"]) * 100, 0), "p_profit": round(float(r["p_profit"]) * 100, 0), "p_double": round(float(r["p_double"]) * 100, 0),
        "iv": round(float(r["iv"]) * 100, 0), "realised_vol": round(float(realised_vol) * 100, 0) if realised_vol == realised_vol else None,
        "delta": round(float(r["delta"]), 2), "spread_pct": round(float(r["spread"]) * 100, 0), "hold_days": int(hold_days),
        "exp_ret_pct": round(float(exp_ret) * 100, 1), "candidates": int(len(c)), "positive_ev": bool(r["ev"] > 0),
    })
    return out


def condor(ch: pd.DataFrame, spot: float, underlying: str, dte: tuple[int, int], wing: float, short_delta: float = 0.16) -> dict | None:
    """Defined-risk premium sale: short the ~1-sigma put and call, long wings `wing` dollars out.
    Deltas come from implied vols solved here, since the free feed carries none."""
    if ch is None or ch.empty:
        return None
    c = ch[ch["dte"].between(*dte) & (ch["bid"] > 0) & (ch["ask"] > 0) & ch["mid"].notna()]
    if c.empty:
        return None
    target = (dte[0] + dte[1]) / 2
    expiry = c.assign(d=(c["dte"] - target).abs()).sort_values("d")["expiry"].iloc[0]
    c = c[c["expiry"] == expiry].copy()
    T = c["dte"].to_numpy() / 365.0
    c["iv"] = implied_vol(c["mid"].to_numpy(), spot, c["strike"].to_numpy(), T, c["type"].to_numpy())
    c = c[c["iv"].notna()]
    if c.empty:
        return None
    c["delta"] = delta(spot, c["strike"].to_numpy(), c["dte"].to_numpy() / 365.0, c["iv"].to_numpy(), c["type"].to_numpy())
    puts, calls = c[(c["type"] == "put") & (c["strike"] < spot)], c[(c["type"] == "call") & (c["strike"] > spot)]
    if puts.empty or calls.empty:
        return None
    sp = puts.iloc[(puts["delta"] + short_delta).abs().argsort().iloc[0]]
    sc = calls.iloc[(calls["delta"] - short_delta).abs().argsort().iloc[0]]
    lp, lc = puts[puts["strike"] == sp["strike"] - wing], calls[calls["strike"] == sc["strike"] + wing]
    if lp.empty or lc.empty:
        return None
    def leg(r, side):
        return {"contract": r["contract"], "side": side, "type": r["type"], "strike": float(r["strike"]), "expiry": r["expiry"],
                "bid": round(float(r["bid"]), 2), "ask": round(float(r["ask"]), 2), "mid": round(float(r["mid"]), 2),
                "delta": round(float(r["delta"]), 2), "iv": round(float(r["iv"]), 3)}
    legs = [leg(lp.iloc[0], 1), leg(sp, -1), leg(sc, -1), leg(lc.iloc[0], 1)]
    credit = sum(-l["side"] * l["mid"] for l in legs)
    if credit <= 0.02 or credit >= wing:
        return None
    k = lambda x: f"{x:g}"
    return {"name": f"{underlying} {k(sp['strike'])}/{k(sp['strike'] - wing)}P · {k(sc['strike'])}/{k(sc['strike'] + wing)}C", "kind": "Iron condor",
            "underlying": underlying, "expiry": expiry, "legs": legs, "spot": round(float(spot), 2), "dte": int(sp["dte"]),
            "note": "sell both 1-sigma strikes, buy the wings; keep the credit if the index stays inside",
            "net": round(credit, 2), "credit": round(credit, 2), "max_loss": round(wing - credit, 2), "max_gain": round(credit, 2),
            "ror": round(credit / (wing - credit) * 100), "pop": round((1 - (abs(float(sp["delta"])) + float(sc["delta"]))) * 100),
            "lo": round(float(sp["strike"]) - credit, 2), "hi": round(float(sc["strike"]) + credit, 2),
            "lo_pct": round((float(sp["strike"]) / spot - 1) * 100, 1), "hi_pct": round((float(sc["strike"]) / spot - 1) * 100, 1)}
