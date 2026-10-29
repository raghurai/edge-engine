"""Runs the engine on the universe and writes docs/data.json for the Home page.

Usage: python scripts/export.py [--news]
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from edge import universe
from edge.data import alpaca
from edge.engine import core
from edge.signals import price

OUT = Path(__file__).resolve().parents[1] / "docs" / "data.json"
HORIZON = "weekly"
use_news = "--news" in sys.argv

symbols = universe.ALL + [price.BENCHMARK]
bars = alpaca.history(symbols, days=365 * 5)   # long horizon needs years of complete futures to train on
close = bars["close"].unstack(level="symbol")
volume = bars["volume"].unstack(level="symbol")
spreads = alpaca.session_spread(universe.ALL)

extra, flow = {}, []
if use_news:
    from edge.data import news as news_data
    from edge.signals import news as news_signal
    headlines = news_data.headlines(universe.ALL, days=30)
    scored = news_signal.score_headlines(headlines)
    dates = close.index.tz_localize(None).normalize()
    sent = news_signal.sentiment_series(scored, dates)
    sent.index = close.index
    extra["news"] = sent.reindex(columns=close.columns.drop(price.BENCHMARK)).fillna(0)
    recent = scored[scored["time"] >= scored["time"].max() - pd.Timedelta(days=2)]
    for _, h in recent.reindex(recent["score"].abs().sort_values(ascending=False).index).head(40).iterrows():
        flow.append({"type": "news", "time": h["time"].isoformat(), "symbol": h["symbol"],
                     "text": h["headline"], "score": round(float(h["score"]), 2)})

today = core.predict_today(bars, HORIZON, extra)
today["volume_ratio"] = price.volume_ratio(bars)
today["price"] = close.iloc[-1]
today["change_pct"] = (close.iloc[-1] / close.iloc[-2] - 1) * 100
today["spread_pct"] = spreads
today["edge"] = core.edge(today["expected_move"], today["spread_pct"].fillna(0.2))
today["sector"] = pd.Series(universe.SECTOR_OF).reindex(today.index).fillna("ETF")
today["dollar_volume"] = volume.iloc[-1] * close.iloc[-1]

def row(sym, r):
    return {
        "symbol": sym, "sector": r["sector"],
        "price": round(float(r["price"]), 2), "change_pct": round(float(r["change_pct"]), 2),
        "volume_ratio": round(float(r["volume_ratio"]), 2),
        "p_up": round(float(r["p_up"]), 3), "expected_move": round(float(r["expected_move"]), 2),
        "spread_pct": None if pd.isna(r["spread_pct"]) else round(float(r["spread_pct"]), 3),
        "edge": round(float(r["edge"]), 2),
        "signals": {k: round(float(r[k]), 2) for k in today.attrs["weights"]},
    }

stocks = {sym: row(sym, r) for sym, r in today.iterrows()}

# Every horizon for the Stocks page. Long term reuses the price signals over a
# year; the quality signals (growth, margins, valuation) are still to come.
horizons = {}
for h in price.HORIZON_DAYS:
    t = today if h == HORIZON else core.predict_today(bars, h, extra)
    if h != HORIZON:
        t["volume_ratio"], t["price"], t["change_pct"] = today["volume_ratio"], today["price"], today["change_pct"]
        t["spread_pct"], t["sector"] = today["spread_pct"], today["sector"]
        t["edge"] = core.edge(t["expected_move"], t["spread_pct"].fillna(0.2))
    horizons[h] = {
        "weights": {k: round(float(v), 3) for k, v in t.attrs["weights"].items()},
        "avg_abs_move": round(float(t["expected_move"].abs().mean()), 2),
        "picks": [row(s, r) for s, r in t.sort_values("edge", ascending=False).iterrows()],
    }

# Sector attraction: how strongly the engine leans in each sector, 0-100.
sectors = []
for name, members in universe.SECTORS.items():
    m = today.loc[[s for s in members if s in today.index]]
    lean = float((m["p_up"] - 0.5).mean() * 20 + m["momentum"].mean() * 0.5 + m["volume_surge"].mean() * 0.3)
    score = float(100 / (1 + np.exp(-lean)))
    sectors.append({
        "name": name, "score": round(score, 1),
        "direction": "up" if m["momentum"].mean() >= 0 else "down",
        "momentum": round(float(m["momentum"].mean()), 2),
        "volume": round(float(m["volume_ratio"].mean()), 2),
        "top": [row(s, r) for s, r in m.sort_values("edge", ascending=False).head(3).iterrows()],
    })

# Market mood from SPY: trend over a month and how choppy the last 20 days were.
spy = close[price.BENCHMARK]
trend = spy.iloc[-1] / spy.iloc[-21] - 1
vol = spy.pct_change().tail(20).std() * np.sqrt(252) * 100
mood = ("Bull" if trend > 0.02 else "Bear" if trend < -0.02 else "Choppy") + " · " + ("Calm" if vol < 15 else "Fear" if vol > 25 else "Active")

# The whole US market: every tradable symbol, one year of bars, the full earnings calendar.
from edge.data import nasdaq_cal, wide
from edge.signals import earnings
all_symbols = wide.all_us_stocks()
wbars = wide.daily_bars(all_symbols, years=1)
wclose = wbars["close"].unstack(level="symbol")
wclose.index = wclose.index.tz_convert("UTC").normalize().tz_localize(None)
if "SPY" not in wclose:
    wclose["SPY"] = close[price.BENCHMARK].reindex(wclose.index)
scan = [s for s in wclose.columns if s != "SPY"]
events = nasdaq_cal.events(back_days=380, ahead_days=28)
pead_picks = earnings.live_picks(events, wclose, wclose["SPY"])
pead_upcoming = earnings.upcoming(events, wclose.index[-1])
print(f"universe {len(scan)} symbols · events {len(events)} · fired {len(pead_picks)}", flush=True)

# Call log for the Portfolio page, written after the layered verdicts below so only
# names that reached the WILL tier (every layer agreeing) are logged as engine calls.
LOG = OUT.parent / "calls.json"
log = json.loads(LOG.read_text()) if LOG.exists() else []

def write_call_log(will_rows):
    known = {(c["symbol"], c["entry_date"]) for c in log}
    for p in will_rows:
        if p.get("status") == "pending" or not p.get("entry_date"):
            continue   # logged on the day it actually enters
        if (p["symbol"], p["entry_date"]) not in known:
            log.append({**{k: p[k] for k in ("symbol", "announced", "entry_date", "entry", "price", "days_left", "ret", "excess", "surprise", "day0")},
                        "signal": "earnings_drift · all layers", "status": "open"})
def update_call_log():
    for c in log:
        if c["status"] == "open" and c["symbol"] in wclose:
            live = next((p for p in pead_picks if p["symbol"] == c["symbol"] and p["entry_date"] == c["entry_date"]), None)
            if live:
                c.update(price=live["price"], ret=live["ret"], excess=live["excess"], days_left=live["days_left"])
            else:
                # Past its exit: close it at the exit-date price.
                ei = wclose.index.searchsorted(pd.Timestamp(c["entry_date"]))
                xi = min(ei + earnings.HOLD_DAYS, len(wclose) - 1)
                px, spx = wclose[c["symbol"]], wclose["SPY"]
                c.update(status="closed", exit_date=wclose.index[xi].strftime("%Y-%m-%d"), price=round(float(px.iloc[xi]), 2),
                         ret=round(float(px.iloc[xi] / c["entry"] - 1) * 100, 2),
                         excess=round(float((px.iloc[xi] / c["entry"] - 1) - (spx.iloc[xi] / spx.iloc[ei] - 1)) * 100, 2), days_left=0)
    LOG.write_text(json.dumps(log, indent=1))

# ---- Three tiers for the Stocks and Options pages: will / maybe / lotto.
from edge.data import options as opt

wvol = wbars["volume"].unstack(level="symbol"); wvol.index = wclose.index
wchg = (wclose.iloc[-1] / wclose.iloc[-2] - 1) * 100
wvr = wvol.iloc[-1] / wvol.iloc[-21:-1].mean()
spot = wclose.iloc[-1]

# ---- Layered engine: every name gets a verdict from all layers; the tiers come from it.
from edge.engine import layers as layered
open_events = {p["symbol"]: p for p in pead_picks}
# Company layers (Yahoo, per name) are fetched only where they can matter: names with a
# fired signal, names reporting within the window, and the liquid core that is already cached.
window = events[(events["date"] >= wclose.index[-1] - pd.Timedelta(days=45)) & (events["date"] <= wclose.index[-1] + pd.Timedelta(days=14))]
catalyst_names = set(open_events) | set(window["symbol"]) | set(wide.most_liquid(1000)[:300])
verdicts = {}
for sym in scan:
    if sym == "SPY" or sym not in spot or sym not in catalyst_names or pd.isna(spot[sym]):
        continue
    pr = stocks.get(sym, {})
    v = layered.score(sym, wclose.index[-1], open_events.get(sym), pr.get("signals", {}))
    v.update(symbol=sym, price=round(float(spot[sym]), 2), change_pct=round(float(wchg.get(sym, 0)), 2),
             sector=universe.SECTOR_OF.get(sym, pr.get("sector", "—")))
    if sym in open_events:
        v.update({k: open_events[sym][k] for k in ("announced", "surprise", "day0", "entry", "entry_date", "days_left", "excess", "ret", "threshold", "status")})
    verdicts[sym] = v

from edge.data import fundamentals as _fund
if _fund.BUDGET["skipped"]:
    print(f"company layers: budget used; {_fund.BUDGET['skipped']} names deferred to the next run", flush=True)
will_rows = sorted([v for v in verdicts.values() if v["tier"] == "will"], key=lambda v: -v["expected_40d"])
maybe_all = sorted([v for v in verdicts.values() if v["tier"] == "maybe"], key=lambda v: -v["expected_40d"])

# The engine's own portfolio trades the WILL tier automatically.
from edge.engine import portfolio as engine_pf
engine_state = engine_pf.update(engine_pf.load(), will_rows, wclose, wclose["SPY"])
print(f"engine portfolio: equity {engine_state['summary']['equity']:,.0f} · open {engine_state['summary']['n_open']} · closed {engine_state['summary']['n_closed']}", flush=True)
if LOG.exists():
    LOG.unlink()   # the old call log is replaced by the engine portfolio

def maybe_rows(h, n=8):
    # The layered verdicts are 40-day evidence; they populate the month tier. Weekly and years
    # keep the price engine's odds until a layer earns those horizons.
    if h == "monthly":
        return maybe_all[:12]
    return [p for p in horizons[h]["picks"] if p["edge"] > 0][:n]

maybe = {"weekly": maybe_rows("weekly"), "month": maybe_rows("monthly"), "years": maybe_rows("long")}

# Lotto stocks: a catalyst inside a week (earnings due) or already moving violently on volume.
due = {u["symbol"]: u["date"] for u in pead_upcoming}
lotto_stocks = []
for sym in scan:
    if sym not in spot or sym == "SPY" or pd.isna(spot[sym]) or spot[sym] < 1:
        continue
    reasons = []
    if sym in due:
        reasons.append(f"earnings {due[sym][5:]}")
    if wvr.get(sym, 0) >= 2.5:
        reasons.append(f"{wvr[sym]:.1f}× volume")
    if abs(wchg.get(sym, 0)) >= 6:
        reasons.append(f"{wchg[sym]:+.1f}% today")
    if reasons:
        v = verdicts.get(sym, {})
        sf = v.get("short_pct_float")
        if sf and sf >= 0.1:
            reasons.append(f"{sf * 100:.0f}% short")
        lotto_stocks.append({"symbol": sym, "price": round(float(spot[sym]), 2), "change_pct": round(float(wchg[sym]), 2),
                             "volume_ratio": round(float(wvr.get(sym, 0)), 1), "reasons": reasons,
                             "earnings": due.get(sym), "short_pct_float": sf, "for": v.get("for", []), "against": v.get("against", [])})
# Crowded + catalyst first: that's where violent moves come from.
lotto_stocks.sort(key=lambda r: (r["earnings"] is None, -((r["short_pct_float"] or 0) * 10 + (r["volume_ratio"] or 0))))
lotto_stocks = lotto_stocks[:20]

# Contracts. One chain fetch per name, reused across tiers.
chains = {}
def get_chain(sym):
    if sym not in chains:
        chains[sym] = opt.chain(sym, float(spot[sym])) if sym in spot else None
    return chains[sym]

def contract_for(sym, kind, dte, otm, max_ask=None):
    ch = get_chain(sym)
    if ch is None or ch.empty:
        return None
    c = opt.pick(ch, kind, *dte, *otm, max_ask=max_ask)
    if c:
        c["spot"] = round(float(spot[sym]), 2)
        c["breakeven_move_pct"] = round((c["strike"] + c["ask"] * (1 if kind == "call" else -1)) / c["spot"] * 100 - 100, 1)
    return c

opt_will = []
for p in will_rows:
    if p.get("status") == "pending" and not pead_picks:
        continue
    c = contract_for(p["symbol"], "call", (35, 90), (-4, 4))
    if c: opt_will.append({**p, "contract": c})

opt_maybe = {"weekly": [], "month": [], "years": []}
for h, (dte, otm) in {"weekly": ((5, 14), (-2, 3)), "month": ((25, 50), (-2, 4)), "years": ((300, 800), (-5, 10))}.items():
    for p in maybe[h][:6]:
        bullish = p.get("p_up", 0.5) >= 0.5 if "p_up" in p else p.get("expected_40d", 0) >= 0
        c = contract_for(p["symbol"], "call" if bullish else "put", dte, otm)
        if c: opt_maybe[h].append({**p, "contract": c})

# Lotto by horizon: how far out of the money and how cheap scale with time.
LOTTO = {"weekly": ((1, 16), 8, 25, 0.60), "month": ((20, 50), 12, 35, 1.50), "years": ((300, 800), 30, 80, 3.00)}
opt_lotto = {h: [] for h in LOTTO}
for h, (dte, lo, hi, cap) in LOTTO.items():
    for s in lotto_stocks[:12]:
        for kind, otm in (("call", (lo, hi)), ("put", (-hi, -lo))):
            c = contract_for(s["symbol"], kind, dte, otm, max_ask=cap)
            if c: opt_lotto[h].append({**s, "contract": c})
    opt_lotto[h].sort(key=lambda r: r["contract"]["ask"])

# Board snapshot for the Home page when no live relay is connected: every name in the
# sector universe ranked by today's move, with the day's range for the zone card.
last = bars.groupby(level="symbol").tail(1).reset_index(level="timestamp")
prev = bars.groupby(level="symbol").nth(-2).reset_index(level="timestamp")
board_rows = []
for sym, r in last.iterrows():
    if sym == price.BENCHMARK or sym not in prev.index:
        continue
    pc = float(prev.loc[sym, "close"]); rng = float(r["high"] - r["low"])
    board_rows.append({
        "symbol": sym, "price": round(float(r["close"]), 2), "chg": round((float(r["close"]) / pc - 1) * 100, 2) if pc else 0,
        "high": float(r["high"]), "low": float(r["low"]), "vwap": round(float(r["vwap"]), 2) if pd.notna(r["vwap"]) else None,
        "vol_ratio": round(float(r["volume"] / prev.loc[sym, "volume"]), 2) if prev.loc[sym, "volume"] else None,
        "range_pos": round((float(r["close"]) - float(r["low"])) / rng, 2) if rng > 0 else 0.5,
        "sector": universe.SECTOR_OF.get(sym, "ETF"), "time": r["timestamp"].isoformat(),
    })
board_rows.sort(key=lambda x: -x["chg"])

# Marks for paper positions: latest price per name, latest quote per contract referenced anywhere.
marks = {sym: round(float(spot[sym]), 2) for sym in spot.index if pd.notna(spot[sym])}
# Full-market marks run to ~12k names; the page only needs names it can show, plus any paper position.
shown = {r["symbol"] for group in (will_rows, maybe_all, lotto_stocks, *[horizons[h]["picks"] for h in horizons]) for r in group}
marks = {s: v for s, v in marks.items() if s in shown or s in universe.ALL or s in wide.most_liquid(1000)}
contract_marks = {}
for group in (opt_will, *opt_maybe.values(), *opt_lotto.values()):
    for p in group:
        c = p["contract"]
        contract_marks[c["contract"]] = {"bid": c["bid"], "ask": c["ask"], "mid": c["mid"], "spot": c["spot"], "dte": c["dte"]}

data = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "marks": marks, "contract_marks": contract_marks,
    "board": board_rows,
    "earnings_drift": {"picks": pead_picks, "upcoming": pead_upcoming, "evidence": earnings.EVIDENCE,
                       "hold_days": earnings.HOLD_DAYS, "scanned": len(scan)},
    "earnings_calendar": earnings.calendar(events, wclose),
    "earnings_weeks": earnings.week_grid(events, wclose),
    "tiers": {
        "stocks": {"will": {"month": will_rows}, "maybe": maybe, "lotto": lotto_stocks},
        "engine": "layered",
        "options": {"will": {"month": opt_will}, "maybe": opt_maybe, "lotto": opt_lotto},
        "options_feed": opt.FEED,
    },
    "horizon": HORIZON,
    "mood": mood,
    "spy": {"price": round(float(spy.iloc[-1]), 2), "change_pct": round(float((spy.iloc[-1] / spy.iloc[-2] - 1) * 100), 2)},
    "weights": {k: round(float(v), 3) for k, v in today.attrs["weights"].items()},
    "sectors": sorted(sectors, key=lambda s: -s["score"]),
    "most_volume": [stocks[s] for s in today.sort_values("dollar_volume", ascending=False).head(15).index],
    "top_edges": [stocks[s] for s in today.sort_values("edge", ascending=False).head(10).index],
    "horizons": horizons,
    "flow": flow,
}
OUT.write_text(json.dumps(data, indent=1))
print(f"Wrote {OUT}  mood={mood}  sectors={[(s['name'], s['score']) for s in data['sectors'][:3]]}")
