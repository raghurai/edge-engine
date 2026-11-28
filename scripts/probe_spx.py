"""Build the SPX desk once and print a summary: run by the probe workflow, where the keys live."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edge.signals import spx

t = time.time()
X = spx.build()
json.dumps(X, allow_nan=False)
print("SPXOUT built in", round(time.time() - t), "s", {k: X[k] for k in ("spot", "change_pct", "iv30", "vix", "as_of", "contracts")})
print("SPXOUT expiries", X["expiries"])
for m in X["expected"]:
    print("SPXOUT move", m["label"], m["expiry"], "±", m["move"], f"{m['move_pct']}%", "iv", m["iv"], "1s", m["d16_lo"], m["d16_hi"])
W = X["walls"]
print("SPXOUT walls pc", W.get("pc_ratio"), "pain", W.get("max_pain"), "gamma", W.get("gamma_strike"), "oi", W.get("total_oi"), W.get("calls", [])[:3], W.get("puts", [])[:3])
for tier, horizons in X["tiers"].items():
    for h, rows in horizons.items():
        for s in rows:
            print("SPXOUT", tier, h, s["expiry"], s["name"], s["kind"], "net", s["net"], "maxloss", s.get("max_loss"), "pop", s.get("pop"), "delta", s.get("delta"))
print("SPXOUT marks", len(X["marks"]))
