"""Cboe's public delayed-quote feed for index options (SPX / SPXW).

No account, no key. 15-minute delayed, but it carries what Alpaca's free feed
does not for index products: spot, 30-day implied vol, and per contract the
implied vol, open interest, volume and greeks. Cached for a few minutes because
the SPX file is ~13 MB.
"""
import json
import re
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_{sym}.json"
CACHE = Path(__file__).resolve().parents[2] / "data" / "cboe"
_OCC = re.compile(r"^([A-Z]+?)(\d{6})([CP])(\d{8})$")
_HEADERS = {"User-Agent": "Mozilla/5.0 (raidical-stonkz research)"}


def _raw(sym: str, max_age_s: int = 240) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{sym}.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        return json.loads(path.read_text())
    r = requests.get(URL.format(sym=sym), headers=_HEADERS, timeout=60)
    r.raise_for_status()
    path.write_text(r.text)
    return r.json()


def index_chain(sym: str = "SPX") -> tuple[dict, pd.DataFrame]:
    """(header, chain). Header: spot, change_pct, iv30, open/high/low, prev_close, as_of.
    Chain: one row per contract with root (SPX = monthly AM-settled, SPXW = weekly/daily
    PM-settled), type, expiry, dte, strike, bid/ask/mid, iv, oi, volume, delta, gamma."""
    d = _raw(sym)["data"]
    header = {
        "symbol": sym, "spot": d["current_price"], "change": d["price_change"], "change_pct": d["price_change_percent"],
        "open": d["open"], "high": d["high"], "low": d["low"], "prev_close": d["prev_day_close"], "iv30": d["iv30"],
        "as_of": d.get("last_trade_time"),
    }
    today, rows = date.today(), []
    for o in d["options"]:
        m = _OCC.match(o["option"])
        if not m:
            continue
        expiry = datetime.strptime(m.group(2), "%y%m%d").date()
        bid, ask = o.get("bid") or 0.0, o.get("ask") or 0.0
        rows.append({
            "contract": o["option"], "root": m.group(1), "underlying": sym, "type": "call" if m.group(3) == "C" else "put",
            "expiry": expiry.isoformat(), "dte": (expiry - today).days, "strike": int(m.group(4)) / 1000,
            "bid": bid, "ask": ask, "mid": (bid + ask) / 2 if ask > 0 else None, "last": o.get("last_trade_price") or None,
            "iv": o.get("iv"), "oi": int(o.get("open_interest") or 0), "volume": int(o.get("volume") or 0),
            "delta": o.get("delta"), "gamma": o.get("gamma"), "theta": o.get("theta"),
        })
    ch = pd.DataFrame(rows)
    ch["otm_pct"] = (ch["strike"] / header["spot"] - 1) * 100
    return header, ch
