# Shadow Acceptance and Research Checkpoint

Checked 2026-10-20. This records completed work and remaining evidence for the
[streaming release](REALTIME_SHADOW_RELEASE.md). It does not activate broker orders.

## Hosted Evidence

Railway production project `confident-curiosity` reports the trading worker running
and PostgreSQL online, with no reported service issues or pending changes. The
active deployment is `d0bfdf0c-d4d7-48dd-a9b9-9c3613c31136`, built from
`b3b410b4df58ea5b28fe5a18e0f71893f822c17d`. The native cron is `40 11 * * 1-5`,
one replica, restart NEVER, and the start command is
`python -u scripts/realtime_runner.py --once --max-wait-minutes 120`.

Runtime logs confirm authentication and 22 IEX subscriptions. The
[Slack acknowledgement](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792496713698289)
confirms the same subscription count. The initial preparation message at 07:45 New
York showed unknown inputs; this is not evidence of completed market preparation.
Neither startup nor subscription acknowledgement establishes regular-session
coverage, acceptable latency, or a complete trading lifecycle.

Railway's source configuration still displays an older SHA (`8fc3e94`), whereas the
active deployment metadata and worker message identify `b3b410b`. Treat the actual
deployment/runtime identity as the observed version. The connector redacts variable
values, so secret rotation and hosted journal contents have not been independently
verified through that connector. No credentials were printed or copied.

## Changes Prepared at This Checkpoint

- Preparation notifications now have separate initial, watch-start and completed-OR
  keys. Phase transitions trigger fresh messages, while repeated updates within a
  phase remain deduplicated. Each message carries its preparation timestamp.
- Bounded per-minute telemetry measures bar completion-to-receive delay, quote
  timestamp-to-receive delay, queue wait and whole-evaluation duration. Counts and
  maxima are exact within each window. Percentiles use at most the latest 2,048
  observations per metric in that window; they are not whole-session percentiles.
  Revised bars have separate latency samples from initial bar publications.
- The worker persists and logs telemetry and an end-of-session acceptance report.
  Missing data, lane exceptions, incomplete delivery, unresolved positions and
  schedule conflicts remain explicit. The report is operational evidence only.
- During deployment the replacement worker waits up to 90 seconds for the shared
  lease. It does not take ownership from an active worker. Startup/subscription
  notifications include the deployed version and are deduplicated per version.
  Interrupted runs record checkpoints; a final recap must come from the current
  release at/after cutoff. A previous worker's message cannot complete acceptance.
- `scripts/check_shadow_session.py` audits existing storage with SELECT queries and
  acquires no worker lease. SQLite is opened read-only, and a missing database is
  never initialized. Exit codes: 0 PASS, 1 FAIL, 2 INCOMPLETE/UNAVAILABLE.
- `scripts/reconcile_baseline.py` explains the old/current baseline difference using
  stored trades and historical session eligibility, without modifying those rows.

These changes are a follow-up to the deployed commit identified above. Their
presence locally or in a pull request is not evidence that Railway runs them.

## EMA9 Schedule Finding

`VWAP_EMA9_CROSS_2R` requires `ema_length + 2` three-minute bars, including the next
entry reference. Under completed-bar streaming, ten completed source bars are first
available at 10:00 New York. The entry gate also closes at 10:00, so this lane has no
eligible entry window under the current defaults. Configuration and acceptance now
report `NO_ENTRY_WINDOW`. Neither its warmup nor the session deadline was changed.
A separately versioned timing experiment is needed before this lane can contribute.

## Baseline Reconciliation

| Policy/Stored Subset | Trades | Total R | Profit Factor | Maximum Drawdown |
| --- | ---: | ---: | ---: | ---: |
| Current calendar-aware stored control | 251 | +4.32569357 | 1.03830196 | -14.82453693R |
| Old eligibility applied to those trades | 247 | +2.43602023 | 1.02176250 | -17.39442940R |

The old subset exactly matches the AGENTS.md handoff to stored precision. The four
additional sessions account for the entire +1.88967334R difference:

| Session | R | Reason Absent From Old Sample |
| --- | ---: | --- |
| 2024-01-02 | +0.31978087 | Old fixed-R runner required previous-day levels inside its loaded sample |
| 2024-07-03 | -1.00000000 | Old 390-bar rule excluded the shortened session |
| 2024-11-29 | +2.00000000 | Old 390-bar rule excluded the shortened session |
| 2024-12-24 | +0.56989247 | Old 390-bar rule excluded the shortened session |

