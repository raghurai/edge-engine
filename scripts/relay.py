"""Live flow relay: Alpaca trade + news streams → the dashboard, tick by tick.

Runs next to the page. The page connects to ws://localhost:8766 and gets:
  - a "board" message every second: every symbol in the universe with its
    live price, % change, day high/low and VWAP, ranked (from snapshot polls
    plus the trade stream, so the busiest names update tick by tick)
  - every trade and headline the moment it arrives

Usage: python scripts/relay.py [--test]
  --test  uses Alpaca's always-on test stream (symbol FAKEPACA) so the tape
          moves even when the market is closed.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import NewsDataStream, StockDataStream
from alpaca.data.requests import StockSnapshotRequest

from edge import universe
from edge.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

import os
PORT = int(os.getenv("RELAY_PORT", "8766"))
HOST = os.getenv("RELAY_HOST", "localhost")   # "0.0.0.0" on a server; the relay only ever sends public market data
MAX_SYMBOLS = 30          # free-plan cap on streamed symbols
BLOCK_USD = 100_000       # a single print this large is tagged as a block
SNAPSHOT_EVERY = 2.0      # seconds between snapshot polls of the whole universe
BOARD_EVERY = 1.0         # seconds between board broadcasts
TEST = "--test" in sys.argv

clients: set = set()
queue: asyncio.Queue = asyncio.Queue()
state: dict[str, dict] = {}   # symbol -> live row
rest = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def _snapshot_rows() -> dict[str, dict]:
    snaps = rest.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=universe.ALL, feed=DataFeed.IEX))
    rows = {}
    for sym, s in snaps.items():
        bar, prev, last = s.daily_bar, s.previous_daily_bar, s.latest_trade
        if not bar or not prev:
            continue
        price = last.price if last else bar.close
        rows[sym] = {
            "symbol": sym, "price": price, "prev_close": prev.close,
            "open": bar.open, "high": bar.high, "low": bar.low, "vwap": bar.vwap, "volume": bar.volume,
            "prev_volume": prev.volume, "time": (last.timestamp if last else bar.timestamp).isoformat(),
        }
    return rows


async def poll_snapshots():
    while True:
        try:
            fresh = await asyncio.to_thread(_snapshot_rows)
            for sym, row in fresh.items():
                live = state.get(sym)
                # Keep a newer price from the trade stream over the polled one.
                if live and live.get("time", "") > row["time"]:
                    row["price"], row["time"] = live["price"], live["time"]
                    row["high"], row["low"] = max(row["high"], row["price"]), min(row["low"], row["price"])
                state[sym] = row
        except Exception as e:
            print("snapshot error:", e)
        await asyncio.sleep(SNAPSHOT_EVERY)


async def push_board():
    while True:
        await asyncio.sleep(BOARD_EVERY)
        if not state or not clients:
            continue
        rows = []
        for r in state.values():
            chg = (r["price"] / r["prev_close"] - 1) * 100 if r["prev_close"] else 0
            rng = r["high"] - r["low"]
            rows.append({
                "symbol": r["symbol"], "price": round(r["price"], 2), "chg": round(chg, 2),
                "high": r["high"], "low": r["low"], "vwap": round(r["vwap"], 2) if r["vwap"] else None,
                "vol_ratio": round(r["volume"] / r["prev_volume"], 2) if r["prev_volume"] else None,
                # where price sits in today's range: 1 = at the high, 0 = at the low
                "range_pos": round((r["price"] - r["low"]) / rng, 2) if rng > 0 else 0.5,
                "sector": universe.SECTOR_OF.get(r["symbol"], "ETF"), "time": r["time"],
            })
        rows.sort(key=lambda x: -x["chg"])
        await queue.put({"type": "board", "time": datetime.now(timezone.utc).isoformat(), "rows": rows})


def pick_symbols() -> list[str]:
    """Stream the busiest names from the last export, else the universe's first 30."""
    data = Path(__file__).resolve().parents[1] / "docs" / "data.json"
    if data.exists():
        busiest = [s["symbol"] for s in json.loads(data.read_text())["most_volume"]]
        rest = [s for s in universe.ALL if s not in busiest]
        return (busiest + rest)[:MAX_SYMBOLS]
    return universe.ALL[:MAX_SYMBOLS]


async def on_trade(t):
    usd = t.price * t.size
    if t.symbol in state:
        row = state[t.symbol]
        row.update(price=t.price, time=t.timestamp.isoformat(),
                   high=max(row["high"], t.price), low=min(row["low"], t.price))
    await queue.put({
        "type": "block" if usd >= BLOCK_USD else "trade",
        "time": t.timestamp.isoformat(), "symbol": t.symbol,
        "price": t.price, "size": t.size, "usd": round(usd),
        "exchange": getattr(t, "exchange", None),
    })


async def on_news(n):
    await queue.put({
        "type": "news", "time": n.created_at.isoformat(),
        "symbol": (n.symbols or ["—"])[0], "symbols": n.symbols, "text": n.headline,
    })


async def broadcast():
    while True:
        msg = json.dumps(await queue.get(), default=str)
        dead = []
        for ws in clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)


async def serve(ws):
    clients.add(ws)
    await ws.send(json.dumps({"type": "hello", "time": datetime.now(timezone.utc).isoformat(),
                              "text": "relay connected" + (" (test stream)" if TEST else "")}))
    try:
        await ws.wait_closed()
    finally:
        clients.discard(ws)


async def main():
    symbols = ["FAKEPACA"] if TEST else pick_symbols()
    trades = StockDataStream(ALPACA_API_KEY, ALPACA_SECRET_KEY, feed=DataFeed.IEX,
                             url_override="wss://stream.data.alpaca.markets/v2/test" if TEST else None)
    trades.subscribe_trades(on_trade, *symbols)
    tasks = [asyncio.create_task(trades._run_forever()), asyncio.create_task(broadcast()),
             asyncio.create_task(poll_snapshots()), asyncio.create_task(push_board())]
    if not TEST:
        news = NewsDataStream(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        news.subscribe_news(on_news, "*")
        tasks.append(asyncio.create_task(news._run_forever()))
    print(f"Relay on ws://{HOST}:{PORT}  streaming {len(symbols)} symbols" + (" [TEST]" if TEST else ""), flush=True)
    async with websockets.serve(serve, HOST, PORT, ping_interval=20, ping_timeout=20):
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
