"""Post-earnings drift: the engine's first proven signal (see RESEARCH.md).

Rule: EPS surprise in the top 20% of the trailing year's surprises AND the stock
rose more than 2% on announcement day → buy the first close after, hold 40
trading days. Backtest: +4.7% excess over SPY per trade, t=3.5, 62% hit rate.
"""
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

HOLD_DAYS = 40
MIN_DAY0 = 2.0          # % move on announcement day
TOP_SURPRISE = 0.8      # quantile of trailing-year surprises
CACHE = Path(__file__).resolve().parents[2] / "data" / "earnings_live.parquet"
EVIDENCE = {"excess_40d": 4.7, "hit_rate": 0.62, "t": 3.5, "trades": 274, "avg_win": 18.9, "avg_loss": -10.8}


def fetch_events(symbols: list[str], max_age_hours: float = 20) -> pd.DataFrame:
    """Past and upcoming earnings dates with EPS surprise, cached ~daily."""
    if CACHE.exists() and (time.time() - CACHE.stat().st_mtime) < max_age_hours * 3600:
        return pd.read_parquet(CACHE)
    rows = []
    for sym in symbols:
        try:
            ed = yf.Ticker(sym).get_earnings_dates(limit=12)
            if ed is None or ed.empty:
                continue
            for t, row in ed.iterrows():
                ts = pd.Timestamp(t)
                hour = ts.hour if ts.tzinfo is None else ts.tz_convert("America/New_York").hour
                rows.append({"symbol": sym, "date": ts.tz_localize(None).normalize() if ts.tzinfo is None else ts.tz_convert("America/New_York").tz_localize(None).normalize(),
                             # Yahoo stamps before-open reports in the morning and after-close ones at/after 16:00.
                             "session": "before" if hour < 9 or (hour == 9 and ts.minute < 30) else "after" if hour >= 16 else "during",
                             "surprise": row.get("Surprise(%)", np.nan), "eps": row.get("Reported EPS", np.nan),
                             "estimate": row.get("EPS Estimate", np.nan)})
        except Exception:
            pass
        time.sleep(0.25)
    ev = pd.DataFrame(rows)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    ev.to_parquet(CACHE)
    return ev


def live_picks(events: pd.DataFrame, close: pd.DataFrame, spy: pd.Series) -> list[dict]:
    """Open earnings-drift positions as of the latest close: entered within the last
    HOLD_DAYS trading days, with how they're doing and when they exit."""
    dates = close.index
    past = events.dropna(subset=["surprise"])
    past = past[past["date"] <= dates[-1]]
    picks = []
    for _, e in past[past["date"] >= dates[-1] - pd.Timedelta(days=75)].iterrows():
        sym = e["symbol"]
        if sym not in close or close[sym].isna().iloc[-1]:
            continue
        trailing = past[(past["date"] < e["date"]) & (past["date"] >= e["date"] - pd.Timedelta(days=365))]["surprise"]
        if len(trailing) < 50:
            continue
        # A % surprise on a near-zero estimate is noise ("beat by 1000%" on a 1-cent forecast).
        est = e.get("estimate")
        if pd.isna(e["surprise"]) or e["surprise"] > 300 or (est is not None and not pd.isna(est) and abs(est) < 0.05):
            continue
        threshold = trailing.quantile(TOP_SURPRISE)
        pos = dates.searchsorted(e["date"])
        if pos >= len(dates) or pos == 0:
            continue
        p = close[sym]
        if pd.isna(p.iloc[pos]) or pd.isna(p.iloc[pos - 1]) or p.iloc[pos - 1] <= 0:
            continue
        day0 = (p.iloc[pos] / p.iloc[pos - 1] - 1) * 100
        if e["surprise"] < threshold or day0 < MIN_DAY0 or p.iloc[pos] < 2:
            continue
        entry_i = pos + 1
        held = len(dates) - 1 - entry_i
        if held >= HOLD_DAYS:
            continue
        now = float(p.iloc[-1])
        base = {"symbol": sym, "announced": e["date"].strftime("%Y-%m-%d"), "surprise": round(float(e["surprise"]), 1),
                "day0": round(float(day0), 1), "threshold": round(float(threshold), 1), "price": round(now, 2)}
        if entry_i >= len(dates):
            # Reported today: the rule buys the next close. Show it so the tier isn't blank in between.
            picks.append({**base, "status": "pending", "entry_date": None, "exit_date": None, "days_left": HOLD_DAYS,
                          "entry": None, "ret": 0.0, "excess": 0.0})
            continue
        entry = float(p.iloc[entry_i])
        bench = (spy.iloc[-1] / spy.iloc[entry_i] - 1) * 100
        picks.append({
            **base, "status": "open", "entry_date": dates[entry_i].strftime("%Y-%m-%d"),
            "exit_date": None if entry_i + HOLD_DAYS >= len(dates) else dates[entry_i + HOLD_DAYS].strftime("%Y-%m-%d"),
            "days_left": int(HOLD_DAYS - held), "entry": round(entry, 2),
            "ret": round((now / entry - 1) * 100, 2), "excess": round((now / entry - 1) * 100 - bench, 2),
        })
    picks.sort(key=lambda x: -x["days_left"])
    return picks


