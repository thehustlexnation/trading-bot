# Research, Premarket Preparation, and Real-Time Deployment Plan

Research date: 2026-10-19. This extends the
[code audit](TRADING_AUDIT_2026-10-19.md) and
[component architecture](TRADING_SYSTEM_ARCHITECTURE.md).

Implementation update: the first broker-free streaming implementation is described
in [the shadow release notes](REALTIME_SHADOW_RELEASE.md). The sections below retain
the original proposal; research promotion and hosted acceptance are not complete.

Status: researched design and implementation backlog, not an implemented trading
release, profitability certification, or verified Railway deployment. No orders,
production configuration changes, pushes, or deployments were made for this plan.

## 1. Decision

The audit is a reproducible snapshot of defects, not the final possible strategy
set. Its execution blockers remain relevant regardless of which indicator, model,
or information source is added. Correct them before unattended paper submission.

The next research priority is stock selection and preparation, followed by a small
number of complementary setups. Do not add every proposed feature to one strategy.
Collect context without changing decisions first; measure each subsequent change.
Preserve `ORB_BASELINE_2R` and its definition of R as the historical control.

The objective is positive expected portfolio return after costs within explicit
loss and exposure limits. It is not to make every trade profitable. A model cannot
know an unexpected future order, announcement, or price reversal in advance.
If losses are financially unaffordable, keep real-money submission disabled.

## 2. What the external evidence supports

### Stocks in play are a worthwhile ORB experiment

Zarattini, Barbon, and Aziz studied more than 7,000 US stocks over 2016-2023.
They found materially different ORB results when selecting unusually active stocks.
Their opening relative volume compares the first five minutes with the same window
over the previous 14 sessions. Their strategy uses opening-candle direction,
stop-order entries, ATR-based stops, and end-of-day exits. This is not our confirmed
retest/next-open/2R/10:15 policy. The paper includes commissions; it does not establish
our spread, fill, borrow, or slippage assumptions. Its large reported returns must
not be advertised as our expected results.
[Original research PDF](https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content).

Recommendation: test the selection hypothesis on our existing universe and exits,
without simultaneously copying the paper's entries, stops, and holding period.

### Preparation is an actual trading-desk practice, not proof of an edge

