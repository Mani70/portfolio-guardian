# Portfolio Guardian (Phase 1)

Checks your INDstocks holdings every evening against rules you write in `config.yaml`,
and sends you a Telegram alert when one is breached. **It never places orders.**

Pre-loaded with the exit levels agreed on 3 Oct 2026:

| Rule | Trigger |
|---|---|
| HCL Tech | daily close below ₹1,195 |
| Waaree Energies | daily close below ₹2,300 |
| Shriram Properties | daily close below ₹60 |
| ITC | weekly close below ₹250 |
| Any single stock | above 15% of the portfolio |
| Any sector | above 25% of the portfolio |

## 1. Install (Python 3.10+)

```bash
cd portfolio-guardian
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Try it offline first

```bash
python -m guardian.main --mock --summary
```

This uses `sample_data/` (a copy of your holdings as of 3 Oct 2026) and should report
the IT and Financials sector alerts.

Run the tests: `python -m pytest -q`

## 3. Connect your INDstocks account

1. Go to **indstocks.com/app/api-trading/access-tokens** and generate an access token.
   No static IP is needed: INDstocks only requires one for placing orders, and the guardian only reads.
2. `cp .env.example .env`, paste the token into `INDSTOCKS_ACCESS_TOKEN`, then `chmod 600 .env`.
3. `cp config.example.yaml config.yaml` and adjust rules.
4. Check the token, then do a full run without sending anything:

```bash
python -m guardian.main --check-auth
python -m guardian.main --dry-run --summary
```

Check that every holding appears with the symbol you expect. If a rule's symbol
doesn't match what INDstocks returns, fix the symbol in `config.yaml`. Holdings that
get no price are listed in a warning; give those a `scrip:` override.

**Tokens expire every 24 hours.** For unattended runs, set up TOTP on the same
page and fill in `INDSTOCKS_CLIENT_ID`, `INDSTOCKS_MPIN` and `INDSTOCKS_TOTP_SECRET`
(when these are set they take priority over a pasted token). How the guardian handles it:

- It generates a token once and caches it in `.token_cache.json` (owner-only permissions),
  reusing it until the **daily 7 AM IST reset** (all INDstocks tokens die at 7 AM under SEBI rules). This matters because **INDstocks keeps only the newest TOTP token
  alive**: every new token kills the previous one, so anything else using your token would break.
- If the token is rejected, it regenerates **once** per run, then stops and tells you.
- It never retries a failed login, because 5 wrong codes lock token generation for 15 minutes.
  If logins fail with a code your authenticator app shows as valid, sync your computer's clock.

Read the warning in `.env.example` first: that file becomes as sensitive as your trading password.

## 4. Telegram alerts

1. In Telegram, message **@BotFather**, send `/newbot`, copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot, then get your chat id from **@userinfobot** into `TELEGRAM_CHAT_ID`.
3. Run `python -m guardian.main --repeat` once to confirm a message arrives.

Each breach is sent once; it re-arms when the condition clears. If a run fails
(expired token, network), you get a message saying so instead of silence.

## 5. Schedule it (weekdays, 15:45 IST)

**macOS / Linux** (`crontab -e`, machine clock in IST):

```
45 15 * * 1-5 cd /path/to/portfolio-guardian && .venv/bin/python -m guardian.main >> logs/cron.log 2>&1
```

**Windows**: Task Scheduler → Create Basic Task → Daily 15:45, Mon–Fri →
Program: `C:\path\to\portfolio-guardian\.venv\Scripts\python.exe`,
Arguments: `-m guardian.main`, Start in: `C:\path\to\portfolio-guardian`.

The laptop has to be awake at 15:45. Moving to a small cloud server comes later,
before any automated orders.

## Rule types

| Type | Needs | Fires when |
|---|---|---|
| `close_below` / `close_above` | `level`, `timeframe: day|week` | last **completed** candle closes beyond the level |
| `price_below` / `price_above` | `level` | live price beyond the level |
| `loss_from_cost` | `pct` | live price ≥ pct% below your average cost |
| `off_52w_high` | `pct` | live price ≥ pct% below the 52-week high |
| `max_position_weight` | `pct` | any stock above pct% of the stock portfolio |
| `max_sector_weight` | `pct` + `sectors:` map | any sector above pct% |

Running before 15:30 ignores today's unfinished candle; weekly rules wait for Friday's
close (holiday Fridays: evaluated from Saturday). For a stock you don't hold, add
`scrip: NSE_<SECURITY_ID>` from the instruments file (`GET /market/instruments?source=equity`).

## Notes from the API docs (checked against the full INDstocks documentation)

- Holdings return a bare `security_id` with no exchange. The guardian prices it as
  `NSE_<id>` and falls back to `BSE_<id>`; anything still unpriced is logged.
- `/generate/token` returns the token in a field named `token`. INDstocks marks this endpoint's
  response envelope as provisional, so both `{"token": ...}` and `{"data": {"token": ...}}` work.
- Historical data takes at most 5 instruments per call; the guardian batches accordingly.
- Error responses come in several shapes; the guardian reports `debug_info`/`message`/`error`
  so you see the real reason, and retries only server errors (5xx) and rate limits (429).

## Layout

```
guardian/broker.py    INDstocks REST client (read-only, rate-limited, retries 429)
guardian/rules.py     rule types + evaluation, completed-candle logic
guardian/state.py     one alert per breach
guardian/notifier.py  Telegram + console
guardian/main.py      CLI entry point
guardian/mock.py      offline client for sample_data/
tests/                unit tests
```

## Next: Phase 2 (swing-trading research)

Reuses `broker.py` for historical candles (1-year windows per call, paged), adds a
backtester for swing strategies on cash stocks, then paper-trades on the live feed.
No live orders until a strategy survives both.

---

# Phase 2: Backtester (research only, places no orders)

Tests swing strategies on real INDstocks daily history before any money is involved.

```bash
pip install -r requirements.txt          # adds pandas, numpy, matplotlib
python -m backtest.run --demo            # offline check with synthetic prices
python -m backtest.run                   # real history (first run downloads ~6 years, ~1 minute)
python -m backtest.run --strategy rsi2_pullback --years 3
```

Settings: copy `backtest.example.yaml` to `backtest.yaml` (universe, capital, slots, costs, parameters).
Results land in `results/<date-time>/`: `summary.csv`, `equity.csv`, `trades_<strategy>.csv`, `equity.png`.

**Daily strategies** (long only, cash stocks):
| Name | Idea |
|---|---|
| `sma_trend` | Buy when the 50-day average crosses above the 200-day; sell on the cross back |
| `donchian_breakout` | Buy a close above the prior 50-day high in an uptrend; sell below the prior 20-day low |
| `rsi2_pullback` | Buy a sharp 2-day dip (RSI(2) < 10) in an uptrend; sell when price closes above its 5-day average |
| `momentum_rotation` | Hold the top 10 by volatility-adjusted 6/12-month return (NSE momentum-index method), only stocks passing a Minervini-style trend template; monthly review; cash when the Nifty is below its 200-day average |
| `index_trend` | Hold the Nifty ETF while it's above its 200-day average (checked monthly), else a liquid fund |

Idle cash earns `cash_yield` (default 6%, a liquid fund) in every strategy, so comparisons are fair.

## Intraday: Opening Range Breakout, long and short

```bash
python -m backtest.intraday --demo       # offline check
python -m backtest.intraday              # real 5-minute history (first run downloads it)
python -m backtest.intraday --days 90
```

Rules (from Zarattini, Barbon & Aziz 2024, adapted to NSE): each day, take the 5 stocks with the
highest opening-5-minute volume relative to their 14-day average; go long above the opening range
if the first candle is bullish, short below it if bearish; stop at 10% of the stock's daily ATR;
square off at 15:15. It runs three versions: long+short, long only, short only.
Expect a low win rate (about 1 in 5): the strategy depends on rare big days. Settings live under
`intraday:` in `backtest.yaml`.

**How it keeps the test honest**
- Signals use the day's close; trades fill at the **next day's open**.
- A protective stop at entry − 3 × ATR(14) applies to every strategy; gaps below it fill at the open.
- Every trade pays slippage plus delivery costs (STT, exchange, SEBI, stamp, GST, ₹10 brokerage, DP charge).
- An extra year is downloaded as warm-up, so strategies and buy-and-hold start on the same day.
- The last 30% of the period is reported separately (out-of-sample). Choose parameters before
  looking at it; tuning until the past looks good is the most common way backtests mislead.
- Stocks with a >35% one-day move are skipped (often an unadjusted split or bonus); `--keep-jumps` overrides.

**Known limits**
- The universe is today's Nifty 50, which flatters the past (stocks that fell out aren't tested).
- Returns are before income tax (short-term capital gains 20%).
- History is cached in `cache/history/`; `--refresh` re-downloads it.

---

# Phase 3: Month-end momentum paper trading (no orders)

`momentum_rotation` was the only strategy that beat both the Nifty and an FD in the backtest,
including out-of-sample. Before any money goes in, `guardian.momentum` forward-tests it on live data
with an imaginary ₹5 lakh portfolio (the `capital` in `backtest.yaml`).

On the **last weekday of each month, after 15:35 IST**, it:
1. tops up daily prices for the `universe` in `backtest.yaml` (cached, a few API calls),
2. ranks stocks with the same rules as the backtest (6/12-month risk-adjusted momentum, trend template,
   Nifty 200-day filter, keep holdings in the top 20, fill to 10),
3. rebalances the paper portfolio at that day's close, with slippage and delivery costs,
4. sends a Telegram message: market filter, top 10, BUY/SELL/HOLD list, and paper return vs NIFTYBEES and FD.

On every other day it exits immediately without contacting INDstocks, so it's safe to run daily.
`run_guardian.bat` already calls it after the guardian.

```bash
python -m guardian.momentum --preview --dry-run   # today's ranking and what it would trade; saves nothing
python -m guardian.momentum --force               # start tracking now (once), then month-end runs take over
python -m guardian.momentum --force --reset       # throw the paper portfolio away and start again
```

Files: `paper/momentum_portfolio.json` (positions, cash) and `paper/momentum_log.csv` (every paper trade).
If the laptop was off on the last weekday, run `--force` the next morning; it uses the latest completed
session and won't trade twice for the same day.

**When to consider real money:** after at least 3-6 month-ends, if the paper return is ahead of NIFTYBEES
and the drawdowns are ones you could sit through. Real orders would go at the next morning's open;
the paper version uses the close, which flatters it slightly.

---

# Phase 4: Strategy research (Oct 2026)

Every widely published method we could test with price data, on the same data and costs.

```bash
python -m backtest.run                             # 12 swing strategies + Nifty ETF, equal-weight hold and FD
python -m backtest.run --universe liquid --top 200 # same, on the 200 most liquid NSE stocks (first run: 10-20 min download)
python -m backtest.intraday                        # tops up the 5-minute cache
python -m backtest.intraday_lab                    # 11 intraday methods, long and short, all stocks and the 10 most volatile
```

**Swing methods** (`backtest.run`): Minervini SEPA breakout, Weinstein Stage 2, leader pullback,
Turtle-style Donchian, Connors RSI(2), 50/200 trend, NSE momentum, 52-week high (George & Hwang),
Clenow *Stocks on the Move*, low volatility, equal weight with a 200-day market filter, and the index filter alone.

**Compare against "Equal-weight hold", not the Nifty ETF.** The universe is today's Nifty 50, so simply
holding those stocks looks great in hindsight (survivorship bias). A rule only adds value if it beats
holding the same stocks.

**Data fixes:** INDstocks back-adjusts splits and bonuses only so far back. Such boundaries
(HDFCBANK, KOTAKBANK, TRENT) are now detected and back-adjusted instead of the stock being dropped.

**Intraday methods** (`backtest.intraday_lab`): opening range breakout (15 and 30 minutes, with a
market filter), Zarattini/Aziz/Barbon noise area, VWAP trend, late-day momentum (Gao et al.),
gap fade, gap-and-go and overreaction fade. Each is reported long+short, long only and short only.
The t-stat is computed per day; below about 2 means the result can't be told apart from luck.

---

# Phase 5: The trading engine (`trader/`)

Trades only when a strategy that passed the research gives a signal. Most days it does nothing.
**Paper trading is the default and always runs; real orders need several deliberate switches.**

Strategies (see `research/FINDINGS.md` for the evidence, `trader.example.yaml` for settings):
| Strategy | Kind | What it does | Status |
|---|---|---|---|
| `core_allocation` | long-term, ETFs | Fixed mix NIFTYBEES 35 / JUNIORBEES 15 / MID150BEES 10 / MON100 20 / GOLDBEES 10 / LIQUIDCASE 10; month-end check, all back to target when one is 5+ points off and every year-end (partial sells, top-ups); valuation tilt on the Nifty 50 share (dividend yield) | **passed the 20-year test** (Addenda 13, 16, 19) |
| `trend_allocation` | swing, ETFs | Month-end: hold each of NIFTYBEES, JUNIORBEES, MON100, GOLDBEES (¼ each) while above its 200-day average, else cash | failed the 20-year test (Addendum 12) |
| `momentum_rotation` | swing, stocks | Month-end: top 10 Nifty 50 stocks by risk-adjusted momentum; cash when Nifty < 200-day average | failed the 20-year test (Addendum 12) |
| `gap_reversal` | intraday | 09:20: buy a stock that opened 3%+ lower on a calm market day; stop 1 ATR; exit 15:15 | **paper only**; no real-money intraday (Addendum 13) |

## Setup
```bash
cp trader.example.yaml trader.yaml       # set capital; leave mode: paper
python -m trader.run check               # token, funds, tick sizes, gate status
python -m trader.replay --start 2016-01-01 --strategy trend_allocation   # replay the real engine on history
```

## Daily schedule (Windows Task Scheduler, weekdays)
| Time | Script | What happens |
|---|---|---|
| 09:10 | `run_trader_intraday.bat` | scans every 5 minutes until 15:15 square-off; trades only strong signals |
| 09:30 | `run_trader_swing_check.bat` | records opening fills; re-prices unfilled orders within 3% of plan, else cancels |
| 15:45 | `run_trader_swing_plan.bat` | month-end decisions; orders go in as after-market orders for the next open, Telegram lists them |

The 15:45 plan arrives on Telegram the evening before; `python -m trader.run cancel` before 09:00 stops it.

## Going live (only when you decide to)
1. Let paper run until `python -m trader.run check` shows the strategy's gate as passed
   (ETF trend: 4 filled orders over 60+ days; momentum: 10 over 60+ days; intraday: 60 trades with t >= 2).
2. Whitelist a **static IP** on the INDstocks Access Tokens page (home broadband IPs usually change; a small
   cloud server with a fixed IP is the usual answer). Without it every order is rejected.
3. Set up TOTP in `.env` so the 09:10 job can log in by itself.
4. In `trader.yaml`: `mode: live` and `live: true` on that strategy only. Start with a small `capital`.

How much real money is used: each live swing strategy is planned at `capital.swing` x its `capital_share`
(plus its own profits/losses). If the account holds less than that (free cash + what the live strategies
already hold), positions are sized down to the money that is there (`capital.live_from_account: true`, the
default), and the evening Telegram says by how much. Money above the plan is never touched; `capital.reserve`
keeps a fixed amount untouched too. Every live buy is also checked against INDstocks' free funds just before it
is sent and cut to what they cover; a buy that can't be paid for is skipped with an alert and retried the next
evening. The 09:05 heartbeat shows free funds against the live plan.

## Corner cases (what the bot does by itself, and what it leaves to you)

| Situation | What happens |
|---|---|
| Bonus issue or split on a stock the bot holds | Price history no longer matches the position after a standard ratio (1:1 bonus = half, etc.). The share count and average price are adjusted once the account has GROWN by the new shares (usually a day or two after the ex-date); until then its sells are held and one Telegram message says so. No new shares within 3 sessions: treated as a real fall (`trader.run split --stock X --ratio 1` says so at once). If you buy or sell that stock yourself while the bot waits, check its message: your trade can stop (or, rarely, fake) the confirmation. Total cost unchanged (`trader/corporate.py`). By hand: `trader.run split --stock HDFCBANK --ratio 2` (new shares per old share). |
| Ex-date of a bonus/split during market hours | The 5-minute watch says the drop is close to a bonus/split ratio and that it is a real crash unless one was announced. A sell due that morning waits a day. |
| Big dividend (ex-dividend date) | The first fall alert reminds you the price drops by the dividend; dividends go to your bank account and are not in the monthly report. |
| Demerger, rights entitlement, IPO allotment, your own buy | New shares the bot didn't buy are named once in the morning heartbeat; the bot does not manage them. |
| Merger or delisting of a held stock | The morning heartbeat shows a holdings mismatch; `trader.run reconcile --fix` drops the bot's record. |
| A buy INDstocks accepted and then rejected for its own problem ("problem in placing your trade... try again") | The strategy's decision is reopened: the evening plan (16:10) decides again and re-places it for the next open. At most 3 times a week per strategy; other rejections wait for the strategy's next decision. |
| A sell rejected, or one that expired unfilled at the close | Sent again at the next morning check while that position is held. After 3 rejections in a week it stops and asks you to sell by hand. |
| A sell cancelled outside the bot (by you in the app, or by the exchange) | Told on Telegram, not re-sent: the strategy sells at its next decision, or keep the stock with `trader.run adopt --release SYM`. |
| You want to keep a few shares of a stock the bot is about to sell | `trader.run keep --stock RELIANCE --qty 1` (weekdays after 16:00 or before 08:55): the pending sell is cancelled and re-placed for the rest at the same limit; the kept shares are yours and the bot ignores them. Don't edit the sell in the app. |
| An old order INDstocks can no longer cancel (gone from its order book) | Telegram alert; after checking the app, `trader.run cancel --forget` marks it cancelled and a sell is re-sent next morning. |
| A stock stuck at its lower price limit | A sell rejected for being outside the day's price band is re-sent at the last price, so it queues at the limit. |
| Exchange holiday on the last weekday of a month | NSE's holiday list (`trader.run holidays`, Mondays 08:37, saved in trader/state/holidays.json) makes the session before it the month-end. A holiday NSE adds at short notice: put it under `holidays:` in trader.yaml (YYYY-MM-DD). With no list (download failing) the bot falls back to weekdays and the rebalance runs one session late; the morning heartbeat warns. Paper after-market orders placed before a holiday fill at the session after it. |
| Your own alert rules (config.yaml, 15:50 check) | Off on the server since your holdings went to the bot; the 5-minute watch still alerts on sharp falls in every holding. To use them again, add `50 15 * * 1-5 $PG guardian` back to deploy/oci/crontab. |
| Broker down at 16:10 on rebalance day | The rebalance is retried each evening in the first week. |
| Nifty 50 changes (end of March / September) | `trader.run universe` (Mondays 08:35) downloads NSE's list; momentum sells stocks that left at its next month-end. |
| Not handled - check yourself | Weekend special sessions (Budget day, Muhurat trading): after-market orders placed on Friday may execute there. NSE lists the Diwali (Muhurat) day as a holiday: if it is a month's last weekday the rebalance is decided the day before, and its after-market orders may fill in the short Muhurat session rather than the next regular open. Exchange surveillance lists (ASM/GSM) can block or restrict orders in small caps; the Nifty 50 universe is not affected. |

## Paper experiments (lab)
`trader/experiments.yaml` (shipped with the code; trader.yaml is not touched) lists strategies that run on the
server beside the real ones, in their own journal (mode "lab"), with notional money. They never send a real order,
whatever trader.yaml says, run after the real engines at the evening plan and the morning check, and a failure in
them is logged without stopping anything real. `trader.run status` lists them; the monthly report has a section.
Running now (research/PREREGISTRATION.md Addendum 9): momentum split into 3 tranches reviewed on the 7th, 14th and
last session of each month, beside one portfolio reviewed on the last session (the live rule), ₹3.45L each.
A strategy can be given `review_session: 7` (or `last`) to be reviewed on that session of each month; it then
starts on its own day and a missed review is caught up within 3 sessions.
Also running (Addendum 10): the single portfolio with idle cash parked in a liquid ETF, beside the unparked one.
And (Addendum 11): the single portfolio with a second check - cash also while NSE's Quality 30 index is below its
150-day average (`quality_filter:`). The index closes come from NSE's daily index file, read at the evening plan
(`trader.run indices` updates them by hand; trader/index_data.py).

### Parking idle cash (`park:`)
`park: {symbol: LIQUIDCASE, min: 25000}` under a swing strategy: every evening, when its idle money (its capital
minus what it holds) is at least `min` and it has no review waiting and no order working, the bot buys the liquid
ETF with it (one after-market order, allowed above `max_order_value` up to the strategy's own capital, or
`park.max_order_value` if set). When a review has buys to make, the whole ETF position is sold with that evening's sells; with real
money the buys go in the next evening, when the sale money is free - exactly the buys the review chose (no new
ranking), and never smaller because the money is still in the ETF. No parking when a review is due within 2
sessions, while orders are working, or after `trader.run cancel` until the strategy's next order. To stop parking,
remove `park:`; the ETF is then sold at the strategy's next review. The parked ETF does not count as one of the
strategy's stocks, and the price watch ignores liquid ETFs. Its gains are taxed at your slab rate (debt fund); the monthly report lists them apart from the
equity tax estimate. Research: +1 point a year for momentum after costs, nothing for the ETF trend (FINDINGS.md,
Addendum 10). On paper only for now; for real money add it to momentum_rotation in trader.yaml.

## Safety switches
- `STOP` file in the project folder: no new entries (exits still allowed). `python -m trader.run flatten` closes intraday positions.
- Limits: max order value, orders per day, intraday positions/entries per day, 1.5% daily intraday loss limit,
  0.5% risk per intraday trade, LIMIT orders only, 2 orders/second (SEBI's retail threshold is 10).
- An order is written to the journal before it is sent; if a reply is lost it is looked up in the order book
  by its tag and never blindly resent.
- A live strategy that loses 8% of its capital goes back to paper automatically.
- Everything is in `trader/state/journal.db` (signals, orders, fills, trades); `python -m trader.run status` shows it.

## The final rules (research/FINDINGS.md Addendum 12 and 13)
| Horizon | Rule | Evidence (2006-2015 out of sample / 2016-2026, with dividends) |
|---|---|---|
| Long-term | `core_allocation`: the 80%-equity ETF mix above | 14.9% / -48% worst fall and 15.2% / -27%, against the Nifty 50 held: 12.1% / -60% and 11.4% / -38% |
| Momentum | the published Nifty200 Momentum 30 ETF beat the Nifty 50 (17.2% and 15.8%); a 200-day brake on it did not | not added as a second live strategy: one live strategy, one set of rules |
| Intraday | none with real money | SEBI FY23: 71% of individual intraday traders lost money; no method here passed |

### Autopilot (research/PREREGISTRATION.md Addendum 14) - the bot runs the rulebook by itself
`trader.yaml` then needs only your own settings:
```yaml
mode: live
autopilot: {enabled: true, reserve: 10000}   # rupees never invested; optional paper_capital for the paper control
```
| What | How it happens |
|---|---|
| Which strategies run | `trader/rulebook.yaml` (versioned): the active rule as one long-term `core`, gap_reversal on paper; `trader.yaml`'s own `strategies:` are ignored |
| Money | ALL free cash in the INDstocks account above `reserve`. New deposits are invested within a day or two (evening cash sweep, buys only, from Rs 10,000 or 2% of the portfolio); withdrawals shrink it |
| Old strategies | Their live holdings are handed to the core at cost on the next run (Telegram says so), then they stop |
| Rebalancing | Month-end check, 5-point bands, every year-end; buys that need sale money wait a day |
| Valuation tilt (Addenda 16/16a) | At the month-end review the Nifty 50's dividend yield is ranked against every day since 1999 (niftyindices.com, refreshed before each evening plan): lowest 20% = expensive, NIFTYBEES 20 / LIQUIDCASE 25; highest 20% = cheap, NIFTYBEES 45 / no LIQUIDCASE; otherwise the usual weights. Missing or stale data = no tilt. The health message shows the current reading |
| Changing the rule | Only the yearly re-test (Saturdays in January, `trader.run retest`): L1 vs the Nifty 50 on the full history and the last 5 years; the pre-set fallback F when L1 fails, back to L1 when it passes. Result on Telegram; state in `trader/state/autopilot.json` |
| New code | `deploy/oci/autodeploy.py`, weekdays 17:15: GitHub `main` is tested in a separate checkout and deployed only if every test passes; else the running version stays and Telegram says why |
| Your controls | deposits / withdrawals, `STOP` file, `trader.run cancel`, `mode: paper`, merging pull requests |

The first time: update the server once by hand (the command above; it installs the new schedule with the nightly
deploy), then put the two lines above in `trader.yaml`. From then on nothing is manual.

Switching by hand instead (without the autopilot; after the 16:30 backup, outside market hours):
1. `git pull` the code (README: Updating the server from GitHub), then in the server's `trader.yaml`: add the
   `core_allocation` block from `trader.example.yaml` with `enabled: true`, `live: true`; set `live: false` on
   `trend_allocation` and `momentum_rotation`. The core's paper gate (3 filled orders) can't be met before its
   first review; `override_gate: true` skips it (Addendum 12: the 20-year test replaces paper months; the engine
   replay, `research/replay_core.py`, checks the code against the research).
2. `python -m trader.run transfer --from trend_allocation --to core_allocation` (asks first): the ETFs the trend
   strategy holds become the core's, at their cost, so they are trimmed or topped up instead of sold and re-bought.
3. `python -m trader.run preview`: what the core would trade at the next month-end with today's prices.
At the month's last session the core buys its mix (orders for the next open, listed on Telegram as always;
`trader.run cancel` before 09:00 stops them). Buys that need money from same-evening sells wait a day.
Taxes: each rebalance can realise gains (about 16% of the portfolio is traded a year).

### Daily swing ideas on Telegram (information only - never traded)
`trader.run insights` (weekdays 19:30, again 21:30 if NSE's files were late; sends once a day) screens every NSE
stock on the day's official data (bhavcopy, delivery %, splits/bonuses) and sends up to 5 BUY ideas and 5 AVOID/EXIT
ideas, each with its facts (trend, NSE-style momentum, 52-week high/low, relative strength, delivery, liquidity,
volatility), the official filings, the risks and the level that would invalidate it; your INDstocks holdings that
look weak are flagged. When nothing qualifies it shows how many stocks each rule removed. In a falling market (Nifty
ETF below its 200-day average) there are no BUY ideas, only a labelled watchlist. Past ideas are recorded
(`cache/insights/ideas.csv`) and their 20-session results against the Nifty ETF are reported. The first run downloads
~400 sessions of NSE files (about 20 minutes). Rules: `trader/insights.py` RULES - a professional-style screen.
Tested over 2011-2026 (`research/insights20.py`, FINDINGS Addendum 15): BUY ideas beat the Nifty 50 after costs in
2011-15 but not reliably in 2016-26, and AVOID ideas did no worse than the Nifty, so neither list is a proven signal
(the report says so). The rules are not re-tuned to that result.

### F&O practice run (paper only - never an order)
`trader.run fo-paper` (weekdays 20:15, again 22:45 if NSE's F&O file was late; reports once a day) practises Nifty
option strategies on paper for 4 weeks with NSE's real closing prices: bull put spreads and iron condors (monthly and
weekly), crash insurance and trend option buying, all with defined risk (research/PREREGISTRATION.md Addenda 20/20a).
Each evening one plain-language Telegram message says what is open, what it would be worth if closed today, the most
each position can lose, and the running total. The verdict comes from the 14-year test (`research/fo20.py`), not
from the paper weeks; state in `trader/state/fo_paper.json`.

### Daily education Reel for Instagram (trader/reel)
`trader.run reel` (every day 07:40 IST) makes a 60-90 second vertical Hinglish video and sends it to you on Telegram
with a ready-to-paste caption; you watch it and post it yourself. Each Reel: a hook, one plain money lesson, one fact
from this project's own tests, and (when there is one) an official NSE announcement from a heavily traded company,
explained - with how shares reacted to that TYPE of news in the past (research Addendum 22), never a call on the share.

Education only (SEBI's Jan 2025 circular: unregistered people may not give investment advice or make performance
claims). The script is written by Claude from the facts the bot passes in, then checked by rules: no instruction to
buy or sell, no target or stop-loss for a share, no prediction, no promise, no price next to a named company. A script
that fails twice is replaced by a plain template. Every Reel ends with the same spoken disclaimer, and the caption
carries it too. Check SEBI's current rules before posting; this is not legal advice.

Setup (once): add three keys to the OCI Vault secret, next to the others -
`ANTHROPIC_API_KEY` (console.anthropic.com), `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` (elevenlabs.io: pick a
Hindi-English voice in the Voice Library and copy its voice ID). Optionally put your handle in trader.yaml:
```yaml
reel: {handle: "@yourhandle"}          # optional: voice_model: eleven_multilingual_v2 (best voice, ~2x credits)
```
Voice credits: the default model (eleven_flash_v2_5) uses about 20-22k ElevenLabs credits a month for a daily Reel
(Starter plan: 30k); eleven_multilingual_v2 sounds a little better and needs about 40-44k (Creator plan).
Without the keys the job still sends a silent video from a template, and its note says which key is missing.

# Phase 6: Running on an OCI server (Oracle Linux 9)

Files in `deploy/oci/`:

| File | What it does |
|---|---|
| `push.sh` | Run from Git Bash on the laptop: `bash deploy/oci/push.sh`. First time: copies the project and runs `setup.sh` on the server. Later: copies code only (the server's .env, configs, journal, logs and cache are kept) |
| `setup.sh` | One-time setup, safe to re-run: grows the disk, IST time zone + clock sync, updates, Python 3.12 venv, firewall (SSH only) + fail2ban, security updates on Saturdays only, log rotation, cron schedule, then runs the tests and a token check |
| `job.sh <job>` | Runs one job with a lock (never two copies), a time limit, a log in `logs/cron/<job>.log`, and a Telegram alert if it fails |
| `crontab` | The weekday schedule (IST): 09:05 health, 09:10 intraday, 09:12 watch, 09:30 swing-check, 16:10 swing-plan, 16:30 backup; Mondays 08:35 Nifty 50 list and 08:37 NSE holiday list refresh; 1st of the month 08:40 report; a notice after any reboot |
| `health.py` | Morning Telegram heartbeat: token, clock, disk, memory, last run of each job, positions, backup age, public IP. **No message on a weekday morning = server down.** |
| `backup.py` | Daily journal + config backup to `backups/` (30 kept); also to Object Storage if `OCI_BACKUP_PAR_URL` is set. Never includes `.env` |
| `alert.py` | Failure alerts with the log tail, secrets masked |

**Updating the server from GitHub** (instead of `push.sh` once the server folder is a git checkout; server data -
`.env`, `trader.yaml`, `config.yaml`, `trader/state/`, cache, logs, backups - is git-ignored and never touched):

```bash
ssh manipraocispaces_vm 'cd ~/portfolio-guardian && git pull --ff-only origin main \
  && .venv/bin/python -m pip install -q -r requirements.txt -r deploy/oci/requirements-server.txt \
  && sed "s#__APP__#$HOME/portfolio-guardian#g" deploy/oci/crontab | crontab - \
  && .venv/bin/python -m pytest -q tests | tail -2'
