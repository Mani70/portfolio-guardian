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

## Addendum 12a (8 Oct 2026, after a first run showed the price adjustment is wrong; before any valid result is seen)

What went wrong: NSE's bhavcopy "previous close" is NOT adjusted on an ex-date (RELIANCE bonus 1:1, 28 Oct 2024:
previous close 2,655.70, close 1,334.35; NIFTYBEES 1:10 split, 19 Dec 2019: previous close 1,292.54, close 130.20).
Addendum 12's adjustment therefore missed every split and bonus and also fired on every stock after weekend special
sessions (budget days, muhurat trading), which the download skips. The first run's numbers (NIFTYBEES -9.8% a year in
2016-2026) are invalid and were deleted. No rule, parameter or pass bar of Addendum 12 changes; only the price data.
The owner chose this replacement (8 Oct 2026):
1. Official corporate actions, Jan 2010 on: the "Bc" file inside NSE's daily PR zip (archives/equities/bhavcopy/pr),
   every trading day from 4 Jan 2010, EQ-series rows, de-duplicated by symbol, ex-date and purpose.
   - BONUS a:b (a new shares for every b held): factor b / (a + b).
   - Face-value split or consolidation FROM x TO y: factor y / x.
   - DEMERGER (no ratio in the file): factor = open on the ex-date / close the session before.
   - Rights, dividends and everything else: no adjustment (dividends are not added back anywhere, as in Addendum 12).
   Prices before an ex-date are multiplied by its factor; previous close is no longer used.
2. Before 2010 (no official file exists; NSE returns 404), a detection rule, fixed now: on day t, g = open(t) /
   close(t-1) and c = close(t) / close(t-1). With k the nearest of 2/3, 1/2, 2/5, 1/3, 1/4, 1/5, 1/10, it is a split
   or bonus with factor k when |g / k - 1| <= 3% and |c / k - 1| <= 8%. Smaller events (bonus 1:4 = 0.8 and the like)
   cannot be told apart from ordinary moves and stay unadjusted; their count in 2010-2026 is reported.
   Validation (run before the strategy tests, reported whatever it shows): on 2010-2026, for every stock that was ever
   in the monthly top 100 and the four live ETFs, the rule's events against the official events with factor <= 2/3
   (same day or one session apart). The rule is used for 2005-2009 only if recall AND precision are both >= 90%.
   If it fails: period A becomes Jan 2011 - Dec 2015 (2010 is needed as history), stated in the results.
3. The four live ETFs: official events where the file lists them; otherwise the detection rule in every year.
4. Index closes and opens for Oct 2014 on come from NSE's daily index files (the same files as before Oct 2014), so
   the Quality 30 check and the intraday Nifty-open filter have data for the whole of period B.

## Addendum 12b (8 Oct 2026): data fixes found while checking Addendum 12a's prices

Order of events, stated plainly: a run with 12a's code produced strategy numbers; before accepting them, the
detection check's "false" events turned out to be real 2010 splits (M&M, KOTAKBANK, LUPIN) that the code did not
read from the Bc files. Those numbers are discarded. Fixes, none of which touches a rule, parameter or pass bar:
1. Wording: 2010 files write "FV SPLIT RS.10 TO RS.5" (no "FROM"); others "FVSPLT FRMRS 100 TO RE 1",
   "BONUS1:2/FVSPLIT10TO2" (bonus and split on one ex-date: the product), "BONUS- 1:2", "BON-1:1". All are read now;
   special dividends ("SPL RS 5"), debenture and preference-share bonuses are not price events and stay out.
2. A revised ex-date (the same action listed again within 30 days in a later file) replaces the earlier one
   instead of being applied twice (NBCC bonus, Feb 2017).
3. Check, applied to every case: after adjustment, every overnight move below 0.6x or above 1.7x of a stock while it
   was in the monthly top 100 (2010 on) was looked up. Corrected where a corporate action explains it: INFY bonus
   1:1 on 15 Jun 2015 (Bc text cut off), SHRIRAMFIN split 10 to 2 on 10 Jan 2025 (not in the Bc files), and three
   demergers listed as "SCHEME OF ARRANGEMENT" (ADANIENT 3 Jun 2015, ABIRLANUVO 20 Jan 2016, CGPOWER 15 Mar 2016),
   which get 12a's demerger treatment - except ADANIENT, which opened at the old price (573.30 vs 637.00 the day
   before) and closed at 109.75, so its factor is the ex-date close / previous close (0.172). Every other demerger
   among the tested stocks opened and closed at a similar gap (checked, 38 events). Left as real: 63MOONS 2013, DHFL 2018, Infibeam 2018, JETAIRWAYS 2019,
   YESBANK 2020. Before 2010 the detection rule decides, as pre-registered.

