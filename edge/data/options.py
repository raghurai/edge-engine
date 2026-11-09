"""Options chains from Alpaca's free indicative feed.

Indicative = derived prices, slightly delayed, no Greeks. Enough to name a
contract and its rough cost; real-time OPRA (free after signing the agreement
in the Alpaca dashboard) replaces it with one line here.
"""
import re
from datetime import date, datetime

import pandas as pd
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest

from edge.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

FEED = "indicative"
_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
_OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def chain(underlying: str, spot: float) -> pd.DataFrame:
    """All listed contracts for one stock with bid/ask, expiry, strike, DTE and moneyness."""
    try:
        snaps = _client.get_option_chain(OptionChainRequest(underlying_symbol=underlying, feed=FEED))
    except Exception:
        return pd.DataFrame()
    rows, today = [], date.today()
    for sym, s in snaps.items():
        m = _OCC.match(sym)
        if not m:
            continue
        expiry = datetime.strptime(m.group(2), "%y%m%d").date()
        strike = int(m.group(4)) / 1000
        q = s.latest_quote
        bid = q.bid_price if q else None; ask = q.ask_price if q else None
        rows.append({
            "contract": sym, "underlying": underlying, "type": "call" if m.group(3) == "C" else "put",
            "expiry": expiry.isoformat(), "dte": (expiry - today).days, "strike": strike,
            "bid": bid, "ask": ask, "mid": (bid + ask) / 2 if bid is not None and ask is not None else None,
            "last": s.latest_trade.price if s.latest_trade else None,
            # how far the strike sits from spot, in %: + is above
            "otm_pct": (strike / spot - 1) * 100,
        })
    return pd.DataFrame(rows)


def pick(ch: pd.DataFrame, kind: str, dte_lo: int, dte_hi: int, otm_lo: float, otm_hi: float,
         max_ask: float | None = None) -> dict | None:
    """One contract: the given type, inside the DTE window, strike inside the moneyness
    window, with a real quote. Chooses the expiry closest to the window's middle, then the
    strike closest to the window's middle."""
    if ch.empty:
        return None
    c = ch[(ch["type"] == kind) & (ch["dte"].between(dte_lo, dte_hi)) & (ch["otm_pct"].between(otm_lo, otm_hi))
           & ch["ask"].gt(0) & ch["mid"].notna()]
    if max_ask is not None:
        c = c[c["ask"] <= max_ask]
    if c.empty:
        return None
    c = c.assign(_d=(c["dte"] - (dte_lo + dte_hi) / 2).abs(), _m=(c["otm_pct"] - (otm_lo + otm_hi) / 2).abs())
    r = c.sort_values(["_d", "_m"]).iloc[0]
    out = {}
    for k, v in r.drop(["_d", "_m"]).items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float):
            v = None if v != v else round(v, 2)   # NaN → null so the JSON stays valid
        out[k] = v
    return out
