# AI Trading System — Project Handoff

## Why this file exists

This document transfers the research history and current plan from the previous ChatGPT conversation into the repository so Codex can continue without relying on chat history.

## Current status

The system is at the end of the deterministic ORB baseline research phase.

The current control strategy is `ORB_BASELINE_2R`.

The next planned experiment is `ORB_RETEST_2R`.

The baseline must remain intact so every new strategy can be compared against it.

## Environment

- MacBook Pro / macOS
- VS Code
- Python 3.12.14
- Git 2.39.5
- PostgreSQL 16.15 via Homebrew
- Database: `ai_trading`
- Virtual environment: `.venv`
- Alpaca SDK: `alpaca-py 0.44.0`
- Alpaca paper trading account configured
- Alpaca historical 1-minute market data available
- Timezone: America/New_York

## Repository

```text
ai-trading-system/
├── .env
├── .gitignore
├── database/
│   ├── __init__.py
│   └── connection.py
├── data/
│   ├── __init__.py
│   ├── alpaca_client.py
│   ├── db_writer.py
│   ├── validator.py
│   └── historical_downloader.py
├── strategy/
│   ├── __init__.py
│   ├── session.py
│   ├── orb.py
│   ├── signals.py
│   ├── daily_levels.py
│   └── trade_setup.py
├── backtest/
│   ├── __init__.py
│   ├── engine.py
│   ├── storage.py
│   ├── excursion.py
│   └── metrics.py
├── scripts/
│   ├── __init__.py
│   ├── download_data.py
│   ├── download_historical.py
│   ├── test_orb.py
│   ├── run_backtest.py
│   └── compare_pdh.py
└── tests/
```

## Data

`market_candles` stores 1-minute candles:

```sql
CREATE TABLE IF NOT EXISTS market_candles (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open NUMERIC(20, 8) NOT NULL,
    high NUMERIC(20, 8) NOT NULL,
    low NUMERIC(20, 8) NOT NULL,
    close NUMERIC(20, 8) NOT NULL,
    volume BIGINT NOT NULL,
    trade_count BIGINT,
    vwap NUMERIC(20, 8),
    UNIQUE (ticker, timestamp)
);
```

AAPL 2024:
- 187,812 total 1-minute bars
- raw data includes extended hours
- strategy filters regular session 09:30-16:00 ET
- shortened sessions are currently skipped unless they have exactly 390 regular-session bars

## ORB Definition

- opening range = 09:30-09:34 ET, five 1-minute candles
- breakout starts at 09:35
- LONG signal = candle close > OR high
- SHORT signal = candle close < OR low
- entry = next candle open
- one trade/day

## Baseline Exit

`ORB_BASELINE_2R`:
- stop = opposite OR boundary
- target = 2R
- if neither target nor stop is reached, exit at session close
- if both target and stop occur in one 1-minute candle, stop is assumed first
- `risk` currently means per-share price risk
- `pnl` therefore represents per-share P&L, not account P&L

## Baseline Results — AAPL 2024

- trades: 247
- total R: +2.43602023R
- profit factor: 1.0217625
- maximum drawdown: -17.39442940R
- peak cumulative R: +17.83044963R
- drawdown trough: 2024-12-30, cumulative R +0.43602023R
- final 2024 result: about +2.436R

Interpretation:
The strategy is not production-ready. A nearly flat annual result combined with a ~17.4R maximum drawdown means the raw ORB has unstable behavior.

## Direction Regime Analysis

H1 2024:
- LONG: 56 trades, +0.47957343R, avg +0.00856381R
- SHORT: 67 trades, +4.31206459R, avg +0.06435917R

H2 2024:
- LONG: 66 trades, +11.17924422R, avg +0.16938249R
- SHORT: 58 trades, -13.53486201R, avg -0.23335969R

Important:
The directional difference is an in-sample observation. Do not simply convert it into a long-only filter without out-of-sample validation.

## Breakout Timing Regime Analysis

H1 2024:
- 09:35-09:45: 93 trades, +5.25499506R
- 09:45-10:00: 19 trades, +0.91910954R
- 10:00-10:30: 7 trades, +0.60031167R
- 10:30+: 4 trades, -1.98277825R

H2 2024:
- 09:35-09:45: 88 trades, -6.55186638R
- 09:45-10:00: 19 trades, +4.95890130R
- 10:00-10:30: 11 trades, -1.70973041R
- 10:30+: 6 trades, +0.94707770R

Important:
Do not choose 09:45-10:00 just because H2 performed well there. That is still in-sample optimization if selected using the 2024 dataset.

## Monthly Results

```text
Jan +4.4523R
Feb +4.4753R
Mar -5.7462R
Apr -1.3886R
May -1.7882R
Jun +4.7870R
Jul +8.5145R
Aug +2.4898R
Sep -3.9347R
Oct -3.5969R
Nov -4.6951R
Dec -1.1332R
```

Peak year-to-date equity was around +17.83R, followed by a very large drawdown into year-end.

## Previous Strategy Experiments

January 2024 experiments:

- ORB_V0: 7 trades, +0.5436R
- ORB_V1_BE_1R: same result
- ORB_V2_TP_1R: -1.5832R
- ORB_BASELINE: 20 trades, -0.1854R
- ORB_BASELINE_1_5R: 20 trades, +2.6829R
- ORB_BASELINE_2R: 20 trades, +4.4523R
- ORB_BASELINE_3R: 20 trades, +2.2564R
- ORB_BASELINE_2R_EARLY: 12 trades, +3.7797R
- ORB_BASELINE_PDH: 13 trades, -2.5106R

