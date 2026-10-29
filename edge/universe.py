"""Starter universe: liquid US names grouped by sector, plus ETFs.

The full-market scan replaces this once the engine is proven; the sector map
is what the Home page's 3D shape is built from.
"""
SECTORS = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AMD", "AVGO", "ORCL", "CRM", "INTC", "PLTR", "SMCI", "MSTR"],
    "Communication": ["META", "GOOGL", "NFLX", "DIS", "AMC"],
    "Consumer Disc.": ["AMZN", "TSLA", "HD", "NKE", "SBUX", "GME"],
    "Consumer Staples": ["WMT", "COST", "PG", "KO", "PEP"],
    "Healthcare": ["LLY", "UNH", "JNJ", "PFE", "MRK"],
    "Financials": ["JPM", "BAC", "GS", "V", "MA", "COIN"],
    "Energy": ["XOM", "CVX", "COP", "OXY", "SLB"],
    "Industrials": ["CAT", "BA", "GE", "UPS", "DE"],
    "Materials": ["LIN", "FCX", "NEM", "NUE"],
    "Utilities": ["NEE", "DUK", "SO"],
    "Real Estate": ["PLD", "AMT", "O"],
}
ETFS = ["QQQ", "TQQQ", "SOXL", "IWM", "XLE", "XLF", "XLK"]

SECTOR_OF = {s: sector for sector, members in SECTORS.items() for s in members}
STOCKS = list(SECTOR_OF)
ALL = STOCKS + ETFS
