# Upstream repository audit — October 28, 2026

**Scope:** Focused source/README/code-path review of the seven repositories discussed in this conversation. This is **not** a line-by-line security audit, formal dependency vulnerability scan, or proof of historical performance. No upstream code has been copied into this project; ideas and public data schemas are independently implemented. The container had no public DNS access, so we examined public repositories through an authenticated GitHub reader and public documentation. Builds against the actual live endpoints remain untested.

| Project | Inspected | Adopted here | Not adopted, and why |
| --- | --- | --- | --- |
| [sfrbuilds/polymarket-wallet-analyzer](https://github.com/sfrbuilds/polymarket-wallet-analyzer) | `README.md`, `wallet_analyzer.py` | Wallet-centric export, useful aggregate analysis; independent V2 cursor exporter and cohort audit | Offset-based `/_paginate` implementation; V2 refuses offset and legacy endpoints have limited depth. Does not establish that fills represent order intent or actual net P&L |
| [Polymarket/py-sdk](https://github.com/Polymarket/py-sdk) | `README.md` and public usage, release architecture | Official SDK is the forward-compatible reference for optional public data integration | No authenticated trading SDK dependency; deliberately minimal GET-only client cannot sign, transfer, or create orders. SDK itself has broad authenticated capabilities not needed here |
| [Josh-Alot/polymarket_5min_bot](https://github.com/Josh-Alot/polymarket_5min_bot) | `README.md`, `pm5/strategy.py`, `pm5/pricefeed.py` | Exact UTC market-window discovery, paper-mode separation, paired outcomes, reference-oracle observation concept | End-window momentum is a different, unvalidated hypothesis. The sample arb compares top-of-book prices, not a full two-leg depth/fee/latency execution. WebSocket Chainlink feed requires a separate authenticated/verified settlement alignment test |
| [0xbrutusdev/polymarket-5min-crypto-trading-bot](https://github.com/0xbrutusdev/polymarket-5min-crypto-trading-bot) | `README.md`, `spork30/reference.py`, `spork30/fillmodel.py`, `spork30/audit.py`, `spork30/diagnostics.py` | Public-wallet activity including SPLIT, MERGE, REDEEM, MAKER_REBATE; time-frozen train/test policy; fill-model queue and latency sensitivity; no fabricated maker fills | Spork30's strategy is predominantly sell-side, split/mint and posted asks. StepAhead evidence and the seller describe buys, so we don't port Spork30's implementation or its assumption of historical rebates. The repo's replay itself notes historical synthetic-fill errors |
| [mileswangs/polymarket-historical-data](https://github.com/mileswangs/polymarket-historical-data) | `README.md` | Optional authoritative **recorded** L2+Chainlink dataset for execution validation; data provenance and UTC normalization required | It covers August 8–21, 2026, not the StepAhead October active period. Free dataset is a 5.6GB external download not bundled. Its provider performance statements require independent replication |
| [mileswangs/pm-hftbacktest](https://github.com/mileswangs/pm-hftbacktest) | `README.rst`, fee/queue/latency usage example | Queue, latency, fill-model sensitivity as a research standard; an optional external test runner could be added after obtaining comparable data | Large Rust/Numba dependency not necessary for wallet forensics or snapshot capture. Provider example requires its own dataset/API key; not installed or run during this build |
| [marketlenstrade/polymarket-historical-data](https://github.com/marketlenstrade/polymarket-historical-data) | `README.md`, declared Parquet event schema | Offline format reference for snapshot/delta/trade reconstruction and an independent historical sample | June 15 BTC sample is outside the target's 2026-09/10 active dates. Data and market metadata compatibility must be validated; no unseen large download bundled. Sample data CC-BY-4.0 requires attribution |

## Public wallet and source triangulation

- Target: `0xe29042f5d913dcc4015aab3455c13c58514ca33f`.
- [Polymarket profile](https://polymarket.com/profile/0xe29042f5d913dcc4015aab3455c13c58514ca33f) is the canonical *public* wallet profile.
- [StepAhead seller](https://stepaheadpol.com/) claims paired UP/DOWN buys, directional favorite + partial hedge, and never selling.
- [Independent POLBOTS review](https://polbots.com/bot/stepahead) reports a selected July–October profit window, an earlier losing period and material rebates; sampled wallet executions were buy-only. These are third-party observations, **not verified from a fresh downloaded tape here**.
- [future.news wallet page](https://future.news/address/0xe29042f5d913dcc4015aab3455c13c58514ca33f?period=all&tab=history) was inaccessible in this environment. Do not cite inferred observations from that UI. Monitor the **underlying Polymarket public API** instead.

## Model-specific traps

1. Buying equal *gross* share quantities does not guarantee equal settlement payouts when taker fees are deducted in shares. The paired evaluator computes UP-wins and DOWN-wins separately after book depth and fee-share deductions.
2. A non-atomic pair of public books is **not executable arbitrage**. REST snapshots are a research signal, not a fill record. Book age and skew gates mitigate artifacts but cannot eliminate legging risk.
3. Passive quote value requires counterfactual fill modelling, which is unprovable from wallet fills alone. Queue position, cancellations and own impact are missing.
4. Splits, merges, redemptions, P&L snapshots and rebates use different accounting grains; do not double-count. Never assume a constant maker rebate bps from another account's paper or a seller's dashboard.
5. Dataset provenance matters: June and August are not October. Even a perfect historical simulator trained on those dates is not StepAhead's realized October edge.
6. Avoid importing upstream wallet-key/signing/live modules, auto-trading wrappers or vendor UI: no direct need and extra attack surface.

## Remaining due diligence before any performance claim

- Run `stepahead doctor` on an internet-connected host; validate current V2 and Gamma response schemas.
- Export complete active-period activity and trades with stable start/end windows and audit the cursor chains.
- Establish which unique fill IDs and on-chain events correspond to the wallet without assuming a transaction hash identifies only one fill.
- Reconcile actual account P&L from official `user-stats` and time series, with separate trading, fees and rebate components, respecting timestamps.
- Obtain contemporaneous recorded books, not backfilled or current prices, for the **same market windows** and calibrate queue/latency against known prints.
- Pre-register prospective hypotheses; time-order calibration and holdout periods and report cash-only vs rebates-included P&L, drawdown, variance and costs.
- Restrict operations to public GETs and local files. No test is evidence or authorization to trade.
