"""Headlines from the Alpaca News API (Benzinga), cached locally.

Fetching years of headlines is slow, so results are saved to data/news/ and
only new days are fetched on later runs.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from edge.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

_client = NewsClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
CACHE = Path(__file__).resolve().parents[2] / "data" / "news"


def _fetch(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    # The SDK follows every page itself when no limit is given.
    result = _client.get_news(NewsRequest(symbols=symbol, start=start, end=end, include_content=False))
    rows = [{"symbol": symbol, "time": item.created_at, "headline": item.headline,
             "source": item.source, "id": item.id} for item in result.data.get("news", [])]
    return pd.DataFrame(rows, columns=["symbol", "time", "headline", "source", "id"])


def headlines(symbols: Iterable[str], days: int = 365) -> pd.DataFrame:
    """All headlines mentioning each symbol over the last `days`, one row per headline."""
    CACHE.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    frames = []
    for symbol in symbols:
        path = CACHE / f"{symbol}.parquet"
        cached = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        fetch_from = start
        if not cached.empty:
            fetch_from = max(start, cached["time"].max().to_pydatetime())
        fresh = _fetch(symbol, fetch_from, end)
        merged = pd.concat([cached, fresh]).drop_duplicates("id")
        merged.to_parquet(path, index=False)
        frames.append(merged[merged["time"] >= pd.Timestamp(start)])
    return pd.concat(frames, ignore_index=True)
