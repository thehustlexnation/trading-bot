# Trading System Audit - 2026-10-19

Historical pre-implementation snapshot. See the
[shadow release notes](REALTIME_SHADOW_RELEASE.md) for fixes, contained risks and
remaining acceptance gates; the findings below are not a claim about release status.

## Assessment

The repository contains useful deterministic research and alerting components, but the
current watcher is not ready for unattended order submission. Engineering failures
must be corrected before deciding whether any entry rule is profitable. Passing unit
tests and a successful GitHub job do not establish an investment edge.

The objective should be positive expected return after costs, subject to drawdown,
exposure, and execution constraints. Converting every losing trade into a maximum
winner is not an achievable specification. A losing short does not establish that a
long was identifiable in advance, executable, or profitable along the price path.

This review changes no strategy logic, broker setting, scheduled job, or deployment.
See [the architecture](TRADING_SYSTEM_ARCHITECTURE.md) for the proposed design.
The [research and real-time plan](TRADING_RESEARCH_AND_REALTIME_PLAN_2026-10-19.md)
adds source research, premarket preparation, experimental strategies and promotion
criteria without changing the audited trading logic.

## Evidence and scope

- Reviewed the current local `main` at `78e3e57`, including strategy detectors,
  watcher, broker adapter, sizing, historical simulator, metrics, journals,
  scheduler, workflow, and human-approved order path.
- GitHub remote `main` was `0e72122` at inspection. Local `88c8f29` and `78e3e57`
  were absent from the live branch.
- Read today's Slack channel and the actual Actions job logs.
- Ran `PYTHONPATH=. .venv/bin/python -m pytest -q tests`:
  **196 tests passed, plus 10 subtests**, with one dependency deprecation warning.
- Ran [12 offline audit probes](audits/audit_probes_2026_10_19.py). Broker calls were
  mocked; no real or paper order was sent.
- No new historical database backtest, quote replay, or fill reconciliation was
  performed. Findings about historical profitability remain unproven until those run.
- Railway and external cron settings were not inspected directly. GitHub and Slack
  confirm the observed Actions execution, not a Railway streaming deployment.

Reproduce the isolated probes from the repository root:

```sh
PYTHONPATH=. .venv/bin/python -B docs/audits/audit_probes_2026_10_19.py
```

They print observed behavior and counterexamples. Successful script completion means
the probes ran; it does not mean the defects are fixed.

## What actually happened on Monday

