# Production Deployment Verification

Verified during the October 20 regular session after explicit owner authorization.

Latest production release: `582b642c8c04b2404b9be7c06bcd95a0ff55fc2a`, with the
quote-diagnostics follow-up described below. Broker submission is disabled.
Earlier release evidence is retained chronologically, not presented as current.

## Original Release

- Repository: `thehustlexnation/trading-bot`.
- Production code: `19de0a7f4b4a358c814998fbd2f5010c56c4f8f1`.
- [Pull request 2](https://github.com/thehustlexnation/trading-bot/pull/2).
- [Hosted CI](https://github.com/thehustlexnation/trading-bot/actions/runs/39432710921)
  passed tests, sanitized configuration and the Docker build for this commit.
- Local verification: 265 tests plus 10 subtests passed; PostgreSQL integration
  passed separately in a temporary UUID schema.
- Railway project: `confident-curiosity`; environment: `production`.
- Worker service: `trading-bot`; deployment:
  `2008d343-2537-4937-8b91-9ef8be860727`.
- Railway reported the build SUCCESS at 13:45 UTC. The existing cron deployment
  was started manually for the remaining session; the new worker bootstrapped and
  authenticated at approximately 13:46 UTC.
- Runtime logs and Slack identify the exact code SHA above and config hash
  `ab9fa5c364acaee698a6`. Storage is shared PostgreSQL; the feed is IEX.

## Production Configuration

The existing service is pinned to the verified commit. It uses the repository
Dockerfile and retains one replica, restart NEVER, native cron `40 11 * * 1-5`,
and start command:

```sh
python -u scripts/realtime_runner.py --once --max-wait-minutes 120
```

Deployment overlap is zero and draining is 30 seconds. No additional service or
public database endpoint was created. No secret values were printed or copied.
The pending Railway patch was reviewed before deployment.

## Slack and Live Observations

The production worker sent:

- [Versioned startup](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792503988824559).
- [Updated opening preparation for all 20 instruments](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792503988879319).
- [22-symbol subscription acknowledgement with the deployed SHA](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792503994292009).
- [INTC shadow exit after deployment](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792504024421959):
  `FAILED_BREAKDOWN_RECLAIM`, approximately -0.25R / -$5.76 before fees. This was
  an existing position resumed from the prior worker. Its intervening path is
  unverified, and the message excludes it from verified-path P/L totals.

The prior worker also delivered a
[V shadow stop result](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792503465228209),
approximately -4.20R / -$76.14, marked unverified after a quote-data gap. The
restart-era recap identifying `b3b410b` is the old worker's output, not the final
recap for the new release. The replacement records interrupted runs as checkpoints
and uses a separate versioned key for its final recap.

The first telemetry window, ending 13:47:29 UTC, recorded 22 symbols, 22 initial
minute bars and 64,922 quotes. Initial bar receive lag was at most 0.239 seconds;
the sampled quote-receive p99 was approximately 0.0156 seconds. Evaluation p95 was
approximately 0.117 seconds, with a 0.538-second maximum. Queue high water was 987
of its 10,000-event capacity. Percentiles describe bounded recent samples within
this window, not a whole-session latency guarantee.

## Scope

Broker submission remains disabled. These are hypothetical quote-based entries
and exits delivered by the real production service, not broker orders or fills.
No trades were fabricated to exercise notifications.

Deployment and post-deployment Slack result delivery are verified. The normal
10:15 New York recap and acceptance report run at session completion. A deployment
mid-session produces mixed code identities and interrupted-path results, so this
day must not be represented as an uninterrupted acceptance session. The default
EMA9 warmup/entry-window conflict also remains explicit. See the
[acceptance and research checkpoint](SHADOW_ACCEPTANCE_2026-10-20.md) for the
remaining research and broker-lifecycle work.

## Quote-Diagnostics Follow-Up

Production now pins `582b642c8c04b2404b9be7c06bcd95a0ff55fc2a`, deployment
`4fcbac61-a701-4354-95d8-df615fc6de1d`. Its
[hosted CI](https://github.com/thehustlexnation/trading-bot/actions/runs/39435817975)
passed tests, sanitized configuration and Docker build. Local verification passed
269 tests plus 10 subtests, with PostgreSQL integration passed separately.

Railway reported build success at 14:07:03 UTC. The cron deployment was manually
started for the remaining watch, and at 14:07:53 UTC runtime logs confirmed the
new SHA, unchanged config hash, shared PostgreSQL and broker submission disabled.
The old worker exited with an interrupted checkpoint, not a final session recap.

- [Versioned startup](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792505274040359).
- [22-symbol subscription acknowledgement](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792505279106259).
- Startup diagnostics read both stored closed positions without rewriting them.
  The V record confirms the 393.76 bid / 406.09 ask, approximately 308.3-bps spread,
  exact planned risk of $18.14352 and calculated -4.19654R. The INTC record confirms
  approximately -0.24870R. Both paths remain unverified.

See the [quote incident report](SHADOW_QUOTE_INCIDENT_2026-10-20.md) for the evidence
and changes to classification, reporting and future gap diagnostics. The reviewed
Railway patch changed only the existing worker's source commit pin. No strategy,
risk, schedule, credential or broker-mode setting was changed.

The interrupted 14:07 checkpoint reported missing completed minutes for COST, JPM
and MA, 20 invalid market events, and the existing EMA9 schedule conflict. These
are unresolved acceptance findings, not permission to synthesize bars, relax quote
validation or enable orders. A source-complete catalyst snapshot and the normal
10:15 New York recap were then observed for the replacement worker.

The [source-complete preparation refresh](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792505319190059)
arrived at 14:08:39 UTC: 19 issuer records were `AVAILABLE_UNVERIFIED`, with QQQ
explicitly `ETF_CONTEXT_ONLY`. This establishes source retrieval, not verified
catalysts or SEC coverage; `SEC_USER_AGENT` is not configured. Several premarket
levels and relative-volume values remain unknown on the observed IEX feed.

The first new telemetry window ending 14:08:55 UTC covered all 22 subscriptions,
53,872 quotes and 22 initial minute bars. Maximum initial bar receive lag was
0.343 seconds, sampled quote-receive p99 approximately 0.0176 seconds and evaluation
maximum approximately 0.1245 seconds. These are window-local measurements, not a
claim of complete or uninterrupted session data.

## Final Cutoff Evidence

The [final recap](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792505704565359)
arrived at 14:15:04 UTC (10:15 New York) from `582b642`. It reports two closed
hypothetical positions, no unresolved exposure and both INTC/V excluded from
verified-path P/L. Its $0 verified total does not mean the raw marks broke even.

The persisted/logged acceptance report at 14:15:06 UTC reports **FAIL**, with:

- PASS: completion, all 16 notifications delivered (zero pending), all 34 lanes
  recorded, all 22 symbols represented in quote telemetry, latency evidence,
  preparation inventory, prior-session levels and submission disabled.
- FAIL: mixed release identities from the intraday deployments; EMA9 has no entry
  window; missing completed IEX minutes out of the expected 45 per symbol
  (COST 9, JPM 6, MA 4); 20 invalid market events recorded over the session.
- UNVERIFIED: V and INTC trade paths. Legacy evidence cannot establish the exact
  intervening quotes, and the report does not retroactively label them verified.
- Zero recorded backfill request errors or preparation-source exceptions. This
  does not imply complete bars, verified catalysts or configured SEC coverage.

The final latency window ending 14:15:00 UTC includes all 22 initial bars for
14:14 UTC and all 22 symbols' quotes. Maximum initial bar receive lag was about
0.0507 seconds; evaluation maximum about 0.0101 seconds. Recent quote-receive p99
was about 0.1685 seconds. Earlier missing minutes remain missing.

The release and complete Slack notification lifecycle are verified. A clean
uninterrupted acceptance session, richer rejected-event evidence, feed-completeness
policy, separately validated EMA9 timing, realistic full-policy replay and broker
protection/reconciliation remain required before paper-order activation.
