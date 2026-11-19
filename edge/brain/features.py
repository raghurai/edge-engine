"""Feature library for the brain. One implementation, used by research and by the
live scorer, so the model never sees a different input in production than in training.

Everything works on wide matrices (rows = trading days, columns = symbols) and only
looks backwards: a feature on day t uses data up to the close of day t. Decisions are
made after that close; trades are assumed at the NEXT open.
"""
import numpy as np
import pandas as pd

BENCH = "SPY"
MIN_HISTORY = 235          # trading days of history a live panel must carry
EVENT_MEMORY = 80          # days an earnings report stays "the last report"
MIN_DOLLAR_VOL = 2e6       # tradable universe: median daily dollar volume over 63 days


def wide(long: pd.DataFrame) -> dict:
    """Long bars (symbol, date, open, high, low, close, volume) -> dict of wide float32 frames."""
    out = {}
    for f in ("open", "high", "low", "close", "volume"):
        out[f] = long.pivot(index="date", columns="symbol", values=f).astype("float32").sort_index()
    return out


def reaction_index(P: dict, events: pd.DataFrame) -> pd.DataFrame:
    """For every earnings report, the trading day the market first reacted on.
    before-open -> that day; after-close -> the next day; time unknown -> whichever of
    the two days traded more against its own norm."""
    dates, cols = P["close"].index, P["close"].columns
    col_of = {s: i for i, s in enumerate(cols)}
    dv = (P["close"] * P["volume"])
    rel = (dv / dv.rolling(20, min_periods=5).mean().shift(1)).to_numpy()
    ev = events[events["symbol"].isin(col_of)].copy()
    ev["j"] = ev["symbol"].map(col_of).astype(int)
    ev["di"] = dates.searchsorted(ev["date"].to_numpy())
    ev = ev[ev["di"] < len(dates)]
    di, j = ev["di"].to_numpy(), ev["j"].to_numpy()
    nxt = np.minimum(di + 1, len(dates) - 1)
    v0, v1 = rel[di, j], rel[nxt, j]
    sess = ev["session"].to_numpy()
    exact = dates[di] == ev["date"].to_numpy()          # report dated on a non-trading day reacts on the next session
    ri = np.where(~exact, di, np.where(sess == "before", di, np.where(sess == "after", nxt, np.where(np.nan_to_num(v1) > np.nan_to_num(v0), nxt, di))))
    # A report after today's close has not reacted yet: its reaction day is still in the future.
    pending = (sess == "after") & exact & (di + 1 > len(dates) - 1)
    ev["ri"] = ri
    ev["pending"] = pending
    return ev


