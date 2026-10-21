# Streaming Shadow Release

This is the first implementation release of the
[research and deployment plan](TRADING_RESEARCH_AND_REALTIME_PLAN_2026-10-19.md).
It is a broker-free research service, not a validated profitable strategy or an
order-enabled trading release. Production deployment requires verified host access.

Follow-up: [the October 20 acceptance checkpoint](SHADOW_ACCEPTANCE_2026-10-20.md)
records the verified hosted release, baseline reconciliation, fixed-parameter
research rerun, and prepared telemetry/reporting changes. Consult that checkpoint
for the distinction between deployed code and the subsequent local follow-up.

## Implemented

- One Alpaca market-data stream subscribes to bars, revised bars and quotes for the
  20-instrument watchlist plus QQQ/SPY benchmark coverage and SMH (22 unique symbols
  under the default configuration).
- Existing reviewed ORB, HTF and EMA9 detectors consume closed source bars and a
  separately marked quote-only entry reference. This reference cannot confirm a
  candle pattern. Incomplete three-minute bars are excluded.
- New entries use a current bid/ask reference and fresh submission-time checks,
  not a retrospective next-open fill. `STREAM_SHADOW_V1` identifies this policy;
  `ORB_BASELINE_2R` and historical signal definitions were not changed.
- Required stock/benchmark minute gaps, stale quotes, excessive spreads, stale
  signals, unavailable macro context and actual entry deadlines block approval.
- The shared journal persists as-received bar versions, current bars, preparation,
  decisions, shadow positions, notifications and ownership leases.
- Database transactions enforce a single owner, one claimed symbol per session,
  aggregate hypothetical risk, notional and sector-group limits. Realized shadow
  losses reduce remaining budget. Stops can exceed planned loss on a quote gap.
- Hypothetical positions are marked from subsequent bid/ask observations. Early ORB
  invalidation, stop/target and cutoff handling remain explicitly simulated, never
  described as broker-confirmed exits. Missing cutoff quotes leave positions OPEN.
- Resumed positions and gaps between quote observations are labelled unverified;
  those outcomes are excluded from verified-path P/L totals. Unverified profits
  cannot replenish the risk budget as if uninterrupted execution had been proven.
- Entry/exit state and its notification are committed atomically. Slack delivery
  retries pending outbox records. Webhooks provide at-least-once delivery, not an
  exactly-once promise when an HTTP acknowledgement is lost.
- Preparation covers every configured instrument. It includes prior regular-session
  levels, premarket observations, same-window activity, provider news/earnings and
  optional SEC filing metadata. Unknown/unverified context stays labelled.
- Scheduler recovery resumes an active session instead of skipping to tomorrow.
  New York calendar logic handles holidays and DST. One-session bounded operation
  is the default, even when an existing host start command omits arguments.
- A Docker image definition, pinned runtime dependencies, health endpoint and CI
  workflow are included. The container never copies `.env`, logs or local state.

The existing legacy watcher has shared final gating before its adapter, sorted lane
evaluation, corrected waiting states and a final completed-bar cutoff check. The
late-candle reversal IndexError is fixed. These fixes do not validate its performance.

## Submission is deliberately unavailable

The low-level adapter rejects non-dry-run submission. Both command-line execution
paths reject paper-submit requests, and the streaming service has no trading client.
`REALTIME_SUBMIT_PAPER=true` is an error, not an activation switch.

Broker-backed exits, partial fills, protection, account-wide reconciliation,
broker-derived P&L and live-risk recovery are not implemented by this release.
They are mandatory before any future paper-order activation, much less real money.
This release contains the audited submission risk by removing that capability;
it does not claim that broker lifecycle defects have been repaired and validated.

## Proposed Railway Settings

Verify these on the existing `trading-bot` service after connecting Railway. Do not
create a second active watcher or another paid service without checking the budget.

| Setting | Value |
| --- | --- |
| Source | Reviewed release commit, after CI passes |
| Builder | Repository Dockerfile |
| Start command | `python -u scripts/realtime_runner.py --once --max-wait-minutes 120` |
| Native cron | `40 11 * * 1-5` (UTC) |
| Restart policy | Never for a scheduled one-session job |
| Replicas | 1 |
| Submission | Disabled in code and `REALTIME_SUBMIT_PAPER=false` |
| Storage | Shared PostgreSQL `REALTIME_DATABASE_URL` or `DATABASE_URL` |

11:40 UTC is 07:40 New York during daylight time and 06:40 during standard time.
The process waits for the 07:45 New York preparation phase. Both waits fit the
120-minute bound, including a modest scheduling delay. Session eligibility and entry
deadlines remain in New York time; an early cron is not permission for early entries.
If the job starts late but before cutoff, it recovers and runs the remaining window.

