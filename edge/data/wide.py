"""Wide universe: every tradable US stock on Alpaca, then the most liquid N.

Daily bars for ~1000 names over years are cached in data/wide/ as parquet so
experiments can rerun instantly.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from edge.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

CACHE = Path(__file__).resolve().parents[2] / "data" / "wide"
_data = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
_trading = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def all_us_stocks() -> list[str]:
    """Every active, tradable US common stock / ETF symbol (no warrants, units, etc.)."""
    # Raw request: the SDK's exchange enum lags new venues (e.g. TXSE) and rejects the whole list.
    assets = _trading.get(f"/assets", {"status": "active", "asset_class": "us_equity"})
    ok = [a["symbol"] for a in assets if a.get("tradable") and a.get("exchange") in ("NYSE", "NASDAQ", "ARCA", "AMEX", "BATS", "TXSE")
          and a["symbol"].isalpha() and len(a["symbol"]) <= 5]
    return sorted(set(ok))


def most_liquid(n: int = 1000, min_price: float = 2.0) -> list[str]:
    """Top-n symbols by yesterday's dollar volume. Cached per day."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"liquid_{n}_{datetime.now():%Y%m%d}.txt"
    if path.exists():
        return path.read_text().split()
    symbols = all_us_stocks()
    rows = []
    for i in range(0, len(symbols), 500):
        chunk = symbols[i:i + 500]
        try:
            snaps = _data.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=chunk, feed=DataFeed.IEX))
        except Exception as e:
            print("snapshot chunk failed:", str(e)[:80]); continue
        for sym, s in snaps.items():
            bar = s.previous_daily_bar or s.daily_bar
            if bar and bar.close >= min_price:
                rows.append((sym, bar.close * bar.volume))
    top = [s for s, _ in sorted(rows, key=lambda r: -r[1])[:n]]
    path.write_text(" ".join(top))
    return top


def daily_bars(symbols: list[str], years: int = 4) -> pd.DataFrame:
    """Daily bars (symbol, timestamp) index. Fetched in chunks, cached per symbol set + horizon."""
    CACHE.mkdir(parents=True, exist_ok=True)
    # Key by size bucket, not exact membership: the tradable list drifts by a few names a day
    # and must not trigger a full re-download.
    key = f"bars_{years}y_{len(symbols) // 250 * 250}.parquet"
    path = CACHE / key
    end = datetime.now(timezone.utc)

    def fetch(start: datetime) -> pd.DataFrame:
        frames = []
        for i in range(0, len(symbols), 200):
            chunk = symbols[i:i + 200]
            try:
                df = _data.get_stock_bars(StockBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
                                                           start=start, end=end, feed=DataFeed.IEX)).df
                if len(df):
                    frames.append(df)
            except Exception as e:
                print("bars chunk failed:", str(e)[:80])
        return pd.concat(frames) if frames else pd.DataFrame()

    if path.exists():
        cached = pd.read_parquet(path)
        last = cached.index.get_level_values("timestamp").max()
        # Top up: anything newer than the cache, plus the last cached day in case it was a
        # partial intraday bar when it was saved. Stale by more than an hour = refresh.
        if (end - last.to_pydatetime()).total_seconds() > 3600:
            fresh = fetch(last.to_pydatetime() - timedelta(days=1))
            if len(fresh):
                cached = pd.concat([cached, fresh])
                cached = cached[~cached.index.duplicated(keep="last")].sort_index()
                cached.to_parquet(path)
        return cached

    out = fetch(end - timedelta(days=365 * years))
    out.to_parquet(path)
    return out


def adjusted_bars(symbols: list[str], days: int = 430) -> pd.DataFrame:
    """Split/dividend-adjusted consolidated (SIP) daily bars through the last COMPLETED session,
    long format (symbol, date, open, high, low, close, volume). This is what the brain is
    trained on, so it is what it must be scored on. Cached and topped up daily; a symbol whose
    history was re-adjusted (a split) is refetched whole."""
    from alpaca.data.enums import Adjustment
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "adjusted_bars.parquet"
    now = datetime.now(timezone.utc)
    end = now - timedelta(minutes=20)                       # the free plan serves SIP data older than 15 minutes
    now_et = pd.Timestamp(now).tz_convert("America/New_York")
    session_done = now_et.hour * 60 + now_et.minute >= 16 * 60 + 20
    last_complete = (now_et.normalize() if session_done else now_et.normalize() - pd.Timedelta(days=1)).tz_localize(None)

    def fetch(syms, start):
        frames = []
        for i in range(0, len(syms), 200):
            try:
                df = _data.get_stock_bars(StockBarsRequest(symbol_or_symbols=syms[i:i + 200], timeframe=TimeFrame.Day, start=start, end=end,
                                                           feed=DataFeed.SIP, adjustment=Adjustment.ALL)).df
            except Exception as e:
                print("adjusted bars chunk failed:", str(e)[:80]); continue
            if len(df):
                df = df.reset_index()
                df["date"] = df["timestamp"].dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
                frames.append(df[["symbol", "date", "open", "high", "low", "close", "volume"]])
        out = pd.concat(frames) if frames else pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
        return out[out["date"] <= last_complete]

    start_full = now - timedelta(days=days)
    if not path.exists():
        out = fetch(symbols, start_full)
        out.to_parquet(path)
        return out
    cached = pd.read_parquet(path)
    last = cached["date"].max()
    missing = sorted(set(symbols) - set(cached["symbol"].unique()))
    if last >= last_complete and not missing:
        return cached
    fresh = fetch(symbols, (last - pd.Timedelta(days=5)).to_pydatetime().replace(tzinfo=timezone.utc)) if last < last_complete else cached.iloc[:0]
    # A split re-adjusts all history: where the overlap disagrees, refetch that symbol in full.
    both = cached.merge(fresh, on=["symbol", "date"], suffixes=("", "_new"))
    moved = both.loc[(both["close_new"] / both["close"] - 1).abs() > 0.005, "symbol"].unique().tolist()
    redo = sorted(set(moved) | set(missing))
    full = fetch(redo, start_full) if redo else cached.iloc[:0]
    keep = cached[~cached["symbol"].isin(redo)]
    out = pd.concat([keep, fresh[~fresh["symbol"].isin(redo)], full]).drop_duplicates(["symbol", "date"], keep="last")
    out = out[out["date"] >= pd.Timestamp(start_full.date())].sort_values(["symbol", "date"]).reset_index(drop=True)
    out.to_parquet(path)
    return out
