# Research log

Every signal we test, what it showed, and the verdict. Scripts are in `scripts/`
and rerun from cached data in `data/`.

## Verdicts so far

| Signal | Universe / span | Result | Verdict |
|---|---|---|---|
| Momentum + volume, weekly | 16 names, 4 yr | top picks = average | ❌ no edge |
| News sentiment (FinBERT) | 16 names, 2 yr | zero correlation; contrarian bounce flips sign by quarter | ❌ no edge |
| Intraday learner, 15 min | 24 names, 20 days, 122k calls | 51% overall, 32% when confident | ❌ no edge |
| Cross-sectional momentum 12-1 | 1,000 names, 36 months | winners +2.1%/mo vs market, t=1.8; W-L spread nil | ⚠️ suggestive, biased sample |
| Short-volume squeeze (FINRA) | 1,000 names, 400 days | all t < 1, deciles flat | ❌ no edge |
| **Post-earnings drift, big beats** | 234 names, 3,371 events, 2022-26 | **+3.4% excess over 40 days, t=4.3, positive every full year** | ✅ **first signal that passes** |

## Experiment 1: cross-sectional momentum (`exp_momentum.py`)
12-month return skipping the last month, 1,000 most liquid names, decile sort, hold one month.

```
decile   d0    d1    d2    d3    d4    d5    d6    d7    d8    d9
next-mo  3.83  1.66  1.70  1.43  1.11  1.78  1.74  2.02  2.08  4.24
```
- U-shape: both losers and winners beat the middle. The classic winners-minus-losers
  spread is +0.2%/month, t=0.1 — nothing, because 2023 was a loser-rebound year.
- Winners vs the whole market: +2.08%/month, t=1.79. Suggestive, but 36 months only.
- **Bias:** the universe is "liquid today". Stocks that collapsed and vanished are
  missing, so past losers are survivors who recovered. That inflates d0 especially.
  A clean test needs a point-in-time universe.

## Experiment 2: post-earnings-announcement drift (`exp_earnings.py`)
Yahoo EPS surprise for the 300 most liquid names (234 had data), 3,371 events,
Nov 2022 to Jul 2026. Returns are excess over SPY, measured from the first close
after the announcement, so the announcement-day jump is excluded.

```
surprise    day0   +20d   +40d   +60d
big miss   -1.00   0.39   0.83   1.06
miss       -0.00  -0.56  -1.36  -0.93
inline      0.21   0.24  -0.22   0.29
beat        0.44   0.47   0.97   2.44
big beat    1.75   1.39   3.36   5.88
```
Big beat (top 20% surprise) vs everything else:
```
+20d  +1.41% vs +0.13%   diff +1.28   t=+2.62
+40d  +3.40% vs +0.04%   diff +3.35   t=+4.28
+60d  +5.88% vs +0.71%   diff +5.16   t=+3.94
```
By year (40d): 2023 +3.2 (n=188) · 2024 +2.0 (191) · 2025 +6.2 (176) · 2026 +2.5 (109).
Positive in every full year.

Sharper cut, **big beat and the market liked it** (day-0 move > +2%): n=263,
+4.6% over 40 days, 57% hit rate, +8.1% over 60 days.

Also visible: "crushed" reactions (day-0 < −7%) bounce +2.3% over 40d — the same
overreaction-reversal we saw in the news data. Weaker, and not the trade.

**Why this survives when momentum didn't:** the buyer is slow. Institutions
reposition over weeks after a surprise; nobody can front-run a process that
takes a quarter. This is the 1989 Bernard-Thomas result still alive in 2026.

**Caveats:** same survivorship bias as above (liquid-today universe), Yahoo
surprise data quality, excess over SPY rather than sector, and costs of ~0.1%
are tiny against +3% but real. Needs: point-in-time universe, more names,
a walk-forward portfolio simulation with position sizing.

## Experiment 3: short-volume squeeze (`exp_shortvol.py`)
FINRA daily short-sale volume share, 1,000 names, 400 days. "Trap" = short share
2σ above its norm + stock up >2% on high volume.

```
horizon  trap_n  trap_excess  high_short_only  baseline  t
5d        414     -0.16         0.15            0.08     -0.54
10d       407      0.73         0.47            0.19      0.92
20d       394      0.99         0.60            0.41      0.73
```
Deciles of short share are flat (0.1–0.4% over 10 days, no pattern). Daily short
*volume* is not short *interest*; it's mostly market-maker hedging. The real
squeeze signal needs FINRA's twice-monthly short interest + borrow cost, which is
a different dataset. Verdict: no edge in this proxy; the hypothesis isn't dead.

## Experiment 4: which company layers sharpen the drift? (`exp_features.py`)
3,461 earnings events, 235 names, with point-in-time fundamentals (Yahoo quarterlies),
dated analyst actions and insider trades attached. Among the 271 big beats with a
positive day-0 reaction (base +4.4% over 40d):

```
feature              n   above  below   diff     t
gm_change           68   +8.81  -2.57  +11.4  +2.31   gross margin expanded last quarter
day0               271   +6.29  +2.47   +3.8  +1.58   size of the day-0 reaction
opm_change          68   +6.51  -0.27   +6.8  +1.35
downgrades_90d     271   +2.33  +5.12   -2.8  -1.08   a downgrade in the prior 90 days hurts
surprise           271   +4.32  +4.42   -0.1  -0.04   beat size adds nothing past the bar
```
Analyst reaction in the 10 days *after* the beat, vs the drift from day 10 to 40:
upgrades → +5.5% (n=44) vs +3.3% (n=227); downgrades → −1.4% (n=12).

Across all 3,461 events: a recent downgrade is the most robust negative (t=−2.8),
margin expansion positive (t=+2.2), high operating margin negative (t=−2.3, mean
reversion). Revenue growth is positive but only 170 events have the data.

**Caveats:** Yahoo keeps only 5 quarters, so fundamentals cover the last ~year of
events (n=68 for margins). Promising, not yet proven at scale.

**What was built from it:** `edge/engine/layers.py`. Every name gets a verdict from
every layer, with these measured effects as the weights. WILL = the event fired and
no layer argues against; MAYBE = the event fired but a layer objects, or no event but
two or more layers stack up; LOTTO = catalyst + crowding. First live result: HPE and
EQT (both fired) were demoted to MAYBE, HPE for two recent downgrades, EQT for a
34-point gross-margin collapse and falling revenue.

## What this means for the engine
The first proven signal is **event-driven, monthly-horizon, long-only**:
big EPS beat → hold 20-60 trading days. It belongs in the Monthly and 6-month
horizons of the Stocks page. The engine should:
1. Pull the earnings calendar daily, flag big beats the morning after.
2. Score each by surprise size and day-0 reaction.
3. Show entry (first close after), target horizon (40d), and the expected +3%
   excess with its 57% hit rate — odds, not certainty.
4. Log every call to the Portfolio page and keep score.

## Next experiments, in order
1. Rebuild PEAD on a point-in-time universe (remove survivorship), add sector
   excess, size by liquidity. Confirm it holds.
2. Revisions drift: analyst estimate revisions after the beat (same slow buyer).
3. Options: gamma pin at expiry and dealer-hedging flow, once the OPRA agreement
   is signed in the Alpaca dashboard (free for non-professionals).
4. Small-cap news lag: LLM sentiment in names under $2B, where news isn't priced in minutes.
