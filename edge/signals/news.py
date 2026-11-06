"""News sentiment signal (N): an AI model reads each headline.

Uses FinBERT, a free finance-tuned model that runs locally. Each headline gets
a score from -1 (bad) to +1 (good). Scores are summed per stock per day, with
bad news weighted more and fading slower than good news (research shows
markets price good news fast and bad news slowly).
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from edge.signals.price import _zscore

MODEL = "ProsusAI/finbert"
HALF_LIFE_DAYS = 2    # how fast a headline's effect fades

# What the data showed (scripts/news_diag.py, 16 large names, 2 years): headline
# sentiment has ~zero correlation with next-day direction, and the strongest
# effect is contrarian: very negative news days are followed by above-average
# 5-21 day returns. So the signal is the raw recent sentiment, not a z-score,
# and the engine learns the sign itself.
CACHE = Path(__file__).resolve().parents[2] / "data" / "news_scores.parquet"


@lru_cache(maxsize=1)
def _pipeline():
    from transformers import pipeline
    return pipeline("text-classification", model=MODEL, top_k=None, truncation=True)


def score_headlines(headlines: pd.DataFrame, batch_size: int = 64) -> pd.DataFrame:
    """Adds a `score` column (-1..+1) to the headlines frame. Scores are cached by id."""
    cached = pd.read_parquet(CACHE) if CACHE.exists() else pd.DataFrame(columns=["id", "score"])
    todo = headlines[~headlines["id"].isin(cached["id"])].drop_duplicates("id")
    if not todo.empty:
        results = _pipeline()(todo["headline"].tolist(), batch_size=batch_size)
        scores = []
        for labels in results:
            p = {d["label"]: d["score"] for d in labels}
            scores.append(p.get("positive", 0) - p.get("negative", 0))
        cached = pd.concat([cached, pd.DataFrame({"id": todo["id"].values, "score": scores})])
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        cached.to_parquet(CACHE, index=False)
    return headlines.merge(cached, on="id", how="left")


def sentiment_series(scored: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """(date x symbol) recent mean sentiment (-1..+1, 0 when no news), aligned to
    the trading dates in `dates` and decayed over a couple of days.

    Headlines after the close count toward the next trading day, so the signal
    never uses news the market hadn't seen yet."""
    s = scored.dropna(subset=["score"]).copy()
    # Shift to the next trading date on or after the headline (16:00 ET cutoff ~ 20:00 UTC).
    day = (s["time"].dt.tz_convert("UTC") - pd.Timedelta(hours=20)).dt.normalize().dt.tz_localize(None) + pd.Timedelta(days=1)
    pos = dates.searchsorted(day)
    s = s[pos < len(dates)]
    s["date"] = dates[pos[pos < len(dates)]]
    daily = s.groupby(["date", "symbol"])["score"].mean().unstack().reindex(dates)
    # Days with no news carry a fading echo of the last news day, then settle at 0.
    return daily.fillna(0).ewm(halflife=HALF_LIFE_DAYS).mean()