## Addendum 13 (8 Oct 2026, before any of this data is downloaded): the final rules per horizon

Owner's request: settle intraday, momentum swing and long-term investing on what the evidence supports, then
automate what passes. Owner's choices: a growth core (~80% equity); live strategies go to whatever passes here.
Data (none of it seen yet): NSE's total-return indices (TRI, dividends included) from niftyindices.com - Nifty 50,
Nifty Next 50, Nifty200 Momentum 30, a 4-8 yr G-Sec index - and ETF prices from the bhavcopy (Addendum 12a/12b
adjusted) for GOLDBEES (from 2007) and MON100 (from 2011). Each equity sleeve earns its index TRI minus an expense
ratio rounded up: Nifty 50 0.10%, Next 50 0.20%, Momentum 30 0.45%, G-Sec 0.20% a year; GOLDBEES and MON100 are their
own ETF prices. Idle cash earns the overnight rate minus 0.23% (history20.py RATE_PATH), as if parked in a liquid ETF.
Periods as Addendum 12: A = Jan 2006 - Dec 2015 (out of sample), B = Jan 2016 - Sep 2026. ETF costs, Rs 4.2L.
Before a sleeve exists, its weight is spread pro rata over the sleeves that do (gold before Mar 2007, global before
Mar 2011).

Intraday - decided on evidence, not tested again: no real-money intraday. SEBI found 71% of individual intraday
traders lost money in FY23 (80% above 500 trades a year), and no method in Addendum 3/7/12 passed. gap_reversal stays
on paper; nothing here can switch it on.

Momentum swing - hold the published index instead of picking stocks (Addendum 12: the DIY rule failed):
- M1: hold the Nifty200 Momentum 30 ETF. Benchmark: Nifty 50 TRI held. M1 is a valid momentum sleeve only if its
  Sharpe AND CAGR beat the benchmark in A and in B.
- M2: M1 with a trend brake: on the last session of each month, hold it while the Momentum 30 TRI closes above its
  200-session average, else the money goes to the liquid ETF. M2 replaces M1 only if its Sharpe is higher in A and B,
  its CAGR is not lower by more than 1 point in either, and the 150- and 250-session neighbours also beat M1's Sharpe
  in both.

Long-term core (80% equity), checked on the last session of each month; all sleeves go back to target when any one
is more than 5 points off its target weight, and on the last session of each year in any case:
- L1: Nifty 50 45%, Next 50 15%, MON100 20%, GOLDBEES 10%, G-Sec 10%.
- L2: L1 with momentum: Nifty 50 30%, Next 50 15%, Momentum 30 15% (the M rule that passed; M1 if neither), MON100 20%,
  GOLDBEES 10%, G-Sec 10%.
- Benchmark: Nifty 50 TRI held. L1 is adopted if its Sharpe beats the benchmark in A and B and its worst fall is
  shallower in both. L2 replaces L1 only if its Sharpe is higher in A and B and its worst fall is no deeper by more
  than 2 points in either. Reported too: turnover and number of rebalances (each sale can realise taxable gains).

What goes live (owner: "go with the best"): the long-term rule that passes becomes the live strategy for the swing
capital at the 30 Oct 2026 review, replacing trend_allocation and momentum_rotation, after an engine replay over the
20 years matches the research within 1 point a year. If no long-term rule passes, both live strategies move to paper
and the cash is parked in the liquid ETF; the owner decides from there. A momentum sleeve outside the core is not
added: one live strategy, one set of rules.

## Addendum 13a (8 Oct 2026, before any Addendum 13 data is downloaded)

niftyindices.com serves price and total-return history for equity indices only; its G-Sec indices return nothing
(checked: "Nifty 4-8 yr G-Sec Index", "Nifty 8-13 yr G-Sec", both endpoints, 2006 and 2016). So the 10% G-Sec sleeve of
L1/L2 is the liquid ETF instead: it earns the overnight rate minus 0.23% (as idle cash), and live it is LIQUIDCASE.
This understates the sleeve in falling-rate years; nothing else changes.

## Addendum 14 (8 Oct 2026, before any of it runs): autopilot - how the bot may change its own rules

Owner's decision: the bot runs without manual steps, manages all free cash in the INDstocks account above a reserve,
and changes strategy only through a yearly re-test fixed here. Nothing else may change the rules: no reaction to
recent profits or losses, no new strategies, no re-tuning of weights, bands or thresholds.

Rulebook (trader/rulebook.yaml, versioned in git):
- L1 (Addendum 13) is the active rule: NIFTYBEES 45 / JUNIORBEES 15 / MON100 20 / GOLDBEES 10 / LIQUIDCASE 10,
  5-point bands, every year-end.
