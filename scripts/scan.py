"""First scan: rank a watchlist by momentum + volume surge for one horizon.

Usage: python scripts/scan.py [weekly|monthly|6month]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from edge.data import alpaca
from edge.signals import price

horizon = sys.argv[1] if len(sys.argv) > 1 else "weekly"

# Starter watchlist; the full-market scan comes once the engine is proven.
watchlist = ["NVDA", "TSLA", "AAPL", "AMD", "PLTR", "META", "AMZN", "MSFT",
             "SOXL", "TQQQ", "COIN", "MSTR", "GME", "AMC", "SMCI", "INTC"]

bars = alpaca.history(watchlist + [price.BENCHMARK], days=price.HORIZON_DAYS[horizon] * 6 + 60)

table = pd.concat([
    price.momentum(bars, horizon),
    price.volume_surge(bars),
    price.volume_ratio(bars),
], axis=1)
# Equal weights for now; the engine learns real weights from backtests later.
table["signal"] = table["momentum"] + table["volume_surge"]

pd.set_option("display.float_format", "{:.2f}".format)
print(f"Horizon: {horizon}  (momentum vs SPY, volume vs 20-day normal)\n")
print(table.sort_values("signal", ascending=False))
