# Pre-registered research plan (written 3 Oct 2026, before running any test on the new data)

Data: INDstocks daily bars Oct 2014 - Oct 2026 (49 current Nifty 50 stocks + ETFs: NIFTYBEES, BANKBEES,
JUNIORBEES, GOLDBEES, MON100, LIQUIDBEES, CPSEETF, PSUBNKBEES; ITBEES/PHARMABEES/SILVERBEES are shorter)
and 5-minute bars Jun 2024 - Oct 2026 (same stocks + NIFTYBEES, BANKBEES).

Costs: stocks as in backtest.engine.Costs / backtest.intraday.IntradayCosts. ETFs: no STT on buys,
0.001% on sells (equity ETFs; none on gold/international ETFs), stamp 0.015% delivery / 0.003% intraday,
exchange + SEBI + GST, ₹10 per order, ₹16 DP charge per delivery sell, slippage 0.02% per side.
Idle cash earns 6% a year (liquid fund average 2014-2026).

## Candidates (fixed now)
Swing, stocks: momentum_rotation, equal_weight_trend, weinstein_stage2, minervini_breakout, high52_rotation,
clenow_rotation, lowvol_rotation, donchian_breakout, rsi2_pullback, sma_trend, leader_pullback.
Swing, ETFs (cross-asset, monthly or signal-driven):
- dual_momentum: hold the one of NIFTYBEES / MON100 / GOLDBEES with the best 12-month return if it beats
  cash, else cash. Monthly (Antonacci).
- gtaa: NIFTYBEES, JUNIORBEES, MON100, GOLDBEES at 25% each while above the 200-day average, else that
  quarter in cash. Monthly (Faber).
- etf_rsi2: NIFTYBEES / BANKBEES / JUNIORBEES: buy RSI(2) < 10 above the 200-day average, sell on a close
  above the 5-day average (Connors).
- turn_of_month: NIFTYBEES held from the last session of the month to the 3rd session of the next.
Intraday: the 11 lab methods on stocks (all, top-10 volatile), plus noise area / VWAP trend / late-day
momentum on NIFTYBEES and BANKBEES. "Strong signal" thresholds (gap size, relative volume, first-half-hour
move) are chosen on the FIRST half of the 5-minute sample only and judged on the second half.

## Pass criteria (fixed now)
Swing: (1) CAGR above both the Nifty ETF and the FD over the full test; (2) stock strategies must also beat
equal-weight buy-and-hold of the same stocks on Sharpe ratio (the survivorship-bias-matched yardstick);
(3) positive excess return over cash in both halves of the period; (4) neighbouring parameter values keep at
least 70% of the excess return; (5) at least 30 trades (or 10 for monthly allocation strategies).
Intraday: (1) average net-of-cost trade > 0 in BOTH halves; (2) day-clustered t-stat >= 2 over the whole
sample; (3) at least 100 trades; (4) neighbouring thresholds keep the sign of the edge.

Anything that fails stays in paper mode only, whatever its headline return.

## Addendum (written after the 5-minute results, before running this test)
The 5-minute sample (548 sessions) showed no intraday method passing. The only positive pattern, buying
large opening gap-downs, came almost entirely from 10 market-wide gap-down days, too few to judge.
So the gap-reversal idea is re-tested on 12 years of DAILY bars (enter at the open, exit at the close):
- NIFTYBEES and BANKBEES: market gap-down of at least 1%, 1.5% or 2% -> buy the open, sell the close;
  also the mirror (gap-up -> short) and "gap-and-go" for comparison.
- Stocks: own gap-down of at least 3% on a day the market (NIFTYBEES) gaps down at least 1%.
Same pass criteria: net > 0 in both halves (2014-2020 / 2020-2026), day-clustered t >= 2, >= 30 events,
neighbouring thresholds keep the sign. Costs: ETF intraday round trip 0.075%, stock 0.119%.

