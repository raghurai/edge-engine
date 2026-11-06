"""Self-improving intraday engine.

Every minute it predicts whether each stock will be higher 15 minutes from now.
When those 15 minutes pass it checks the answer, scores itself, and nudges its
weights (online logistic regression). Nothing is ever refit on the future: a
prediction is only learned from once its outcome is actually known.
"""
import json
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDClassifier

AHEAD_MIN = 15                 # how far ahead each prediction looks
FEATURES = ["mom5", "mom15", "mom30", "vol_surge", "range_pos", "vwap_dist"]
ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "data" / "online"


def features_at(bars: pd.DataFrame) -> pd.Series | None:
    """Signals from one symbol's 1-minute bars (oldest first). Needs ~60 bars."""
    if len(bars) < 60:
        return None
    c, v = bars["close"], bars["volume"]
    ret1 = c.pct_change()
    scale = ret1.tail(60).std() or 1e-6
    mom = lambda n: (c.iloc[-1] / c.iloc[-1 - n] - 1) / (scale * np.sqrt(n))
    win = bars.tail(30)
    rng = win["high"].max() - win["low"].min()
    vwap = (bars["vwap"].iloc[-1] if "vwap" in bars and pd.notna(bars["vwap"].iloc[-1])
            else (c.tail(30) * v.tail(30)).sum() / max(v.tail(30).sum(), 1))
    logv = np.log1p(v)
    return pd.Series({
        "mom5": mom(5), "mom15": mom(15), "mom30": mom(30),
        "vol_surge": (logv.iloc[-1] - logv.tail(30).mean()) / (logv.tail(30).std() or 1),
        "range_pos": (c.iloc[-1] - win["low"].min()) / rng if rng > 0 else 0.5,
        "vwap_dist": (c.iloc[-1] / vwap - 1) * 100,
    }).clip(-4, 4)


@dataclass
class Learner:
    model: SGDClassifier = field(default_factory=lambda: SGDClassifier(
        loss="log_loss", alpha=1e-3, learning_rate="constant", eta0=0.003, random_state=0))
    pending: list = field(default_factory=list)   # predictions waiting for their outcome
    resolved: list = field(default_factory=list)  # scored predictions, newest last
    history: list = field(default_factory=list)   # (time, rolling accuracy) every N resolutions
    seen: set = field(default_factory=set)        # (symbol, bar time) already predicted
    n_updates: int = 0

    # ---- predicting
    def predict(self, symbol: str, t: pd.Timestamp, price: float, feats: pd.Series) -> dict | None:
        key = (symbol, t.isoformat())
        if key in self.seen:
            return None
        self.seen.add(key)
        x = feats[FEATURES].values.reshape(1, -1)
        p = float(self.model.predict_proba(x)[0, 1]) if self.n_updates else 0.5
        pred = {"symbol": symbol, "t": t.isoformat(), "target_t": (t + pd.Timedelta(minutes=AHEAD_MIN)).isoformat(),
                "price": price, "p_up": p, "x": [float(a) for a in x[0]]}
        self.pending.append(pred)
        return pred

    # ---- learning
    def resolve(self, closes: dict[str, pd.Series]) -> int:
        """Score every pending prediction whose target time has a bar now, and learn from it."""
        done, still = [], []
        for pr in self.pending:
            series = closes.get(pr["symbol"])
            if series is None:
                still.append(pr); continue
            target = pd.Timestamp(pr["target_t"])
            after = series[series.index >= target]
            if after.empty:
                still.append(pr); continue
            pr["outcome_price"] = float(after.iloc[0])
            pr["up"] = int(pr["outcome_price"] > pr["price"])
            pr["ret_pct"] = (pr["outcome_price"] / pr["price"] - 1) * 100
            pr["correct"] = int((pr["p_up"] > 0.5) == bool(pr["up"]))
            done.append(pr)
        self.pending = still
        if done:
            X = np.array([d["x"] for d in done]); y = np.array([d["up"] for d in done])
            self.model.partial_fit(X, y, classes=[0, 1])
            self.n_updates += len(done)
            self.resolved.extend(done)
            self.history.append({"t": done[-1]["target_t"], "acc": self.accuracy(500), "n": len(self.resolved)})
        return len(done)

    # ---- reporting
    def accuracy(self, last: int | None = None) -> float | None:
        rows = self.resolved[-last:] if last else self.resolved
        return float(np.mean([r["correct"] for r in rows])) if rows else None

    def weights(self) -> dict:
        if not self.n_updates:
            return {f: 0.0 for f in FEATURES}
        return {f: round(float(w), 3) for f, w in zip(FEATURES, self.model.coef_[0])}

    def summary(self, suggestions: list[dict]) -> dict:
        conf = [r for r in self.resolved[-300:] if abs(r["p_up"] - 0.5) >= 0.05]
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "ahead_min": AHEAD_MIN,
            "n_predictions": len(self.resolved) + len(self.pending),
            "n_resolved": len(self.resolved),
            "accuracy_all": self.accuracy(),
            "accuracy_recent": self.accuracy(500),
            "accuracy_confident": float(np.mean([r["correct"] for r in conf])) if conf else None,
            "n_confident": len(conf),
            "weights": self.weights(),
            "history": self.history[-400:],
            "suggestions": suggestions,
            "recent": [{k: r[k] for k in ("symbol", "t", "p_up", "ret_pct", "correct")} for r in self.resolved[-12:]][::-1],
        }

    # ---- persistence
    def save(self, path: Path = STATE_DIR / "learner.pkl"):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: Path = STATE_DIR / "learner.pkl") -> "Learner":
        if path.exists():
            with open(path, "rb") as f:
                return pickle.load(f)
        return cls()
