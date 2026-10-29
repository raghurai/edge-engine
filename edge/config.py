"""Loads keys from .env. Every other module reads settings from here."""
import os

from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    raise SystemExit("Alpaca keys missing. Fill in .env (or set the env vars) first.")