- F, the fallback: NIFTYBEES 80 / LIQUIDCASE 20, same bands (the owner's 80% equity, without the parts whose edge
  the re-test found gone).
- trend_allocation and momentum_rotation are retired (Addendum 12): their live holdings are handed to the active rule
  at cost; gap_reversal stays paper only.

Yearly re-test (first Saturday of January, data to the last session of December; research/allocation20.py
unchanged, same costs and expense ratios; benchmark = Nifty 50 TRI held):
- Test 1, full sample (Jan 2006 to the end): L1's Sharpe above the benchmark's AND L1's worst fall shallower.
- Test 2, the last 5 calendar years: L1's Sharpe above the benchmark's.
- Switch to F when test 1 fails, or when test 2 fails in two consecutive yearly re-tests. Switch back to L1 when
  both tests pass again. Switching changes the target weights only; the bands then move the money (so a switch is
  an ordinary rebalance, traded at the next evening run).
- Data update rule: the bhavcopy, corporate-action and niftyindices downloads of Addenda 12-13 are extended to the
  new year; idle cash after 2025 earns NSE's "Nifty 1D Rate Index" (overnight rate) minus 0.23% instead of the
  hand-entered RATE_PATH. A re-test that cannot get its data changes nothing and says so.

## Addendum 15 (8 Oct 2026, before any of it runs): do the daily insights ideas have an edge?

The daily Telegram report (trader/insights.py) lists up to 5 BUY ideas and 5 AVOID ideas each evening. It is
information only and places no orders; this test only decides what the report may claim about its own record.
The rules are tested exactly as written in trader/insights.py RULES and screen() on 8 Oct 2026, with no change
before or after the run. Changing a threshold after seeing these results would be fitting to this data, so any
change needs a new addendum and a new hold-out.

Data and method (research/insights20.py):
- The bhavcopy panel of Addendum 12, EQ series only (rows in other series are blank, as in the live store), with
  official corporate actions (12a/12b). The pre-2010 detection rule failed its validation, so **period A is Jan 2011 -
  Dec 2015** (the 252-session lookback then starts in 2010, when official actions begin). **B is Jan 2016 to the last
  idea day with 21 sessions after it.**
