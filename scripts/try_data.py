"""Smoke test for the data layer: history, quotes, then a short live stream."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edge.data import alpaca

symbols = ["SPY", "NVDA", "TSLA"]

bars = alpaca.history(symbols, days=30)
print(f"History: {len(bars)} daily bars for {symbols}")
print(bars.groupby(level="symbol").tail(1)[["close", "volume"]])

print("\nLatest quotes:")
print(alpaca.latest_quotes(symbols))

if "--stream" in sys.argv:
    print("\nStreaming live trades (Ctrl+C to stop)...")
    alpaca.stream_trades(symbols, lambda t: print(f"{t.symbol} {t.price} x{t.size}"))
