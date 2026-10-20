# V Shadow Quote Incident

## Observed Message

The [V shadow entry](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792503385108539)
at approximately 09:36 New York reported HTF long, six shares, entry $406.45,
planned stop $403.43 and planned risk $18.14. The
[subsequent stop observation](https://thehustlex.slack.com/archives/C0C62C6DMU7/p1792503465228209)
reported -4.20R / -$76.14 and explicitly marked the intervening path unverified.
No broker order or fill occurred.

## Corroborating Provider Evidence

Read-only Alpaca historical IEX requests made after the incident returned:

- At 2026-10-20 13:37:43.346225 UTC (09:37:43 New York), a V quote with bid
  **393.76** and ask **406.09**, both on IEX, spread approximately **308.3 bps**.
  Several subsequent quotes repeated those prices before 13:37:45 UTC.
- The 09:37 IEX trade bar had low **405.615**, high **405.95**, close **405.73**.
  Its low was above the displayed planned stop of 403.43.

The quote exactly reproduces the displayed dollar observation:

```text
(393.76 - 406.45) * 6 = -76.14 dollars
-76.14 / approximately 18.14 planned dollars per R = approximately -4.20R
```

These are historical provider responses, not an as-received replay or a record of
an actual fill. A wide venue quote can exist without a trade at its bid; this does
not prove that the provider reported a malformed quote or that every venue's price
stayed above the stop. It does corroborate the simulation's use of the unusually
wide bid, rather than a confirmed traded-price stop execution.

## Code Finding and Follow-Up

Entry checks enforce `max_spread_bps` (20 bps under the defaults). Exit marking
previously checked quote age with an infinite spread limit. A fresh low bid could
therefore trigger a long stop mark well below the stop even when the quote was
hundreds of basis points wide. The observation was already excluded from verified
P/L because of a quote gap, but the Slack heading still called it a shadow result.

The follow-up changes:

- Apply quote-quality classification at every shadow close. An excessive exit
  spread, stale/unavailable exit quote, observation gap or restart keeps the path
  explicitly unverified. Raw observed prices and R are retained, not capped to -1R.
- Persist structured gap evidence, including previous/next quote timestamps and
  elapsed seconds, plus the exit quote and its spread limit where applicable.
- Label affected messages `UNVERIFIED SHADOW OBSERVATION`, displaying entry, planned
  stop, observed exit, the dollar definition of 1R, spread and verification reasons.
- Include per-trade evidence in acceptance reports and startup logs, allowing the
  existing stored V record to be examined without exposing credentials or adding a
  public database endpoint. Unverified trade paths cannot pass session acceptance.
- Keep the existing conservative risk-budget treatment of uncertain losses and
  profits. Historical ORB entry/stop/target definitions and stored trades are not
  rewritten. Reliable fill modeling and broker-backed protection remain separate
  validation work; these changes do not create a broker stop or guarantee -1R losses.
- Refresh the full preparation snapshot after catalyst-source collection completes,
  rather than leaving a restart-time UNKNOWN snapshot as the only message.

Verification: 269 offline tests plus 10 subtests pass. PostgreSQL integration passes
separately in a temporary UUID schema, including persisted gap diagnostics and
trade-audit decoding. Regression tests reproduce an uncapped -4.20R wide-quote
observation, exclude it from verified totals, preserve a normal tight-spread -1R
stop mark, retain gap timestamps and keep affected acceptance reports incomplete.
