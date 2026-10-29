"""Price-based signals: momentum (M) and volume surge (V).

Each signal is a z-score, roughly -3 to +3: how unusual the value is against
the stock's own recent history. That keeps every signal on the same scale so
the engine can weight and add them.

The *_series functions return a (date x symbol) frame for backtests; the
plain functions return just today's row for scans.
"""
import numpy as np
import pandas as pd

# Lookback in trading days per horizon, and the benchmark used for relative momentum.
HORIZON_DAYS = {"weekly": 5, "monthly": 21, "6month": 126, "long": 252}
BENCHMARK = "SPY"


def _zscore(series: pd.Series, window: int) -> pd.Series:
    rolling = series.rolling(window, min_periods=window // 2)
    return (series - rolling.mean()) / rolling.std()


def momentum_series(bars: pd.DataFrame, horizon: str) -> pd.DataFrame:
    """Momentum z-score: the stock's return over the horizon minus SPY's,
    scaled by how much that excess return usually varies.

    `bars` is the multi-index frame from alpaca.history() and must include SPY.
    """
    days = HORIZON_DAYS[horizon]
    close = bars["close"].unstack(level="symbol")
    returns = close.pct_change(days)
    excess = returns.sub(returns[BENCHMARK], axis=0)
    return excess.apply(lambda col: _zscore(col, window=days * 4)).drop(columns=BENCHMARK)


def volume_surge_series(bars: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """Volume z-score: each day's volume against the previous `lookback` days."""
    volume = bars["volume"].unstack(level="symbol")
    z = volume.apply(lambda col: _zscore(np.log1p(col), window=lookback))
    return z.drop(columns=BENCHMARK, errors="ignore")


def momentum(bars: pd.DataFrame, horizon: str) -> pd.Series:
    return momentum_series(bars, horizon).iloc[-1].rename("momentum")


def volume_surge(bars: pd.DataFrame, lookback: int = 20) -> pd.Series:
    return volume_surge_series(bars, lookback).iloc[-1].rename("volume_surge")


def volume_ratio(bars: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Plain 'x times normal' volume, for display next to the z-score."""
    volume = bars["volume"].unstack(level="symbol")
    ratio = volume.iloc[-1] / volume.iloc[-lookback - 1:-1].mean()
    return ratio.drop(BENCHMARK, errors="ignore").rename("volume_ratio")
