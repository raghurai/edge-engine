"""One command keeps the whole thing live.

  python scripts/run.py

Starts the live relay, the self-learning engine and the dashboard server, then
re-runs the export on a schedule: every 5 minutes while the market is open,
once right after the close (that's when earnings-drift picks are confirmed),
and hourly otherwise. Ctrl+C stops everything.
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
ET = ZoneInfo("America/New_York")


def market_open(now: datetime) -> bool:
    m = now.hour * 60 + now.minute
    return now.weekday() < 5 and 570 <= m < 960


def spawn(*args):
    return subprocess.Popen([PY, "-u", *args], cwd=ROOT)


def export():
    print(f"[{datetime.now():%H:%M:%S}] export...", flush=True)
    r = subprocess.run([PY, "scripts/export.py"], cwd=ROOT, capture_output=True, text=True)
    tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    print(f"[{datetime.now():%H:%M:%S}] {'ok' if r.returncode == 0 else 'FAILED'}: {tail}", flush=True)
    if r.returncode:
        print(r.stderr[-800:], flush=True)
    elif os.getenv("PUBLISH") == "1":
        # On a server: push the fresh snapshot so GitHub Pages republishes it.
        subprocess.run("git add docs/data.json docs/engine_portfolio.json docs/learning.json && "
                       "(git diff --cached --quiet || git commit -qm 'Refresh dashboard data') && "
                       "git pull -q --rebase --autostash origin main && git push -q", shell=True, cwd=ROOT)


procs = [spawn("scripts/relay.py"), spawn("scripts/learn.py")]
if os.getenv("SERVE_DASHBOARD", "1") == "1":
    procs.append(spawn("-m", "http.server", os.getenv("DASH_PORT", "8765"), "--bind", os.getenv("RELAY_HOST", "localhost"), "--directory", "docs"))
print("Dashboard: http://localhost:8765   relay + learner running. Ctrl+C to stop.", flush=True)

export()
last, closed_done = time.time(), None
try:
    while True:
        time.sleep(30)
        now = datetime.now(ET)
        due = 300 if market_open(now) else 3600
        just_closed = now.weekday() < 5 and now.hour == 16 and now.minute < 10 and closed_done != now.date()
        if just_closed or time.time() - last >= due:
            export()
            last = time.time()
            if just_closed:
                closed_done = now.date()
except KeyboardInterrupt:
    pass
finally:
    for p in procs:
        p.terminate()