```
Deploy from `main` only, by hand (not from cron). `push.sh` remains for a brand-new server (it also copies `.env`).

Only one machine may run the jobs: two machines logging in with the same TOTP will invalidate each
other's tokens. Once the server runs, don't schedule anything on the laptop.

Useful commands on the server:

```bash
crontab -l                                   # the schedule
tail -f ~/portfolio-guardian/logs/cron/intraday.log
~/portfolio-guardian/deploy/oci/job.sh health    # send a heartbeat now
cd ~/portfolio-guardian && .venv/bin/python -m trader.run status
touch ~/portfolio-guardian/STOP              # block all new orders; rm STOP to resume
cd ~/portfolio-guardian && .venv/bin/python -m trader.run test-order   # live order-path check (asks first)
```

`test-order` places one real BUY of 1 NIFTYBEES 3% below the market (an after-market order outside market
hours), confirms it in the order book, cancels it and confirms the cancel. It proves the static IP, token and
order format before any strategy trades real money. Nothing executes, so nothing is charged.

## Secrets in OCI Vault (server)
`guardian/secrets.py` loads the secrets for every job. With `OCI_SECRET_ID=<secret OCID>` in the server's `.env`,
they come from ONE OCI Vault secret (a JSON object), read with the server's own identity (no OCI key on the
server); the day's first read is kept in RAM (`/dev/shm`, yours only) so a short Vault outage can't stop a job.
Without it, or if Vault can't be read, `.env` is used (the morning health message warns).
`python -m guardian.secrets` shows where they came from (never the values).

One-time setup (OCI console):
1. Compute > Instances > your VM: copy its OCID; note its compartment.
2. Identity & Security > Vault > Create Vault `pg-vault` (default vault, not a private one).
3. In the vault: Master Encryption Keys > Create Key `pg-key`, protection mode Software, AES 256.
4. From Git Bash on the laptop, copy the JSON to the clipboard without showing it:
   `ssh manipraocispaces_vm "cd ~/portfolio-guardian && .venv/bin/python -m guardian.secrets --json-from-env" | clip`
5. In the vault: Secrets > Create Secret `pg-secrets`, key `pg-key`, plain text, paste (Ctrl+V). Copy the secret's
   OCID. Copy something else afterwards to clear the clipboard.
6. Identity & Security > Domains > Default > Dynamic groups > Create `pg-vm`, rule:
   `instance.id = '<VM OCID>'`
7. Identity & Security > Policies (same compartment as the vault) > Create `pg-read-secret`:
   `Allow dynamic-group 'Default'/'pg-vm' to read secret-bundles in compartment <name> where target.secret.id = '<secret OCID>'`
   (vault in the root compartment: `in tenancy` instead of `in compartment <name>`).
8. `ssh manipraocispaces_vm "cd ~/portfolio-guardian && echo 'OCI_SECRET_ID=<secret OCID>' >> .env && .venv/bin/python -m guardian.secrets"`
   must print `secrets from: vault`.
9. After a morning whose health message says "secrets from OCI Vault": delete the secret lines from the server's
   and the laptop's `.env` (the health message lists any left), and delete old boot volumes that held a copy.
To change a secret later: Vault > Secrets > pg-secrets > Create Secret Version (the full JSON again); jobs pick it up
the next day, or at once after a reboot or `rm /dev/shm/pg-secrets-$(id -u).json`.