These are useful research history only. Do not infer that January optimization is valid.

## Trade Management Research

MAE/MFE analysis was fixed to measure only:

`entry_time <= timestamp <= exit_time`

Previously the code measured post-exit candles and produced impossible excursion numbers.

Observed full-year behavior:
- LOSS trades often had MAE around -1R and MFE below target
- SESSION_CLOSE trades frequently had positive MFE but failed to reach the 2R target
- WIN trades hit the 2R target
- many trades therefore raise a future question about dynamic exits, but this should be researched only after entry quality improves

## Important Bugs Fixed

- missing `signal_time` DB column
- duplicate in-memory trade append
- breakout counter placement
- MAE/MFE post-exit measurement
- dynamic strategy title in terminal output
- SQL uniqueness/upsert behavior

## Next Strategy Definition

### `ORB_RETEST_2R`

The hypothesis is that an immediate ORB breakout is too noisy and that requiring a retest/confirmation can improve trade quality.

Sequence:

```text
5-minute OR
   ↓
confirmed breakout
   ↓
pullback / retest of broken OR boundary
   ↓
confirmation
   ↓
entry on next candle open
   ↓
2R target / existing risk framework
```

LONG:
1. Candle closes above OR high.
2. Price subsequently pulls back toward OR high.
3. Retest holds/reclaims the OR high area.
4. Confirmation candle closes bullish / demonstrates continuation.
5. Entry at the next candle open.

SHORT:
1. Candle closes below OR low.
2. Price subsequently pulls back toward OR low.
3. Retest holds/rejects the OR low area.
4. Confirmation candle closes bearish / demonstrates continuation.
5. Entry at the next candle open.

Before implementation, define:
- maximum bars after breakout to allow a retest
- retest tolerance
- invalidation condition
- confirmation rule
- exact stop placement
- target placement
- what happens when no retest occurs

Keep these deterministic and simple initially.

## Why Retest Is the Next Experiment

The video-derived strategy structure being researched is closer to:

`PDH/PDL -> liquidity interaction/sweep -> ORB break -> retest -> confirmation -> entry`

The first clean experiment is to add only the retest/confirmation layer to raw ORB.

Do NOT simultaneously add:
- PDH/PDL sweep
- VWAP
- volume filters
- ATR
- news
- event sentiment
- ML

Those are later experiments.

## Longer Roadmap

1. `ORB_BASELINE_2R` — control
2. `ORB_RETEST_2R`
3. `ORB_LIQUIDITY_RETEST_2R`
4. context features:
   - VWAP
   - relative volume
   - gap
   - volatility/ATR
   - broader market context
5. event/news regime
6. clean feature dataset
7. ML only after enough observations
8. walk-forward validation
9. transaction cost/slippage modeling
10. multi-symbol validation
11. paper trading
12. live deployment only after validation

Potential event research:
Rockstar Games is privately held; Take-Two Interactive (`TTWO`) is the public parent. Event information and dates must be verified from authoritative current sources before hard-coding.

## Post-Run Follow-Up: Volume Profile Context

After the next live/alert run and before moving the system to a real-time VPS runner, evaluate adding a previous-session Volume Profile context layer.

Source reviewed:
- Video: `The BEST Volume Profile Trading Guide You'll EVER FIND`
- Local files: downloaded MP4 plus auto-generated subtitle `.srt`

Key concepts to convert into deterministic code:
- Calculate previous-session Volume Profile levels for each symbol:
  - POC: highest-volume price area
  - VAH: value area high
  - VAL: value area low
  - value area around 70% of prior-session volume
- Use completed previous-session levels only; do not build signals from changing current-session profile levels.
- Start as a Slack context/filter layer, not automatic execution.

Candidate uses:
- Alert when an ORB/VWAP signal is near prior POC, VAH, or VAL.
- Warn when a long signal is directly below likely VAH/POC resistance.
- Warn when a short signal is directly above likely VAL/POC support.
- Prefer continuation signals that break outside value, retest the boundary, and confirm structure.
- Prefer reversal context when price leaves value and closes back inside value.

Do not add this directly as a live strategy without backtesting. First add context messages, then test separate variants such as:
- `ORB_RETEST_WITH_VOLUME_PROFILE`
- `VWAP_EMA9_WITH_VOLUME_PROFILE`
- `VALUE_AREA_RECLAIM_2R`

## Validation Standard

The core principle is:

**Do not optimize 2024 and then call the optimized 2024 performance validation.**

Preferred future process:
- development data
- frozen parameters
- unseen validation data
- rolling/walk-forward tests
- multi-symbol tests
- realistic costs/slippage
- paper trading

## Immediate Codex Workflow

First inspect:

```text
strategy/signals.py
strategy/trade_setup.py
backtest/engine.py
backtest/metrics.py
backtest/storage.py
scripts/run_backtest.py
```

Then:
- explain current implementation
- identify the minimal changes required for `ORB_RETEST_2R`
- implement only after inspection
- run targeted tests
- run baseline regression
- run the new strategy
- compare baseline vs retest

Never overwrite the baseline implementation just to get the new strategy working.