def upcoming(events: pd.DataFrame, today: pd.Timestamp, days: int = 7) -> list[dict]:
    """Earnings due in the next `days` days: where the next signals can come from."""
    up = events[(events["date"] > today) & (events["date"] <= today + pd.Timedelta(days=days))]
    return [{"symbol": r["symbol"], "date": r["date"].strftime("%Y-%m-%d")} for _, r in up.sort_values("date").iterrows()]


def _num(v):
    return None if v is None or pd.isna(v) else round(float(v), 2)


def week_grid(events: pd.DataFrame, close: pd.DataFrame, weeks: tuple[int, ...] = (-1, 0, 1, 2)) -> dict:
    """Mon-Fri columns for several weeks, each day split into before-open / after-close /
    unknown, every entry with estimate, actual, surprise and day-0 move when known."""
    today = close.index[-1]
    monday = (today - pd.Timedelta(days=today.weekday())).normalize()
    ev = events.drop_duplicates(["symbol", "date"]).copy()
    if "session" not in ev:
        ev["session"] = "during"
    out = {}
    for w in weeks:
        start = monday + pd.Timedelta(weeks=w)
        days = []
        for d in range(5):
            day = start + pd.Timedelta(days=d)
            rows = ev[ev["date"] == day].sort_values("symbol")
            groups = {"before": [], "after": [], "during": []}
            for _, r in rows.iterrows():
                sym = r["symbol"]
                pos = close.index.searchsorted(day)
                day0 = None
                if pd.notna(r.get("surprise")) and sym in close and 0 < pos < len(close) and close.index[pos] == day:
                    day0 = _num((close[sym].iloc[pos] / close[sym].iloc[pos - 1] - 1) * 100)
                groups[r.get("session", "during")].append({
                    "symbol": sym, "estimate": _num(r.get("estimate")), "eps": _num(r.get("eps")),
                    "surprise": _num(r.get("surprise")), "day0": day0, "reported": bool(pd.notna(r.get("surprise"))),
                })
            days.append({"date": day.strftime("%Y-%m-%d"), "is_today": day == today.normalize(), **groups})
        out[str(w)] = {"start": start.strftime("%Y-%m-%d"), "end": (start + pd.Timedelta(days=4)).strftime("%Y-%m-%d"),
                       "days": days, "count": int(sum(len(x[g]) for x in days for g in ("before", "after", "during")))}
    return out


def calendar(events: pd.DataFrame, close: pd.DataFrame, ahead_days: int = 21, back_days: int = 10) -> dict:
    """Upcoming reports with the Street estimate, and recent reports with the surprise,
    day-0 reaction and whether they fired the drift signal."""
    today = close.index[-1]
    ev = events.drop_duplicates(["symbol", "date"])
    up = ev[(ev["date"] > today) & (ev["date"] <= today + pd.Timedelta(days=ahead_days))].sort_values(["date", "symbol"])
    upcoming_rows = [{"symbol": r["symbol"], "date": r["date"].strftime("%Y-%m-%d"), "estimate": _num(r.get("estimate"))}
                     for _, r in up.iterrows()]
    past = ev[(ev["date"] <= today) & (ev["date"] >= today - pd.Timedelta(days=back_days))].dropna(subset=["surprise"])
    trailing = ev[(ev["date"] < today) & (ev["date"] >= today - pd.Timedelta(days=365))]["surprise"].dropna()
    bar = float(trailing.quantile(TOP_SURPRISE)) if len(trailing) >= 50 else None
    recent_rows = []
    for _, r in past.sort_values("date", ascending=False).iterrows():
        sym = r["symbol"]
        pos = close.index.searchsorted(r["date"])
        day0 = None
        if sym in close and 0 < pos < len(close):
            day0 = _num((close[sym].iloc[pos] / close[sym].iloc[pos - 1] - 1) * 100)
        recent_rows.append({"symbol": sym, "date": r["date"].strftime("%Y-%m-%d"), "estimate": _num(r.get("estimate")),
                            "eps": _num(r.get("eps")), "surprise": _num(r["surprise"]), "day0": day0,
                            "fired": bool(bar is not None and r["surprise"] >= bar and day0 is not None and day0 > MIN_DAY0)})
    return {"upcoming": upcoming_rows, "recent": recent_rows, "bar": _num(bar)}