This reconciles stored metrics and policy eligibility. It is not an independent
replay of the historical data version used for the original handoff.

## Fixed-Parameter Research Rerun

Executed the existing `scripts/compare_multisymbol_retest.py` without tuning.
Symbols: AAPL, AMD, AMZN, GOOGL, META, MSFT, NVDA, TSLA. Five-bar timeout, tolerance
0.10 of opening range, opposite-OR stop, fixed 2R, next-open entry, one trade per
symbol/session, conservative same-candle ordering and calendar-aware eligibility.

| Period | Baseline Trades | Baseline R | Body-Reclaim Trades | Body-Reclaim R | Positive Symbols, Body |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2024 | 2,009 | +98.854388 | 797 | +41.374334 | 5/8 |
| 2025 | 1,988 | -32.494494 | 766 | +28.611101 | 6/8 |
| 2026 available sample | 1,422 | +40.425405 | 578 | +30.307473 | 5/8 |

The 2026 database coverage for all eight symbols is **2026-01-02 through
2026-09-18**, with 179 observed dates each. Incomplete sessions can reduce each
symbol's eligible-trade count. The script's requested end of 2026-12-31 does not
mean data exists through that date. September 19 through October 19 remains absent
from this validation snapshot.

| Symbol | Body R, 2024 | Body R, 2025 | Body R, 2026 Available |
| --- | ---: | ---: | ---: |
| AAPL | +4.536591 | +3.743909 | +6.035381 |
| AMD | -0.043982 | -3.597307 | +1.765971 |
| AMZN | +1.470790 | +14.580790 | -4.255688 |
| GOOGL | +21.967415 | +0.360604 | -4.010377 |
| META | +15.191028 | +3.219681 | -7.293658 |
| MSFT | -10.774718 | +2.998479 | +13.506794 |
| NVDA | +11.714132 | -6.311389 | +13.383186 |
| TSLA | -2.686922 | +13.616333 | +11.175865 |

These are sums of independently simulated R, not capital-constrained portfolio
returns. They exclude fees, spread, slippage, borrow costs, the streaming entry
reference and the actual 10:00/10:15 watch policy. The results do not establish a
deployable edge or a newly untouched holdout. Historical feed provenance also needs
verification before equating these stored bars with the live IEX feed.

The stored AAPL 2024 experiments remain recorded, including failures:

| Experiment | Trades | Total R | Profit Factor |
| --- | ---: | ---: | ---: |
| `ORB_RETEST_2R` | 104 | -4.305036 | 0.9012 |
| `ORB_RETEST_RECLAIM_2R` | 143 | -2.655432 | 0.9577 |
| `ORB_RETEST_RECLAIM_BODY_2R` | 100 | +4.536591 | 1.1100 |

## Verification and Remaining Work

265 offline tests and 10 subtests passed; the opt-in PostgreSQL test was skipped
in that run and passed separately in its UUID-isolated schema. The integration
check includes reading the acceptance report through PostgreSQL's JSON decoder.
Focused checks cover read-only SQLite reporting, unavailable evidence, explicit
failures, phase deduplication, bounded telemetry and EMA9 schedule readiness.

Reproduce with:

```sh
PYTHONPATH=. .venv/bin/python -B -m pytest -q tests
RUN_POSTGRES_TESTS=1 PYTHONPATH=. .venv/bin/python -B -m pytest -q tests/test_realtime_postgres.py
PYTHONPATH=. .venv/bin/python -B scripts/reconcile_baseline.py
PYTHONPATH=. .venv/bin/python -B scripts/compare_multisymbol_retest.py
PYTHONPATH=. .venv/bin/python -B scripts/check_shadow_session.py --session 2026-10-20 --expected-sha REVIEWED_DEPLOYED_SHA
```

Remaining release evidence and research:

1. Review/build/deploy this follow-up, then observe a completed hosted session with
   its telemetry and report. Earlier workers cannot supply telemetry retroactively.
2. Address EMA9 as an explicit strategy/timing experiment and validate its own costs
   and chronological sample before changing a deployed rule.
3. Verify historical feed provenance and fill the missing recent history into a
   reproducible dataset. Replay the full watch policy, including constrained capital,
   competition, timing, quote execution, costs and outage behavior.
4. Continue the source-adapter and single-variable in-play research backlog from the
   original plan. Do not treat UNKNOWN catalyst or corporate-action state as verified.
5. Build and validate broker protection, fills, exits and reconciliation before any
   paper-order activation. Live-money activation remains a separate decision.
