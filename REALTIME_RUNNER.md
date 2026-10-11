# Real-Time Runner

This worker is for the VPS/Railway-style Monday paper-trading setup.

The production setup is cron-triggered so the host can stay offline most of
the time. When Railway starts near market open, it:

1. Runs the ForexFactory macro news risk check.
2. Runs the stock catalyst risk check.
3. Starts the live signal watcher before the open.
4. Sends Slack alerts and recap messages.
5. Exits after the session cutoff.

Default schedule is New York time:

- Start: `09:25`
- Last new entry: `10:00`
- Watch cutoff: `10:15`

## Start Command

```bash
python scripts/realtime_runner.py --once --max-wait-minutes 60
```

The included `Procfile` runs the same command as a worker process.

With this command, the worker only waits if the next session is within 60
minutes. If it is started on a weekend or many hours early, it exits so Railway
does not keep charging for an idle process.

## Required Environment Variables

Set these in the server dashboard. Do not commit secrets.

```text
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
SLACK_WEBHOOK_URL=...
```

Optional:

```text
FINNHUB_API_KEY=...
ALPACA_DATA_FEED=iex
REALTIME_EQUITY=10000
REALTIME_SUBMIT_PAPER=false
REALTIME_POLL_SECONDS=30
REALTIME_START=09:25
REALTIME_LAST_ENTRY=10:00
REALTIME_CUTOFF=10:15
REALTIME_SYMBOLS=AAPL,NVDA,AMD,AVGO,SNDK,INTC,GOOGL,QQQ
REALTIME_HTF_SYMBOLS=AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD,AVGO,JPM,V,MA,NFLX,COST,ORCL,CRM,ADBE,QQQ
REALTIME_STRATEGIES=ORB_RETEST_RECLAIM_BODY_2R,HTF_BREAKOUT_RETEST_2R,VWAP_EMA9_CROSS_2R
REALTIME_NEWS_START=08:00
REALTIME_NEWS_END=10:30
REALTIME_MAX_WAIT_MINUTES=60
```

Keep `REALTIME_SUBMIT_PAPER=false` for the first Monday run. That keeps the system in alerts-only mode.

## Local Smoke Test

This validates argument parsing and exits if the next session is too far away.
Do not use it during market hours unless you expect alerts.

```bash
python scripts/realtime_runner.py --once --max-wait-minutes 60
```

If the next session is within 60 minutes, the runner will sleep until start and
run that session. Otherwise, it exits cleanly.
