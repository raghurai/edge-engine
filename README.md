# Raidical Stonkz

US stock and options prediction engine. Shows only picks with a positive,
backtested edge after trading costs.

## Setup

```
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
copy .env.example .env   # then add your Alpaca paper keys
```

## Run

```
.\.venv\Scripts\python check_connection.py          # verify Alpaca keys
.\.venv\Scripts\python scripts\scan.py weekly        # rank the watchlist today
.\.venv\Scripts\python scripts\backtest.py monthly 4 # walk-forward backtest
.\.venv\Scripts\python scripts\backtest.py weekly 2 --news   # with AI news sentiment
```

## Run it

```
.\.venv\Scripts\python scripts\run.py
```

One command: starts the live relay, the self-learning engine and the dashboard at
http://localhost:8765, and re-runs the export every 5 minutes while the market is
open, once after the close, hourly otherwise. The page re-reads its data every
minute, so it stays current without a reload.

Pieces on their own:
```
scripts\export.py     # engine → docs/data.json + docs/calls.json
scripts\relay.py      # live board + tape (--test for the after-hours test stream)
scripts\learn.py      # intraday learner (--replay 5 after hours)
```

The GitHub Pages copy refreshes after every close via `.github/workflows/refresh.yml`
once the repo has `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` as Actions secrets.
The live board needs the relay on your machine.

## Layout

- `edge/data/` — data sources (Alpaca prices, news)
- `edge/signals/` — signal z-scores (price, news)
- `edge/engine/` — odds, expected move, edge, walk-forward backtest
- `scripts/` — runnable entry points
- `docs/` — GitHub Pages site
