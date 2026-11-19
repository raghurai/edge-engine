"""The Raidical Stonkz engine core.

    Signal  = weighted sum of signal z-scores        (weights learned from history)
    P(up)   = 1 / (1 + e^-Signal)                    (logistic regression)
    Move    = P(up) * avg up move - (1 - P(up)) * avg down move
    Edge    = Move - spread - slippage

Everything is walk-forward: weights are fit only on data before the date
being predicted, so the backtest never sees the future (the time-machine rule).
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from edge.signals import price

SLIPPAGE_PCT = 0.05  # flat allowance on top of the quoted spread, in percent
NOT_SIGNALS = {"fwd_return", "up"}


def build_features(bars: pd.DataFrame, horizon: str, extra: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """One row per (date, symbol) with every signal as of that date.

    `extra` adds more (date x symbol) signal frames, e.g. {"news": sentiment_series}."""
    frames = {
        "momentum": price.momentum_series(bars, horizon),
        "volume_surge": price.volume_surge_series(bars),
        **(extra or {}),
    }
    data = pd.concat({k: v.stack() for k, v in frames.items()}, axis=1).dropna()
    data.index.names = ["date", "symbol"]
    return data


def build_dataset(bars: pd.DataFrame, horizon: str, extra: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Features plus what the stock actually did over the following horizon.
    Rows without a complete future are dropped."""
    days = price.HORIZON_DAYS[horizon]
    close = bars["close"].unstack(level="symbol").drop(columns=price.BENCHMARK)
    fwd_return = ((close.shift(-days) / close - 1) * 100).stack()
    fwd_return.index.names = ["date", "symbol"]
    data = build_features(bars, horizon, extra).join(fwd_return.rename("fwd_return"), how="inner")
    data["up"] = (data["fwd_return"] > 0).astype(int)
    return data


def predict_today(bars: pd.DataFrame, horizon: str, extra: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Fit on all complete history, then score the latest date. Returns one row per
    symbol with the signals, p_up and expected_move."""
    train = build_dataset(bars, horizon, extra)
    features = build_features(bars, horizon, extra)
    today = features.loc[features.index.get_level_values("date").max()].copy()
    model = fit_weights(train)
    today["p_up"] = model.predict_proba(today[signal_columns(train)])[:, 1]
    avg_up = train.loc[train["up"] == 1, "fwd_return"].mean()
    avg_down = -train.loc[train["up"] == 0, "fwd_return"].mean()
    today["expected_move"] = today["p_up"] * avg_up - (1 - today["p_up"]) * avg_down
    today.attrs["weights"] = dict(zip(signal_columns(train), model.coef_[0]))
    return today


def signal_columns(data: pd.DataFrame) -> list[str]:
    return [c for c in data.columns if c not in NOT_SIGNALS and c in data.columns]


def fit_weights(train: pd.DataFrame) -> LogisticRegression:
    model = LogisticRegression()
    model.fit(train[signal_columns(train)], train["up"])
    return model


def walk_forward(data: pd.DataFrame, horizon: str, min_train_days: int = 250,
                 refit_every: int = 21) -> pd.DataFrame:
    """Predict every date using weights fit only on dates before it.

    Refits every `refit_every` trading days; a gap of one horizon is left between
    the training set and the prediction date so no training row overlaps the future."""
    days = price.HORIZON_DAYS[horizon]
    dates = data.index.get_level_values("date").unique().sort_values()
    out = []
    for i in range(min_train_days, len(dates), refit_every):
        train = data.loc[dates[: i - days]]
        test = data.loc[dates[i : i + refit_every]]
        if train.empty or test.empty:
            continue
        model = fit_weights(train)
        test = test.copy()
        test["p_up"] = model.predict_proba(test[signal_columns(test)])[:, 1]
        # Typical size of past up/down moves, from the training window.
        test["avg_up"] = train.loc[train["up"] == 1, "fwd_return"].mean()
        test["avg_down"] = -train.loc[train["up"] == 0, "fwd_return"].mean()
        out.append(test)
    result = pd.concat(out)
    result.attrs["weights"] = dict(zip(signal_columns(train), model.coef_[0]))
    result["expected_move"] = result["p_up"] * result["avg_up"] - (1 - result["p_up"]) * result["avg_down"]
    return result


def edge(expected_move: pd.Series, spread_pct: pd.Series) -> pd.Series:
    """What's left of the expected move after the real cost of getting in and out."""
    return expected_move - spread_pct - SLIPPAGE_PCT


def calibration(result: pd.DataFrame, bins: int = 5) -> pd.DataFrame:
    """Did a stated 60% actually come true 60% of the time? Groups predictions by
    stated odds and compares with what happened."""
    bucket = pd.qcut(result["p_up"], bins, duplicates="drop")
    return result.groupby(bucket, observed=True).agg(
        predicted=("p_up", "mean"), actual=("up", "mean"),
        avg_return=("fwd_return", "mean"), n=("up", "size"),
    )


def summary(result: pd.DataFrame, top_frac: float = 0.2) -> dict:
    """Headline numbers: accuracy, and how the top picks did versus everything."""
    cutoff = result["p_up"].quantile(1 - top_frac)
    top = result[result["p_up"] >= cutoff]
    return {
        "predictions": len(result),
        "accuracy": ((result["p_up"] > 0.5) == result["up"]).mean(),
        "all_avg_return_pct": result["fwd_return"].mean(),
        "top_avg_return_pct": top["fwd_return"].mean(),
        "top_hit_rate": top["up"].mean(),
    }
