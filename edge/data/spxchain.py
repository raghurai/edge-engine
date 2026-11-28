"""SPX / SPXW index option chain from the keys we already have.

Quotes: Alpaca's indicative option feed (delayed). Open interest: Alpaca's contract
list (updated once a day). Index level and VIX: Yahoo. Implied vol, delta and gamma
are not supplied, so they are solved here from the mid price (Black-Scholes), using
the underlying level each expiry's own put-call parity implies.

This replaced Cboe's public file, which started refusing our requests (403). The
result is cached for several minutes so the refresh loop stays gentle on the API.
"""
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from scipy.stats import norm

from edge.brain.contracts import RATE, delta, implied_vol
from edge.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

CACHE = Path(__file__).resolve().parents[2] / "data" / "spx"
MAX_AGE_S = 600
NEAR_DAYS = 10          # daily and weekly SPXW expiries
FAR_DAYS = 430          # monthly SPX expiries out to the one-year LEAP
_OCC = re.compile(r"^([A-Z]+?)(\d{6})([CP])(\d{8})$")


def _quotes() -> tuple[pd.DataFrame, str]:
    client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    today = date.today()
    snaps = {}
    for under, days in (("SPXW", NEAR_DAYS), ("SPX", FAR_DAYS)):
        snaps.update(client.get_option_chain(OptionChainRequest(
            underlying_symbol=under, feed="indicative", expiration_date_gte=today, expiration_date_lte=today + timedelta(days=days))))
    rows, newest = [], None
    for sym, s in snaps.items():
        m = _OCC.match(sym)
        if not m:
            continue
        expiry = datetime.strptime(m.group(2), "%y%m%d").date()
        q = s.latest_quote
        bid, ask = (q.bid_price or 0.0, q.ask_price or 0.0) if q else (0.0, 0.0)
        if q and q.timestamp and (newest is None or q.timestamp > newest):
            newest = q.timestamp
        rows.append({"contract": sym, "root": m.group(1), "underlying": "SPX", "type": "call" if m.group(3) == "C" else "put",
                     "expiry": expiry.isoformat(), "dte": (expiry - today).days, "strike": int(m.group(4)) / 1000,
                     "bid": bid, "ask": ask, "mid": (bid + ask) / 2 if ask > 0 else np.nan,
                     "last": s.latest_trade.price if s.latest_trade else None})
    as_of = pd.Timestamp(newest).tz_convert("America/New_York").strftime("%Y-%m-%dT%H:%M:%S") if newest else None
    return pd.DataFrame(rows), as_of


def _open_interest() -> dict:
    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    today = date.today()
    out, token = {}, None
    for _ in range(12):
        r = client.get_option_contracts(GetOptionContractsRequest(
            underlying_symbols=["SPX"], expiration_date_gte=today, expiration_date_lte=today + timedelta(days=FAR_DAYS), limit=10000, page_token=token))
        for c in r.option_contracts or []:
            out[c.symbol] = int(c.open_interest or 0)
        token = r.next_page_token
        if not token:
            break
    return out


def _header() -> dict:
    h = yf.Ticker("^GSPC").history(period="7d").dropna(subset=["Close"])
    vix = yf.Ticker("^VIX").history(period="7d")["Close"].dropna()
    last, prev = h.iloc[-1], float(h["Close"].iloc[-2])
    spot = float(last["Close"])
    return {"symbol": "SPX", "spot": spot, "change": spot - prev, "change_pct": (spot / prev - 1) * 100, "open": float(last["Open"]),
            "high": float(last["High"]), "low": float(last["Low"]), "prev_close": prev, "iv30": float(vix.iloc[-1])}


def _greeks(ch: pd.DataFrame, spot: float) -> pd.DataFrame:
    ch["iv"], ch["delta"], ch["gamma"] = np.nan, np.nan, 0.0
    for expiry, e in ch[ch["mid"].notna()].groupby("expiry"):
        T = max(float(e["dte"].iloc[0]), 0.3) / 365.0
        pairs = e.pivot_table(index="strike", columns="type", values="mid").dropna()
        under = spot
        if len(pairs) and {"call", "put"} <= set(pairs.columns):
            k = (pairs["call"] - pairs["put"]).abs().idxmin()
            under = float(k * np.exp(-RATE * T) + pairs.loc[k, "call"] - pairs.loc[k, "put"])
        K, kind = e["strike"].to_numpy(), e["type"].to_numpy()
        iv = implied_vol(e["mid"].to_numpy(), under, K, np.full(len(e), T), kind)
        ok = ~np.isnan(iv)
        v = np.where(ok, iv, 1.0)
        d1 = (np.log(under / K) + (RATE + 0.5 * v ** 2) * T) / (v * np.sqrt(T))
        ch.loc[e.index, "iv"] = iv
        ch.loc[e.index, "delta"] = np.where(ok, delta(under, K, T, v, kind), np.nan)
        ch.loc[e.index, "gamma"] = np.where(ok, norm.pdf(d1) / (under * v * np.sqrt(T)), 0.0)
    return ch


def index_chain() -> tuple[dict, pd.DataFrame]:
    """(header, chain). One row per (expiry, strike, type): where the monthly SPX and the
    weekly SPXW share one, their open interest is added and the SPX quote is kept."""
    CACHE.mkdir(parents=True, exist_ok=True)
    table, meta = CACHE / "chain.parquet", CACHE / "header.json"
    if table.exists() and meta.exists() and time.time() - table.stat().st_mtime < MAX_AGE_S:
        return json.loads(meta.read_text()), pd.read_parquet(table)
    ch, as_of = _quotes()
    if ch.empty:
        raise RuntimeError("no SPX option quotes returned")
    header = {**_header(), "as_of": as_of}
    oi = _open_interest()
    ch["oi"] = ch["contract"].map(oi).fillna(0).astype(int)
    key = ["expiry", "strike", "type"]
    ch["oi"] = ch.groupby(key)["oi"].transform("sum")
    # Prefer a live quote, then the monthly SPX root.
    ch = ch.assign(_dead=ch["mid"].isna()).sort_values(["_dead", "root"]).drop_duplicates(key).drop(columns="_dead").reset_index(drop=True)
    ch["volume"], ch["theta"] = 0, np.nan
    ch = _greeks(ch, header["spot"])
    ch["otm_pct"] = (ch["strike"] / header["spot"] - 1) * 100
    ch.to_parquet(table)
    meta.write_text(json.dumps(header))
    return header, ch
