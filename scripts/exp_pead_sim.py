"""Walk-forward portfolio simulation of the earnings-drift signal.

Rule (decided before looking at the future on each day):
  - EPS surprise in the top 20% of surprises seen over the trailing 12 months
  - day-0 reaction > +2%
  - buy at the first close after the announcement, hold 40 trading days,
    equal weight across open positions, 0.1% cost each way
Reports the portfolio's excess return over SPY, hit rate and drawdown.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from edge.data import wide

HOLD, COST = 40, 0.1
ev = pd.read_parquet(wide.CACHE / "earnings_300.parquet")
top = wide.most_liquid(1000)
bars = wide.daily_bars(top, years=4)
close = bars["close"].unstack(level="symbol")
close.index = close.index.tz_convert("UTC").normalize().tz_localize(None)
dates = close.index
spy = close["SPY"]
ev = ev[(ev["date"] > dates[0] + pd.Timedelta(days=5))].sort_values("date").reset_index(drop=True)

trades = []
for _, e in ev.iterrows():
    pos = dates.searchsorted(e["date"])
    if pos + 1 + HOLD >= len(dates) or e["symbol"] not in close:
        continue
    # Point-in-time threshold: only surprises announced before this one.
    past = ev[(ev["date"] < e["date"]) & (ev["date"] >= e["date"] - pd.Timedelta(days=365))]["surprise"]
    if len(past) < 100 or e["surprise"] < past.quantile(0.8):
        continue
    p = close[e["symbol"]]
    day0 = p.iloc[pos] / p.iloc[pos - 1] - 1 if pos > 0 else np.nan
    if not (day0 > 0.02):
        continue
    entry, exit_ = p.iloc[pos + 1], p.iloc[pos + 1 + HOLD]
    bench = spy.iloc[pos + 1 + HOLD] / spy.iloc[pos + 1] - 1
    ret = (exit_ / entry - 1) * 100 - 2 * COST
    trades.append({"symbol": e["symbol"], "entry_date": dates[pos + 1], "exit_date": dates[pos + 1 + HOLD],
                   "surprise": e["surprise"], "day0": day0 * 100, "ret": ret, "excess": ret - bench * 100})
t = pd.DataFrame(trades)

pd.set_option("display.float_format", "{:.2f}".format)
print(f"trades: {len(t)}   span {t['entry_date'].min():%Y-%m} to {t['exit_date'].max():%Y-%m}")
print(f"per trade: return {t['ret'].mean():+.2f}%  excess over SPY {t['excess'].mean():+.2f}%  "
      f"hit rate {(t['ret'] > 0).mean():.0%}  excess hit rate {(t['excess'] > 0).mean():.0%}")
print(f"winners avg {t.loc[t['ret'] > 0, 'ret'].mean():+.2f}%   losers avg {t.loc[t['ret'] <= 0, 'ret'].mean():+.2f}%")
se = t["excess"].std() / np.sqrt(len(t))
print(f"t-stat of excess: {t['excess'].mean() / se:+.2f}")
print("\nBy entry quarter (mean excess %, trades):")
print(t.groupby(t["entry_date"].dt.to_period("Q"))["excess"].agg(["mean", "size"]).round(2).to_string())

# Daily portfolio curve: equal weight across whatever is open each day.
daily = pd.Series(0.0, index=dates); count = pd.Series(0, index=dates)
r = close.pct_change()
for _, tr in t.iterrows():
    window = dates[(dates > tr["entry_date"]) & (dates <= tr["exit_date"])]
    daily[window] += r.loc[window, tr["symbol"]].fillna(0)
    count[window] += 1
port = (daily / count.replace(0, np.nan)).fillna(0)
curve = (1 + port).cumprod(); bench = (1 + spy.pct_change().fillna(0)).cumprod()
dd = (curve / curve.cummax() - 1).min()
invested = (count > 0).mean()
print(f"\nPortfolio (equal weight across open positions, in cash when none):")
print(f"  total {curve.iloc[-1] - 1:+.1%}   SPY same span {bench.iloc[-1] - 1:+.1%}   max drawdown {dd:.1%}   "
      f"days invested {invested:.0%}   avg open positions {count[count > 0].mean():.1f}")
t.to_parquet(wide.CACHE / "pead_trades.parquet")