def event_panel(P: dict, events: pd.DataFrame, future: pd.DataFrame | None = None) -> dict:
    """Matrices describing each name's most recent earnings report as of every day."""
    C, V = P["close"], P["volume"]
    dates, cols = C.index, C.columns
    T, N = C.shape
    R = C.pct_change(fill_method=None).to_numpy()
    mkt = R[:, cols.get_loc(BENCH)] if BENCH in cols else np.nanmedian(R, axis=1)
    dvr = (C * V / (C * V).rolling(20, min_periods=5).mean().shift(1)).to_numpy()
    H, L, c = P["high"].to_numpy(), P["low"].to_numpy(), C.to_numpy()
    ev = reaction_index(P, events.dropna(subset=["surprise"]))
    ev = ev[~ev["pending"]].sort_values(["j", "ri"])
    names = ("surp", "react", "react_vol", "react_clv", "prev_surp", "beats4", "n_est", "est_small", "react_close",
             "sue", "eps_yoy", "eps_accel")
    M = {k: np.full((T, N), np.nan, dtype="float32") for k in names}
    idx = np.full((T, N), np.nan, dtype="float32")
    last_j, hist, dollars, eps_hist = -1, [], [], []
    for ri, j, s, ne, est, eps in zip(ev["ri"].to_numpy(), ev["j"].to_numpy(), ev["surprise"].to_numpy(), ev["n_estimates"].to_numpy(),
                                      ev["estimate"].to_numpy(), ev["eps"].to_numpy()):
        if j != last_j:
            last_j, hist, dollars, eps_hist = j, [], [], []
        s = float(np.clip(s, -200, 200))
        # SUE: this quarter's dollar surprise against the spread of the name's own past surprises.
        d = eps - est if (eps == eps and est == est) else np.nan
        if d == d and len(dollars) >= 3:
            sd = float(np.std(dollars[-8:]))
            M["sue"][ri, j] = float(np.clip(d / sd, -10, 10)) if sd > 1e-6 else 0.0
        # Year-over-year EPS growth (same quarter last year), scaled so sign flips stay sane, and its change.
        if eps == eps and len(eps_hist) >= 4 and eps_hist[-4] == eps_hist[-4]:
            base = max(abs(eps_hist[-4]), 0.10)
            g = float(np.clip((eps - eps_hist[-4]) / base, -5, 5))
            M["eps_yoy"][ri, j] = g
            if len(eps_hist) >= 5 and eps_hist[-5] == eps_hist[-5] and eps_hist[-1] == eps_hist[-1]:
                g_prev = float(np.clip((eps_hist[-1] - eps_hist[-5]) / max(abs(eps_hist[-5]), 0.10), -5, 5))
                M["eps_accel"][ri, j] = g - g_prev
        if d == d:
            dollars.append(d)
        eps_hist.append(eps)
        M["surp"][ri, j] = s
        M["react"][ri, j] = R[ri, j] - mkt[ri]
        M["react_vol"][ri, j] = dvr[ri, j]
        rng = H[ri, j] - L[ri, j]
        M["react_clv"][ri, j] = (c[ri, j] - L[ri, j]) / rng - 0.5 if rng > 0 else 0.0
        M["prev_surp"][ri, j] = hist[-1] if hist else np.nan
        M["beats4"][ri, j] = sum(1 for x in hist[-3:] + [s] if x > 0)
        M["n_est"][ri, j] = ne if ne == ne else np.nan
        M["est_small"][ri, j] = 1.0 if (est == est and abs(est) < 0.05) else 0.0
        M["react_close"][ri, j] = c[ri, j]
        idx[ri, j] = ri
        hist.append(s)
    out = {}
    row = np.arange(T, dtype="float32")[:, None]
    idx_f = pd.DataFrame(idx).ffill(limit=EVENT_MEMORY).to_numpy()
    out["days_since"] = row - idx_f
    for k in names:
        out[k] = pd.DataFrame(M[k]).ffill(limit=EVENT_MEMORY).to_numpy()
    out["post_drift"] = c / out.pop("react_close") - 1
    out["is_reaction"] = ~np.isnan(idx)
    # Days to the next scheduled report (capped at 30: dates firm up only a few weeks out).
    sched = events if future is None else pd.concat([events, future])
    sched = sched[sched["symbol"].isin(set(cols))]
    ext = dates.append(pd.bdate_range(dates[-1] + pd.Timedelta(days=1), periods=31))
    nidx = np.full((len(ext), N), np.nan, dtype="float32")
    di = ext.searchsorted(sched["date"].to_numpy())
    ok = di < len(ext)
    nidx[di[ok], cols.get_indexer(sched["symbol"].to_numpy()[ok])] = di[ok]
    nxt = pd.DataFrame(nidx).bfill(limit=30).to_numpy()[:T]
    out["days_to_next"] = np.clip(nxt - row, 0, 30)
    return {k: (v if v.dtype == bool else v.astype("float32")) for k, v in out.items()}


def universe_mask(P: dict) -> np.ndarray:
    """Tradable on day t: really traded that day, and liquid enough over the last quarter."""
    dv = P["close"] * P["volume"]
    med = dv.rolling(63, min_periods=40).median()
    return (P["close"].notna() & (med >= MIN_DOLLAR_VOL)).to_numpy()