- Every number facts() uses is rebuilt as rolling matrices; on 5 sample days the vectorised numbers must equal
  facts() on the last 400 sessions (the live store's window) to 1e-6. Two exceptions to "exactly": the price filter
  (≥ ₹50) uses the unadjusted close (what the bot saw that day, not a price scaled by a later split); history counts
  sessions within the last 400, as in the live store.
- Not reproducible from history, so left out: the filings check (red flags block very few stocks) and delivery %
  (its 10% weight in the ranking is neutral for every stock). The market filter is the live one (NIFTYBEES vs its
  200-day average).
- An idea dated d (the report is sent after the close) is bought at the close of d+1 and sold at the close of d+21:
  20 sessions, the track record's horizon. No close on d+1: the idea is skipped. A stock that stops trading before
  d+21 is sold at its last close (counted and reported). Costs: INDstocks delivery charges on ₹1,00,000 an idea plus
  0.05% slippage each side (backtest Costs, DP ₹21.83).
- Benchmark: the Nifty 50 price index over the same two closes (prices only on both sides: no dividends anywhere).
  Reference: the equal-weighted average of all liquid stocks that day ("buying any liquid stock").
- Each idea day is one observation: the mean of that day's ideas. Ideas overlap (20-session holds started daily), so
  t-statistics are Newey-West with 19 lags.

Pass bars, each in A AND in B:
- BUY has an edge: mean excess over the Nifty 50 after costs > 0 with t ≥ 2.
- BUY beats buying any liquid stock: mean excess over the liquid average (both before costs) > 0 with t ≥ 2.
- AVOID is a useful warning: mean excess over the Nifty 50 (before costs: the advice is not to own it) < 0 with t ≤ -2.
Reported without a bar: hit rates; 5- and 60-session horizons; the watchlist (would-be BUY ideas on days the market
filter blocked them) against the BUY ideas; ideas per day.

What changes: the report's footer states, in one line each, whether BUY and AVOID passed and by how much. A failed
bar is stated as plainly as a passed one. Nothing trades on these ideas either way; the autopilot rulebook is untouched.

## Addendum 16 (8 Oct 2026, before any of it runs): Dalio's All Weather, and the investment committee's valuation lens

Owner's request: check Ray Dalio's strategies, then review the live rule and the daily report through the lenses of
Graham, Buffett, Munger, Fisher, Lynch, Templeton, Marks, Soros, Druckenmiller and Jhunjhunwala. Everything that can
be tested is tested here against fixed bars; the rest is reasoning, labelled as such in FINDINGS.

Data added: Nifty 50 P/E, P/B and dividend yield, daily from Jan 1999 (niftyindices.com, getpepbHistoricaldataDBtoString;
research/data/hist/pepb.csv). NSE's G-Sec total-return indices (4-8 yr, 8-13 yr, 15 yr and above) exist in NSE's
daily index files from Oct 2015 only; the RBI yield history (data.rbi.org.in) is not reachable from here.

Measuring stick (a correction): Addendum 13 compared Sharpe ratios on raw returns (no risk-free rate). That flatters
any mix holding cash or bonds. Here Sharpe = mean daily return OVER the liquid sleeve (what idle cash earns) / its
volatility, annualised; Addendum 13's L1-vs-Nifty verdict is re-reported on this basis too.

Part 1 - Dalio (Nov 2015 - Sep 2026 only, the bond data's span; research/allocation20.py's simulate, costs, 5-point
bands and year-end rebalance, G-Sec sleeves at 0.20% a year expense):
- AW, All Weather adapted: Nifty 50 30 / G-Sec 15 yr+ 40 / G-Sec 4-8 yr 15 / gold 15 (Dalio's 7.5% commodities go
  to gold: India lists no broad commodity ETF). No leverage.
- RP, risk parity with bonds: inverse 3-year volatility of Nifty 50, MON100, gold and G-Sec 8-13 yr, monthly.
- L1g: L1 with its 10% liquid sleeve as G-Sec 4-8 yr (Addendum 13's original design, before 13a).
- Compared with L1 over the same span. Whatever the result, none of these can replace L1: Addendum 14 lets a rule go
  live only on the 20-year test, and the bonds cover 11 years. The result is for the owner.

Part 2 - valuation (Graham's margin of safety, Marks' cycle, Templeton's pessimism), 2006 - 2026, periods A and B as
Addendum 13:
- V1: at each month-end, the Nifty 50 dividend yield's percentile among all its daily values since Jan 1999 up to that
  day. Bottom 20% (expensive): Nifty 50 sleeve 45 -> 30 and liquid 10 -> 25. Top 20% (cheap): Nifty 50 45 -> 55 and
  liquid 10 -> 0. Otherwise L1's weights. Dividend yield, not P/E: NSE moved the Nifty's P/E and P/B to consolidated
  earnings in 2021 (a level break), and P/E explodes when earnings collapse (38 in Dec 2020).
- Neighbours: 10% and 30% cut-offs; and the same rule on P/E (top 20% = expensive), reported with the 2021 caveat.
- V1 replaces L1 only if its cash-adjusted Sharpe is higher than L1's in A AND B, its CAGR is no more than 0.5 point
  lower in either, and both dividend-yield neighbours also beat L1's Sharpe in A and B. A pass goes to the owner as a
  rulebook change (Addendum 14 lets the bot change nothing by itself); a fail changes nothing.

Part 3 - the daily report (no test possible, so measurement only): point-in-time company fundamentals (ROCE, debt,
earnings, pledges) are not available for 2006-2026, so no fundamental claim is added to the report's reasoning. The
report's own track record adds the 60-session result beside the 20-session one (Addendum 15's untested observation is
judged on ideas published from now on), and the header shows the Nifty 50's P/E, P/B and dividend yield with their
percentile since 1999 (a fact, not a signal).

## Addendum 16a (8 Oct 2026): the owner adopts V1; how it runs live

Decision: the owner adopted V1 (Addendum 16) on 8 Oct 2026. Rule L1 in trader/rulebook.yaml gains the valuation tilt;
its weights, bands, year-end rebalance and the fallback F (no tilt) are unchanged.
- Data: the Nifty 50's daily dividend yield from niftyindices.com (cache/valuation/pepb.csv on the server, refreshed
  before each evening plan and by the insights job; the yearly re-test downloads its own copy).
- The regime is read at the month's review: the latest dividend yield on or before the anchor day (the review's
  session if it is the month's last; otherwise the previous month's last day), its percentile among all daily values
  since Jan 1999 up to that day. At the 16:10 run the day's own value is not out yet, so the previous session's is used.
  Between reviews (cash sweeps, a review left open) the same anchor gives the same regime.
- Expensive (percentile <= 20): NIFTYBEES 30, LIQUIDCASE 25. Cheap (>= 80): NIFTYBEES 55, LIQUIDCASE 0. Otherwise
  L1. A switch is an ordinary rebalance (15 points is beyond the 5-point band).
- No data, or the latest value more than 10 days older than the anchor: L1's own weights (no tilt), and the evening
  message says so. The tilt never trades on a guess.
