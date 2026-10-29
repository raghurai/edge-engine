"""Alpaca data source: history bars, latest quotes, and a live trade stream.

This is the only module that talks to Alpaca. The engine calls these
functions, so swapping to another provider later means replacing this file.
"""
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from edge.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

# Free plan: IEX only. Switch to DataFeed.SIP after upgrading for full-market data.
FEED = DataFeed.IEX

TIMEFRAMES = {
    "1min": TimeFrame(1, TimeFrameUnit.Minute),
    "5min": TimeFrame(5, TimeFrameUnit.Minute),
    "1hour": TimeFrame(1, TimeFrameUnit.Hour),
    "1day": TimeFrame(1, TimeFrameUnit.Day),
}


def history(symbols: str | Iterable[str], days: int = 365, timeframe: str = "1day") -> pd.DataFrame:
    """Price bars for one or more symbols, indexed by (symbol, timestamp).

    Columns: open, high, low, close, volume, trade_count, vwap.
    """
    end = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=[symbols] if isinstance(symbols, str) else list(symbols),
        timeframe=TIMEFRAMES[timeframe],
        start=end - timedelta(days=days),
        end=end,
        feed=FEED,
    )
    return _client.get_stock_bars(request).df


def latest_quotes(symbols: Iterable[str]) -> pd.DataFrame:
    """Current bid/ask for a batch of symbols. Spread is what the engine subtracts as cost."""
    quotes = _client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=list(symbols), feed=FEED))
    rows = []
    for symbol, q in quotes.items():
        mid = (q.bid_price + q.ask_price) / 2 if q.bid_price and q.ask_price else None
        rows.append({
            "symbol": symbol,
            "bid": q.bid_price,
            "ask": q.ask_price,
            "spread_pct": (q.ask_price - q.bid_price) / mid * 100 if mid else None,
            "timestamp": q.timestamp,
        })
    return pd.DataFrame(rows).set_index("symbol")


def session_spread(symbols: Iterable[str], days_back: int = 3) -> pd.Series:
    """Median bid/ask spread (%) during the last regular session's final half hour.

    After-hours quotes can show 10%+ spreads that nobody trades at, so the engine
    costs trades at what the spread looks like when the market is actually open."""
    from alpaca.data.requests import StockQuotesRequest

    out = {}
    now = datetime.now(timezone.utc)
    for symbol in symbols:
        spread = None
        for d in range(days_back + 1):
            day = (now - timedelta(days=d)).date()
            start = datetime(day.year, day.month, day.day, 19, 30, tzinfo=timezone.utc)  # 15:30 ET
            end = start + timedelta(minutes=30)
            try:
                quotes = _client.get_stock_quotes(StockQuotesRequest(
                    symbol_or_symbols=symbol, start=start, end=end, limit=300, feed=FEED)).df
            except Exception:
                continue
            if quotes.empty:
                continue
            mid = (quotes["bid_price"] + quotes["ask_price"]) / 2
            pct = ((quotes["ask_price"] - quotes["bid_price"]) / mid * 100)
            pct = pct[(quotes["bid_price"] > 0) & (quotes["ask_price"] > 0)]
            if not pct.empty:
                spread = float(pct.median())
                break
        out[symbol] = spread
    return pd.Series(out, name="spread_pct")


def stream_trades(symbols: Iterable[str], on_trade: Callable) -> None:
    """Blocks and calls on_trade(trade) for every live trade on the given symbols.

    Free plan: IEX exchange only. Trades only arrive during market hours.
    """
    stream = StockDataStream(ALPACA_API_KEY, ALPACA_SECRET_KEY, feed=FEED)

    async def handler(trade):
        on_trade(trade)

    stream.subscribe_trades(handler, *symbols)
    stream.run()
