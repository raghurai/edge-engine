"""Walk-forward backtest of the engine on the watchlist.

Usage: python scripts/backtest.py [weekly|monthly|6month] [years] [--news]

--news adds the AI headline sentiment signal (slow the first time: it fetches
and scores every headline, then caches them).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from edge.data import alpaca
from edge.engine import core
from edge.signals import price

args = [a for a in sys.argv[1:] if not a.startswith("--")]
horizon = args[0] if args else "weekly"
years = int(args[1]) if len(args) > 1 else 4
use_news = "--news" in sys.argv

watchlist = ["NVDA", "TSLA", "AAPL", "AMD", "PLTR", "META", "AMZN", "MSFT",
             "SOXL", "TQQQ", "COIN", "MSTR", "GME", "AMC", "SMCI", "INTC"]

bars = alpaca.history(watchlist + [price.BENCHMARK], days=365 * years)
extra = {}
if use_news:
    from edge.data import news as news_data
    from edge.signals import news as news_signal
    headlines = news_data.headlines(watchlist, days=365 * years)
    print(f"Headlines: {len(headlines)}")
    scored = news_signal.score_headlines(headlines)
    dates = bars.index.get_level_values("timestamp").unique().sort_values().tz_localize(None).normalize()
    extra["news"] = news_signal.sentiment_series(scored, dates)
    extra["news"].index = bars["close"].unstack(level="symbol").index

data = core.build_dataset(bars, horizon, extra)
result = core.walk_forward(data, horizon)

pd.set_option("display.float_format", "{:.3f}".format)
print(f"Horizon: {horizon}   rows: {len(data)}   predicted: {len(result)}")
print("Learned weights:", {k: round(v, 3) for k, v in result.attrs["weights"].items()}, "\n")
print("Calibration (predicted odds vs what happened):")
print(core.calibration(result), "\n")
for k, v in core.summary(result).items():
    print(f"{k:>20}: {v:.3f}" if isinstance(v, float) else f"{k:>20}: {v}")

latest = result.loc[result.index.get_level_values("date").max()]
quotes = alpaca.latest_quotes(latest.index)
latest = latest.join(quotes["spread_pct"])
latest["edge"] = core.edge(latest["expected_move"], latest["spread_pct"])
print("\nLatest picks (expected move and edge in %):")
print(latest[["p_up", "expected_move", "spread_pct", "edge"]].sort_values("edge", ascending=False))