- The yearly re-test (Addendum 14) evaluates L1 with the tilt (research/allweather16.py's V1), same tests, same
  benchmark, same Sharpe definition as Addendum 14 (Addendum 16 found the verdicts identical on either definition).
- Before going live, the engine replay (research/replay_core.py) must match V1's research numbers within 1 point a
  year in A and B, as Addendum 13 required of L1.

## Addendum 17 (9 Oct 2026, before any return is downloaded): do NSE's quality / value / low-volatility indices add anything?

Why: the owner's "investment framework" asks whether Buffett / Graham-style company selection works in India. NSE
publishes indices that select companies mechanically on such rules (quality: ROE, debt/equity, earnings stability;
value: earnings yield, book-to-price, dividend yield; low volatility). They are a blind test only AFTER each index's
launch: the years before it were back-calculated by people who already knew the outcome.

Indices (launch date from each index's NSE factsheet; benchmark = its parent universe, total return, niftyindices.com):
| index | launched | benchmark |
|---|---|---|
| Nifty100 Quality 30 | 19 Mar 2015 | Nifty 100 |
| Nifty50 Value 20 | 28 Mar 2014 | Nifty 50 |
| Nifty100 Low Volatility 30 | 8 Jul 2016 | Nifty 100 |
| Nifty Alpha Low-Volatility 30 | 10 Jul 2017 | Nifty 200 |
| Nifty Quality Low-Volatility 30 | 10 Jul 2017 | Nifty 200 |
| Nifty200 Quality 30 | 17 Apr 2018 | Nifty 200 |
| Nifty Midcap150 Quality 50 | 24 Oct 2019 | Nifty Midcap 150 |
| Nifty Dividend Opportunities 50 | 22 Mar 2011 | Nifty 500 |
Reported only (launched 2024, or launch date not found: too little live history): Nifty200 Value 30, Nifty500 Quality
50, Nifty500 Value 50.

Method (research/factor_indices17.py): daily total-return values; live period = the first month-end after launch to
the last month-end available. CAGR, worst fall, Sharpe over cash (the liquid sleeve of research/allocation20.py), and
monthly excess returns over the benchmark: annualised mean and t = mean / sd x sqrt(months). An ETF on a factor index
costs more than a broad one, so 0.30 point a year is taken off the factor index before the excess is judged.

Verdict per index (live period only):
- EDGE SHOWN: excess after the 0.30-point cost > 0, t >= 2, and Sharpe over cash above the benchmark's.
- CONSISTENT, NOT PROVEN: excess > 0 but t < 2.
- NO EDGE: excess <= 0.
The back-calculated years are reported beside, labelled as such, and judge nothing.

What changes: nothing live (Addendum 14: a live rule needs 20 years of blind evidence, and these have 7-15). The result
goes to the owner and steers Addendum 18 (the company-level test): pillars whose index shows no edge are not expected
to carry much weight there either.

## Addendum 18 (9 Oct 2026, before any company result is downloaded): the owner's investment framework, scored blind

The owner's framework ("Indian Equity Investment Framework - Historical Validation") weights seven pillars: business
economics and moat 20, growth runway 15, financial strength and cash conversion 15, management and governance 15,
valuation and margin of safety 20, growth inflection and catalysts 10, downside resilience 5. The weights are the
owner's, taken as given (hypotheses, never fitted). Each pillar is measured ONLY with numbers a company had filed on NSE
before the decision date, so the score is computed by code and cannot use hindsight.

Why no hand-written memos: the analyst (Claude) knows how Titan, Satyam, Yes Bank and the rest turned out; a memo
"as of 2008" would be contaminated. The named companies are reported as checkpoints of the mechanical score instead.

Data (research/fundamentals18.py): NSE's own filing records (www.nseindia.com/api/corporates-financial-results, annual,
standalone), with the date each was filed; the result itself from NSE's archive pages (FY2005-FY2017) and XBRL files
(FY2018 on, the full-year context). Prices: the bhavcopy panel of Addendum 12, corporate actions applied. Dividends
paid: the dividend rows of NSE's corporate-action files (from 2010). A result counts from the session after it was
filed; FY2005-FY2006 results (no filing date on NSE) count from 1 Jul 2007.

Decision dates: 15 July (or the next session) each year 2008-2025: the March year's audited results are due by the
end of May. Universe on each date: the 500 most traded EQ stocks (median daily value, 126 sessions; 252 sessions of
history; ETFs excluded), from the bhavcopy, so failed and delisted companies are in it. A company enters the scoring
only with an annual result for a year ending within the last 16 months.

Measures (each turned into a percentile among the scored companies that day; a pillar = the mean of its measures'
percentiles; a missing pillar counts 50; the score = the weighted sum, 0-100):
1. Business economics (20): return on equity (profit / (paid-up capital + reserves)), mean of the last 3 years
   (at least 2); its stability (minus the standard deviation of ROE over up to 5 years); non-financials also the
   operating margin ((profit before tax + interest) / revenue), mean of 3 years.
2. Growth runway (15): revenue growth a year over up to 3 years (at least 2); profit growth over the same span (a
   loss at either end ranks last). Market size and share cannot be measured from filings: not scored.
3. Financial strength (15): non-financials: interest cover ((profit before tax + interest) / interest, capped at 50;
   no interest = 50). Financials: not measurable (no balance sheet in the results before 2016): pillar left out and
   the other weights scaled up. Cash conversion: cash-flow statements are in results only from FY2020: not scored.
4. Management and governance (15): share count growth over 3 years (dilution; lower is better); days from the year's
   end to the filing of its results (lower is better); a dividend paid in the 12 months before the date (yes ranks
   above no). Promoter pledges are in the results only for some years: not scored.
5. Valuation (20): earnings yield (last year's EPS / price) and book-to-price (book value per share / price) at the
   decision date's close.
6. Growth inflection (10): last year's revenue growth minus the 3-year rate; last year's profit growth.
7. Downside resilience (5): loss years among the last 5 (fewer is better); price volatility over the last year
   (lower is better).
Financial companies: NSE's bank format, or interest cost at least 35% of revenue (lenders) - their margin is profit /
revenue and pillar 3 is left out.
Disqualified (never bought, still scored and reported): a loss in the last year; negative or zero net worth;
non-financial interest cover below 1.5; latest annual result older than 16 months.

Portfolio rule: on each decision date the 20 highest-scoring qualified companies, equal weights, bought at the next
session's close and held a year (then the next date's 20); a company that stops trading is sold at its last close;
delivery costs (backtest Costs, ₹1 lakh a position, 0.05% slippage a side). Prices only, no dividends, on both sides.

Pass bars (A = decisions 2008-2015, B = 2016-2025; the weights are not fitted, so both are out of sample):
- Portfolio: after costs, CAGR AND Sharpe over cash above the equal-weighted universe (all 500, before costs) in A
  and in B.
- Ranking: the yearly rank correlation between the score and the next 12 months' return, averaged over the 18 years,
  t >= 2, and positive on average in both A and B.
- Multibaggers: among the top fifth by score, the share that went up 3x or more within 5 years at least 1.5 times
  the universe's share, in A and in B (B: decisions to 2021).
Reported without a bar: worst falls; the Nifty 500 total-return index (it includes dividends, the portfolio does not);
sensitivity (equal pillar weights; each pillar left out; 10 / 30 / 50 stocks; two-year holds); the named companies'
score and rank on every date they were in the universe (Titan, Asian Paints, Eicher Motors, Infosys, HDFC Bank, Bajaj
Finance, Satyam, Yes Bank, DHFL, Kingfisher Airlines); the false positives (top-20 picks that lost half their value)
and missed winners (3x in 5 years from the bottom half).

What changes: a pass goes to the owner (Telegram long-term ideas, a paper portfolio first; Addendum 14 keeps real money
on the rulebook). A fail is reported as plainly, with which pillars carried signal and which did not.

## Addendum 18a (9 Oct 2026, after checking the parser on 4 companies, before any scoring or outcome): data handling

Found while checking the parser (Titan, Yes Bank, Bajaj Finance, Satyam), fixed before anything is scored:
- Unit slips in companies' own filings (e.g. Bajaj Finance's reserves filed in crore on a lakh form): an amount more
  than 50 times smaller or larger than the median of the same company's other years is treated as missing. A profit
  before tax of exactly 0 with a non-zero net profit (a misread bank form) is missing. Reserves of 0 = not given.