[Actions run 39259971761](https://github.com/thehustlexnation/trading-bot/actions/runs/39259971761)
succeeded on `0e72122`. It ran from approximately 09:25 to 10:15 New York in dry-run
mode. The privately inspected Slack recap reported zero emitted trade alerts.
This is zero reported trade exposure for this
watcher, not proof that all rejected opportunities would have lost.

The job log recorded five builder-level `APPROVED` candidates that were rejected by
later watch filters:

| Candidate | Final watcher decision |
| --- | --- |
| GOOGL ORB | Weak relative strength |
| AVGO VWAP/EMA9 | Quality C |
| QQQ VWAP/EMA9 | Quality C |
| INTC ORB | Entry after last-entry deadline |
| GOOGL VWAP/EMA9 | Entry after last-entry deadline |

The log also recorded only four HTF `NO_BREAKOUT` decisions, one each for AAPL,
ADBE, JPM, and V. These lanes were then removed. They were not repeatedly monitored
for later breakouts. Log output was buffered, so log flush timestamps must not be
treated as exact decision timestamps.

The new failed-breakdown reversal was absent. No high-impact macro event was
reported in the configured window. Stock-event dates and headlines were delivered
as context; their accuracy and individual relevance were not independently verified
in this audit.

## Prioritized findings

P0 means a blocker for enabling unattended submission. P1 means a material execution,
availability, or research-correctness defect. P2 means a significant measurement or
design limitation. Submission-related defects are latent while `submit_paper=false`.

### F01 - P0 - Orders can be submitted before the watcher rejects them

**Evidence:** `scripts/paper_trade.py:1374`, `:1542`, `:1550`.
`run_once()` calls the broker adapter before returning to the watch loop. Only then
does the loop evaluate macro risk, quality C, last-entry time, market direction, and
ORB relative strength. The single-run path bypasses those watch checks entirely.
An offline probe confirmed a broker-submit call for a candidate that the next layer
classified C and `MARKET_WEAK_FOR_LONG`.

**Fix:** Build candidates without side effects. Apply every required gate in one
policy function. Submit only a persisted, approved intent through a single executor.

### F02 - P0 - Reported exits do not close broker positions

**Evidence:** `scripts/paper_trade.py:1050`, `:1089`, `:1686`.
Early invalidation and cutoff handling set an in-memory `closed` flag and send text.
They do not submit a closing order, reconcile fills, or cancel bracket children.
If submission is enabled, Slack can say a position exited while it remains open at
the broker. The bracket's DAY time in force does not implement a 10:15 flatten.

**Fix:** Separate simulated outcomes from broker-confirmed exits. Persist an exit
intent, coordinate protective child orders, close the actual remaining quantity,
and mark flat only after reconciliation confirms it.

### F03 - P0 - Daily and portfolio risk are not enforced across trades

**Evidence:** `scripts/paper_trade.py:136`, `:1276`, `paper_trading/risk.py:49`.
Realized daily P&L is a command-line number defaulting to zero; it is not updated
from fills. Risk checks evaluate each order independently and reserve no risk for
other positions or pending orders. Buying power is read but not used by these checks.
The probe approved eight $25-risk positions: $200 combined risk against a configured
$100 daily-loss threshold. Each also used up to $2,500 notional independently.

**Fix:** Central atomic risk reservations, broker-backed realized/unrealized P&L,
portfolio notional/open-risk caps, sector concentration limits, and a daily lockout
that includes outstanding risk. Validate both ordinary and human-approved orders.

### F04 - P1 - Temporary waiting states permanently stop strategy lanes

**Evidence:** `scripts/paper_trade.py:1632`, `paper_trading/htf_breakout.py:120`.
`NO_BREAKOUT`, `NO_RETEST`, `NO_REVERSAL_CONFIRMATION`, `NO_BREAKDOWN`,
`NO_FAILED_BREAKDOWN_CONFIRMATION`, and `POST_NEWS_RANGE_NOT_READY` are absent from
the non-terminal set. Several mean only that sufficient data or a later event is
not available yet. Today AAPL/ADBE/JPM/V HTF lanes were stopped after no breakout.

**Fix:** Explicit per-strategy states such as WAITING, SETUP_ACTIVE, EXPIRED,
INVALIDATED, and COMPLETE, with expiry based on event time and configured windows.
Test incremental data prefixes through the entire watch lifecycle.

### F05 - P1 - The new reversal can crash on normal incremental data

**Evidence:** `paper_trading/failed_breakdown_reversal.py:55`.
The loop can evaluate the last available candle as confirmation, then reads
`df.iloc[index + 1]` without checking that it exists. A seven-candle prefix
reproduced `IndexError: single positional indexer is out-of-bounds`.

**Fix:** Return a waiting-for-entry state and resume when execution data is available.
Do not publish this lane before adding incremental and negative-case coverage.

### F06 - P1 - The reversal is not the recovery process described in the chat

**Evidence:** `scripts/paper_trade.py:1536`, `:1628`;
`paper_trading/failed_breakdown_reversal.py:18`, `:49`.
One accepted signal claims a symbol for the whole session. Other lanes, including
reversal, are retired even after the original trade closes. The detector also
handles only breakdown-to-long, requires no actual prior ORB short/exit, and searches
only eight bars after the first breakdown. Friday's discussed failure at 09:59 after
a 09:35 breakdown falls outside that window.

**Fix:** Define a separately versioned, independently validated reversal setup and
transition from broker-confirmed flat to reversal eligibility. Limit re-entry count
and session risk. Study the inverse failed-breakout-to-short case separately.
Changing the eight-bar parameter to fit Friday alone would be hindsight fitting.

### F07 - P1 - The post-news lane cannot trade under the default schedule

**Evidence:** `paper_trading/post_news_breakout.py:17`, `:49`;
`scripts/realtime_runner.py:138` and `:141`.
Its range starts after the configured news window ends at 10:30, but new entries
end at 10:00 and monitoring ends at 10:15. Its fixture enters at 10:51. Furthermore,
the initial `POST_NEWS_RANGE_NOT_READY` retires the lane under F04.

**Fix:** Anchor a post-event range to the actual event timestamp, with a separately
validated strategy entry/holding window. Reject impossible configurations at startup.
The macro calendar watch window is not the event's release time.

### F08 - P1 - Historical reference entries are treated as executable prices

**Evidence:** `strategy/signals.py:332`, `paper_trading/vwap_ema_cross.py:138`,
`scripts/paper_trade.py:1252`, `paper_trading/alpaca_adapter.py:57`.
Each poll rescans the day's history and returns an earlier next-candle open. The
executor submits a market order now, while sizing, stop/target, and reported R use
the earlier price. There is no quote-age, signal-age, spread, or current-price-drift
gate. The last-entry check compares the old signal time, not actual submission time.

**Fix:** Record signal time, receipt time, decision time, executable quote, and fill
time separately. Expire stale candidates, check current clock and quote, recompute
actual risk, and measure P&L from fills. Historical next-open remains a simulation
assumption that needs measured latency and slippage.

### F09 - P1 - Benchmark context is not aligned to the signal timestamp

**Evidence:** `scripts/paper_trade.py:397`, `:1208`.
Relative strength uses the last available stock and benchmark closes, fetched at
different times, even if the selected signal is older. On historical frames this
can use bars after the signal; on live frames it mixes old entry assumptions with
current context. A probe showed +1% stock strength at 09:37 becoming -10% when a
09:59 bar was included for that same earlier signal.

**Fix:** Calculate signal-time features from one common as-of snapshot and perform
a separate, explicitly logged current-market revalidation before execution.

### F10 - P1 - Candle readiness and feed identity are not enforced consistently

**Evidence:** `scripts/paper_trade.py:1178`, `:1262`;
`paper_trading/vwap_ema_cross.py:42`; `data/alpaca_client.py:15`.
The watcher accepts DATA_GAP sessions, permits the current minute in its selection,
and resampling accepts bins with fewer than three one-minute observations. There is
no shared closed-bar watermark, bounded signal-data freshness, or setup-window
continuity check. The configured default feed is IEX; runtime overrides were not
read from secrets. Missing input can change ORB levels, VWAP, crosses, and timing.

**Fix:** Timestamp/price validation, explicit expected observations per bin,
strategy-specific completeness checks, and a consistent feed/adjustment identity.
Alpaca documents IEX as one exchange and SIP as consolidated coverage. It also
documents later bar updates, so preserve what arrived when instead of silently
replacing decision history. [Feed documentation](https://docs.alpaca.markets/us/docs/market-data-faq),
[stream and updated bars](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data).

### F11 - P1 - Symbols are scanned sequentially with nondeterministic arbitration

**Evidence:** `scripts/paper_trade.py:209`, `:1528`, `:1672`.
Lanes live in a set and are evaluated by serial network calls, followed by a full
poll sleep. Scan time adds to the polling interval; repeated symbol/benchmark/daily
requests increase skew. When two strategies qualify, set iteration order can decide
which claims the symbol. A network exception can terminate the whole loop.

**Fix:** One shared data subscription/cache, independent symbol state, bounded task
execution, per-symbol error isolation, and explicit deterministic arbitration using
candidate timestamps and a frozen priority policy. Earlier chat statements that the
current stock scans execute in parallel were incorrect.

### F12 - P1 - Restart and duplicate-execution recovery are incomplete

**Evidence:** `scripts/realtime_runner.py:43`; `scripts/paper_trade.py:1520`;
`paper_trading/alpaca_adapter.py:57`.
A scheduler restart at Monday 09:26 calculates Tuesday 09:25 as the next run.
Session claims and trade state exist only in memory. Orders lack a deterministic
client ID for retry recovery. Position/order checks are separate from submission,
so concurrent runners can both pass them; a closed earlier position is forgotten.

**Fix:** Session-aware catch-up policy, durable state, process leadership lease,
deterministic intent/client IDs, transactional reservations, and startup broker
reconciliation before resuming entries.

### F13 - P1 - Reported outcomes do not match the historical research experiment

**Evidence:** `scripts/run_backtest.py:421`, `backtest/engine.py:181`;
`scripts/paper_trade.py:953`, `:1089`.
Historical ORB simulations hold to scheduled session close and omit the watcher's
relative-strength gates, last-entry deadline, 10:15 cutoff, and failed-reclaim exit.
The walk-forward script compares only two ORB versions. Its cost sensitivity code
exists, but it does not validate the current multi-lane watcher policy.

**Fix:** One strategy/exit state machine driven by either recorded events or the
live adapter. Include timing, arbitration, risk limits, and fills in replay. Keep
the original baseline as a control instead of rewriting its historical definition.

### F14 - P1 - No demonstrated edge for the complete deployed policy

**Evidence:** `AGENTS.md`, `scripts/analyze_walk_forward_retest.py:25`,
`scripts/analyze_cost_sensitivity.py:17`.
The recorded AAPL 2024 baseline is +2.436R on 247 trades, PF about 1.022, and
maximum drawdown about 17.394R. That is a documented historical control, not a fresh
result for today's deployed filters. Its gross average is about 0.00986R per trade;
a hypothetical 0.01R round-trip cost would consume that whole margin. The new lanes
have small functional fixtures, not evidence of portfolio profitability.

**Fix:** Freeze experiments and perform chronological out-of-sample tests, cost
stress, sensitivity analysis, and portfolio-level forward validation. Record every
experiment, including failures. Continually modifying filters after each observed
loss creates selection bias. [Backtest-overfitting research](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

### F15 - P2 - A/B/C is an alignment label, not a win probability

**Evidence:** `scripts/paper_trade.py:410`, `:427`, `:440`, `:482`.
The grade comes from stock-versus-benchmark return spread. It is not calibrated
against outcome frequency or net expectancy. Fixed percent thresholds apply across
symbols and regimes; an ORB counter-market requirement is 1.5 percentage points,
while the ordinary ORB relative-edge floor is 0.15 percentage points. Those are
different checks, but their effectiveness requires testing. Missing benchmark
context yields B and bypasses the market filters.

**Fix:** Show alignment as alignment. Track the missing-data state explicitly.
Validate thresholds on held-out regimes before using them as trade-selection rules.
Introduce calibrated probabilities only after a clean labelled dataset exists.

### F16 - P2 - News context is disconnected from execution and loses provenance

**Evidence:** `.github/workflows/mobile-signal-alerts.yml:72`,
`scripts/stock_catalyst_risk.py:92`, `scripts/paper_trade.py:539`, `:577`.
Stock catalysts are sent to Slack, but no earnings/headline state enters the
strategy gate. Company-news objects drop publication timestamps and attach the
requested ticker without validating headline relevance. Today's AAPL list includes
an unrelated digital-health market report. Macro context is fetched once; status
unavailable does not prevent new entries and a high event in the window blocks the
ordinary strategy for the whole watch rather than an event-specific interval.

**Fix:** Structured events with source URL, entity relevance, publication and
first-seen times, event type, expiry, and explicit unknown-state behavior. Separate
scheduled event controls from any researched directional news feature.

### F17 - P2 - Cutoff accounting can miss a stop or target in the final interval

**Evidence:** `scripts/paper_trade.py:1672`, `:1686`, `:1103`.
After sleeping past cutoff, the watcher fetches data and takes its final close
without first processing stop/target touches in newly received bars. It also does
not bound that frame to the cutoff timestamp. A probe produced +0.5R at cutoff
although the final candle's high had already touched the +2R target.

**Fix:** Process every eligible event through the exit engine to the cutoff
watermark, then mark remaining simulated exposure or execute the configured broker
flatten. Record the last processed timestamp to avoid replaying earlier bars.

### F18 - P2 - Drawdown and legacy breakeven calculations can understate losses

**Evidence:** `backtest/metrics.py:85`, `backtest/engine.py:94`.
Drawdown does not include starting equity zero: [-1R, -1R] reports -1R instead
of -2R. It also assumes incoming trade order is correct. When legacy breakeven mode
is used, the simulator moves the stop before checking whether the original stop was
hit earlier in the same OHLC candle. A candle touching both returned breakeven
instead of the conservative -1R. Current default backtests pass `break_even_r=None`.

**Fix:** Include starting equity and deterministic ordering. Make intrabar ambiguity
explicit, using conservative ordering or finer observed data. Do not count this
legacy breakeven bug as the cause of today's zero-alert result.

### F19 - P1 - Cloud execution does not persist an authoritative trading journal

**Evidence:** `paper_trading/audit.py:10`, `.github/workflows/mobile-signal-alerts.yml`.
Decisions/outcomes are JSONL files on the runner. Today's Actions run had no uploaded
artifacts. There is no runtime DB writer for this watch journal, no immutable run
manifest, and no broker-fill ledger here. Backtest upserts overwrite rows by
strategy/ticker/date without an experiment ID; direction is not updated on conflict.

**Fix:** Durable event/decision/order/fill records with run, setup, config, feed, and
code hashes. Persist candidates rejected by downstream gates too. Use an outbox for
Slack; notification delivery must not define trade state. Preserve independent
backtest runs and remove stale-run ambiguity instead of overwriting experiments.

### F20 - P1 - Human approval uses a separate, weaker execution path

**Evidence:** `scripts/execute_approved_trade.py:63`, `:80`;
`paper_trading/order_plan.py:148`; `cloudflare/approval-worker.js:25`.
Fixed-dollar sizing bypasses the ordinary account/daily/notional risk checks and
uses caller-provided old prices. The worker source dispatches based on query
parameters without an in-source authentication, expiry, or replay check; external
Cloudflare protection was not inspected. An approval link is not an authoritative
quote, current risk approval, or unique executable intent.

**Fix:** Send both manual and automatic orders through the same risk/execution
service. Store signed expiring intent IDs, verify the approving identity, reprice
and revalidate at execution, and make approval consumption idempotent.

### F21 - P1 - Deployment identity and runtime defaults diverge

**Evidence:** `.github/workflows/mobile-signal-alerts.yml:17`, `:98`;
`scripts/paper_trade.py:119`; observed remote/local hashes above.
The workflow input default, fallback shell string, direct CLI default, realtime
environment override, and external cron payload can select different strategy sets.
The latest workflow input default adds reversal while its fallback omits it. Direct
CLI defaults still include VWAP pullback, which the discussion intended to remove
from scheduled use. Today's run lacked the two local commits.

**Fix:** A single versioned runtime config with per-strategy enable/shadow flags.
Every start message and persisted run must show the actual code/config hash, feed,
enabled lanes, entry windows, execution mode, and deployment identity. Validate
before publishing; simply pushing the current reversal code is insufficient.

### F22 - P2 - Market data validation and correction storage are incomplete

**Evidence:** `data/validator.py:35`, `data/db_writer.py:68`.
The validator checks sorting, duplicates, high >= low, and nonnegative volume, but
not timezone awareness, finite numeric values, positive prices, or open/close inside
the OHLC range. Candle storage ignores later corrections and does not identify the
feed or adjustment policy. This permits malformed data and obscures replay origins.

**Fix:** Validate all candle invariants, preserve feed/adjustment metadata, and store
as-received revisions separately from the corrected research view. Avoid backfilling
revised prices into a historical decision as though they were known originally.

## Algorithm, ML, or LLM?

Recommendation: deterministic execution and risk, optional ML selection after
validation, and bounded LLM text analysis. This is an engineering recommendation
for this system, not a claim that one approach always outperforms the others.

| Approach | Useful role here | Required proof / limitations |
| --- | --- | --- |
| Deterministic rules | Signals, clocks, position state, sizing, exits | Reproducible but can still have no economic edge |
| Statistical ML | Estimate conditional net outcome; rank candidates/regimes | Clean as-of data, chronological validation, calibration and drift checks |
| LLM | Extract entities/events from news, summarize filings, generate research hypotheses | Source-grounding, timestamps, schema checks, cost/latency limits, forward evaluation |
| Multiple LLM decision-makers | Research experiment | More opinions do not establish independent evidence or improve net returns |

The original TradingAgents paper describes specialized LLM roles and reports
favorable results in a daily-decision simulation. It does not validate this bot's
one/three-minute entries, broker behavior, or risk controls. Its reported simulation
window is June 19-November 19, 2024. Treat it as architectural research, not proof
that replacing this system with an LLM desk will improve performance.
[TradingAgents paper](https://arxiv.org/html/2412.20138v1).

Historical LLM experiments also need protection from model knowledge of later
events. A benchmark explicitly studies this risk; prospective shadow runs remain
necessary. [Look-Ahead-Bench](https://arxiv.org/abs/2601.13770).

Even broker paper simulations omit some live effects, including latency slippage
and market impact. Our candle-based Slack estimates are a further simulation layer.
Distinguish all three: hypothetical signal outcome, paper broker fill, live fill.
[Alpaca paper-trading assumptions](https://docs.alpaca.markets/us/docs/paper-trading).

## Improving outcomes without hindsight

1. Repair the event/order lifecycle before adjusting predictive rules.
2. Record every valid candidate and rejection with its actual available inputs.
3. Replay alternative policies on untouched dates using identical executable costs.
4. Compare leaving a trade alone, early invalidation, partial exit, trailing exit,
   time exit, and independently confirmed reversal as separate frozen experiments.
5. Measure the whole short-plus-reversal sequence, including both commissions and
   slippage and the possibility that both trades lose. Do not erase the first loss.
6. Evaluate all blocked candidates, including winners, losers, and no-fills. This
   measures filter value without only selecting favorable counterexamples.
7. Promote a rule only if it improves net portfolio expectancy and acceptable risk
   out of sample; do not optimize raw win rate or daily alert count.

For an idealized system paying exactly +2R on wins and -1R on losses, expected gross
R is `3p - 1`; break-even win rate is 1/3 before costs. With fixed round-trip cost
`c` in R, the threshold becomes `(1+c)/3`. Actual cutoff/early-exit outcomes differ,
so estimate their actual return distribution. For example, 45 wins at +2R and 55
losses at -1R produce +35R before costs, despite more losing than winning trades.
This arithmetic illustrates the objective; it is not a performance forecast.

## Strategy design questions to test after correctness

- **2R reachability:** All main brackets target 2R, but a 10:15 exit and a wide
  opposite-OR stop can produce very different realized returns. Measure target-hit
  probability versus stop distance, remaining session time, realized volatility,
  and spread. A profit target is not a predicted destination.
- **Regime selection:** A raw EMA/VWAP cross can occur repeatedly in a sideways
  market. Test trend versus range conditions as frozen hypotheses rather than
  adding a different filter after every losing example.
- **HTF definition:** The current resistance detector uses the highest high of
  forty sessions and counts nearby highs. It does not search all support/resistance
  structures, and the HTF entry rule is long-only. `NEUTRAL_BEARISH` therefore means
  this long setup is ineligible, not that a profitable short is confirmed.
- **Diversification:** Several long tech symbols plus QQQ can represent the same
  market exposure. More symbols and agents do not necessarily add independent bets.
- **Exit tradeoffs:** Earlier exits can reduce some losses and truncate later
  winners. Compare net paired outcomes across all candidates and untouched dates.
  Widening a stop, averaging down, or reversing solely because the last trade lost
  is not evidence-based recovery.

## Recommended implementation order

1. **Execution correctness:** F01-F03, F08, F20. One approval gate, one executor,
   real position management, current quote checks, and central risk reservations.
2. **Lifecycle correctness:** F04-F07, F10-F12, F17. Incremental state, data readiness,
   viable strategy clocks, explicit arbitration, and restart recovery.
3. **Measurement:** F09, F13-F14, F18-F19, F21-F22. Shared replay engine, durable
   journals, reproducible releases, realistic costs, and correct metrics.
4. **Signal research:** evaluate regime, liquidity, event, exit, and reversal
   hypotheses one at a time. Preserve the baseline control.
5. **Optional intelligence:** add news extraction and then a statistically validated
   ML selector. A large multi-LLM deployment is not a prerequisite.

Keep the current system in alert/shadow mode until the first three stages have
passing integration tests and replay evidence. Expand to all-day trading only after
separate time-of-day validation and proven position management.
