"""FINRA's twice-monthly consolidated short interest (public files, no account).

Each file covers every listed symbol for one settlement date (mid-month and month-end)
and is published about eight business days later. recent() keeps the last few months
cached in data/finra/ and returns them long: symbol, settle, short, adv, chg.
"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

CACHE = Path(__file__).resolve().parents[2] / "data" / "finra"
URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{d:%Y%m%d}.csv"
WANT = ["symbolCode", "currentShortPositionQuantity", "averageDailyVolumeQuantity", "changePercent", "settlementDate"]


def _candidates(months: int) -> list[list[date]]:
    out, d = [], date.today().replace(day=1)
    for _ in range(months + 1):
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        for anchor in (d.replace(day=15), nxt - timedelta(days=1)):
            out.append([anchor - timedelta(days=k) for k in range(5)])
        d = (d - timedelta(days=1)).replace(day=1)
    return out


def parse(path) -> pd.DataFrame:
    # Quotes in issuer names are not escaped consistently, so read them as plain characters.
    d = pd.read_csv(path, sep="|", quoting=3, dtype=str, on_bad_lines="skip", encoding_errors="ignore")
    d.columns = [c.strip('"') for c in d.columns]
    if not set(WANT) <= set(d.columns):
        return pd.DataFrame(columns=["symbol", "short", "adv", "chg", "settle"])
    d = d[WANT].apply(lambda c: c.str.strip('"'))
    d.columns = ["symbol", "short", "adv", "chg", "settle"]
    d["settle"] = pd.to_datetime(d["settle"], errors="coerce")
    for c in ("short", "adv", "chg"):
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("float32")
    return d.dropna(subset=["symbol", "settle"])


def recent(months: int = 5) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    for options in _candidates(months):
        if any((CACHE / f"shrt{x:%Y%m%d}.csv").exists() for x in options):
            continue
        for x in options:
            if x > date.today():
                continue
            try:
                r = requests.get(URL.format(d=x), timeout=60)
            except Exception:
                continue
            if r.status_code == 200 and len(r.content) > 100_000:
                (CACHE / f"shrt{x:%Y%m%d}.csv").write_bytes(r.content)
                break
    files = sorted(CACHE.glob("shrt*.csv"))[-(months * 2 + 2):]
    parts = [parse(f) for f in files]
    return pd.concat(parts).drop_duplicates(["symbol", "settle"]) if parts else pd.DataFrame(columns=["symbol", "short", "adv", "chg", "settle"])