## Addendum 2 (4 Oct 2026, written before running): the "5-section analyst report" method
Source: a popular LLM prompt for single-stock analysis (technical setup, fundamentals, macro/sector,
catalysts, trade plan with structure-based stop, 1-2 targets and risk/reward). Horizon: 1-3 week swing.
What can be tested with our data (12 years of daily OHLCV, 49 Nifty 50 stocks + ETFs) is sections 1 and 5,
plus two proxies. Sections 2 (P/E, P/B, debt, cash flow, institutional flows) and most of 4 (earnings dates,
management news) need point-in-time data we do not have; they are NOT tested and nothing is inferred about them.

Indicators (all computed from data up to day t's close; orders fill at day t+1's open):
EMA50/EMA200 daily; weekly trend = last completed week's close > its 20-week EMA and that EMA rising over
4 weeks; RSI(14); MACD(12,26,9); Bollinger(20,2) and bandwidth; volume vs 20-day average; ATR(14);
resistance = highest high of the previous 60 sessions; Fibonacci zone = 38.2-61.8% retracement of the
move from the lowest low to the highest high of the last 60 sessions.
Market proxy for section 3: NIFTYBEES close above its 200-day average.

Setups (each is one candidate):
- A "pullback to support": daily + weekly uptrend (close > EMA200, EMA50 > EMA200), low within 1% of EMA50
  or inside the Fibonacci zone in the last 3 sessions, RSI(14) dipped below 45 in the last 5 sessions,
  trigger: close above the previous day's high and MACD histogram rising.
- B "breakout with volume": daily + weekly uptrend, close above the 60-session resistance, volume >= 1.5x
  average, RSI(14) between 55 and 80, Bollinger bandwidth in the lowest 25% of its last 126 sessions at
  some point in the previous 10 sessions (squeeze).
- C "Bollinger reversion in an uptrend": close below the lower band, close > EMA200, weekly uptrend, RSI(14) < 35.
- D "MACD cross in an uptrend": MACD crosses above its signal line, close > EMA50 > EMA200, weekly uptrend,
  volume >= 1.2x average.
- E "analyst checklist score": 1 point each for close > EMA50, EMA50 > EMA200, weekly uptrend, RSI 50-70,
  MACD > signal, close > Bollinger middle, volume > average, market filter; enter at score >= 7 (of 8) on
  the first day it reaches 7.

Trade plan (section 5), identical for all setups:
- Stop = lowest low of the last 10 sessions - 0.25 x ATR (structure). Skip if the stop is more than 8% away.
- T1 = the 60-session resistance if it is above the entry, else entry + 2R; T2 = entry + 3R.
  Risk/reward filter: skip unless (T1 - close) / (close - stop) >= 2.
- Half the position exits at T1 and the stop moves to the entry price; the rest exits at T2, at the stop,
  or at the close of the 15th session (3 weeks), whichever comes first. A day touching both stop and
  target counts as the stop (conservative); gaps through a level fill at the open.
- Sizing: risk 1% of equity per trade, position at most 25% of equity, at most 5 positions, whole shares;
  when more signals than free slots, take the highest risk/reward first. Market filter must be on.
- Capital ₹1.5 lakh, delivery costs as in backtest.engine.Costs, idle cash 6%. Test 2016-2026, halves as before.

Neighbours (for criterion 4): R:R filter 1.5 / none; time stop 10 / 20 sessions; stop lookback 5 / 20;
no market filter. Proxy for section 4 (catalysts): skip new entries during results seasons
(15 Jan-15 Feb, 15 Apr-15 May, 15 Jul-15 Aug, 15 Oct-15 Nov) - reported, not used for selection.

Pass criteria: the five swing criteria above, plus trade level: average net return per trade > 0 in BOTH
halves and t-stat >= 2 with trades clustered by entry week. Five setups are tested, so a single t just
above 2 is treated as weak evidence. A passing setup is also checked for whether adding it to the current
50/50 ETF-trend + momentum portfolio improves that portfolio's Sharpe ratio; only then would it go to paper.

## Addendum 3 (4 Oct 2026, written before running): can intraday days be picked in advance?
The engine already trades intraday only on days a strategy signals. Question: does information known at or
before 09:20 identify days when intraday trading pays? Daily strategy return series tested: ORB 15m on stocks
(both sides), ORB 15m on NIFTYBEES/BANKBEES, and stock gap-down reversal (12-year daily version).
Pre-open features: Nifty (NIFTYBEES) overnight gap size, previous day's range / ATR(14) (and NR7),
previous 5 days' strategy P&L, day of week, monthly expiry week, market above/below its 200-day average,
first 5-minute bar range / ATR. Each feature is split in terciles using the FIRST half only; the best
tercile is chosen on the first half and judged on the second: it passes only with net > 0 and day t >= 2
in the second half. Seven features x several strategies are tested, so a lone pass is treated as a lead
for paper trading, not a live signal.

