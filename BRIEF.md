# Raidical Stonkz — project brief

*Drawn from the ideation conversation, 25–28 Oct 2026.*

## Goal
Predict what is going to happen in the US market so we can take advantage of it.
Second goal, stated three times: **profit**. The tool must find real, achievable
profit, not profit that only exists on screen.

## Scope decisions
- **Universe:** every US stock and ETF, leveraged ETFs included. No filters from us;
  the market filters, by costing each trade at its real spread and volume.
- **Two halves:** short term = weekly, monthly, up to 6 months. Long term = anything
  over 6 months (even 6 months and a week).
- **Instruments:** stocks/ETFs and options. No borrowed money: no margin, no naked
  selling. Allowed options trades: buy calls/puts, covered calls, cash-secured puts.
- **Risk chip** on both stocks and options: Conservative / Balanced / Aggressive /
  🎰 Lotto. For options it sets strike (ITM / ATM / OTM / far OTM) and expiry.
- **Options flow:** two steps — pick the stock, then pick the contract.
- **Lotto Engine:** can't predict single lottos; score the conditions where big fast
  moves happen more often (catalysts, options flow, coiled spring, squeeze fuel,
  implied vs historical move) → Lotto Score 0–100, judged only across many picks.
- **Speed:** as fast as Bloomberg. Live, not polled.
- **Data:** free. Raw OPRA preferred over packaged flow APIs (Unusual Whales,
  Bullflow). No account that needs an SSN. Result: Alpaca paper account (free,
  email only) for prices, news and history; FINRA/EDGAR/Finnhub for the rest.
- **Engine principle:** output odds, not certainties. Edge = expected move minus
  real trading costs. Show only positive edge. Time-machine rule (no future data),
  walk-forward backtests, calibration checks, a self-correcting feedback loop.

## Engine architecture: layers (decided 28 Oct)
The engine reads every layer of data about a company and the market, in one
feature store, and one model weighs them. A layer earns its weight only by
sharpening predictions on the walk-forward backtester; until then it is
collected and shown, not trusted.

| Layer | What | Source | State |
|---|---|---|---|
| Price & volume | momentum, volume, range, volatility | Alpaca | in |
| Events | earnings dates, EPS surprise, day-0 reaction | Yahoo | in — the proven signal |
| Fundamentals | revenue growth/acceleration, margins, cash flow, debt | Yahoo quarterlies | collected, under test |
| Analysts | upgrades/downgrades (dated), targets, estimate revisions | Yahoo | collected, under test |
| Positioning | short % float, institutional %, insider buys/sells | Yahoo, FINRA | collected, under test |
| Options | implied move, skew, put/call, open interest, gamma | Alpaca chain (OPRA after sign-off) | chain only |
| Text | call transcripts and filings: demand, bottlenecks, pricing power, guidance tone | SEC EDGAR + Claude | planned |
| News / sentiment | headline sentiment, retail and social flow | Alpaca news, FinBERT; paid for flow | tested, no edge on large caps; retest on small caps |
| Macro | rates, dollar, VIX, sector ETFs, regime | FRED, Alpaca | planned |

Output stays the same shape regardless of layers: odds, expected move, edge
after costs, and a logged call that gets scored.

## Information architecture (the spec)
```
NAV: Home | Stocks | Options | Portfolio
HOME      3D shape of the market (top-left) · Most volume: stocks/ETFs + options
          (bottom-left) · Live flow of the market (right, tall column)
STOCKS    short term (weekly / monthly / ≤6 mo) + long term, risk chip
OPTIONS   stock pick → contract pick, risk chip incl. 🎰 Lotto
PORTFOLIO new section
```
Corrections given along the way: risk chip belongs on Stocks/Options only, not
Home. Live flow should be a live ranked board like order-block.com (Bullish /
Bearish lists with a clear-zone card), with real events only on the tape. The
3D shape as built (cones on a ring) needs a rethink.

Stocks and Options are each split into three tiers by trust (user's sketch,
28 Oct), each by weekly / month / years:
- **Will make money** — proven signals only (earnings drift, month horizon)
- **Maybe will make money** — unproven engine odds, labelled as such
- **Lotto** — names with a catalyst this week; on Options, the specific cheap
  far-out-of-the-money contracts that show the lotto possibility

## What is built
- Repo: github.com/raghurai/edge-engine · Pages: raghurai.github.io/edge-engine
- Data layer (Alpaca): history, session spreads, live trade + news streams.
- Engine core: signals → odds → expected move → edge after spread; walk-forward
  backtester with calibration; export to `docs/data.json` for the dashboard.
- Signals tested: momentum, volume surge, AI news sentiment (FinBERT).
- Self-learning intraday engine: predicts 15 min ahead every minute, scores
  itself, updates weights; replay mode for after hours.
- Dashboard: Home (shape, most volume, live board + tape), Stocks (four horizons,
  risk chip, intraday learner), freshness indicator, local relay for live data.

## What the evidence says so far (full detail in RESEARCH.md)
Six signals tested on up to 1,000 stocks. Five failed honestly: weekly momentum
+ volume, news sentiment, the intraday learner, short-volume squeeze, and the
classic winners-minus-losers momentum spread.

**One passed: post-earnings drift after big EPS beats.** +3.4% excess over SPY
in the 40 trading days after a top-quintile surprise, t=4.3, positive in every
full year 2023-2026; +4.6% with a 57% hit rate when the market also liked the
print. Slow institutional repositioning is the mechanism. It is the engine's
first real signal, for the monthly and 6-month horizons.

## Open questions
1. Harden the earnings signal: point-in-time universe, sector excess, a
   walk-forward portfolio simulation. Then wire it into the Stocks page and
   start logging calls to Portfolio.
2. Next experiments: analyst-revision drift; gamma pin and dealer flow once the
   OPRA agreement is signed (free); small-cap news lag.
3. Rethink the 3D shape of the market.
4. Options section still needs the options feed; Portfolio is a placeholder.

## Rules of engagement
One question at a time. Honest results over flattering ones. Keys are never
pasted in chat. I don't give personal trade picks.
