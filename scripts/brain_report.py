"""Print the brain's out-of-sample record from models/brain/card.json (or a given card)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
card = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "models" / "brain" / "card.json").read_text())
print("rows", card["rows"], "span", card["span"], "symbols", card["symbols"], "features", len(card["features"]))
for name, m in card["models"].items():
    print(f"\n== {name}")
    if name.startswith("xs_"):
        print(f"  IC {m['ic']} (t={m['ic_t']})  periods {m['independent_periods']}  universe vs SPY {m['universe_vs_spy']}%")
        print(f"  top decile: {m['top_decile_vs_spy']}% vs SPY, {m['top_decile_vs_universe']}% vs universe (t={m['top_decile_t']}), net vs SPY {m['top_decile_net_vs_spy']}% (t={m['top_decile_net_t']}), hit {m['top_decile_hit']}%")
        print(f"  top 20   : net {m['top20_net_vs_spy']}% vs SPY (t={m['top20_t']}), hit {m['top20_hit']}%   bottom decile {m['bottom_decile_vs_spy']}%")
        L = m["liquid"]
        print(f"  LIQUID   : IC {L['ic']}  universe {L['universe_vs_spy']}%  top decile {L['top_decile_vs_spy']}%  top20 net {L['top20_net_vs_spy']}% (t={L['top20_t']}, vs liquid universe t={L['top20_vs_liquid_universe_t']}), hit {L['top20_hit']}%, years+ {L['years_positive']}")
        print("  liquid top20 by year:", L["by_year"])
        print("  by year (top decile vs universe | top20 vs SPY):", {k: (v["top_vs_universe"], v["top20_vs_spy"]) for k, v in m["by_year"].items()})
    else:
        print(f"  events {m['events']}  all events vs SPY {m['all_events_vs_spy']}%  quintiles {m['quintiles_vs_spy']}")
        print(f"  model top decile: net {m['top_decile_net']}% (t={m['top_decile_t']}, n={m['top_decile_n']}, hit {m['top_decile_hit']}%)   by year {m['top_decile_by_year']}")
        print(f"  old rule        : net {m['rule_net']}% (t={m['rule_t']}, n={m['rule_n']}, hit {m['rule_hit']}%)   by year {m['rule_by_year']}")
        L = m["liquid"]
        print(f"  LIQUID events {L['events']} all {L['all_vs_spy']}%")
        print(f"    model top decile: net {L['top_decile_net']}% (t={L['top_decile_t']}, n={L['top_decile_n']}, hit {L['top_decile_hit']}%)  {L['top_decile_by_year']}")
        print(f"    old rule        : net {L['rule_net']}% (t={L['rule_t']}, n={L['rule_n']}, hit {L['rule_hit']}%)  {L['rule_by_year']}")
    print("  calibration mean % by bucket:", [round(b["mean"] * 100, 2) for b in m["calibration"]])
    print("  top features:", [(i["feature"], i["share"]) for i in m["importance"][:8]])
