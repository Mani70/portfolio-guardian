# Research findings (3 Oct 2026)

Plan and pass criteria were written down first: see PREREGISTRATION.md. Scripts: `swing_research.py`,
`intraday_research.py`. Data: INDstocks, daily Oct 2014 - Oct 2026, 5-minute Jun 2024 - Oct 2026.

## Data problems found and handled
- INDstocks back-adjusts splits/bonuses only partly: boundaries in BAJFINANCE, HDFCBANK, KOTAKBANK, NESTLEIND,
  POWERGRID and TRENT are detected and back-adjusted; ADANIENT's 2015 demerger day is trimmed.
- ETF daily OPEN prices are unreliable (often equal to the previous close): NIFTYBEES "opens" average 0.7%
  above the day's close. Any ETF test that trades at the daily open is invalid; ETF strategies were re-run
  with close fills, and the engine's replay fills ETF opening orders at the close.
- Diwali muhurat sessions and days missing the opening bar are dropped from intraday tests.

## Swing, 2016-2026 (10.75 years, ₹5 lakh, all charges, idle cash at 6%)
| Strategy | CAGR | Worst fall | Sharpe | 1st half | 2nd half | Verdict |
|---|---|---|---|---|---|---|
| Nifty ETF, buy and hold | 11.4% | -36% | 0.82 | 13.9% | 8.9% | benchmark |
| Cross-asset ETF trend (Faber) | 13.5-15.7% | -13 to -16% | 1.38-1.51 | 11.7% | 15.3% | **PASS** (no survivorship bias) |
| Stock momentum, top 10 + market filter | 16.1% (neighbours 13.5-16.2%) | -25% (-20 to -32%) | 1.07 (0.86-1.19) | 18.4% | 13.8% | **PASS** (beats same-stocks hold on Sharpe 1.07 vs 0.97) |
| Equal weight + 200-day filter | 16.7% | -13% | 1.38 | 18.8% | 14.6% | pass, but 49 tiny positions; not used |
| Nifty ETF + 200-day filter | 11.2% | -14% | 1.06 | 13.1% | 9.4% | fails (CAGR below the ETF) |
| Dual momentum (single asset) | 14.5% | -36% | 0.76 | 4.6% | 25.3% | fails (unstable halves) |
| 52-week high, Clenow, low-vol | 10-13% | -21 to -33% | 0.78-0.97 | | | fail (not above same-stocks hold) |
| Minervini, Weinstein, Donchian, 50/200 | 6-10% | -20 to -38% | 0.48-0.74 | | | fail |
| RSI(2) on stocks; leader pullback | -7% / 7% | -58% / -34% | | | | fail (charges) |
| RSI(2) on index ETFs; turn of month | 4-8% | | | | | fail (below Nifty ETF) |

Combined 50% ETF trend + 50% stock momentum: 14.7% CAGR, worst fall -18%, Sharpe 1.31, both halves ~15%,
worst calendar year -10% (Nifty ETF: 11.4%, -36%, worst year -13%). Correlation of daily returns 0.5.

The engine was replayed day by day over the same history (`trader.replay`): ETF trend 12.1% vs research
12.0%, momentum 15.0% vs 14.5% (no interest on idle cash in either), so the code that trades is the code
that was tested.

## Intraday, Jun 2024 - Oct 2026 (548 sessions, 49 stocks + NIFTYBEES/BANKBEES)
No method passed. Best day-clustered t-stat of any method/universe/side: 1.07 (needs 2).
- Opening range breakout (15/30 min, with/without market filter or relative-volume threshold): negative.
- Noise-area momentum (Zarattini/Aziz/Barbon), VWAP trend, late-day momentum (Gao et al.): negative,
  including on the Nifty and Bank Nifty ETFs where costs are lowest.
- Gap fades/continuations, overreaction fades: negative, except buying gap-downs.
- Buying big gap-downs: the 5-minute result came from ~10 market-wide crash days (e.g. 7 Apr 2025).
  Re-tested on 12 years of daily bars (addendum to the plan): stock-specific gap-downs of 3%+ rose 0.7% net
  from open to close on average (t 3.4; about 1% a day in 2016-2023), but averaged about zero in 2024-2026. Kept as a
  PAPER-ONLY strategy (`gap_reversal`) behind the strict intraday gate.
- Long vs short: shorts looked better in some tests only because the Nifty fell during parts of the sample.

