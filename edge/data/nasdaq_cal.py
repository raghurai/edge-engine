"""Whole-market earnings calendar from Nasdaq's public daily feed.

One request per day returns every US name reporting that day with report time,
consensus EPS, and (once reported) actual EPS and % surprise. Cached per day in
data/nasdaq_cal/; past days with actuals are kept, today and future days refresh.
"""
import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

CACHE = Path(__file__).resolve().parents[2] / "data" / "nasdaq_cal"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
           "Accept": "application/json", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
SESSION = {"time-pre-market": "before", "time-after-hours": "after"}


def _money(s):
    try:
        return float(str(s).replace("$", "").replace(",", "")) if s not in (None, "", "N/A") else None
    except ValueError:
        return None


def _day(d: date, max_age_h: float) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{d.isoformat()}.json"
    if p.exists():
        cached = json.loads(p.read_text())
        fresh = time.time() - p.stat().st_mtime < max_age_h * 3600
        has_actuals = any(r.get("surprise") not in (None, "") for r in cached)
        if fresh or (d < date.today() - timedelta(days=3) and has_actuals):
            return cached
    try:
        r = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()}, headers=HEADERS, timeout=20)
        r.raise_for_status()
        rows = (r.json().get("data") or {}).get("rows") or []
    except Exception:
        return json.loads(p.read_text()) if p.exists() else []
    p.write_text(json.dumps(rows))
    time.sleep(0.4)
    return rows


def events(back_days: int = 60, ahead_days: int = 28, max_age_h: float = 6) -> pd.DataFrame:
    """One row per (symbol, date): session, estimate, eps, surprise, name, market_cap."""
    today = date.today()
    out = []
    for i in range(-back_days, ahead_days + 1):
        d = today + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        for r in _day(d, max_age_h):
            sym = (r.get("symbol") or "").strip().upper()
            if not sym or not sym.replace(".", "").isalpha():
                continue
            out.append({
                "symbol": sym, "date": pd.Timestamp(d), "session": SESSION.get(r.get("time"), "during"),
                "estimate": _money(r.get("epsForecast")), "eps": _money(r.get("eps")),
                "surprise": _money(r.get("surprise")), "name": r.get("name"), "market_cap": _money(r.get("marketCap")),
                "n_estimates": _money(r.get("noOfEsts")),
            })
    df = pd.DataFrame(out)
    return df.drop_duplicates(["symbol", "date"]).sort_values(["date", "symbol"]).reset_index(drop=True) if len(df) else df
