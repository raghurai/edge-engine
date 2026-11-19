"""Research dataset for the brain: years of earnings events and daily bars.

    python scripts/brain_data.py fresh      # clear price chunks so history is re-adjusted (weekly retrain)
    python scripts/brain_data.py finra sectors   # short interest files, sector directory
    python scripts/brain_data.py calendar   # every earnings report since 2017 (Nasdaq calendar)
    python scripts/brain_data.py bars       # SIP, split/dividend-adjusted daily bars since 2016,
                                            # for every listed AND delisted US symbol (no survivorship)

Both are resumable: finished days / chunks are skipped. Output lives in data/research/.
"""
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research"
BARS = OUT / "bars"
EXCHANGES = ("NYSE", "NASDAQ", "ARCA", "AMEX", "BATS", "TXSE")
START_BARS = datetime(2016, 1, 1, tzinfo=timezone.utc)
START_CAL = date(2017, 1, 2)


def calendar():
    """Every earnings report since 2017. Days already cached with actuals are kept; the rest are
    fetched eight at a time (the feed answers in about three seconds a day)."""
    import json
    from concurrent.futures import ThreadPoolExecutor
    import requests
    from edge.data import nasdaq_cal
    nasdaq_cal.CACHE.mkdir(parents=True, exist_ok=True)
    days, d = [], START_CAL
    while d <= date.today():
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    def stale(d):
        f = nasdaq_cal.CACHE / f"{d.isoformat()}.json"
        if not f.exists():
            return True
        if d >= date.today() - timedelta(days=10):          # recent days still gain their actuals
            return True
        return False

    def get(d):
        for attempt in range(3):
            try:
                r = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()}, headers=nasdaq_cal.HEADERS, timeout=40)
                r.raise_for_status()
                rows = (r.json().get("data") or {}).get("rows") or []
                (nasdaq_cal.CACHE / f"{d.isoformat()}.json").write_text(json.dumps(rows))
                return True
            except Exception:
                time.sleep(3 * (attempt + 1))
        return False

    todo = [d for d in days if stale(d)]
    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        ok = list(ex.map(get, todo))
    print(f"calendar: fetched {sum(ok)}/{len(todo)} days in {time.time() - t0:.0f}s ({len(days) - len(todo)} cached)", flush=True)
    ev = nasdaq_cal.events(back_days=(date.today() - START_CAL).days, ahead_days=0, max_age_h=24 * 365)
    OUT.mkdir(parents=True, exist_ok=True)
    ev.to_parquet(OUT / "events.parquet")
    print("events", len(ev), "symbols", ev["symbol"].nunique(), "with surprise", int(ev["surprise"].notna().sum()), flush=True)
    if len(ev) < 100_000:
        raise SystemExit("calendar history looks incomplete; not training on it")


def finra():
    """Every FINRA short-interest file since 2018 (mid-month and month-end settlement dates)."""
    from concurrent.futures import ThreadPoolExecutor
    import requests
    out = OUT / "finra"
    out.mkdir(parents=True, exist_ok=True)
    cands, d = [], date(2017, 12, 1)
    while d <= date.today():
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        for anchor in (d.replace(day=15), nxt - timedelta(days=1)):
            cands.append([anchor - timedelta(days=k) for k in range(5)])
        d = nxt

    def get(options):
        for x in options:
            if x > date.today():
                continue
            f = out / f"shrt{x:%Y%m%d}.csv"
            if f.exists():
                return True
            try:
                r = requests.get(f"https://cdn.finra.org/equity/otcmarket/biweekly/shrt{x:%Y%m%d}.csv", timeout=60)
            except Exception:
                continue
            if r.status_code == 200 and len(r.content) > 100_000:
                f.write_bytes(r.content)
                return True
        return False
    with ThreadPoolExecutor(6) as ex:
        got = sum(ex.map(get, cands))
    print(f"finra: {got} settlement dates on disk", flush=True)


def sectors():
    """Sector and industry for every listed name (Nasdaq's directory, one request)."""
    import requests
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept": "application/json"}
    r = requests.get("https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true", headers=h, timeout=60)
    r.raise_for_status()
    rows = r.json()["data"]["rows"]
    if len(rows) < 3000:
        raise SystemExit("sector directory looks incomplete")
    d = pd.DataFrame(rows)[["symbol", "name", "sector", "industry", "country", "ipoyear", "marketCap"]]
    d["symbol"] = d["symbol"].str.strip()
    OUT.mkdir(parents=True, exist_ok=True)
    d.to_parquet(OUT / "sectors.parquet")
    print("sectors", len(d), flush=True)


def fresh():
    """Drop price chunks and derived tables so the next `bars` rebuilds history as it is adjusted today
    (a split or dividend re-adjusts every earlier bar, so old chunks cannot simply be extended)."""
    import shutil
    for sub in ("bars", "panel", "tables"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
    print("cleared bars, panel and tables", flush=True)


def universe() -> list[str]:
    from edge.data.wide import _trading
    syms = set()
    for status in ("active", "inactive"):
        for a in _trading.get("/assets", {"status": status, "asset_class": "us_equity"}):
            s = a["symbol"]
            if a.get("exchange") in EXCHANGES and s.isalpha() and len(s) <= 5:
                syms.add(s)
    return sorted(syms)


def bars(chunk: int = 100):
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from edge.data.wide import _data
    BARS.mkdir(parents=True, exist_ok=True)
    syms = universe()
    print("universe", len(syms), flush=True)
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    t0 = time.time()
    for i in range(0, len(syms), chunk):
        path = BARS / f"chunk_{i:05d}.parquet"
        if path.exists():
            continue
        part = syms[i:i + chunk]
        for attempt in range(4):
            try:
                df = _data.get_stock_bars(StockBarsRequest(symbol_or_symbols=part, timeframe=TimeFrame.Day, start=START_BARS, end=end,
                                                           feed=DataFeed.SIP, adjustment=Adjustment.ALL)).df
                break
            except Exception as e:
                print("retry", i, str(e)[:100], flush=True); time.sleep(10 * (attempt + 1)); df = None
        if df is None:
            continue
        if len(df):
            df = df.reset_index()
            df["date"] = df["timestamp"].dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
            df = df[["symbol", "date", "open", "high", "low", "close", "volume"]]
            for c in ("open", "high", "low", "close", "volume"):
                df[c] = df[c].astype("float32")
        else:
            df = pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
        df.to_parquet(path)
        print(f"bars {i + chunk}/{len(syms)} rows {len(df)} {time.time() - t0:.0f}s", flush=True)
    done = len(list(BARS.glob("chunk_*.parquet")))
    print(f"bars done: {done} chunks", flush=True)
    if done < (len(syms) + chunk - 1) // chunk:
        raise SystemExit("price history incomplete; not training on it")


if __name__ == "__main__":
    for step in sys.argv[1:]:
        {"calendar": calendar, "bars": bars, "finra": finra, "sectors": sectors, "fresh": fresh}[step]()