- Share counts change with splits and bonuses, so per-share numbers are not used across years. Valuation instead:
  market value at the result's filing = shares in the result (paid-up capital / face value) x that day's actual
  close; moved to the decision date by the adjusted price return. Earnings yield = profit / that value; book-to-price
  = (paid-up capital + reserves) / that value.
- Dilution: shares now / shares 3 years earlier, corrected by NSE's official bonus and split factors between the two
  filings (available from 2010; before that the measure is missing and the pillar uses its other measures).
- A year's result whose document NSE no longer serves (e.g. Yes Bank FY2018, Bajaj Finance FY2019) is missing; the
  measures use the years that exist.

## Addendum 19 (9 Oct 2026, before any of it runs): a midcap sleeve in the live mix

Owner's question: why mostly large caps? The Addendum 13 candidates never included midcaps; this tests one.
Live rule today: V1 (L1 with the Addendum 16 valuation tilt).

- M10: Nifty 50 35 / Next 50 15 / Midcap 150 10 / MON100 20 / gold 10 / liquid 10 (equity stays 80%), with the same
  valuation tilt on the Nifty 50 sleeve (expensive: Nifty 50 20, liquid 25; cheap: Nifty 50 45, liquid 0).
- Neighbour M15: Midcap 150 15, Nifty 50 30 (tilt: 15 / 45 ... the same 15-point shift; cheap: 40, liquid 0).
- Midcap sleeve: the Nifty Midcap 150 total-return index minus 0.25% a year (MID150BEES, Nippon India, about
  ₹17 crore traded a day; its expense ratio rounded up). Its own price history (NETFMID150 from 2020, MID150BEES
  from 2022) is compared with the sleeve to report the tracking difference. The index's early years are calculated
  backwards, but by a plain market-cap rule (ranks 101-250), not one picked for its results.