## Addendum 4 (4 Oct 2026, written before running): exits between the monthly decisions
The two live strategies decide entries and exits once a month (ETF trend: above/below 200-day average;
momentum: rank and market filter). Untested so far: protective exits checked DAILY between those decisions.
Overlays (a triggered holding is sold at the next fill and may only come back at the next monthly decision):
- trailing stop: close more than 10% / 15% / 20% below the highest close since entry;
- fixed stop: close more than 8% / 12% below the entry price;
- take-profit: close 25% above entry (tested to check "let winners run");
- faster trend exit: ETF trend - close below the 200-day average checked daily / weekly;
  momentum - market filter (Nifty ETF below its 200-day average) checked daily -> all to cash; and stock
  close below its own 100-day average checked daily.
Fills: stocks at the next open; ETFs at the next session's close (ETF opens are unreliable). ₹1.5 lakh,
delivery/ETF costs as before, idle cash 6%, 2016-2026, halves as before.
Adopt an overlay only if: Sharpe above the base strategy in BOTH halves, full-period CAGR no more than 0.5
points lower, and its neighbouring settings (e.g. 10/15/20% trails) point the same way.

## Addendum 5 (4 Oct 2026, written before running): managing an existing stock portfolio
Question: for a portfolio of ~13 large-cap stocks bought recently (like the owner's: ₹3.5 lakh, 13 stocks,
2-3 year horizon), which monthly rule for keeping, exiting and re-entering gives the best outcome after
costs AND Indian capital-gains tax?
Test: 2,000 random 13-stock portfolios from the 49 Nifty 50 stocks, equal weights, bought at a random month
start 2016-2023, run 30 months, then valued as if sold (tax due on everything). Plus every 30-month window for
the owner's 9 Nifty 50 names (HCLTECH INFY RELIANCE HDFCBANK TCS LT ITC ICICIBANK SHRIRAMFIN).
Monthly steps (month-end close), each stock its own slot. Costs 0.27% per side (STT, stamp, exchange, ₹10
brokerage + DP on a ~₹27k slot, slippage). Tax per financial year: STCG 20% (held < 12 months), LTCG 12.5% above
₹1.25 lakh a year, losses set off (ST against ST then LT, LT against LT), carried forward. Cash while out of a
stock: 0% (left idle in the broker account) and 6% (parked in a liquid ETF) both reported.
Rules:
- H0 hold everything (current behaviour)
- T200: sell a stock at a month-end close below its 200-day average, buy it back at a month-end close above
- T200 band: same with a 3% band (exit below 0.97x, re-enter above 1.03x); neighbours T150 / T250
- MKT: whole portfolio to cash when the Nifty ETF closes a month below its 200-day average, back when above
- TRAIL: sell after a month-end close 25% below the highest month-end close since entry; back above the 200-day
- MOM: each month replace a holding that ranks outside the top 25 of the 49 by 12-1 month momentum with the
  best-ranked stock not held (with and without the market filter)
