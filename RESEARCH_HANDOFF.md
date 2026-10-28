# Engineering handoff — StepAhead Lab v0.2.0

## Completed and verified locally

- Independent Python research repo, no order submission or credentials; no WS-X/WS-Jev modifications.
- Public GET client restricted to official Polymarket hosts; fixed UTC 5-minute market discovery with strict token mappings.
- Complete-set taker research after depth sweep, fee-in-shares, both settlement outcomes, expiry/staleness/skew checks.
- Passive maker quote evaluator that **never** counts a fill or rebate.
- Crash-resumable trades export, new crash-resumable fixed-cohort activity export, integrity audits and activity/portfolio summaries.
- Official profile/volume/status snapshots and local deltas; bounded local watch.
- Quote snapshots, paper decisions, deterministic quote replay, and bounded/explicitly modelled offline queue replays.
- Synthetic test suite: **21/21 passing** on Python 3.13 in this build container. Static import compile passed.

## Not validated: explicit gaps

- The container cannot reach external hosts; **no** Polymarket API handshake, full wallet export, observed real execution or third-party datasets have been downloaded.
- future.news site inaccessible; use underlying canonical Polymarket API for wallet monitoring.
- No provenance-backed account P&L reconciliation, settlement-specific fill audit, rebate verification, causal strategy inference, or out-of-sample profitability claim is possible yet.
- Queue model not calibrated against historical exact book updates or observable live fills. There is no authenticated SDK or live trading.

## Safe next steps for an engineer with network access

1. On an isolated Mac working directory, install and run `python -m unittest discover -s tests -v` and `stepahead doctor`. Capture actual V2 response schemas; fix incompatibilities before invoking large exports.
2. `stepahead wallet export --max-pages 5`, `stepahead activity export --max-pages 5`, inspect samples, then `audit` and analyze. Once API verified, resume with `--max-pages 0` (large storage warning).
3. Establish full user-stats/user-pnl/account reconciliation; account for fee shares vs account-cash fields, actual event redemption outcomes, maker vs taker classification, rebates, and settlement timestamps. Cross-check with independent aggregate totals.
4. Freeze hypothesis definitions *before* observing held-out days. Match reference activity with **contemporaneous recorded order books**; don't simulate based on current or end-window quotes.
5. Run `pm-hftbacktest` in a **separate optional environment** after obtaining dated PMData/Marketlens data. Calibrate with multi-queue assumptions, 0+ ms latency stress, partial fills, adverse selection, stop loss/exposure, zero-rebate and observed-rebate sensitivity.
6. Collect several prospective paper-only weeks; require positive after-fee cash-only results and reasonable drawdown under conservative queue assumptions before making any recommendation to fund a wallet. Never auto-enable trading.

Refer to REPO_AUDIT.md and OPERATIONS.md for boundaries and full commands.

## October 28 v1.0 upgrade

- Runnable offline unit-test suite, macOS `.command`, Windows `.bat` and Linux `.sh` launchers.
- A **foreground** public-book researcher (`stepahead start`); an optional **loopback-only** HTML dashboard (`stepahead dashboard`); and local `stepahead overview`.
- Verified closed-market winner lookup and tamper-evident **counterfactual** settlement caching. Does not claim either order filled and assumes no actual rebate income.
- The public market API could not be integration-tested against live endpoints in the build environment. Do not treat a clean unit test as such an integration pass.
- Full true StepAhead reproduction, live execution, complete active-period on-chain accounting, and calibrated queue modelling remain open scientific and operational work. There are **no** authenticated exchange order methods or permissions in v1.0.
