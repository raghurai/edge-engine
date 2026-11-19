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
| Post-earnings drift, big beats (first study) | 234 names, 3,371 events, 2022-26 | +3.4% over 40 days, t=4.3 | ⚠️ **overturned by Experiment 5**: the sample was today's most liquid names, i.e. past winners |
| Post-earnings drift, whole market | 4,067 companies incl. delisted, 6,249 trades, 2020-26 | −0.26% net vs SPY, t=−0.5, ahead in 3 of 7 years | ❌ no edge |
| Gradient-boosted ranking, 1 week | 1.2M stock-periods, walk-forward 2020-26 | top 20 liquid: +0.12%/wk vs SPY, t=0.5 | ❌ no edge |
| Gradient-boosted ranking, 1 month | same | top 20 liquid: +0.85%/mo net vs SPY, t=1.2, ahead 5 of 7 years; bottom tenth −0.98%/mo | ⚠️ a lean on the long side, real on the avoid side |
| Gradient-boosted ranking, 6 months | same | top picks trail SPY | ❌ no edge |
| Odds of a gain: best stock picks vs the index | same | picks gained 54% of months, SPY 68%; 60% vs 85% at 6 months | ❌ nothing beats the index on odds |
| **Index variance risk premium** | VIX vs next-21-day realised vol, 2012-26 | implied above realised on 84% of days, +3.6 pts on average | ✅ **the one measured edge** (sold with defined risk) |

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


## Experiment 5: the whole market, without survivorship (`scripts/brain_data.py`, `brain_train.py`)
Rebuilt the test on data that removes the bias flagged above. Alpaca's free plan serves
consolidated (SIP), split/dividend-adjusted daily bars back to 2016 **including delisted
symbols** (SIVB, FRC, TWTR, BBBY are there), and Nasdaq's calendar has every earnings report
with EPS surprise back past 2012. Panel: 4,770 companies, 2,733 sessions, 106k dated
surprises, 81k tradable earnings events, 1.2M weekly stock-samples. A name must trade
$2M a day to count; "liquid" means $20M. Entry is the NEXT open after the decision close,
exits carry a delisted name at its last price, costs are 6-40bp round trip by liquidity.

The earnings-drift rule (top-20% surprise and a +2% reaction, hold 40 days), on every
qualifying report 2020-2026: **6,249 trades, −0.26% net against SPY, t=−0.5**. By year:
2020 +3.7, 2021 −3.9, 2022 −0.9, 2023 −0.4, 2024 −2.6, 2025 +1.9, 2026 +0.6. In the same
window the average stock lost 0.9% to SPY over 40 days after reporting and only 44% beat it.
The surprise effect exists (best vs worst decile ≈ 1%) but it is a fifth of what the first
study showed. That study's universe was "the 300 most liquid names today": it selected
stocks that had already won. The +4.7% was look-ahead, and the "proven" label came off.

## Experiment 6: can a model rank stocks? (`edge/brain/`, `scripts/brain_lab.py`)
LightGBM on 59 inputs, all backward-looking and shared by training and live scoring
(`edge/brain/features.py`): returns over 1-231 days, 52-week position, volatility, skew,
gaps, close location, liquidity, beta, overnight vs intraday flow, seasonality, industry
context (peer returns, peer earnings), FINRA short interest as published, and earnings
memory (surprise, SUE, reaction, YoY EPS growth and acceleration, streaks, days to next
report). Walk-forward: to predict year Y the model sees only data ending before Y, less a
gap as long as the holding period. Four training targets tried (rank, mean, odds, winner).

```
horizon   rank IC   top 20 liquid vs SPY (net)        bottom tenth vs SPY
1 week    0.031     +0.12%   t=0.5                    −0.10%
1 month   0.049     +0.85%   t=1.2   5 of 7 years     −0.98%
6 months  0.051     −4.1%    t=−1.4                   (model inverted)
```
Screening each input alone finds nothing with t>2 on the long side at one month, in large
or small companies. The model's skill is mostly in naming losers (high-volatility,
illiquid, sold-off names), which a long-only user can use as an avoid list.

Odds of a gain, head to head, same start dates:
```
            SPY                 model's best liquid picks
1 month     68%   +1.4%         54-58%   +1.2 to +2.3%
6 months    85%   +7.8%         ~60%     +4 to +5%
1 year      83%   +15.9%        57-59%   +3 to +9%
```
**Nothing tested beats holding the index on odds of a gain.** Volatile names have the
highest average and the worst typical outcome (a few huge winners); a model trained on
typical outcomes drifts to dull names, one trained on averages to lottery tickets.

## What the engine is now (`edge/brain/live.py`, `contracts.py`)
- **Will make money**: the index funds, with their own measured odds since inception,
  conditioned on market state (above/below the 200-day average). Options: defined-risk
  iron condors on SPY/QQQ (and SPX on its tab) that harvest the variance risk premium.
- **Maybe**: the monthly and weekly model's top liquid names, each shown with what names
  scored like it actually did out of sample. The engine portfolio trades the monthly list:
  a live test against SPY.
- **Lotto**: catalysts ranked by the name's own earnings-move history and short crowding.
- **Contracts**: every listed strike and expiry is priced against the pick's own
  out-of-sample return distribution (implied vol solved from the mid, fat-tailed scenarios,
  spread and decay included); the winner maximises expected log growth. Negative expected
  value is reported, never dressed up. With no stock edge, almost no call passes: that is
  the correct answer, and it stops the most common way retail accounts bleed.

## Still open
1. **SEC filings** (fundamentals point-in-time via XBRL, insider Form 4s): the best free
   inputs not yet in the model. SEC refuses automated tools that don't declare a contact
   address; needs the owner's decision on which address to declare.
2. Analyst estimate revisions (no free history found).
3. Weekly automatic retraining in the cloud (training takes 90 seconds once the panel is
   built; the panel download is about an hour).
4. An option-price history of our own: every export now solves implied vols, so storing
   them daily would let the contract logic be backtested within months.