- research/allocation20.py's simulation, costs, 5-point bands and year-end rebalance; Sharpe over cash
  (Addendum 16); A = 2006-2015, B = 2016-2026.
- M10 replaces V1 only if its Sharpe over cash is higher than V1's in A AND B, its worst fall is no more than 2
  points deeper in either (Addendum 13's L2 bar), and M15 also has a higher Sharpe than V1 in A and B. A pass goes
  to the owner as a rulebook change; the yearly re-test would then evaluate the new mix.

## Addendum 19a (9 Oct 2026): the owner adopts M10

Rule L1 in trader/rulebook.yaml becomes M10: NIFTYBEES 35 / JUNIORBEES 15 / MID150BEES 10 / MON100 20 / GOLDBEES 10 /
LIQUIDCASE 10, bands and year-end unchanged; the valuation tilt moves the same 15 points (expensive: NIFTYBEES 20,
LIQUIDCASE 25; cheap: NIFTYBEES 45, LIQUIDCASE 0). The rule keeps its name L1 (the autopilot's state refers to it).
Fallback F unchanged. The yearly re-test evaluates the rulebook's own weights and tilt (research/midcap19.py
rulebook_fn), same tests and benchmark. Before going live the engine replay must match M10's research numbers within
1 point a year in A and B. The switch itself is an ordinary rebalance at the next review (the Nifty 50 holding is 10
points over its new target, beyond the 5-point band).

## Addendum 20 (10 Oct 2026, before any F&O data is downloaded): index options, tested on 14 years, then on paper

Owner's request: research F&O and run it on paper for 3-4 weeks before anything else. Context: SEBI's study (Sept
2024) found 93% of individual F&O traders lost money in FY22-FY24. A Nifty lot today is 65 units, about ₹14.6 lakh of
index exposure, almost 4 times this account (₹3.84 lakh), so only defined-risk positions (where the most that can be
lost is known when the trade is placed) are considered; buying or selling futures, and selling options without a
protective option, are excluded.

Why 3-4 weeks of paper cannot decide anything: that is one monthly cycle. Option-selling strategies win most months
and lose rarely but heavily, so a few good paper weeks prove nothing. The evidence is the 14-year test below; the paper
run checks the mechanics (strike choice, prices, costs, margin, messages) on live data.

Data (research/fo20.py): NSE's daily F&O files, Jan 2012 - Oct 2026 (the old fo...bhav.csv files to Jul 2024, then the
UDiFF files), Nifty index options and futures only. Prices: the day's close of each option; only strikes that traded
that day can be chosen (the nearest traded strike to the target). Expiry: the option is worth its intrinsic value at
the Nifty 50's close on expiry day (NSE's final settlement).

Monthly cycle: each position is opened at the close of the first session after a monthly expiry, in the next monthly
expiry, and held to expiry (no stop, no adjustment), so months never overlap.
- S1, bull put spread: sell the put 3% below the Nifty, buy the put 6% below. Most that can be lost: the gap between
  the strikes minus the premium received.
- S2, iron condor: sell puts 4% below and calls 4% above, buy puts 7% below and calls 7% above.
- S3, crash insurance: the Nifty held, plus a put 5% below bought every month; against the Nifty held.
- S4, trend option buying (the common retail approach): if the Nifty is above its 200-day average buy the call at the
  current level (at-the-money), else the put; held to expiry.
Neighbours: S1 with 2%/5% and 4%/7%; S2 with 3%/6% and 5%/8%. Weekly versions of S1 and S2 (weekly expiries, 2019 on)
are reported without a bar.

Costs on every leg: ₹20 brokerage an order; STT 0.0625% of the premium on option sales to Sep 2024 and 0.1% after, and
0.125% of the settlement value on bought options that expire in the money; exchange charges 0.05% of the premium;
stamp duty 0.003% on purchases; SEBI fee ₹10 a crore; GST 18% on brokerage, exchange and SEBI fees; slippage
max(₹1, 2% of the premium) a unit on every leg.