## What this means
Costs decide intraday in India: a round trip costs ~0.12% on shares (0.075% on ETFs); the published
intraday edges are a few hundredths of a percent before costs. The edges that survive are slow:
cross-asset trend and momentum with a market filter, traded monthly. Those are what the engine trades.
Survivorship bias flatters every stock result here (today's Nifty 50 members), less so the ETF strategy.

# Addendum (4 Oct 2026): the "5-section analyst report" method
Plan written first (PREREGISTRATION.md, Addendum 2); script `method_research.py`; ₹1.5 lakh, 2016-2026, all charges.
Sections 1 (technical setup) and 5 (structure stop, targets, R:R >= 2, half out at T1, 3-week time stop) were
turned into five rule sets on the 49 Nifty 50 stocks. Sections 2-4 (valuations, debt, flows, earnings dates,
news) need point-in-time data we don't have and were not tested.

| Setup | CAGR | Worst fall | Sharpe | Net per trade (every signal) | t (by week) |
|---|---|---|---|---|---|
| A pullback to EMA50 / Fibonacci, RSI reset, MACD turn | 1.6% | -18.5% | 0.25 | -0.39% | -1.9 |
| B breakout over 60-day resistance, volume, squeeze | 2.7% | -6.4% | 0.63 | -0.88% | -3.0 |
| C Bollinger lower band in an uptrend | 4.7% | -7.8% | 0.85 | +0.12% | -0.4 |
| D MACD cross in an uptrend, volume | 2.7% | -9.3% | 0.68 | -1.06% | -2.7 |
| E 8-point analyst checklist >= 7 | 0.9% | -21.6% | 0.15 | -0.26% | -1.9 |
| Nifty ETF hold / FD / current 50-50 portfolio | 11.4% / 6.2% / 15.3% | -36% / 0 / -16% | 0.82 / - / 1.39 | | |

All five FAIL: every one returns less than a fixed deposit. Before any costs the setups average -0.6% to +0.6%
per trade; the same stop/target plan on RANDOM uptrend days did better (+0.9%), so the indicator signals add
nothing over simply being long a rising Nifty 50 stock. None of 35 neighbouring variants (R:R 1.5/none, 10/20-day
hold, 5/20-day stop, no market filter) reaches the FD; skipping results seasons helps (4.5-5.7%) only by holding
more cash. Adding any setup as a third sleeve lowers the portfolio's CAGR to 10-12% for at most +0.04 Sharpe.
Why: the checks overlap (most fire together in any uptrend), the levels are what every chartist watches, and
delivery charges (~0.5% a round trip at ₹30k) eat a 1-3 week edge. Not used.

# Addendum (4 Oct 2026): can intraday days be picked in advance?
Plan: PREREGISTRATION.md, Addendum 3; script `intraday_days.py`. Groups chosen on the first half, judged on the second.
- ORB 15m, stocks: -11.6 bps a day net overall; the best first-half group of every pre-open feature (gap, prior
  range, NR7, trend, weekday, expiry week, first-bar range, recent P&L) is still negative in the second half.
- ORB 15m, Nifty/Bank Nifty ETFs: same; best second-half group +4.7 bps on 23 days (t 0.5).
- Gap-down reversal (12 years, daily): +44 / +56 bps a day net in the two halves (t 1.8). One feature "passed"
  (Thursday/Friday, t 2.1 on 34 days) - with nine features tried that is expected by chance and has no reason
  behind it, so it is not used.
Conclusion: nothing known before the open reliably picks good intraday days. The engine already trades intraday
only when its signal fires (gap-reversal: a few days a month) and keeps that strategy on paper.

# Addendum (4 Oct 2026): exits between the monthly decisions
Plan: PREREGISTRATION.md, Addendum 4; script `exit_research.py`; ₹1.5 lakh, 2016-2026. Sharpe shown as full (1st/2nd half).

| Exit rule checked daily | ETF trend: CAGR / worst fall / Sharpe | Momentum: CAGR / worst fall / Sharpe |
|---|---|---|
| none (current: monthly decisions only) | 13.5% / -13.0% / 1.39 (1.29/1.49) | 15.7% / -25.0% / 1.06 (1.16/0.96) |
| trailing stop 10% / 15% / 20% | 12.7-13.1% / -12 to -15% / 1.39-1.45 | 12.6-15.4% / -25 to -26% / 0.96-1.05 |
| fixed stop 8% / 12% | 13.4% / -13% / 1.39 | 15.7% / -21.4% / 1.09 (1.14/1.03); 15.2% / -24% / 1.04 |
| take-profit 25% | 14.0% / -13.1% / 1.59 (1.45/1.73) | 14.0% / -24.7% / 1.05 |
| trend exit daily (ETF: below 200-day; momentum: market filter) | 12.7% / -11.7% / 1.41 (1.21/1.60) | 12.3% / -29.8% / 0.89 |
| stock below its 100-day average (momentum) | | 14.4% / -23.1% / 1.04 |

No stop or faster exit passes: each costs return or loses in one half (the 8% stop on momentum cuts the worst
fall to -21% but is worse in the first half). Checking the market filter daily is clearly worse than monthly
(whipsaw). Take-profit looked good on ETF trend, but its neighbours (15-40%) disagree on CAGR; the reason is
that it trims winners back toward equal weight. Tested directly (exploratory, not pre-registered): rebalancing
the held ETFs back to 25% each month when one drifts more than 10/20/30% gives 14.0-14.1% CAGR, -12.4 to -13.0%
worst fall, Sharpe 1.48-1.50 with BOTH halves better (1.33-1.38 / 1.61-1.63) - this is how Faber's published
model works. Planned for the engine before the 30 Oct rebalance, after the first live fills are verified.

# Addendum (4 Oct 2026): managing an existing stock portfolio (the owner's 13 stocks)
Plan: PREREGISTRATION.md, Addendum 5; script `holdings_research.py`. 2,000 random 13-stock Nifty 50 portfolios
bought at a random month 2016-2023, 30 months, sold at the end; costs and STCG 20% / LTCG 12.5% (₹1.25 lakh
exempt) included. Median and worst-10% are annual after-tax returns; drawdowns are monthly.

| Rule (cash idle at 0% / parked at 6%) | Median | Worst 10% | Typical fall | Worst-10% fall | 2016-19 / 2020-23 starts (median) |
|---|---|---|---|---|---|
| Hold everything | 19.5% | 8.1% | -16% | -30% | 16.8% / 21.5% |
| Sell below 200-day avg, buy back above (150/250, ±3% band) | 11.8-14.4% | 2.0-5.4% | -10 to -14% | -17 to -21% | 9.5-13% / 13.6-16.3% |
| Whole portfolio to cash when Nifty < 200-day | 15.6 / 16.7% | 5.4 / 6.3% | -11% | -16% | 12.5-13.3% / 17.4-18.7% |
| 25% trailing stop | 16.3 / 16.7% | 3.5 / 4.4% | -15% | -27% | |
| Momentum replacement (top 25 of 49) | 24.3% | 7.4% | -23% | -29% | 20.2% / 26.9% |
| Momentum replacement + market filter | 22.0 / 23.3% | 8.4 / 9.3% | -12% | -15% | 14.7-15.5% / 26.1-27.4% |

The owner's 9 Nifty 50 names over every 30-month window: hold 16.5% (worst 10%: 5.6%, worst-10% fall -29%);
momentum + market filter 21.0-22.3% (8.2-9.1%, -17%), 2016-19 starts about equal to holding (16.4-16.8% vs 16.6%).
Verdict against the pre-registered bar: trend exits, market timing and trailing stops all FAIL - after tax they
cost 3-8 points a year for less drawdown. Momentum + market filter comes closest: higher median, better worst
case, half the drawdowns - but for 2016-19 starts its median trailed holding by 1.3-2.1 points (bar: <= 1), so
it FAILS narrowly. Both are flattered by survivorship (today's Nifty 50). Decision left to the owner.

# Addendum (4 Oct 2026): what happens after a single-stock crash
Nifty 50 stocks, one-day fall with the Nifty down less than 1% (company-specific), 2015-2026, return vs Nifty:
| Fall | Events | Next day | 5 days | 20 days | 60 days |
|---|---|---|---|---|---|
| -5% to -8% | 388 | +0.1% | +0.4% | +1.4% (t 2.4) | +2.0% |
| -8% to -12% | 67 | -1.2% (t -2.5) | -0.1% | +0.2% | +3.2% |
| worse than -12% | 23 | -2.7% (t -2.0) | +1.4% | +0.3% | +2.6% |
Selling automatically on a price shock mostly locks in the loss; only the day after an 8%+ fall tends to be
worse. The disasters where selling matters (fraud, insolvency) left the index and are missing here, and price
alone can't tell them apart from overreactions. So the engine alerts within 5 minutes (`trader.run watch`)
and leaves the decision to a person reading the news.

# Addendum (4 Oct 2026): institutional strategies adapted to this account (CTA trend, risk parity, pairs)
Plan: PREREGISTRATION.md, Addendum 6; script `other_strategies.py` -> `other_strategies.csv`. ₹4.2 lakh, 2016-2026,
costs included, idle cash 6%. Sharpe shown as full (1st/2nd half). Overseas futures, FX margin, short selling and
short options can't be used by a resident through LRS, so each brief was rebuilt from what is listed in India.

| Strategy | CAGR | Worst fall | Sharpe |
|---|---|---|---|
| NIFTYBEES, buy and hold | 11.4% | -36.3% | 0.82 (0.89/0.76) |
| Current ETF trend strategy (the bar) | 13.5% | -13.0% | 1.40 (1.30/1.49) |
| CTA 50/200 cross + ADX > 25, 1%/ATR sizing, 10 ETFs | 5.7% | -22.7% | 0.58 (0.27/0.84) |
| same, neighbours (ADX 20 / 30, 10-day cross, "state" entry) | 6.2-10.6% | -17 to -25% | 0.64-0.86 |
| CTA, 49 Nifty 50 stocks | 6.6% | -42.0% | 0.43 (0.52/0.31) |
| same, best neighbour ("state" entry) | 16.5% | -29.1% | 0.87 (1.12/0.54) |
| Risk parity NIFTYBEES/MON100/GOLDBEES: equal weights | 17.9% | -22.5% | 1.52 (1.44/1.59) |
| inverse 3-year volatility | 16.3% | -21.5% | 1.52 (1.44/1.60) |
| equal risk contribution (3-year covariance) | 16.1% | -21.0% | 1.53 (1.43/1.62) |

Pairs, Nifty 50 (correlation > 0.85 over 2 years, |z| > 2.5 vs 60 days): 6,400 trades, +0.15% average net per
trade, halves -0.20% / +0.27%, t 0.65 (by entry week). Every neighbour (z 2.0 / 3.0, correlation 0.80 / 0.90) also
loses in the first half; best t 1.43.

Verdicts against the pre-registered bars:
- CTA trend FAILS in every form. The 50/200 cross is slow on ETFs (enters late, gives back a lot before the exit)
  and the ADX filter skips many of the best trends; on stocks the "state" variant earns 16.5% but with a -29% fall
  and a second-half Sharpe of 0.54. The existing monthly 200-day ETF trend rule is simply better.
- Pairs FAIL: the edge is about zero after costs and changes sign between halves. Even a pass could not be traded
  here (a Nifty 50 stock-futures lot is several lakh per leg).
- Risk parity PASSES the bar narrowly (Sharpe higher in both halves) but with clear warnings. Exploratory checks,
  not pre-registered: without MON100 the same method gives 13.1% / -21.0% / Sharpe 1.28 (1.19/1.37), below the bar -
  the edge rests on one exceptional decade for the Nasdaq 100 plus a weakening rupee. Its worst fall (-21%, March
  2020) is 60% deeper than the trend strategy's (-13%), the data starts in 2014 so 2008 is not tested, there are no
  bonds, and MON100 has traded well above its NAV when overseas fund limits were hit. Two sleeves (half trend, half
  equal-risk, never rebalanced) gave 14.9% / -16.5% / 1.52 (1.44/1.61) - diversification across the two methods,
  also exploratory. Year by year the two alternate: trend better in 2016 (risk parity needs 2 years of data and was
  partly in cash), 2020, 2021 and 2024; risk parity in 2017-2019, 2022-2023 and 2025.
- Decision: no change to the live strategies. Risk parity is a candidate; testing it live first needs the
  rebalance-to-target-weights code that is planned for the ETF strategy anyway.

## Addendum 8: the day of the month (5 Oct 2026; pre-registered, research/rebalance_day.py)

Both live strategies, unchanged except for the review day, 1 Jan 2016 - 1 Oct 2026 (halves split 24 May 2021), Rs 5L,
same costs as the main research. 25 review days: the 1st-20th session of every month and the 5th-last to last.

| | live day (last session) | best day | worst day | average of the 25 days |
|---|---|---|---|---|
| Momentum: CAGR | 16.1% | 17.8% (5th session) | 9.5% (14th) | 13.3% |
| Momentum: worst fall | -25.0% | -21.3% | -42.5% (15th) | -31.6% |
| Momentum: Sharpe | 1.07 (6th of 25) | 1.18 | 0.64 | 0.89 |
| ETF trend: CAGR | 14.4% | 15.5% (7th) | 11.2% (4th-last) | 13.2% |
| ETF trend: worst fall | -13.1% | | -25.8% | -19.1% |
| Live split 82/18: CAGR / worst fall | 15.8% / -21.9% | | | 13.3% / -28.5% |

Verdict against the bar: no block of days beats the last session by 0.10 Sharpe in both halves (none does even
once): the live day stays. Twice a month (last + 10th session) beat monthly in both halves (momentum Sharpe
1.33/1.07 vs 1.18/0.96, 18.1%, -20.5%), so it passes the letter of the frequency bar; every two weeks and weekly
do not (momentum), and see the exploratory check below.

What the spread means: the review day changes the result by up to 8 points a year and the worst fall from -21% to
-43%, with no economic reason to prefer any day. Much of it is a few months: in March 2020 the live day returned
+1.9% and the day before it -20.6%. On Thu 27 Feb 2020 the Nifty ETF closed at 123.64 against a 200-day average of
123.63 (stay invested); on Fri 28 Feb it closed at 119.29 (go to cash before the March crash). The live rule's
16% includes that coin-flip. The honest expectation for the live split is the average over days, about 13% a
year, with a worst fall nearer -30% than -22%. Without the 200-day filter the spread is as large (10.1%-18.5% on the 4 days checked), so
stock selection is also timing-sensitive.

Exploratory (not pre-registered; asked after seeing the above; research/rebalance_day.py --explore,
rebalance_day_explore.csv): momentum at the live size (Rs 3.45L, DP Rs 21.83 per sell), every starting session k:

| family | CAGR mean (range) | worst fall mean (range) | Sharpe mean (range) | halves (mean) |
|---|---|---|---|---|
| one portfolio, monthly | 13.4% (9.3-17.8) | -30.8% (-42.7 to -21.9) | 0.90 (0.63-1.18) | 0.99 / 0.80 |
| 2 tranches (half each, sessions k and k+10) | 13.5% (11.5-15.3) | -27.7% (-34.3 to -22.2) | 0.93 (0.80-1.06) | 1.02 / 0.84 |
| 3 tranches (a third each, k, k+7, k+14) | 13.4% (12.7-13.9) | -27.0% (-29.5 to -24.8) | 0.94 (0.90-0.97) | 1.03 / 0.85 |
| whole portfolio twice a month (k, k+10) | 14.9% (12.6-19.5) | -26.7% (-35.1 to -21.2) | 1.01 (0.86-1.29) | 1.22 / 0.80 |

- Tranches do not raise the average return; they remove the luck: with 3 tranches every start day gives 12.7-13.9%
  and a worst fall of -25% to -30%, instead of 9-18% and -22% to -43%. Costs are included.
- Twice a month is better on average only in the first half (1.22 vs 0.99) and equal in the second (0.80 vs
  0.80), with more trades: its pass in the main test is mostly the lucky last session. Not adopted.
- Decision: the live day stays (pre-registered bar). Splitting momentum into 3 monthly tranches is a candidate
  for reducing risk, not for more return; it would be pre-registered and run on paper before any live change.

## Addendum 9: momentum in 3 tranches (5 Oct 2026; pre-registered; research/rebalance_day.py --tranches)

(1) Historical, exactly the proposed configuration, Rs 3.45L, DP Rs 21.83, idle cash earning 6% as in all research:

| | CAGR | worst fall | Sharpe (halves) | trades | costs |
|---|---|---|---|---|---|
| 3 tranches (7th, 14th, last session) | 13.9% | -26.2% | 0.97 (1.10 / 0.84) | 1,030 | Rs 1.01L |
| control: one portfolio, last session (live rule) | 15.9% | -25.0% | 1.07 (1.17 / 0.96) | 350 | Rs 0.75L |
| tranche on the 7th alone | 15.8% | -28.4% | 1.05 | 332 | |
| tranche on the 14th alone | 9.1% | -36.5% | 0.63 | 348 | |
| tranche on the last session alone | 15.6% | -25.4% | 1.05 | 350 | |

As expected: CAGR within 1 point of the single-day average (13.4%), worst fall above -30%. The control's lead is
its lucky day (2020: +39% vs +15%, the March 2020 exit). Costs are higher (smaller, more frequent orders: about
0.7% of capital a year more) and are inside the returns above.

(2) Engine replay (trader.replay, the code that runs on paper and live), 2016 - 1 Oct 2026: tranches 12.35%,
control 14.86% (tranches 13.95% / 7.44% / 14.41%). Against (1) that is 1.5 points lower: the pre-registered
"within 1 point" is NOT met as written. Cause found: the research credits idle cash with 6% a year; the engine (and
the real account) earns nothing on it, and momentum is in cash about 23% of the time. Research rerun with no
interest on cash: tranches 12.30% (replay 12.35%), control 14.42% (14.86%), each tranche within 0.4 point. So the
engine does what the research does; the gap is the cash interest.
Consequence for the live strategy, not only the tranches: real returns are about 1.5 points a year below the
research figures while idle cash stays idle. Parking it in a liquid ETF (LIQUIDBEES) when a strategy holds cash
would recover most of that; not built - a separate decision.

(3) Paper run: started Oct 2026 (trader/experiments.yaml, journal mode "lab"). First reviews: tranche A Mon 12 Oct,
tranche B Thu 22 Oct, tranche C and the control Fri 30 Oct (NSE holidays 2 and 20 Oct counted).

## Addendum 10: parking idle cash in a liquid ETF (5 Oct 2026; pre-registered; research/cash_parking.py)

2016 - 1 Oct 2026, halves split 24 May 2021. Average parking yield (approximate overnight-rate path minus the
0.23% expense ratio): 5.07% a year. "Live timing" = a buy fills one session later when its money was not free at
the evening it was decided (today's engine).

| Momentum (Rs 3.45L) | CAGR | worst fall | Sharpe (halves) | park trades |
|---|---|---|---|---|
| research assumption: idle cash earns 6% | 15.9% | -25.0% | 1.07 (1.17 / 0.96) | |
| (a) idle cash earns nothing, live timing (today) | 14.9% | -27.0% | 1.02 (1.16 / 0.86) | |
| (b) parked, live timing | 15.9% | -27.4% | 1.07 (1.22 / 0.92) | 31 |
| (b) parked, rate 1 point lower | 15.7% | -27.8% | 1.06 (1.20 / 0.91) | 31 |
| (b) parked, flat 4% | 15.7% | -27.6% | 1.06 (1.20 / 0.91) | 31 |
| (c) parked, research timing | 15.6% | -25.7% | 1.04 (1.15 / 0.93) | 31 |

| ETF trend (Rs 0.76L) | CAGR | worst fall | Sharpe (halves) |
|---|---|---|---|
| (a) idle cash, live timing | 12.8% | -14.4% | 1.32 (1.22 / 1.41) |
| (b) parked, live timing | 12.7% | -13.9% | 1.32 (1.23 / 1.39) |
| (c) parked, research timing | 13.6% | -13.4% | 1.39 (1.28 / 1.49) |

Verdicts: momentum PASSES (+0.96 point a year, +0.75 with a 1-point lower rate; Sharpe higher in both halves;
worst fall 0.3 point deeper, within the 1-point bar). Parking adds about Rs 760 a year in charges here (31 park
trades in 10.75 years). The ETF trend does NOT pass: its slices are small (Rs 19k each, so it parks only when two
or more are in cash) and re-entering a day late, after selling the parked ETF, costs about as much as the yield
earns. Momentum's live timing (one-day delay on buys funded by sells) happens to be slightly better than research
timing here - timing luck, not a reason.
Decision: run parking for momentum on paper (lab) beside the unparked control; no parking for the ETF trend.
Engine check (trader.replay, paper fills, a synthetic LIQUIDCASE price accruing at the same rate path; Rs 3.45L,
max_order_value lifted for the replay because 10 years of compounding takes a position past the live Rs 1.5L cap):
control 14.69%, parked 15.87% (+1.18 point; 18 park round trips, park profit Rs 63k), against +1.19 in the
research code with the same (research) timing. Paper charges for the ETF use liquid-ETF costs (no STT, 0.02%
slippage). After an independent review the engine also: never shrinks a buy while the strategy's cash is in the
ETF (it waits), keeps the review's buys and places exactly those the next evening (no re-ranking), sells the ETF of
a demoted strategy, does not park when a review is due within 2 sessions or after `trader.run cancel`. The first replay with the Rs 1.5L cap left in had blocked late-period buys of the larger,
parked portfolio - a replay artifact, not a live risk at today's size (Rs 34,500 per stock).
(Addendum 9's control replay, 14.86%, had the same cap in it; without the cap the control replays at 14.69%, against 14.42% in the research code. The tranches, a third of the money each, never reached the cap.)

## Addendum 7: fundamentals, flows, options and events (6 Oct 2026; pre-registered; research/addendum7.py)

Data: NSE archives Oct 2014 - Oct 2026 (research/download_nse.py): FII/DII positions, every index's close with
P/E, F&O open interest per symbol, delivery %, results dates (142k board-meeting notices), insider trades (341k
PIT disclosures, to May 2026). Bulk deals: NSE refused the API, so D3 is untested. Test 2016 - 1 Oct 2026, halves
split 24 May 2021, momentum Rs 3.45L / ETF trend Rs 0.76L, same costs and code as the live strategies. An independent
audit of the script found no look-ahead; it found a wrongly joined low-volatility index (fixed; C1 re-run) and
that the script's "PASS" flag only checks Sharpe in both halves and the worst fall - the verdicts below apply the
full pre-registered bars (neighbours, spread t-stats, event horizons).

| Rule | Result (momentum unless noted) | Verdict |
|---|---|---|
| Baselines | momentum 15.9% / -25.0% / Sharpe 1.07 (1.17/0.96); ETF trend 14.3% / -13.2% / 1.45 | |
| A1 Nifty P/E top 20% of 5 years = out (testable from Nov 2019) | 11.3% vs 21.7% with the 200-day switch; alone 4.5%. Expensive months were followed by HIGHER Nifty returns (6m +15% vs +6%, n=13, t -0.8) | FAIL |
| A2 FII index-futures positioning, contrarian | as the switch 2.4%; as an override 12.1% (worst fall -35%) | FAIL |
| A3 Nifty put-call ratio, contrarian | switch 12.6%; override 16.5% / -25.1% (Sharpe 1.20/0.98 vs 1.17/0.96, fall 0.1 deeper; 30/70 passes, 10/90 not) | FAIL |
| B1 skip picks whose price rose while futures OI fell >10% | 18.0% / -17.7%, but filtered-out picks did as well as kept ones next month (+0.11%, t 0.16); 13 review days: better on 8/13, both halves 5/13 | FAIL (spread) |
| B2 skip picks with falling delivery % | 14.4% (bottom third); spread -0.24%, t -0.47 | FAIL |
| B3 promoter buying +5 ranks / selling -5 | 16.6%; promoter-bought top names did WORSE next month (-0.65%, t -1.1) | FAIL |
| C1 trend rule on factor indices (quality, low-vol, alpha low-vol, value) | 8.0% / -15.1% vs live basket 14.3% (price indices lack ~1.3%/yr dividends; the gap is far larger) | FAIL |
| C2 pre-registered: momentum only while Quality 30 > its 200-day (instead of Nifty) | 15.5%, second-half Sharpe 0.90 vs 0.96 | FAIL |
| C2 variant: Quality 30 > 200-day AND Nifty > 200-day | 16.6% / -23.1% / 1.14 (1.23/1.05): passes; 150-day neighbour passes (18.7% / -16.9%), 250-day just fails (0.95 vs 0.96) | NEAR-PASS |
| D1 results-day jump >+5%, next 20/60 days (49 stocks) | +0.18% / +2.19% (t 1.71); +4% passes at 60 days, +6% not | FAIL |
| D1, negative reactions | no reliable drift | FAIL |
| D2 promoter/director open-market buying, next 20 days (49 stocks) | +1.17% (halves +1.07/+1.60, t 2.63, n=757); 60 days fails (second half +0.19%) | FRAGILE (below) |
| D3 bulk deals / E news | no data / no historical archive | NOT TESTED |

Robustness (exploratory, after the main run; Addendum 8 showed the review day alone moves momentum by up to 8 points):
each near-pass re-run on 13 review days against the baseline on the same day.
- Quality 30 trend AND Nifty 200-day: better Sharpe on 13/13 days (150-day average; 10/13 with both halves), CAGR
  +2.7 points (range -0.5 to +8.3); 100-day +3.5, 175-day +1.8, 200-day +1.2 (10/13), 250-day +0.2 (8/13, both
  halves 3/13). On the pre-registered Nifty200 Quality 30 (available only from Nov 2018): +2.8, 13/13.
  Controls: a faster Nifty filter alone (150-day) adds nothing (-0.1); Nifty 100-day AND 200-day adds +1.3 - so about
  half of the gain is "more time in cash" (invested 67% of month-ends vs 78%), half is specific to quality stocks
  weakening first. Caveats: the gain comes from 4-5 episodes (2018, 2020, 2022, 2026; 2017 and 2019 were worse), and
  the index history before 2018 is NSE's predecessor names (joined without level jumps; checked).
- PCR override: 9/13 days, both halves 6/13, +0.5 point. B1: 8/13, 5/13, +0.3. B2: 7/13, 2/13, -1.0. B3: 5/13, 1/13.
- Events on a wider universe (every F&O stock 2016-26 priced by its near-month future, incl. those that left the
  list; research/addendum7_fo_events.py): D2 promoter buying: other F&O stocks +1.37% in 20 days (t 1.81), all
  +0.94% (t 2.15), but the 49 stocks priced this way +0.40% (t 0.00) - sensitive to the universe and entry timing.
  D1 results-day jump >+5%: +1.23% in 20 days (t 2.73, n=855), but +4% (t 1.55) and +6% (t 1.82) don't confirm,
  and 60-day results change sign between halves.

Conclusions:
- Valuation (P/E), FII positioning, options PCR, futures OI, delivery % and insider trades do NOT improve the live
  strategies. The momentum and 200-day rules already contain what these add, or they add noise; several made
  results clearly worse (valuation timing would have kept you out of 2020-24).
- One candidate: a second trend check on the Quality 30 index (sell to cash when quality stocks fall below their
  150-200-day average, even while the Nifty is above its own). It cut the worst fall from -25% to -17% and won on
  every review day tested, but it rests on a handful of episodes and was not the pre-registered form. Next step if
  wanted: a paper run beside the control (needs the bot to read the Quality 30 index each evening).
- Post-results and post-promoter-buying drift: suggestive (+1% a month after big positive surprises / promoter
  buys) but not robust across thresholds, universes and horizons. Not adopted; it would be a separate event
  strategy needing survivorship-free price data.
- News and sentiment can't be tested historically; nothing in this data suggests a reaction rule would beat waiting.

## Addendum 12: 20 years, survivorship-free (8 Oct 2026; pre-registered, with 12a/12b data fixes; research/history20.py)

Data: NSE bhavcopy 3 Jan 2005 - 6 Oct 2026 (5,372 sessions, 4,015 symbols); universe = each month's 50 most traded
stocks (no survivorship bias); 1,311 bonuses, splits and demergers applied from NSE's own corporate-action files.
Not the "previous close" method Addendum 12 planned: NSE does not adjust it (12a). The pre-2010 detection rule
FAILED its validation (recall 73.4%, precision 97.1%, bar 90%/90%: big ex-date moves push it outside its band), so
as pre-registered **period A is Jan 2011 - Dec 2015** (5 years, not 10; 2008 is not in the test). B = 2016 - Oct 2026.
No dividends anywhere (prices only; about 1-1.5% a year understated, the same for every line).

| CAGR / worst fall / Sharpe | A: 2011-2015 | B: 2016-2026 |
|---|---|---|
| NIFTYBEES held (momentum benchmark) | 5.3% / -25.4% / 0.41 | 11.5% / -36.3% / 0.83 |
| Momentum, live rule (top 10 of the 50 most traded) | 4.0% / -22.0% / 0.35 | 8.4% / -31.1% / 0.55 |
| neighbour: top 10 of the 100 most traded | 14.3% / -18.0% / 0.91 | 11.8% / -33.4% / 0.71 |
| (a) 3 tranches | 1.6% / -16.7% / 0.19 | 7.4% / -34.4% / 0.51 |
| (b) + Quality 30 check | 4.0% / -22.0% / 0.35 | 9.9% / -23.3% / 0.65 |
| live rule with the engine's buy delay | 3.5% / -23.7% / 0.32 | 7.9% / -31.9% / 0.53 |
| (c) idle cash parked (rate 1 point lower) | 6.0% / -22.6% / 0.49 | 8.7% / -31.7% / 0.57 |
| Hold all four ETFs (ETF trend benchmark) | 13.3% / -7.3% / 1.25 | 19.5% / -26.2% / 1.31 |
| ETF trend, live rule | 9.5% / -6.9% / 1.12 | 12.7% / -14.3% / 1.27 |

Verdicts against the pre-registered bars:
- **Momentum (live rule): FAILS** in A (Sharpe 0.35 vs 0.41). It also trails the Nifty ETF in B. Without the
  survivorship bias of today's Nifty 50 list (Addendum 10: 15.9% a year in 2016-2026), the same rule makes 8.4%.
  The result also swings with the universe size (top 100: 14.3% in A, 11.8% in B), so even its sign is not settled.
  (a) tranches: not adopted. (b) Quality 30 check: not adopted (identical in A; better in B, Sharpe 0.65 vs 0.55).
  (c) parking passes the variant bar against the live rule (Sharpe 0.49 vs 0.32 in A, 0.57 vs 0.53 in B), mostly
  from 7-9% overnight rates in 2011-2015 while momentum sat in cash; it still trails holding NIFTYBEES in B.
- **ETF trend (live rule): FAILS** in A (Sharpe 1.12 vs 1.25; worst fall -6.9% vs -7.3%). In B it gives up 7 points
  a year against holding the four ETFs for a much smaller worst fall (-14% vs -26%), Sharpe about equal.
- **Intraday gap reversal (the paper rule): not traded** - positive in A and B but negative in the last 3 years
  (-0.09% a day, t -0.4), as the 5-minute research found. Neighbours (2%, 4% gaps): the same.
- **Gap fade both ways (1%+ gaps, stop 0.5 ATR, top 10): passes the bar on paper, NOT credible.** +0.14% a day
  (t 3.2) in the last 3 years from daily bars, but the 5-minute data for Jun 2024 - Oct 2026 gives -0.07% a trade
  (t -1.2) for gap fades. Daily bars assume a fill at NSE's official open (the pre-open auction price) and cannot
  tell whether the stop or the close came first; 0.5 ATR stops make that matter. Not to be traded without a
  5-minute test of this exact rule.

Conclusion: by the bars fixed before the data was seen, no strategy family passes. Read plainly, 2011-2026 says
holding the Nifty ETF (or the four ETFs) did as well as or better than either live rule after costs, and the
earlier 15-16% momentum figure came from testing on today's winners. Per Addendum 12 no rulebook is written and
nothing is switched to live on this evidence; what to do with the paper strategies is the owner's decision.

## Addendum 13: the final rules per horizon (8 Oct 2026; pre-registered 13/13a; research/allocation20.py)

Data: NSE total-return indices from niftyindices.com (dividends included; checked against NSE/AMC factsheets:
Nifty 50 13.6%, Nifty 200 13.9%, Nifty200 Momentum 30 18.8% a year Apr 2005 - Jun 2026, exactly as published), ETF
expense ratios deducted, GOLDBEES (from Mar 2007) and MON100 (from Apr 2011) bhavcopy prices adjusted as 12a/12b,
liquid ETF at the overnight rate minus 0.23%. Decisions at a month's last close, traded at the next close, ETF costs.
A = 2006-2015 (out of sample), B = 2016 - Sep 2026. Rs 4.2L.

| CAGR / worst fall / Sharpe | A: 2006-2015 | B: 2016-2026 | turnover |
|---|---|---|---|
| Nifty 50 TRI held (benchmark) | 12.1% / -59.5% / 0.59 | 11.4% / -38.3% / 0.77 | - |
| M1: Nifty200 Momentum 30 ETF held | 17.2% / -67.8% / 0.76 | 15.8% / -34.0% / 0.87 | - |
| M2: M1 + 200-day brake | 20.1% / -42.3% / 1.00 | 11.1% / -34.0% / 0.74 | 317%/yr |
| (neighbours: 150 / 250 days) | 20.3% / 18.3%, Sharpe 1.03 / 0.91 | 10.6% / 9.8%, Sharpe 0.72 / 0.65 | |
| **L1: Nifty 50 45 / Next 50 15 / MON100 20 / gold 10 / liquid 10** | **14.9% / -48.1% / 0.85** | **15.2% / -27.4% / 1.26** | 16%/yr |
| L2: L1 with Momentum 30 15 (Nifty 50 30) | 15.8% / -50.2% / 0.90 | 15.8% / -27.0% / 1.27 | 16%/yr |
| reference: L1 never rebalanced | 12.1% / -57.2% / 0.64 | 11.5% / -35.1% / 0.81 | |
| reference: L1, 5-point bands only | 15.1% / -48.7% / 0.86 | 15.1% / -28.0% / 1.23 | 13%/yr |

Verdicts against the pre-registered bars:
- **Intraday: no real money** (decided on evidence; SEBI FY23: 71% of individual intraday traders lost money).
- **Momentum: M1 PASSES** - the published index ETF beat the Nifty 50 on return and Sharpe in both periods (+5.1 and
  +4.4 points a year), unlike the DIY rule of Addendum 12. Its 2008 fall was deeper (-68% vs -60%). **M2 FAILS**: the
  brake helped in 2006-2015 (it dodged 2008) and hurt in 2016-2026 (whipsaws; it trades 3x a year's value).
- **Long-term core: L1 PASSES** - higher Sharpe and a shallower worst fall than the Nifty 50 in both periods.
  **L2 FAILS by 0.1 point** (higher Sharpe in both, but its 2008 fall was 2.1 points deeper than L1's; the bar was 2).
- Rebalancing earns its keep: the same mix never rebalanced makes about what the Nifty does (A 12.1%, B 11.5%);
  rebalanced it makes 3-4 points a year more with a shallower fall. Year-end rebalancing on top of the bands adds a
  little (Sharpe 1.26 vs 1.23 in B).

Caveats: MON100 (Nasdaq 100 in rupees) had an exceptional decade and carries 20%; it has traded above its NAV when
India's overseas-investment limits were hit (buying at a premium is a cost the backtest only partly sees). The
2008-type fall of an 80%-equity mix is still about -48%. Taxes: each rebalance can realise gains (equity 20% short
term, 12.5% long term above Rs 1.25L a year); turnover is 16% of the portfolio a year.

Outcome (as pre-registered): the live core becomes L1 at the 30 Oct 2026 review, replacing trend_allocation and
momentum_rotation, once an engine replay over the 20 years matches this within 1 point a year. The momentum ETF is
not added as a second live strategy (one live strategy, one set of rules).

Engine replay (research/replay_core.py: the real CoreAllocation strategy, Engine._allocate, risk checks and paper
broker with real costs, on the same sleeve prices, Dec 2005 - Oct 2026): A 14.66% / -48.4% (research 14.91% /
-48.1%), B 15.17% / -27.4% (research 15.24% / -27.4%): within 1 point a year, as pre-registered. 168 orders, 77
partial sells over 20 years. The first replay found a real bug before any money was involved: the bonus/split check
compared a position's AVERAGE cost with the price on its opening day, which fails once a position is topped up over
years; it "found" splits during the 2020 crash and inflated share counts (replay 46% a year). Topped-up positions now
carry their last fill as the check's reference (corporate.ca_ref; regression test in tests/test_allocation.py).

## Addendum 15: the daily insights ideas over 15 years (8 Oct 2026; pre-registered; research/insights20.py)

The BUY/AVOID rules of trader/insights.py, unchanged, screened on every session of Jan 2011 - Sep 2026 (3,867 days;
survivorship-free EQ panel, official corporate actions). The rolling matrices matched insights.facts() itself on 5
sample days (largest gap 5e-13). Liquid stocks a day (≥ ₹10 cr traded): median 142 in A, 339 in B. BUY ideas on 73%
of days (5 a day), the watchlist on 26% (the market filter), AVOID ideas on 98%. Bought at the next close, held 20
sessions, against the Nifty 50 price index over the same closes (no dividends on either side); one observation per
idea day, Newey-West t.

| per 20 sessions | A: 2011-2015 | B: 2016-2026 |
|---|---|---|
| BUY ideas (gross) / Nifty 50 | +2.52% / +0.24% | +1.74% / +0.62% |
| BUY vs Nifty 50, after costs | **+1.90%, t 3.69** | **+0.75%, t 1.54** |
| BUY vs any liquid stock (gross) | +2.30%, t 4.32 | +0.74%, t 1.80 |
| BUY hit rate (beat the Nifty after costs) | 55% | 49% |
| Watchlist (BUY blocked by the market filter) vs Nifty, after costs | -0.54%, t -0.66 | +0.27%, t 0.34 |
| AVOID ideas vs Nifty 50 (gross) | -0.02%, t -0.04 | +0.03%, t 0.07 |

Verdicts against the pre-registered bars:
- **BUY has an edge: FAIL.** It cleared the bar in 2011-2015 and missed it in 2016-2026 (t 1.54 after costs; 2.30
  before). The edge shrank by more than half in the later decade and costs take a third of what is left.
- **BUY beats buying any liquid stock: FAIL** (t 1.80 in B).
- **AVOID is a useful warning: FAIL.** Over 20 sessions the AVOID stocks did exactly what the Nifty did (excess 0.0%
  in both periods): being weak and near the 52-week low did not predict a further fall.
- Not under a bar (reported only): over 60 sessions BUY ideas beat the Nifty by +4.4% (t 2.61) and +3.8% (t 2.43)
  after costs, and AVOID ideas lagged it by 1.8% in B (t -1.78). The market filter looks right: the would-be BUY
  ideas it blocked did no better than the Nifty. These are observations after the fact, not passes: claiming them
  would need a new addendum tested on data not yet seen (the report's own live record is that data).

Outcome (as pre-registered): rules unchanged; the report's footer now says the 20-year test did not prove an edge
for either list, with these numbers. Nothing trades on the ideas; the autopilot rulebook is untouched.

## Addendum 16: Dalio's All Weather, risk parity with bonds, and a valuation tilt (8 Oct 2026; pre-registered; research/allweather16.py)

Nifty 50 today (8 Oct 2026): P/E 19.0 (34th percentile of daily values since 1999), P/B 2.73, dividend yield 1.24%
(37th percentile): middle of its range, neither cheap nor expensive. RBI's yield history (data.rbi.org.in) is reachable
but its data service takes only payloads encrypted inside its web app; that was not reverse-engineered, so bonds
start Oct 2015 (NSE's G-Sec total-return indices), as pre-registered.

Measuring stick: on Sharpe over cash (not over zero) L1 still beats the Nifty 50 in A (0.51 vs 0.33) and B (0.83 vs
0.44). Re-run of the yearly re-test's Test 2 for every year 2010-2025: same verdict (pass) on both definitions.

| Nov 2015 - Sep 2026 | CAGR | worst fall | Sharpe over cash |
|---|---|---|---|
| Nifty 50 TRI held | 11.1% | -38.3% | 0.43 |
| **L1 (live)** | **14.9%** | **-27.4%** | **0.81** |
| AW: Nifty 50 30 / G-Sec 15y+ 40 / G-Sec 4-8y 15 / gold 15 | 10.3% | -12.2% | 0.82 |
| half L1 / half AW | 12.8% | -19.7% | 0.86 |
| L1g: L1 with G-Sec 4-8y instead of liquid | 15.1% | -27.5% | 0.82 |
| (from Nov 2018) L1 / RP inverse 3-yr vol, Nifty/MON100/gold/G-Sec 8-13y | 15.8% / 11.6% | -27.4% / -11.8% | 0.84 / 1.13 |

Part 1 (for the owner; cannot go live under Addendum 14 with 11 years of bonds): All Weather makes 4.6 points a year
less than L1 for the same return per unit of risk; it suits capital protection, not the owner's Growth goal. Risk
parity has the best return per unit of risk (Dalio's point) but 4 points a year less return: Bridgewater lifts it with
leverage, which this account cannot and should not use. Bonds instead of the liquid sleeve change almost nothing.

| Valuation tilt on L1 | A: 2006-2015 | B: 2016-2026 |
|---|---|---|
| L1 | 14.91% / -48.1% / 0.51 | 15.24% / -27.4% / 0.83 |
| **V1: dividend yield, 20% cut-offs** | **15.84% / -44.1% / 0.57** | **15.34% / -27.4% / 0.84** |
| neighbour 10% / 30% | 15.58% / 15.66%, Sharpe 0.56 / 0.57 | 15.25% / 15.63%, Sharpe 0.83 / 0.88 |
| reference: P/E, 20% (2021 method break) | 15.93% / -45.6% / 0.58 | 14.88% / -22.0% / 0.87 |

- **V1 PASSES its pre-registered bar** (Sharpe over cash above L1 in A and B, CAGR not lower, both neighbours above).
- How strong (reading the result, not a bar): the gain is episodic. "Expensive" held at 41 of 250 month-ends (2007,
  2008, 2009-10, 2017, 2018, 2021) and "cheap" at 3 (Nov 2008, Feb 2009, Mar 2020). Year by year: -7.5 points in 2007
  (it left the bubble's last leg), +5.7 in 2008, +2.5 in 2009, +2.3 in 2011, +1.0 in 2020, -1.6 in 2021, nothing in
  most years. In B the margin is +0.1 point a year and Sharpe +0.01 (the 10% neighbour's Sharpe margin is under
  0.005). A real but small effect, concentrated in two crashes.
- Outcome as pre-registered: no automatic change; it goes to the owner as a rulebook decision (Addendum 14).

Part 3 (done, measurement only): the daily report now (1) says it is a price-and-momentum screen that does not judge
the business, (2) shows the Nifty 50's P/E, P/B and dividend yield with their percentile since 1999 and this test's
result in one line, and (3) tracks its ideas over 20 and 60 sessions.

## Addendum 16a: V1 adopted and replayed (8 Oct 2026)

The owner adopted V1. Engine replay (research/replay_core.py --tilt: the real CoreAllocation with the rulebook's own
tilt settings, Engine._allocate, risk checks, paper broker with real costs): A 15.77% / -43.9% (research 15.84% /
-44.1%), B 15.31% / -27.4% (research 15.34% / -27.4%): within 1 point a year, as required. 304 orders over 20 years
(L1: 168; the tilt's switches add about 7 trades a year in the years it acts). The yearly re-test now evaluates L1
with the tilt: on data to Dec 2025 both tests pass (Sharpe 1.08 vs 0.70 and 1.47 vs 1.06; worst fall -44.1% vs
-59.5%). Today (8 Oct 2026): dividend yield 1.22, above 33% of days since 1999 - neutral, no change to the mix.

## Addendum 17: NSE quality / value / low-volatility indices, before and after launch (9 Oct 2026; pre-registered; research/factor_indices17.py)

Total return, each index against its parent, the factor index charged 0.30 point a year more (an ETF on it costs more).

| index (launched) | LIVE: excess a year, t | LIVE: worst fall vs parent | back-calculated: excess a year | verdict |
|---|---|---|---|---|
| Nifty100 Quality 30 (Mar 2015) | -1.47%, t -0.77 | -32.9% vs -37.9% | +4.32% | NO EDGE |
| Nifty50 Value 20 (Mar 2014) | +0.88%, t +0.43 | -29.6% vs -38.3% | +6.47% | CONSISTENT, NOT PROVEN |
| Nifty100 Low Volatility 30 (Jul 2016) | -0.11%, t -0.06 | -30.7% vs -37.9% | +2.94% | NO EDGE |
| Nifty Alpha Low-Volatility 30 (Jul 2017) | -0.13%, t -0.05 | -31.0% vs -38.0% | +4.35% | NO EDGE |
| Nifty Quality Low-Volatility 30 (Jul 2017) | -1.95%, t -0.75 | -29.4% vs -38.0% | +2.06% | NO EDGE |
| Nifty200 Quality 30 (Apr 2018) | -1.51%, t -0.52 | -29.1% vs -38.0% | +3.90% | NO EDGE |
| Nifty Midcap150 Quality 50 (Oct 2019) | **-7.44%, t -3.44** | -35.2% vs -38.5% | +2.43% | NO EDGE |
| Nifty Dividend Opportunities 50 (Mar 2011) | -0.79%, t -0.45 | -35.9% vs -38.1% | +10.89% | NO EDGE |

- **Every index looked better than its parent in its back-calculated years (+2 to +11 points a year) and none showed
  an edge after launch.** Seven of eight returned less than the parent once live; the one positive (Value 20, +0.9 a
  year) is indistinguishable from chance (t 0.43). The Midcap150 Quality index lagged by 7.4 points a year (t -3.4).
  This is the size of the hindsight in a back-calculated history: the index designers' rules were chosen on data
  whose outcome they knew.
- What did survive launch: every quality, value and low-volatility index fell less than its parent in the worst
  drawdown (by 2-9 points). Mechanical quality selection in India has delivered smaller falls, not higher returns.
- Reported only (too little live history): Nifty200 Value 30 (+3.0% a year over 27 months, t 0.45), Nifty500
  Quality 50 (-2.1% over 22 months).

Outcome as pre-registered: nothing changes live. For Addendum 18: published quality and value screens, run by NSE
with full company data, did not beat their universe after launch, so a company-level framework built on the same
measures starts with a low prior; the defensible claim to test is smaller drawdowns as much as higher returns.
