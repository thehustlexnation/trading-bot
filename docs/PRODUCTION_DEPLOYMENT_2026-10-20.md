# Production Deployment Verification

Verified during the October 20 regular session after explicit owner authorization.

## Release

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