SMB describes a daily process of screening premarket activity, investigating news,
writing scenarios and levels, discussing market conditions, and setting alerts.
This is a useful workflow reference. A practitioner's description and self-reported
results are not independently audited evidence that our implementation will profit.
[SMB's preparation routine](https://www.smbtraining.com/blog/why-i-game-plan-every-day).

### Full-day expansion needs separate time-of-day research

Gao, Han, Li, and Zhou document an association between early and late-session
returns in US ETFs. That finding concerns a particular horizon, not a rule that
every green opening predicts a winning morning long.
[Market Intraday Momentum](https://www.sciencedirect.com/science/article/pii/S0304405X18301351).

A separate SPY study uses time-of-day volatility bands to distinguish ordinary
movement from momentum, with its own execution times, exits, and cost assumptions.
It is a candidate for later replication, not validation of our current VWAP cross.
[Authors' intraday momentum paper](https://concretumgroup.com/wp-content/uploads/2026/02/Beat-the-Market.pdf).

### LLM evidence does not justify an autonomous price oracle

Lopez-Lira and Tang investigate headline interpretation and subsequent returns.
Importantly, their latest abstract distinguishes a non-tradable initial reaction
from later drift. A reported headline-classification or portfolio-day hit rate is
not an executable per-trade win rate for NVDA or our three-minute setups.
[Original LLM/news research](https://arxiv.org/abs/2304.07619).

Recommendation: optional LLM event extraction, then independent validation of any
derived feature. Deterministic code retains exclusive risk and execution authority.

## 3. Preparation and research for every configured instrument

Current watchlist union, 20 instruments:

```text
AAPL NVDA AMD AVGO SNDK INTC GOOGL QQQ MSFT AMZN
META TSLA JPM V MA NFLX COST ORCL CRM ADBE
```

Prepare a dossier for all 20, not just the first three. Keep the current explicit
per-strategy symbol subsets until a separate coverage change has been validated.
MU was mentioned in earlier discussion but is absent from this configured union;
it must not be described as monitored until configuration and coverage tests change.
QQQ is a tradable ETF here; do not calculate its relative strength against itself.

Each instrument receives one versioned, timestamped preparation record:

| Section | Required contents | Decision relevance |
| --- | --- | --- |
| Identity | Symbol, asset type, issuer/CIK where applicable, corporate-action state | Avoid mismatched news and split-induced false gaps |
| Catalyst | Source URL, event type, publication and first-seen times, affected entity, verified/unknown status | Explain unusual activity; identify scheduled risk |
| Event schedule | Earnings release versus call, conferences, macro exposure, before/after-market status | Timing matters more than a vague positive headline |
| Price map | Prior close, PDH/PDL, premarket high/low, prior completed daily/weekly levels | Define conditional locations and invalidation |
| Activity | Gap, same-time premarket volume, dollar volume, opening relative volume when available | Separate active opportunities from ordinary noise |
| Market/sector | Benchmark returns and trend, sector proxy, relative performance at the same cutoff | Distinguish broad moves from stock-specific strength |
| Execution | Quote age, spread, executable size, broker tradability/short eligibility | Determine whether the setup can actually be traded |
| Scenario | Bullish condition, bearish condition, failure condition, expiry, eligible strategy | A plan, not a prediction that a direction must win |
| Provenance | Feed, lookback, data gaps, code/config hash, as-of time | Reproduce what was knowable at decision time |

Company-specific emphasis should differ while the data contract stays consistent:

| Group | Instruments | Relevant research topics, not automatic directional signals |
| --- | --- | --- |
| Chips and storage | NVDA, AMD, AVGO, INTC, SNDK | Results/guidance, product events, customer spending, supply/export developments |
| Software and hardware | AAPL, MSFT, ORCL, CRM, ADBE | Results/guidance, major product or commercial announcements |
| Platforms and media | GOOGL, META, NFLX | Results/guidance, advertising/subscription developments, regulatory events |
| Consumer/discretionary | AMZN, TSLA | Results/guidance and documented company-specific operating releases |
| Financials/payments | JPM, V, MA | Results/guidance, rates and activity context |
| Consumer staples | COST | Results and official company operating updates |
| ETF | QQQ | Broad market and component-event exposure; no invented issuer earnings event |

These topic lists are proposed research coverage, not claims about today's news.
For example, official [NVIDIA events](https://investor.nvidia.com/events-and-presentations/events-and-presentations/default.aspx),
[AMD releases](https://ir.amd.com/news-events/press-releases), and
[Sandisk events](https://investor.sandisk.com/news-events/events) can supply primary
event references. An adapter registry is still needed for every issuer.

Do not fabricate an earnings surprise from the release alone: it requires a
timestamped, appropriately licensed consensus dataset available before the release.
Long-term valuation is background context, not a precise next-minute entry signal.

### Example of the intended preparation output

The numbers below are hypothetical and not today's NVDA analysis:

```text
NVDA | as of 09:20 New York | research only
Catalyst: issuer event verified; source and first-seen timestamp retained
Prior high: 120.00 | prior low: 116.00 | premarket range: 118.50-121.00
Premarket activity: elevated versus the same prior-session time windows
Bull scenario: complete OR, break/retest/hold, then a new confirmed long candidate
Bear scenario: failed breakout and confirmed loss of support, if that lane is enabled
Stand aside: stale quotes, unavailable required inputs, or unapproved spread/risk
Opening relative volume: unavailable until the first five-minute window completes
```

An issuer event is not necessarily bullish. Even a positive announcement can already
be priced in. Price response and a valid, executable setup still need to occur.

## 4. Sources to add, retain, or defer

| Source | Intended role | Cost/access and limitations | Priority |
| --- | --- | --- | --- |
| Existing Alpaca bars and quotes | Price/activity/relative strength and execution validation | Validate the actual entitlement and feed; keep historical/live feeds consistent | Required |
| SEC EDGAR submissions and filing documents | New filings and issuer evidence | Public unauthenticated data APIs; not a complete breaking-news service | High |
| Issuer investor-relations releases/events | Authoritative company announcements and event timing | Public sources; varied formats, coverage, and automation policies | High |
| BLS, BEA, Federal Reserve | Primary macro release calendars | Public calendars; parser health and schedule revisions must be tracked | High |
| Existing Finnhub company news/earnings | Broader discovery and corroboration | Verify account coverage, license, timestamps, quotas, and latency; do not promise a free premium feed | Retain and improve |
| Sector ETF price data | Industry context and concentration | Additional symbol subscriptions; measure incremental value | High, initially one relevant proxy |
| Paid real-time news service | Faster comprehensive catalyst discovery | Compare measured delays and outcome benefit before purchase | Later |
| Options flow, dealer positioning, Level 2 | Specialized future research | Additional data, inference and licensing requirements | Defer |
| YouTube, social feeds, copied trades | Research ideas only | Availability, selection bias, lag and unverifiable execution | Not an execution dependency |

SEC APIs expose submissions and financial data without an API key and update as
filings are disseminated. A filing time is not proof that our process received or
understood it at that moment. Retain both timestamps.
[SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
Respect identified automated-access policies, caching and the SEC's aggregate
10-request-per-second ceiling, rather than allocating that rate to each agent.
[SEC developer guidance](https://www.sec.gov/about/developer-resources).

Use [BLS](https://www.bls.gov/schedule/2026/home.htm),
[BEA](https://www.bea.gov/news/schedule), and
[Federal Reserve calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
as primary macro references. They are not a complete economic/news feed and some
require structured adapters or licensed calendar corroboration. Store the actual
event time and revisions. A provider failure means UNKNOWN, not "no event."

### Normalize news before using it

The existing stock-catalyst script is a reporting tool, not a strategy input.
Its current news record drops publication times and does not enforce relevance.
Replace that information loss before using headlines for decisions.

Required event fields: `event_id`, `symbol`, `issuer_id`, `source_url`,
`published_at`, `first_seen_at`, `event_time`, `event_type`, `verification_state`,
`entity_relevance`, `dedup_key`, `expires_at`, and source/model versions.
Distinguish verified facts, conditional interpretations, and unknowns.

Deduplicate syndicated headlines; ten copies are not ten independent catalysts.
Do not treat generic portfolio articles as company events just because an API tags
the ticker. A headline summary cannot establish an actual earnings surprise,
institutional order flow, or a reliable price target.

## 5. Market analysis that is useful and measurable

Start by recording a small context vector, not a large unvalidated score:

1. SPY/QQQ location versus prior close and session VWAP, using synchronized data.
2. Opening range relative to the instrument's prior completed volatility history.
3. Same-time relative volume and price acceptance/rejection around known levels.
4. Stock performance relative to its benchmark and a relevant sector proxy.
5. Scheduled events, source freshness, spread and current liquidity.

Potential states are UP_TREND, DOWN_TREND, RANGE, and UNKNOWN, assigned using only
past information. Define the numerical rules before testing; do not label a day a
trend day using its eventual close. First log the states, then compare performance
before deciding whether they should rank candidates or exclude a strategy.

Initial sector-context example: SMH for the chip/storage group, subject to tests.
Later evaluate suitable proxies for other groups. Do not force every stock into a
technology-sector benchmark, or use QQQ/SPY/SMH agreement as independent evidence.
Stocks and benchmarks share holdings and market exposure. A genuinely active stock
can also diverge from a red index, so universal directional blocking is a hypothesis
to test, not a known optimal policy.

The fraction of these 20 instruments rising is watchlist participation, not full
NYSE/Nasdaq market breadth. Minute OHLCV cannot reconstruct true order-book depth,
buyer/seller aggressor flow, or an exact trade-by-price volume profile.

Keep feature families distinct: opportunity/activity, setup, market/event context,
execution feasibility, and portfolio risk. Several transforms of the same price
series do not count as independent confirmations or calibrated win probabilities.

## 6. Strategy research queue

These are proposed experiments. None is authorized for real-money deployment by
this document. Repair execution independently of changing the signal rules.

| Order | Experiment | Hypothesis and separation from existing strategies | Initial disposition |
| --- | --- | --- | --- |
| 0 | Corrected current policy replay | Determine actual behavior after timing, waiting-state and execution fixes | Prerequisite |
| 1 | `ORB_RETEST_IN_PLAY_2R` | Unusually active, liquid stocks may make the existing retest more selective | First new version to evaluate |
| 2 | Failed-breakdown reclaim / failed-breakout rejection | A separately confirmed failed move may create a new opposite setup | Repair current long research lane, then mirror short; shadow only |
| 3 | Event-anchored continuation | A fresh, timestamped event followed by a new range/retest may be different from ordinary crossovers | Repair event timing; only if the setup fits the allowed session |
| 4 | Second-day catalyst continuation | A prior event may remain relevant with new same-day activity and confirmation | Later tagged cohort, not a presumed bullish rule |
| 5 | Time-of-day volatility-band momentum | Later-session trend selection may complement the opening strategy | Separate ETF replication and full-day phase |
| 6 | Range-bound mean reversion | Quiet/range conditions may call for a different setup | Lower priority; no fading a move just because it is extended |

For experiment 1, retain entry timing, stop, 2R target, risk sizing and session exits.
First add only same-window relative volume as a separately versioned variable.
Test catalyst tagging and market context separately, rather than combining them
and attributing any improvement to all three.

An initial opening activity measure can be:

```text
opening_relative_volume = today's completed 09:30-09:35 volume
                          / mean(volume in that same window on prior sessions)
```

The 14-session denominator in the cited study is a research starting point, not an
optimized rule for our universe. Record its sensitivity, feed and missing history.
Premarket activity needs its own matching premarket window; never divide incomplete
morning volume by a complete day's average and call it equivalent opening RVOL.
All current-day inputs become available only after their source windows complete.

For experiment 2, the reverse trade needs its own setup ID, confirmation, executable
entry, invalidation, expiry and risk approval. It must be possible to remain flat.
Close/reconcile any existing opposite position before opening another. The local
failed-breakdown lane currently has a last-bar indexing defect, limited detection
window and incompatible symbol-claim behavior; it is not a finished reversal system.
Changing one-trade-per-day is itself an experiment and must not alter the baseline.

For experiment 3, use the actual event time, not `news_end=10:30`. A setup that first
forms after the 10:00 last-entry deadline cannot trade under the current policy.
Do not silently extend trading hours or bypass all event controls to make it fire.

VWAP pullback and EMA/VWAP crossover are related trend hypotheses, not automatic
diversification. Compare signal overlap and return correlation before keeping both.
Volume-profile rules remain future research and require an appropriate data model;
they are not being discarded or described as already implemented.

### Exit research is separate from entry research

Measure target/stop first-passage times, maximum favorable/adverse excursion before
actual exit, and how often entries near the deadline have sufficient follow-through.
Keep fixed 2R as the control. Then compare any time exit, trailing stop, partial exit,
or early-invalidation rule as a distinct experiment under identical entry signals.
Do not tighten stops retrospectively, remove losses from the sample, or assume a
trailing/partial exit increases net expectancy.

## 7. Proposed automatic daily routine

All times below are America/New_York and are proposed schedule changes, not current
deployed settings. The current runner starts news checks around 09:25, not 07:45.

| Time/phase | Automated action | Persisted result |
| --- | --- | --- |
| Prior evening | Refresh prior-session bars, corporate actions and coming events | Versioned history and event calendar |
| 07:45 | Check exchange session, credentials, source health and storage; establish ownership | Session record, feed/config identity, recoverable state |
| 08:00-09:10 | Build dossiers for all 20; refresh news within provider quotas; observe scheduled releases | Dated source evidence and UNKNOWN states |
| 09:10-09:20 | Map levels and rank activity; write bullish, bearish and stand-aside scenarios | Full-universe preparation table |
| 09:20-09:25 | Validate subscriptions, broker/account health, quote freshness, risk and pending exposure | Explicit READY or NOT_READY |
| 09:25 | Publish a concise preparation summary and arm eligible lanes | Delivery record and active-lane inventory |
| 09:30-09:35 | Build the five-minute opening range without premature entries | Completed source observations and OR state |
| 09:35-10:00 | Evaluate closed-bar setups independently per eligible symbol; arbitrate shared risk | Candidates, decisions and any paper intents |
| 10:00 | Stop new entries by actual decision/submission time | Entry lockout; continue position management |
| 10:00-10:15 | Manage existing exposure and verify protective orders | Broker-backed lifecycle events |
| 10:15 onward | Execute required time exits, reconcile orders/fills, persist recap | Verified flat state or explicit exception/escalation |
| After reconciliation | Report results and shadow opportunities, flush storage, close session | Reproducible report and archived run |

Do not shut the service down merely because the clock reaches 10:15 while orders or
positions remain unresolved. A source outage must stop new entries without disabling
management of existing exposure. Failure to send Slack must not stop protection.

Scheduling earlier preparation requires separate preparation and entry phases in
the runner. Merely changing `--start` can launch the current watch too early, and
the current `--max-wait-minutes 60` may exit before an early wake reaches 09:25.
Handle restart during the active window explicitly; the existing scheduler currently
chooses the following session if started after the day's configured start time.

## 8. Real-time implementation and deployment contract

### One market stream, shared snapshots, independent symbol state

```text
Exchange calendar / event sources -> preparation and context tasks
Alpaca multi-symbol stream -> normalized event journal -> closed-bar snapshots
                                         |
                           per-symbol strategy state machines
                                         |
                          deterministic selection + central risk
                                         |
                            single broker execution owner
                                         |
                   fill reconciliation -> PostgreSQL -> Slack outbox
```

The current `realtime_runner.py` launches a watcher with 30-second REST polling;
`data/alpaca_client.py` is historical/HTTP data access. Hosting it on Railway does
not turn that into a WebSocket implementation or guarantee simultaneous evaluation.

For one/three-minute rules, live streaming does not mean acting on unfinished
candles or trading every tick. Alpaca publishes prior-minute bars after the boundary
and can issue later bar updates. Define a watermark and correction policy rather
than assuming the first version is immutable.
[Alpaca stream behavior](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data).

Bootstrap once per instrument with REST, then maintain bounded asynchronous tasks
and cached benchmark/context state. Order events per symbol; other symbols should
not wait for a slow news request. Use a bounded queue, deadlines, rate limits and
backpressure. On reconnect, backfill and deduplicate before allowing new entries.
Record local receive/decision times separately from exchange/event times.

Risk, execution, reconciliation, and the notification outbox follow the contracts in
the architecture document. A proposed context task or LLM must never place orders.
All broker-writing paths, including manual approvals, use the same final authority.

### Feed and cost reality

Alpaca currently lists free Basic equity data as IEX-only with 30 streaming symbols;
Algo Trader Plus is $99/month with broader US-exchange coverage. This is data cost,
not hosting cost. Our 20-instrument union plus SPY and SMH is 22 symbols; confirm the
account's actual subscription and connection limits before implementation. IEX
activity and VWAP are feed-specific observations, not the entire US market.
[Alpaca plan documentation](https://docs.alpaca.markets/us/docs/about-market-data-api).

Do not purchase an upgrade solely on the promise of more winners. First test feed
adequacy, consistent historical/live calculations, and sensitivity to volume gaps.
Historical data entitlement, quotes and benchmark access require a real smoke test.
Storage, news and optional LLM services are additional budget items.

Railway Hobby currently starts at $5/month including $5 of resource usage; usage
above that is billed additionally. This is not a guaranteed all-in monthly trading
system bill. Measure worker/database memory, CPU, network and storage consumption.
[Railway pricing](https://docs.railway.com/pricing/plans).

### Hosting and automatic wake-up

Choose exactly one ownership pattern: a persistent supervised worker, or an early
scheduled one-session worker. Keep GitHub Actions for tests/releases and optional
bootstrap dispatch, not as a second concurrent execution owner.

Railway's native cron uses UTC, may start a few minutes late, and skips a scheduled
execution if the previous process is still running. It does not automatically end
the job. Wake early and use a New York calendar-aware gate; account for DST and
verify graceful completion.
[Railway cron documentation](https://docs.railway.com/cron-jobs).

For a persistent worker, select a compatible restart policy and test recovery;
Railway restricts restart choices on trial/free plans.
[Railway restart policy](https://docs.railway.com/deployments/restart-policy).
Do not publish a new exact cron expression until the preparation start, allowable
wait and selected deployment mode are implemented and tested together.

Use a database leadership lease across deployments and an execution-intent unique
key, not just GitHub workflow concurrency. A redeploy and a manually triggered watch
must not create two independent traders. Never enable submission during migration.

### Actual deployment status at this review

- GitHub remote main was rechecked at `0e72122`; local main is `78e3e57`.
- The two local reversal commits are not on the remote main branch.
- The verified Monday run is the GitHub dry-run described in the audit, not proof of
  a healthy Railway stream or broker execution.
- Railway service access is still required to inspect its current build, variables,
  schedule, logs and health. A Railway connection was offered; deployment has not
  been claimed verified or modified without that evidence.
- No endpoint, token value, or other credential belongs in this document or Slack.
  Rotate the Alpaca credentials previously exposed in chat and update the secret
  stores before enabling any unattended trading. Do not reuse exposed credentials.

## 9. Logical agents, without unnecessary infrastructure

The architecture's agents can be ordinary tasks in one Python service. Proposed
research additions refine their responsibilities rather than multiplying servers:

| Component | New responsibility | Authority |
| --- | --- | --- |
| Session/data | Preparation phases, synchronized snapshots, source-health inventory | May stop new entries on invalid required data |
| Catalyst researcher | All-instrument dossier, event evidence and relevance/deduplication | Facts and uncertainty only |
| Market analyst | Time-of-day activity, market/sector state, annotated levels | Versioned features, no broker access |
| Setup workers | Independent ORB/HTF/crossover/reversal state | Candidates, not orders |
| Selector/risk | Deterministic arbitration, concentration and atomic budget reservations | Sole approval authority; not an LLM |
| Executor/reconciler | Idempotent orders, fill truth, protection and exits | Sole broker-writing component |
| Evaluator/reporter | Accepted and rejected opportunity studies, operational metrics and Slack | Cannot edit a running strategy |

Optional LLM parsing runs outside the latency-sensitive execution path, with a
fixed schema, cached results, source citations, budget cap and timeout. Treat all
retrieved text as untrusted data. No autonomous tool access to broker credentials,
shell execution or risk-limit changes. Missing output remains UNKNOWN.

Only consider supervised ML after stable features and clean labels exist. Begin
with simple calibrated models and compare against the deterministic selector on
untouched chronological data. Do not call several LLMs voting on the same chart
independent evidence, and do not let models change their rules after each loss.

## 10. Validation and promotion gates

Testing more variants on the same history increases the chance of selecting noise.
Maintain an experiment ledger recording every rule/parameter combination tested,
including failed attempts. Repeatedly inspected data is no longer untouched.
[Bailey et al., backtest overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

1. Repair the execution blockers and add regression tests for every reproduced bug.
2. Re-run the baseline and reconcile metrics/storage ordering without changing its
   historical trade definitions. Version any intentional simulation correction.
3. Build a replay of the entire actual watch policy, including cutoffs, filters,
   candidate competition, costs and constrained capital, not just isolated detectors.
4. Save raw/as-received context prospectively; use `first_seen_at` in news tests.
   A historical API response fetched today cannot prove when we could have read it.
5. Freeze one experimental rule on development data. Use rolling chronological
   walk-forward tests plus a final untouched period. Keep all same-day symbols in
   the same fold; their observations are correlated. Purge overlapping outcome
   windows if applicable. Never randomly mix candles across train and test.
6. Report net expectancy, profit factor, daily/portfolio drawdown, tails, turnover,
   signal overlap, concentration, rejected winners and rejected losers. Use day/block
   uncertainty estimates rather than treating every intraday observation as iid.
7. Check sensitivity to spread/slippage, late entries, missed fills, borrow costs,
   source outages and modest parameter changes. Avoid double-counting spread costs.
8. Promote to shadow streaming first, then supervised paper execution after
   operational acceptance. A fixed number of green days alone is insufficient.
9. Consider small live exposure only after separate explicit authorization,
   broker/account eligibility checks, sufficient evidence and affordable risk.

Alpaca paper trading omits important real execution effects, including market impact,
latency slippage and order queue position. Paper profit alone is not a live edge.
[Paper-trading limitations](https://docs.alpaca.markets/us/docs/paper-trading).

For illustration, 40% wins at +2R and 60% losses at -1R produce +0.2R per trade
before costs. This does not predict our win rate. Actual early/time exits change
the distribution, and stop slippage can exceed planned risk. Optimize measured net
expectancy and drawdown, not a headline percentage of winning alerts.

### Operational acceptance before claiming real-time readiness

- Deployed SHA/config hash match the reviewed release; feed and mode are explicit.
- Every configured symbol/strategy appears in the subscription and lane inventory.
- A live shadow session demonstrates bars, benchmark alignment, quote freshness,
  event updates and decisions; measure publication-to-receive and receive-to-decision
  latency separately, including p50/p95/p99 and worst stalls under reconnect load.
- Restart during the active session recovers state without duplicate entries.
- Stream disconnect, rate limit, invalid data, unavailable news and database outages
  have defined outcomes; protection and reconciliation survive entry lockouts.
- Race tests cover partial fills, submission timeouts, duplicate events, cancel/fill
  races, opposing candidates, budget exhaustion and close-order rejection.
- End-of-session orders, positions, fills and reported P&L reconcile to the broker;
  an exit request or Slack message is not proof of closure.
- Durable storage and restore are tested. A laptop's local PostgreSQL is not
  automatically reachable from Railway and must not be exposed publicly without
  secure connectivity. Choose hosted persistence or a secured network explicitly.
- Rollback, kill switch, alert delivery and spending controls have been exercised.

## 11. Implementation order

| Release | Scope | Readiness evidence |
| --- | --- | --- |
| A | Pure candidate creation; shared final gates; actual exit/reconciliation; aggregate risk | Regression probes stop reproducing the defects; no broker call for rejected candidates |
| B | WAIT/EXPIRED states; fresh complete data; restart/idempotency; durable journal | Replay parity and failure/recovery tests; baseline preserved |
| C | All-instrument preparation and source adapters in report-only mode | All 20 dossiers, timestamps, unknown states and source/coverage tests |
| D | Multi-symbol streaming on Railway, submission disabled | Actual deployment and observed shadow-session acceptance |
| E | One in-play ORB experiment | Frozen development parameters and independent net-cost validation |
| F | Supervised paper execution of the validated corrected policy | Broker-confirmed lifecycle and risk acceptance |
| Later | Reversal/event/second-day experiments, optional ML, then later-day sessions | Separate evidence and explicit promotion for each change |

The existing late-bar reversal defect must be corrected before that lane is included
in any active release. Do not push current local defaults as a shortcut to release D.
Preparation work can proceed alongside repairs in report-only mode, but no context
feature or agent removes the requirement to repair execution first.

Bottom line: the next useful system is prepared, source-aware, event-driven and
auditable across the full watchlist. Additional strategy complexity is justified
only when it adds independently measured net value to that corrected system.