Railway must not be assumed to have applied these settings just because a commit
was pushed. Inspect the deployment's actual command, schedule, variables and logs.
No `railway.toml` was introduced: Railway's current documentation deprecates new
Config-as-Code enrollment, so host settings must be verified through supported
project configuration rather than assumed from a newly added legacy file.
[Railway configuration documentation](https://docs.railway.com/config-as-code).

For a separately approved persistent deployment, `--continuous` opts into the
always-running supervisor. Do not use that with the native cron configuration.
`/healthz` reports process liveness and phase; it is not a profitability certificate
or proof that every symbol has complete data. A cron job has no continuously live URL.

## Required Configuration

Use `.env.example` as the names reference; do not commit populated secrets.

- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`: market-data authentication. Rotate the keys
  previously exposed in chat before unattended deployment.
- `ALPACA_DATA_FEED=iex` or an entitled live `sip` feed. Delayed SIP is rejected.
- `REALTIME_DATABASE_URL` or `DATABASE_URL`: reachable PostgreSQL on Railway.
  Local laptop PostgreSQL is not automatically reachable by a hosted container.
- `SLACK_WEBHOOK_URL`: notification destination. It is never printed by preflight.
- `FINNHUB_API_KEY`: optional discovery. Missing credentials produce UNKNOWN context.
- `SEC_USER_AGENT`: optional identified SEC request user agent with valid contact
  details, following SEC access policy. Filing metadata is not a sentiment analysis.
- `REALTIME_PREPARATION_START=07:45`, `REALTIME_WATCH_START=09:25`,
  `REALTIME_LAST_ENTRY=10:00`, `REALTIME_CUTOFF=10:15`.
- `REALTIME_STRATEGIES=ORB_RETEST_RECLAIM_BODY_2R,HTF_BREAKOUT_RETEST_2R,VWAP_EMA9_CROSS_2R`.
  Unreviewed reversal/post-event lanes are rejected in the streaming service.

The old `REALTIME_START` name remains a fallback for watch start, not preparation.
Old configuration containing deferred strategy names must be updated explicitly.
No credentials are copied between services automatically by this code.

## Reproducible Checks

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q tests
.venv/bin/python scripts/realtime_runner.py --check
.venv/bin/python scripts/realtime_runner.py --smoke-test --smoke-seconds 15
.venv/bin/python scripts/realtime_runner.py --bootstrap-test
RUN_POSTGRES_TESTS=1 PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_realtime_postgres.py
```

Normal tests are offline. The optional PostgreSQL test creates/removes only its
own UUID-named schema; existing trading tables are unchanged. The smoke/bootstrap
checks are read-only provider calls: no broker orders, Slack messages or trade writes.

Validated locally during implementation:

- 251 offline tests and 10 subtests passed, with the opt-in PostgreSQL test skipped
  in that run and separately executed successfully against local PostgreSQL.
- The provider accepted authentication and all 22 IEX subscriptions. No events
  arrived during the after-hours 15-second sample; regular-session latency and
  reconnect load remain to be observed.
- Historical bootstrap returned observations for all 22 subscriptions and 1,400 daily
  observations, and generated all 20 preparation dossiers without sending messages.
- PostgreSQL lease, reservation, persistence and close-state integration passed in
  a temporary schema. SQLite-based restart/risk tests also passed.
- A read-only AAPL 2024 baseline replay produced 251 trades, +4.32569357R,
  PF 1.03830196, and -14.82453692R maximum drawdown under the repository's current
  calendar/data policy. The baseline source files are unchanged by this release.
  These differ from the older AGENTS.md handoff (247 trades/+2.436R); reconcile
  that pre-existing control-history discrepancy before claiming performance parity
  with the old handoff. No stored backtest trades were rewritten.

## Remaining Acceptance Gates

1. Verify Railway access, existing service ownership, database connectivity, rotated
   secrets, actual deployment SHA, cron and spending limits.
2. Pass hosted CI, including the Docker build. No local Docker executable was
   available during implementation, so a local image build is not claimed.
3. Observe a regular-session shadow run: actual symbol coverage, queue/decision
   latency, data gaps/corrections, reconnect recovery, preparation and Slack delivery.
4. Resolve the EMA9 warmup/deadline interaction: the current detector requires enough
   completed three-minute bars that first readiness overlaps the 10:00 entry cutoff.
   The release flags this instead of silently changing warmup or extending trading.
5. Complete full-policy replay and out-of-sample experiments before promoting any
   in-play filter, reversal, event strategy, new exit policy or ML ranking.
   Primary issuer-feed adapters and official macro-calendar corroboration also
   remain backlog items; current macro gating uses the existing ForexFactory source.
6. Implement and independently validate a broker execution/reconciliation lifecycle
   before authorizing paper orders. Live trading remains a later explicit decision.

This release is not the entire research roadmap. It supplies a testable real-time
research foundation and safety containment; future strategy/AI additions remain
separate experiments rather than unvalidated production defaults.
