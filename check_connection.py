"""Checks the Alpaca keys in .env work and pulls one live quote."""
import os

from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

load_dotenv()
key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
if not key or not secret:
    raise SystemExit("Add your Alpaca keys to .env first.")

client = StockHistoricalDataClient(key, secret)
quote = client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols="SPY"))["SPY"]
print(f"Connected. SPY bid {quote.bid_price} / ask {quote.ask_price} at {quote.timestamp}")