def _rank(a: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Cross-sectional rank per day among tradable names, mapped to [-0.5, 0.5]."""
    x = np.where(mask, a, np.nan)
    r = pd.DataFrame(x).rank(axis=1, pct=True).to_numpy(dtype="float32")
    return r - 0.5


def short_interest_panel(P: dict, si: pd.DataFrame | None) -> dict:
    """FINRA twice-monthly short interest as daily matrices, available only from the day it
    was actually published (settlement date + 12 calendar days), carried up to 25 sessions."""
    C = P["close"]
    T, N = C.shape
    out = {k: np.full((T, N), np.nan, dtype="float32") for k in ("si_dtc", "si_chg", "si_dtc_3m")}
    if si is None or si.empty:
        return out
    si = si[si["symbol"].isin(set(C.columns))].sort_values(["symbol", "settle"])
    si = si.assign(dtc=(si["short"] / si["adv"].where(si["adv"] > 0)).clip(0, 60),
                   avail=si["settle"] + pd.Timedelta(days=12))
    si["dtc_3m"] = si["dtc"] - si.groupby("symbol")["dtc"].shift(6)
    ti = C.index.searchsorted(si["avail"].to_numpy())
    ok = ti < T
    j = C.columns.get_indexer(si["symbol"].to_numpy())
    for k, col in (("si_dtc", "dtc"), ("si_chg", "chg"), ("si_dtc_3m", "dtc_3m")):
        out[k][ti[ok], j[ok]] = si[col].to_numpy(dtype="float32")[ok]
        out[k] = pd.DataFrame(out[k]).ffill(limit=25).to_numpy(dtype="float32")
    return out


def _peer_mean(X: np.ndarray, valid: np.ndarray, G: np.ndarray, own: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Mean of X over the other names in the same industry (self excluded)."""
    Xf = np.where(valid, X, 0.0).astype("float32")
    s, cnt = Xf @ G, valid.astype("float32") @ G
    S, Cn = s[:, own], cnt[:, own]
    vf = valid.astype("float32")
    return np.where((Cn - vf > 0.5) & known[None, :], (S - Xf) / np.maximum(Cn - vf, 1.0), np.nan).astype("float32")


def iter_features(P: dict, EV: dict, mask: np.ndarray, industry: pd.Series | None = None, SI: dict | None = None):
    """Yield (name, matrix). Stock-level price/volume features are ranked across the
    tradable universe each day (robust to regime); event and regime features stay raw."""
    C, O, H, L, V = (P[k] for k in ("close", "open", "high", "low", "volume"))
    R = C.pct_change(fill_method=None)
    cols = C.columns
    m = R[BENCH] if BENCH in cols else R.median(axis=1)
    dv = C * V
    rk = lambda df: _rank(df.to_numpy(dtype="float32") if isinstance(df, pd.DataFrame) else df, mask)

    for n in (1, 5, 10, 21, 63, 126):
        yield f"ret_{n}", rk(C / C.shift(n) - 1)
    yield "mom_231_21", rk(C.shift(21) / C.shift(231) - 1)
    yield "hi_231", rk(C / H.rolling(231, min_periods=120).max() - 1)
    yield "lo_231", rk(C / L.rolling(231, min_periods=120).min() - 1)
    yield "hi_63", rk(C / H.rolling(63, min_periods=40).max() - 1)
    vol21, vol63 = R.rolling(21, min_periods=15).std(), R.rolling(63, min_periods=40).std()
    yield "vol_21", rk(vol21)
    yield "vol_63", rk(vol63)
    yield "vol_ratio", rk(R.abs().rolling(5, min_periods=3).mean() / vol63)
    yield "max_21", rk(R.rolling(21, min_periods=15).max())
    yield "min_21", rk(R.rolling(21, min_periods=15).min())
    yield "skew_63", rk(R.rolling(63, min_periods=40).skew())
    yield "gap", rk(O / C.shift(1) - 1)
    yield "intraday", rk(C / O - 1)
    clv = ((C - L) / (H - L).where((H - L) > 0) - 0.5).fillna(0.0).where(C.notna())
    yield "clv", rk(clv)
    yield "clv_5", rk(clv.rolling(5, min_periods=3).mean())
    yield "range_21", rk(((H - L) / C).rolling(21, min_periods=15).mean())
    dv63 = dv.rolling(63, min_periods=40).mean()
    yield "dv_63", rk(np.log(dv.rolling(63, min_periods=40).median()))
    yield "dv_ratio_1", rk(dv / dv63.shift(1))
    yield "dv_ratio_5", rk(dv.rolling(5, min_periods=3).mean() / dv63.shift(5))
    yield "amihud_63", rk((R.abs() / dv.where(dv > 0)).rolling(63, min_periods=40).mean())
    # Market beta and what's left over.
    mm = m.rolling(126, min_periods=60)
    cov = R.mul(m, axis=0).rolling(126, min_periods=60).mean() - R.rolling(126, min_periods=60).mean().mul(mm.mean(), axis=0)
    beta = cov.div(mm.var(), axis=0)
    yield "beta_126", rk(beta)
    idio = (R.rolling(126, min_periods=60).var() - (beta ** 2).mul(mm.var(), axis=0)).clip(lower=0) ** 0.5
    yield "idio_126", rk(idio)
    # Risk-adjusted momentum and short-term move against own volatility.
    yield "ret_21_vol", rk((C / C.shift(21) - 1) / (vol63 * np.sqrt(21)))
    yield "ret_5_vol", rk((C / C.shift(5) - 1) / (vol63 * np.sqrt(5)))

    # Overnight vs intraday: who has been buying, the overnight crowd or the day session.
    on, intr = (O / C.shift(1) - 1).clip(-0.5, 0.5), (C / O - 1).clip(-0.5, 0.5)
    yield "overnight_21", rk(on.rolling(21, min_periods=15).sum())
    yield "overnight_63", rk(on.rolling(63, min_periods=40).sum())
    yield "intraday_21", rk(intr.rolling(21, min_periods=15).sum())
    yield "intraday_63", rk(intr.rolling(63, min_periods=40).sum())
    # Same month a year ago (return seasonality).
    yield "seasonal_12m", rk(C.shift(231) / C.shift(252) - 1)

    # Industry context: how the name's peers are doing, and the name relative to them.
    cn = C.to_numpy()
    r21, r126, r5 = (C / C.shift(21) - 1).to_numpy(), (C / C.shift(126) - 1).to_numpy(), (C / C.shift(5) - 1).to_numpy()
    if industry is not None:
        ind = industry.reindex(cols)
        known = ind.notna().to_numpy()
        codes, _ = pd.factorize(ind.fillna("?"))
        G = np.zeros((len(cols), codes.max() + 1), dtype="float32")
        G[np.arange(len(cols)), codes] = 1.0
        pm = lambda X: _peer_mean(X, mask & ~np.isnan(X), G, codes, known)
        p21, p126 = pm(r21), pm(r126)
        yield "ind_ret_21", _rank(p21, mask)
        yield "ind_ret_126", _rank(p126, mask)
        yield "rel_ind_21", _rank(r21 - p21, mask)
        yield "rel_ind_5", _rank(r5 - pm(r5), mask)
        yield "rel_ind_126", _rank(r126 - p126, mask)
        yield "ind_surp", pm(np.clip(EV["surp"], -100, 100))
        yield "ind_react", pm(np.clip(EV["react"], -0.5, 0.5))
        yield "ind_breadth", pm((C > C.rolling(50, min_periods=30).mean()).to_numpy().astype("float32"))
    else:
        for k in ("ind_ret_21", "ind_ret_126", "rel_ind_21", "rel_ind_5", "rel_ind_126", "ind_surp", "ind_react", "ind_breadth"):
            yield k, np.full(cn.shape, np.nan, dtype="float32")

    # Short interest (FINRA, as published): crowding on the short side and its direction.
    SI = SI or short_interest_panel(P, None)
    yield "si_dtc", _rank(SI["si_dtc"], mask)
    yield "si_chg", np.clip(SI["si_chg"], -100, 300)
    yield "si_dtc_3m", _rank(SI["si_dtc_3m"], mask)

    # Earnings memory (raw values: counts and percentages mean the same thing every year).
    for k in ("days_since", "days_to_next", "surp", "react", "react_vol", "react_clv", "prev_surp", "beats4", "n_est", "est_small", "post_drift",
              "sue", "eps_yoy", "eps_accel"):
        v = EV[k]
        if k in ("react", "post_drift"):
            v = np.clip(v, -0.6, 1.5)
        if k == "react_vol":
            v = np.clip(v, 0, 30)
        yield "e_" + k, v

    # Regime: the same number for every name on a day; lets the model condition on the tape.
    T, N = C.shape
    def bc(s):
        return np.repeat(np.asarray(s, dtype="float32")[:, None], N, axis=1)
    px = C[BENCH] if BENCH in cols else C.median(axis=1)
    yield "mkt_ret_21", bc(px / px.shift(21) - 1)
    yield "mkt_ret_63", bc(px / px.shift(63) - 1)
    yield "mkt_vol_21", bc(m.rolling(21).std())
    above = (C > C.rolling(50, min_periods=30).mean()).to_numpy() & mask
    yield "breadth_50", bc(above.sum(axis=1) / np.maximum(mask.sum(axis=1), 1))
    r21 = np.where(mask, (C / C.shift(21) - 1).to_numpy(), np.nan)
    with np.errstate(all="ignore"):
        yield "xs_disp_21", bc(np.nanstd(r21, axis=1))


def forward_returns(P: dict, horizons=(5, 21, 40, 126, 252)) -> tuple[dict, dict]:
    """Labels: (excess over SPY, absolute) return from the NEXT open, held h trading days.
    A name that stops trading inside the window exits at its last price (no survivorship)."""
    O, C = P["open"], P["close"]
    last_valid = C.notna()[::-1].idxmax()
    dead = pd.DataFrame(C.index.to_numpy()[:, None] > last_valid.to_numpy()[None, :], index=C.index, columns=C.columns)
    Of = O.where(~dead, C.ffill())
    entry = Of.shift(-1)
    bench = Of[BENCH]
    Y, A = {}, {}
    for h in horizons:
        r = Of.shift(-1 - h) / entry - 1
        A[h] = r.clip(-0.9, 3.0).to_numpy(dtype="float32")
        Y[h] = r.sub(bench.shift(-1 - h) / bench.shift(-1) - 1, axis=0).clip(-0.9, 3.0).to_numpy(dtype="float32")
    return Y, A


def regime(P: dict) -> pd.Series:
    """Market state each day: True when SPY closed above its 200-day average."""
    px = P["close"][BENCH]
    return (px > px.rolling(200, min_periods=150).mean())


def forward_excess(P: dict, horizons=(5, 21, 40, 126)) -> dict:
    """Label: return from the NEXT open, held h trading days, minus SPY over the same window.
    A name that stops trading inside the window exits at its last price (no survivorship)."""
    O, C = P["open"], P["close"]
    last_valid = C.notna()[::-1].idxmax()                 # last day each symbol really traded
    dead = pd.DataFrame(C.index.to_numpy()[:, None] > last_valid.to_numpy()[None, :], index=C.index, columns=C.columns)
    Of = O.where(~dead, C.ffill())                        # after the last trade, carry the last close
    entry = Of.shift(-1)
    bench = Of[BENCH] if BENCH in Of else None
    out = {}
    for h in horizons:
        r = Of.shift(-1 - h) / entry - 1
        if bench is not None:
            r = r.sub(bench.shift(-1 - h) / bench.shift(-1) - 1, axis=0)
        out[h] = r.clip(-0.9, 3.0).to_numpy(dtype="float32")
    return out