Measured per lot of 65 units (today's size, so rupee results are comparable across years) on a ₹3.84 lakh account
holding one position at a time, the rest earning the liquid fund's rate. Periods: A = Feb 2012 - Dec 2018,
B = Jan 2019 - Sep 2026.

Pass bars (S1, S2, S4), in A AND in B:
- an edge: mean monthly profit after all costs above zero with t >= 2 (months are independent here);
- survivable: the account's worst fall at most 25%, and no single month losing more than 10% of the account;
- worth it: the account's yearly return at least 2 points above the liquid fund alone;
- S1 and S2 only: both neighbours also profitable on average in A and B.
S3 passes if, in A and B, the insured Nifty has a higher Sharpe over cash than the Nifty held and a worst fall at
least 10 points smaller.

What changes: every strategy runs on paper for 3-4 weeks from the next monthly expiry, with a plain-language Telegram
report each evening (position, value, profit or loss, what happens next). Real money only for a strategy that passed
here, one lot, defined risk, and only on the owner's decision after the paper run.

## Addendum 20a (10 Oct 2026, before the paper run and before any F&O result): what the paper run trades

The monthly strategies open at the first session after a monthly expiry (late October 2026) and expire about four
weeks later, so a 3-4 week paper run sees one monthly cycle opened and valued daily, not settled. To see complete
cycles, the paper run also trades the weekly versions of S1 and S2 (opened the session after each weekly expiry,
held to the next). Paper prices are NSE's closing prices from the day's F&O file (published each evening), the same
prices and cost model as the test; the paper run reports, for each position, what it would be worth if closed at
today's close. Nothing in the paper run changes a verdict: the 14-year test decides, the paper run checks the
mechanics.

## Addendum 21 (10 Oct 2026, before any of it runs): one disciplined search for a short-term edge

Owner's question: can history give a strategy with an outstanding record? A search over many rules finds a good-
looking one by chance, so the whole search space, the selection rule and the bar are fixed here, the choice is made on
2012-2019 only, and each family's single choice is tested ONCE on 2020-2026. A high win rate is not the goal: the
measure is profit after all costs.

Search space (256 variants, counted in research/search21.py and checked):
- F1 swing, stocks (the 100 most traded EQ stocks each month, survivorship-free; long only; 10 stocks a day,
  equal weights; bought at the next open, sold at the close H sessions later, H in {1, 5, 10, 20}; delivery costs;
  with and without the market filter (Nifty 50 above its 200-day average)): reversal (the 10 worst over N in {1, 3,
  5, 10} sessions); breakout (closes at an N-session high, N in {20, 55, 120, 250}, the 10 nearest to it by
  momentum); momentum (the 10 best over N in {21, 63, 126, 252} sessions, skipping the last); pullback in an uptrend
  (above the 200-day average, 2-day RSI below {5, 10, 20}); volume surge (an up day on {2, 3} x the 20-day volume).
  17 signals x 4 holds x 2 = 136.
- F2 intraday, stocks (same universe; bought or sold short at the open, closed at the same day's close; intraday
  costs): buy gap-downs of {1, 2, 3, 4}%+, short gap-ups of {1, 2, 3, 4}%+, buy yesterday's 10 biggest losers, short
  yesterday's 10 biggest gainers; with and without the market filter. 10 x 2 = 20.
- F3 Nifty options (research/fo20.py data, costs and strike choice; monthly and weekly cycles; one lot):
  bull put spreads, short strike {2, 3, 4, 5}% below, width {2, 3}% (16), the same only when the Nifty is above its
  200-day average (16); iron condors, short strikes {3, 4, 5, 6}% away, width {2, 3}% (16); each of these 48 credit
  spreads held to expiry or closed once it can be bought back for half the credit received (x2 = 96); long
  straddle / strangle (at the money, 2% away; 4). 100.

Selection (2012-2019): for each variant, net profit per trade after costs, t-statistic with trades grouped by entry
date (stocks; Newey-West over the holding period) or by cycle (options). A variant qualifies only if its t >= 3.5
(the Bonferroni bar for 256 tries at 5%, one-sided) AND it is profitable in both 2012-2015 and 2016-2019. The
qualifying variant with the highest t in each family is that family's single choice; a family with none qualifying
has no choice and is reported as such.

Test (2020-2026, once): the choice passes if its mean net profit is above zero with t >= 2 AND, on the ₹3.84 lakh
account, it beats the benchmark (F1/F2: the Nifty 50 total return; F3: the liquid fund + 2 points a year) with a worst
fall no deeper than 25%. Caveat: 2019-2026 was already seen for Addendum 20's base option strategies (two of the 100
F3 variants); the rest of the space has not been looked at in that period.

What changes: a family choice that passes goes to a paper run (at least 3 months) and then to the owner. A search
with no pass ends the short-term research: the published video says so plainly.
