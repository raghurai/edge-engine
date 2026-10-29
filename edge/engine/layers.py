"""The layered engine: one score per name from every data layer, then a tier.

Each layer contributes only what the backtester has shown it is worth
(scripts/exp_features.py, RESEARCH.md). Numbers below are the measured 40-day
excess returns, not guesses:

  event      big EPS beat + stock up >2% on the day      base  +4.4%  (274 trades, t=3.5)
  margins    gross margin expanded last quarter          +8.8% vs -2.6% among beats (t=2.3)
  analysts   downgrade in the prior 90 days              hurts across all events (t=-2.8)
             upgrade in the 10 days after the beat       +5.5% vs +3.3% remaining drift
  reaction   day-0 move in the top half among beats      +6.3% vs +2.5% (t=1.6)
  price      momentum / volume (the old Edge Engine)     little edge alone; tie-break only

Tiers:
  WILL   event fired AND no layer argues against it (no recent downgrade, margins not shrinking)
  MAYBE  event fired but a layer argues against it, or no event but the other layers stack up
  LOTTO  a catalyst inside a week (earnings) or a violent move, ranked by crowding and volume
"""
import pandas as pd

from edge.data import fundamentals as fund

BASE_DRIFT = 4.4


def score(symbol: str, today: pd.Timestamp, event: dict | None, price_row: dict, snap: dict | None = None) -> dict:
    """Layer-by-layer verdict for one name. `event` is the open earnings-drift pick if it fired."""
    f = fund.features_at(symbol, today)
    after = fund.after_event(symbol, pd.Timestamp(event["announced"])) if event else {}
    snap = snap or {}
    layers, expected, against, for_ = [], 0.0, [], []

    if event:
        expected += BASE_DRIFT
        for_.append(f"beat by {event['surprise']:.0f}%, {event['day0']:+.1f}% on the day")
        layers.append({"layer": "event", "ok": True, "note": f"earnings drift fired · {event['days_left']}d left"})
        if event["day0"] >= 4:
            expected += 1.5; for_.append("strong day-0 reaction")
    gm = f.get("gm_change")
    if gm is not None:
        gm = float(gm)
        if gm > 0:
            if event: expected += 3.0
            for_.append(f"gross margin +{gm * 100:.1f}pt"); layers.append({"layer": "margins", "ok": True, "note": f"gross margin expanded {gm * 100:+.1f}pt"})
        elif gm < -0.01:
            if event: expected -= 4.0
            against.append(f"gross margin {gm * 100:+.1f}pt"); layers.append({"layer": "margins", "ok": False, "note": f"gross margin shrank {gm * 100:+.1f}pt"})
    dn, up = f.get("downgrades_90d", 0), f.get("upgrades_90d", 0)
    if dn:
        expected -= 2.0 if event else 1.0
        against.append(f"{dn} downgrade{'s' if dn > 1 else ''} in 90d"); layers.append({"layer": "analysts", "ok": False, "note": f"{dn} downgrade(s) in the last 90 days"})
    elif up:
        for_.append(f"{up} upgrade{'s' if up > 1 else ''} in 90d"); layers.append({"layer": "analysts", "ok": True, "note": f"{up} upgrade(s) in the last 90 days"})
    if after.get("upgrades_after"):
        expected += 1.5; for_.append("upgraded after the print"); layers.append({"layer": "analysts", "ok": True, "note": "upgraded since the report"})
    if after.get("downgrades_after"):
        expected -= 4.0; against.append("downgraded after the print"); layers.append({"layer": "analysts", "ok": False, "note": "downgraded since the report"})
    ry = f.get("rev_yoy")
    if ry is not None:
        ry = float(ry)
        (for_ if ry > 0.1 else against if ry < 0 else []).append(f"revenue {ry * 100:+.0f}% y/y")
        layers.append({"layer": "fundamentals", "ok": bool(ry >= 0), "note": f"revenue {ry * 100:+.0f}% y/y"})
    ib, isell = f.get("insider_buys_180d", 0), f.get("insider_sells_180d", 0)
    if ib:
        for_.append(f"{ib} insider buy{'s' if ib > 1 else ''}"); layers.append({"layer": "insiders", "ok": True, "note": f"{ib} insider purchase(s) in 180d"})
    sf = snap.get("short_pct_float")
    sf = None if sf is None or sf != sf else float(sf)
    if sf is not None and sf >= 0.1:
        layers.append({"layer": "positioning", "ok": None, "note": f"{sf * 100:.0f}% of float short"})
    mom = price_row.get("momentum")
    if mom is not None and abs(mom) >= 1:
        (for_ if mom > 0 else against).append(f"momentum {mom:+.1f}σ")

    if event:
        tier = "will" if not against else "maybe"
    elif len(for_) >= 2 and not against:
        tier = "maybe"; expected = 1.5 + 0.5 * len(for_)
    else:
        tier = None
    return {"tier": tier, "expected_40d": round(expected, 1), "for": for_, "against": against, "layers": layers,
            "short_pct_float": sf, "n_layers": len(layers)}