Adopt a rule for automation only if, against H0: (1) the worst-10% outcome is better, (2) the median after-tax
annual return is no more than 1 point lower, (3) both start-date halves (2016-2019 / 2020-2023) agree on (1)
and (2), (4) its neighbours agree. Survivorship bias (today's Nifty 50) favours H0, since stocks that collapsed
and left the index (e.g. Yes Bank) are missing; a rule that passes anyway is the more convincing.
If nothing passes, the honest recommendation is to keep holding with alerts only.

## Addendum 6 (4 Oct 2026, written before running): three institutional strategies, adapted to what an
## Indian resident can trade through this account (no overseas margin/derivatives under LRS)
Capital ₹4.2 lakh, 2016-2026, halves as before, idle cash 6%, costs as before.
A. CTA trend (brief #1). Universes: (i) 10 India-listed ETFs - NIFTYBEES BANKBEES JUNIORBEES GOLDBEES SILVERBEES
   MON100 CPSEETF PSUBNKBEES ITBEES PHARMABEES (the nearest thing to a cross-asset futures list here), ETF fills at
   the next close; (ii) the 49 Nifty 50 stocks, fills at the next open. Entry: 50-day SMA crossed above the 200-day
   in the last 5 sessions AND ADX(14) > 25. Exit: 50-day back below the 200-day. Size: shares = 1% of equity /
   ATR(20) (a one-ATR move = 1% of equity), no leverage (only with cash on hand). Neighbours: ADX 20 / 30; cross
   window 10 sessions; "state" entry (50 > 200 and ADX > 25 on any day while flat).
   Adopt only if it beats the current ETF trend strategy (13.5% CAGR, Sharpe 1.39) on Sharpe in both halves.
B. Risk parity (brief #5). Long-term government bonds and broad commodities have no usable history here
   (LIQUIDBEES is overnight money, SILVERBEES starts 2022), so: Indian equities NIFTYBEES, global equities MON100,
   gold GOLDBEES. (a) inverse 3-year volatility, (b) equal risk contribution from the 3-year covariance; monthly,
   no leverage. Compare with equal weights and with the ETF trend strategy. Same adoption bar as A.
C. Pairs (brief #2), on the Nifty 50 instead of the S&P 500. Each month: pairs whose daily log prices correlate
   above 0.85 over the last 504 sessions; hedge ratio by least squares over the same window; spread z-score vs its
   60-day mean and deviation. Enter at |z| > 2.5 (short the rich leg, long the cheap leg, equal rupee notional),
   exit when z crosses 0, stop at |z| > 4.5 or after 30 sessions. Costs: stock futures, 0.24% per pair round trip
   on one leg's notional. Pass: net > 0 in both halves, t >= 2 (by entry week), >= 100 trades, neighbours
   (z 2.0 / 3.0, correlation 0.80 / 0.90) keep the sign. Even a pass stays paper: Nifty 50 futures lots are worth
   several lakh per leg, beyond this account.

## Addendum 7 (5 Oct 2026, written before any of this data was downloaded): fundamentals, flows, options,
## events and news - do they improve the two live strategies?
Data (research/download_nse.py, run on the server; NSE public archives and NSE's website API): participant-wise
open interest (FII/DII/pro/client), the daily close file of every NSE index (includes each index's P/E, P/B and
dividend yield), the daily F&O bhavcopy reduced to per-symbol futures/options open interest, daily delivery %,
board meetings (results dates), insider (PIT) trades and bulk deals. Period: whatever exists from Oct 2014 to
Sep 2026; the halves split as before; costs as before; ₹4.2 lakh. Baselines are the live strategies simulated by
the same code: momentum rotation with the Nifty 200-day filter, and the monthly ETF trend strategy.
Every rule below is decided here; thresholds use only data available at that date (rolling windows, no
full-sample percentiles). Signals are acted on at the next session's open (stocks) or close (ETFs, as before).

A. Market timing (would replace or add to the "Nifty above its 200-day average" switch)
 A1 Valuation: Nifty 50 trailing P/E vs its own previous 5 years. Rule: equity exposure off when P/E is in the top
    20% of that window, on otherwise (alone, and combined with the 200-day switch: on only if both say on).
 A2 Foreign investors' index-futures positioning: FII long contracts / (long + short) in index futures. Contrarian
    rule: below its 20th percentile over the previous 250 sessions = buy signal, above the 80th = sell signal.
    Tested (i) as the switch, (ii) as an override that keeps momentum invested when the 200-day switch says cash
    but FII positioning is at a contrarian low.
 A3 Nifty options put-call ratio (total put OI / call OI, all expiries): high PCR (top 20% over 250 sessions) =
    contrarian buy, low (bottom 20%) = sell. Same two uses as A2.
 Pass for A: the momentum strategy (and separately the ETF trend strategy) with the rule beats the same strategy
 without it on Sharpe in BOTH halves AND does not deepen the worst fall; the 10th/30th percentile neighbours keep
 the sign. Also reported: forward 1/3/6-month Nifty returns by signal state (t-stat with overlapping-window
 correction).
B. Stock selection inside momentum (tie-breakers / filters on the ranked list, same slots and exits)
 B1 Futures open-interest build-up: 20-session change in a stock's total futures OI together with its price
    change. Filter: skip a momentum pick whose price rose while OI fell >10% (short covering, not fresh buying).
 B2 Delivery %: 20-session average delivery % minus its 120-session average. Filter: skip picks whose delivery %
    is falling (bottom third of the universe that month).
 B3 Insider/promoter buying: open-market purchases by promoters or directors (PIT disclosures) in the previous 60
    days. Rule: such stocks get +5 ranks; promoter SELLING beyond 0.5% of shares gets -5 ranks.
 Pass for B: momentum with the filter beats plain momentum on Sharpe in both halves, CAGR not lower by more than 1
 point, and the cross-sectional spread behind it (filtered-out vs kept picks, next-month return) has t >= 2 by
 month.
C. Fundamentals through NSE's factor indices (point-in-time by construction; history before each index's launch
   is NSE's back-calculation - noted, not corrected)
 C1 Quality / low volatility / value: Nifty200 Quality 30, Nifty100 Low Volatility 30, Nifty Alpha Low-Volatility
    30, Nifty200 Value 30, Nifty50 Value 20, Nifty200 Momentum 30 (those with ETFs are tradable here). Test the
    ETF trend rule (monthly, 200-day filter) on a basket of them vs the live ETF basket.
 C2 A quality filter for momentum: hold momentum only while Nifty200 Quality 30 is above its 200-day average.
 Price indices exclude dividends (value indices are understated by ~1-2%/yr; noted in the results).
 Pass for C: same bar as A (Sharpe both halves, worst fall not deeper) against the live ETF strategy for C1 and
 the live momentum strategy for C2.
D. Events
 D1 Results-day reaction: abnormal return (stock minus Nifty) over the results day and the next session; events
    above +5% or below -5%. Measured: abnormal return over the following 20 and 60 sessions.
 D2 Promoter open-market buying (as B3) as a stand-alone event.
 D3 Large bulk deals by named institutions (buy > 0.5% of shares).
 Pass for D (to become a signal): average abnormal return beyond costs (0.3%) in both halves with the same sign,
 t >= 2 by event month, >= 100 events, and the +/-4% / +/-6% neighbours agree.
E. News and sentiment: no historical news or social-media archive is available, so nothing here can be tested
   historically. Live exchange-filing alerts may be added for information only (no automatic trades).
Survivorship bias (today's index members) flatters every "buy the beaten-down / buy on insider buying" rule; a
pass must survive it. With ~15 rules tested, one or two "passes" are expected by luck: a rule must also make sense
and agree with its neighbours before it is used, and any adopted rule runs on paper first.

## Addendum 8 (5 Oct 2026, written before any run): which day of the month to rebalance

Question from the owner: why the last session of the month? Test: the live momentum rotation (top 10 of the
cached Nifty 50, exit rank 20, 200-day Nifty filter) and the live ETF trend (4 ETFs, 200-day average), unchanged
except for the review day, 2016-2026, same costs and cash yield as the main research (orders at the next open).
 1. Day of month: review on the k-th session of every month, k = 1 .. 20 counted from the start, and the 1st-5th
    from the end (5th-last .. last). The last session is the live rule (baseline).
 2. Frequency: twice a month (last session and the 10th session), every two weeks, every week.
Reported per variant: CAGR, worst fall, Sharpe for the full period and for each half (split at the middle
session), number of trades and costs.
Expected (the reason the date was never treated as a parameter): momentum and 200-day signals change slowly, so
the review day should only shift returns by luck ("timing luck"), with no day better in a way that repeats.
Decision rule: the live day changes only if a block of at least 3 consecutive review days beats the last session
on Sharpe in BOTH halves by at least 0.10 and its worst fall is not deeper by more than 2 points; a single good
day is luck. A higher frequency is adopted only if it beats monthly on Sharpe in both halves AFTER costs.
Otherwise the last session stays, and the spread across days is reported as the timing-luck range to expect.

## Addendum 9 (5 Oct 2026, before the run): momentum in 3 tranches, paper test

Origin: Addendum 8's exploratory check (tranches remove timing luck without changing the average return). That
check looked at the same history, so the historical numbers below are not independent evidence; the case rests on
the mechanism (the review day is luck, splitting the review over 3 days averages it) and on the paper run.
Configuration (fixed now): the live momentum rules (top 10 of the Nifty 50 list, exit rank 20, 200-day Nifty
filter), capital split in 3 equal tranches, each a separate portfolio reviewed once a month: tranche A on the 7th
trading session, B on the 14th, C on the last session (the live day). A month with fewer sessions uses its last.
Control: one portfolio with the same total capital reviewed on the last session (the live rule).
1. Historical check of exactly this configuration, Rs 3.45L, real DP charge, 2016-2026, both halves: reported
   against the control and against the average single day of Addendum 8. Expected: CAGR within 1 point of the
   single-day average, worst fall no deeper than -30%.
2. Engine check: trader.replay of the tranche strategies must reproduce (1) within 1 point of CAGR (the same code
   then runs on paper and would run live).
3. Paper run on the server (journal mode "lab", Rs 3.45L notional, no real orders), from Oct 2026, beside the
   control. Checked monthly: every review happens on its day (holidays included), orders and fills match the
   rules, and the cost per rebalance is within 25% of the backtest cost model (smaller orders pay the fixed
   charges more often).
Bar for proposing a live switch (the owner decides): (1) and (2) as expected, and 3 months of clean paper runs
(9 tranche reviews). Paper returns over 3 months are NOT a test of the edge and are not used to decide.

## Addendum 10 (5 Oct 2026, before any run): parking idle cash in a liquid ETF

Origin: Addendum 9 (2) - the research credited idle cash with 6% a year, the account earns nothing, momentum is in
cash about 23% of the time.
Vehicle: LIQUIDCASE (Zerodha Nifty 1D Rate Liquid ETF): overnight government-backed lending (TREPS), growth
option (the return is in the price, no dividend units), expense ratio 0.23%, AUM about Rs 10,000 crore, listed
Jan 2024. Fallback if INDstocks can't trade it: LIQUIDBEES (price fixed near Rs 1,000, return paid as monthly
dividend units taxed at slab rate, expense ratio about 0.65%) - worse for the bot (leftover units) and for tax.
Rule (per strategy): every evening, if the strategy's idle money (its capital minus what it holds and is buying)
is at least Rs 25,000, buy the ETF with it (after-market order). At a review that has buys to make, the whole ETF
position is sold with the evening's sells; the buys wait for that money and go in the next evening (the engine
already works this way when buys depend on same-night sells), i.e. they fill one session later than in research.
Backtest (research/cash_parking.py), 2016 - 1 Oct 2026, both halves, live momentum (Rs 3.45L) and live ETF trend
(Rs 0.75L): (a) idle cash earns nothing (the live engine today); (b) parking with real frictions: Rs 10
brokerage + GST each side, DP Rs 21.83 per sell, 0.02% slippage each side, the Rs 25,000 threshold, the one-session
delay for buys after unparking; (c) as (b) without the delay. Yield: the overnight rate approximated from RBI policy
rates (repo minus 0.25 point; 3.35% from Apr 2020 to Apr 2022, when overnight money traded at the reverse repo),
minus the 0.23% expense ratio; sensitivity: the path minus 1 point, and a flat 4%.
Also reported: (a) vs research's 6% assumption, and the one-session delay applied to ALL buys funded by
same-night sells (how the live engine already trades a fully invested rotation).
Pass (to run parking on paper, then propose it live): (b) beats (a) on CAGR by at least 0.5 point and on Sharpe in
BOTH halves for momentum, also with the path minus 1 point; worst fall not deeper by more than 1 point. The ETF
trend is reported the same way (smaller money, so fixed costs weigh more).

## Addendum 11 (6 Oct 2026, before the paper run): the Quality 30 check on paper

From Addendum 7 (C2 and its robustness check; not the pre-registered form, so a candidate only). Rule, fixed now:
the live momentum rules, and cash also while the Quality 30 index (NSE: NIFTY100 Quality 30, earlier names chained)
closes below its 150-session average; reviewed on the last session of the month like the live rule. Index closes
come from NSE's daily index file (trader/index_data.py); if today's file isn't out at 16:10 the latest one is used.
Paper run in the lab (Rs 3.45L notional) beside the control from Oct 2026. Checked monthly: the filter reads fresh
data every evening and its in/out state matches the research code on the same dates. A live proposal needs (a) 3
clean months and (b) agreement with a re-run of Addendum 7's C2 on data that has arrived since (no new tuning).
Paper returns over a few months are not evidence either way.

## Addendum 12 (6 Oct 2026, before any of this data is downloaded): 20 years, and the final rulebook

Owner's decision: settle the rules on ~20 years of history instead of months of paper running, then automate all
three strategy families on those rules only.
Data (research/download_history.py, run on the server): NSE's daily equity bhavcopy for every trading day since
Jan 2005 (all stocks and ETFs: open, high, low, close, previous close, volume, value), NSE's daily index-close
files for the same years where they exist, and index history from niftyindices.com where they don't.
- Prices are adjusted for splits, bonuses and rights with the exchange's own figure: on an ex-date NSE's
  "previous close" is the adjusted one, so factor = previous close(t) / close(t-1). Dividends are not added back
  (understates stock returns by ~1-1.5%/yr, the same for every rule).
- Universe without survivorship bias: at each month-end the 50 most traded EQ-series stocks by median daily traded
  value over the previous 6 months (at least 12 months of history). Neighbour: the 100 most traded. Today's
  Nifty 50 list is NOT used for history.
- Periods: A = Jan 2006 - Dec 2015 has never been looked at by any rule here: it is the out-of-sample test.
  B = Jan 2016 - Sep 2026 is where the rules were built (reported, but it cannot confirm them).
- Costs as in the live models (delivery Costs with DP Rs 21.83; ETF costs; intraday IntradayCosts); idle cash
  earns nothing unless a parking rule is tested; capital Rs 4.2L split 82/18 as live.
Rules tested (all parameters fixed here):
1. Momentum (live rule): top 10 by the momentum score, keep while in the top 20, cash when the Nifty is below its
   200-day average, reviewed on the last session of each month. Variants: (a) 3 tranches on the 7th / 14th / last
   session; (b) + Quality 30 check (Quality 30 below its 150-day average = cash), only where the index history
   exists; (c) idle cash parked at the overnight rate minus 0.23% (rate path from RBI policy rates, approximate),
   one-session delay on buys funded by the parked cash.
2. ETF trend (live rule): NIFTYBEES, JUNIORBEES, GOLDBEES, MON100 from their own NSE prices, each held while above
   its 200-day average, equal slices, monthly; an ETF joins once it has 200 days of prices (GOLDBEES 2007, MON100
   2011).
3. Intraday, from daily bars (no 5-minute data exists that far back): (a) gap reversal - buy at the open a stock
   (top 100 by traded value) that opens 3%+ below its previous close while the Nifty opens within 1% of its own;
   exit at the close, or at a stop 1 x ATR(14) below the open if the day's low reaches it; at most 3 a day (largest
   gaps). (b) gap fade both ways (short side as the mirror image). Intraday costs, 0.03% slippage each side.
Pass bars:
- Live momentum and ETF trend stay only if in period A they beat their benchmark (NIFTYBEES; for the ETF trend a
  monthly equal-weight buy-and-hold of the same ETFs) on Sharpe, with a worst fall no deeper than the benchmark's.
- A variant replaces the live rule only if it beats it on Sharpe in A AND in B, CAGR not lower by more than 0.5
  point in either, worst fall not deeper by more than 2 points.
- An intraday rule goes live only if its net daily return is positive with day t >= 2 in A, in B, AND in the last
  3 years on its own (an edge that has gone is not traded), with at least 300 trades in each.
- Failing everywhere is a valid answer: a strategy family with no passing rule is not traded.
Afterwards: the passing rules are written into one rulebook (docs), each live change is replayed through the real
engine over the 20 years to confirm the code matches the research, and the owner switches it on. No further paper
months are required for rules that pass here; paper (lab) runs continue only as a live plumbing check.
