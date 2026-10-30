# Cross-platform publishing and Mac migration fix (v1.1.2)

The 2026-10-28 Windows publisher appeared to stop mid-file-list with a `:` pager prompt. v1.1.2 disables Git's interactive pager for all staging/diff output, rejects unexpected pre-staged files, normalizes a newly initialized `master` branch to `main`, and adds `.gitattributes` for portable source line endings. The Mac launcher now selects an installed Python 3.11+ interpreter instead of accepting an incompatible system default.

**IMPORTANT:** Your GitHub repository must display a real commit before cloning. On Windows, stop `start.bat`, extract the v1.1.2 hotfix without removing `data/` or `.git/`, run `start.bat`, then `publish-to-github.bat` and look for `PUBLISHED`. On macOS: `mkdir -p ~/Documents && cd ~/Documents && git clone https://github.com/Wandersport/WS-StepAhead-Lab.git && cd WS-StepAhead-Lab && bash start.command`. For updates: stop the process, `git pull --ff-only`, relaunch. Git intentionally ignores local `data/`, so a Mac clone begins with fresh research observations unless you transfer that folder separately.

# Windows 11 hotfix (v1.1.1)

**Fixes the user's 2026-10-28 Windows startup failure.** The old duplicate-dashboard-port unit test failed on Windows because Winsock can permit a second listener when SO_REUSEADDR is used. The v1.1.1 release applies Windows `SO_EXCLUSIVEADDRUSE` before the bind, disables address reuse on other platforms, and closes any unexpected test socket. The GitHub publishing script now supplies a **repository-local** Git identity if none is configured (not global); it uses the verified `Wandersport` GitHub owner/noreply identity.

**Install:** Stop any previous `start.bat` instance. Extract v1.1.1 into the existing `stepahead-lab` folder, replacing source files, while preserving `data/`, `.git/` and `.venv/`; run `start.bat` and then `publish-to-github.bat` if you wish to publish to the existing repository. The archive contains no credentials or live-order code. The apparent `3.6797%` and `P&L 50` shown in the failed Windows test were synthetic test fixtures—not real trading observations.

---

# Windows 11 clock repair (v1.1.0)

The 2026-10-28 Windows session measured `observed_at - source_ts = -25.93s` on **both** BTC outcome books. Version 1.0 rejected every quote as `stale_exchange_book`. This release calibrates against the CLOB public `/time` endpoint, uses an anchored monotonic venue clock for market discovery and book timestamps, and **continues to reject** genuinely stale, future-dated, late or mismatched books. It does not place any orders.

**Update on the existing Windows machine:** stop v1.0 with Ctrl+C; extract this archive over your existing `stepahead-lab` directory **without deleting `data/`**; run `start.bat`. The launcher reinstalls the editable local package and runs 59 offline tests, then launches the same loopback dashboard. Do not run old/new copies simultaneously. Existing v1.0 snapshots are **archived without deletion** at `data/paper/snapshots_v1_0_pre_clock_fix.jsonl`; new calibrated snapshots start in a fresh file. Run `diagnose-clock.bat` separately to inspect the venue-versus-Windows clock offset. Windows Settings > Time & language > Date & time > Sync now can correct the host clock, but venue calibration is still required for reliable measurements.

**GitHub:** your existing repository `Wandersport/WS-StepAhead-Lab` was empty when checked. `publish-to-github.bat` stages only project source and documentation, then performs a normal (not forced) push. It requires Git for Windows and GitHub authentication and does not push `data/`, `.venv/` or `.env`. Publishing is an explicit operator action; simply running the monitor never pushes code.

**Important:** Exchange time only addresses the observed timestamp mismatch. It does **not** create positive-edge opportunities, resolve one-leg execution risk, prove queue fills or make the historical target wallet's profit yours. There may legitimately be zero signals for long periods. No live-money trading capability is included.

---

# StepAhead Lab 1.1 — independent Polymarket wallet research bot

**Status: ready-to-run read-only research application, NOT a live-money trading bot, original StepAhead clone, or demonstrated profitable strategy.** Automated trader wallet monitoring, five-minute BTC Up/Down book snapshots, fee-aware hypothetical paired-entry screening, verification of resolved-market winners, a local dashboard, and optional deeper wallet research.

## One-click start

1. Download the ZIP or TAR archive and **extract it**.
2. Install Python **3.11+** if it is not installed.
3. **macOS:** open `start.command` (from Terminal if Finder blocks it); **Windows:** double-click `start.bat`; **Linux:** run `sh start.sh`.
4. In the browser, open <http://127.0.0.1:8765/>. The launcher creates its own `.venv`, installs the only runtime dependency (`requests`), runs the offline tests, and begins foreground observation. **Ctrl+C stops it.**

You need an internet connection; the code fetches public Polymarket APIs over HTTPS and never requests an exchange private key. If Polymarket's API is blocked on your network, the app logs errors and stays in a read-only state. Initial charts will be empty until data accumulates. Nothing runs in the background after the terminal exits.

## Available commands

After launching once, activate the environment: `source .venv/bin/activate` on Mac/Linux, or `.venv\\Scripts\\activate` on Windows. Then:

| Operation | Command |
| --- | --- |
| Full paper-monitor service and dashboard | `stepahead start` |
| Short smoke run (2 × 15 polls) | `stepahead start --cycles 2` |
| Dashboard only | `stepahead dashboard` |
| Public API compatibility | `stepahead doctor` |
| Monitor target profile once | `stepahead monitor once` |
| Periodic profile monitoring (local process) | `stepahead monitor watch --polls 24 --interval 300` |
| Single BTC 5m arbitrage quote check | `stepahead scan` |
| Passive maker candidate analysis | `stepahead maker-scan` |
| Bounded public-book paper observation | `stepahead paper --polls 100 --interval 4` |
| Strict counterfactual settlement with verified winner | `stepahead settle` |
| Re-evaluate recorded snapshots | `stepahead replay` |
| Delayed sequential two-leg replay (exposes stranded legs) | `stepahead shadow-replay --latency-ms 500` |
| Export target wallet public fills, resuming | `stepahead wallet export --max-pages 5` |
| Validate wallet cursor continuity | `stepahead audit --kind trade` |
| Wallet strategy behavior report | `stepahead wallet analyze` |
| Export public activity including rebates and splits | `stepahead activity export --max-pages 5` |
| Audit activity and behavior | `stepahead audit --kind activity && stepahead activity analyze` |
| Model maker queue consumption from externally supplied prints | `stepahead queue-replay --orders orders.json --prints prints.json` |
| Inspect local data without network | `stepahead overview` |

Use `--max-pages 0` for an unlimited page walk. **All API reads are subject to upstream service health, rate limits and availability.** The first long wallet export may take time and disk space, so it is intentionally separate from the one-click book monitor.

## What this implementation does (and does not) prove

- It identifies the BTC 5-minute round by its UTC-epoch slug, validates the binary UP/DOWN token mapping, samples public asks and bids, sweeps visible depth, applies a crypto taker-fee model **as shares withheld on buys**, checks both possible payout outcomes, reserves a cost buffer, and requires repeated eligible observations to log a paper entry.
- A separate delayed sequential replay can model a stranded first leg when later quoted depth disappears. Its fills are still assumptions, not confirmed executions. The two books are separate, asynchronous HTTP snapshots. They do **not** prove either leg actually filled. Even a positive quote, or a verified eventual winner, is **not** earned profit.
- Maker quote research produces hypothetical post-only prices. The optional offline queue replay needs recorded public trade prints and explicitly reports **modelled** fills, never exchange-confirmed fills. Maker rebate P&L is **not fabricated**.
- The dashboard clearly distinguishes official target-profile P&L and volume from this project's **counterfactual paper accounting**. The target wallet's actual source code, risk limits and queue placement remain unknown.
- It does not import or execute any third-party bot code; public repositories were evaluated for methods and ideas, with references in `REPO_AUDIT.md`.
- Only allowlisted HTTPS GET requests can be issued. There are **no methods, credentials, signed orders, cancel actions, or deposit flows** for real-money execution.

## Data storage and integrity

- `data/profile_snapshots.jsonl` — observed target account state, not our performance.
- `data/wallet/page_*.json` — atomic, cursor-resumable official Data API V2 trade pages.
- `data/activity/activity_*.json` — atomic, fixed-window activity history; splits/merges/redeems/rebates.
- `data/paper/snapshots.jsonl` — sampled public order-book observations.
- `data/paper/paper_pairs.jsonl` — fictional paired-fill scenarios, explicitly no live execution.
- `data/paper/settlements.json` — exact closed-market winners matched to those paper entries, with counterfactual P&L.

Data is local and ignored by Git. Back it up separately if needed. Do not edit `page_*.json` or settlement caches manually; audit them first. Launchers will not overwrite any portfolio or GitHub project. `WS-X`, `WS-Jev`, and `WS-Dots` are untouched.

## Technical validation

Run `python -m unittest discover -s tests -v`. The tests use synthetic fixtures and do not touch external APIs. The original repo version v0.2 had 21 passing tests; this release adds endpoint-agnostic settlement, dashboard, service, and explicit zero-order boundary tests. **External live API handshakes cannot be verified in the build environment**, where public DNS is unavailable, so check `stepahead doctor` on your connected computer.

## Further reading

- `OPERATIONS.md` — operating tips and failure recovery.
- `REPO_AUDIT.md` — what was actually adopted and rejected from the repositories.
- `RESEARCH_HANDOFF.md` — remaining scientific validation and execution risk.
- Official API: <https://data-api.polymarket.com/v2/docs>.
- Official fee guide: <https://help.polymarket.com/en/articles/13364478-trading-fees>.

**Not financial advice.** Observe Polymarket rules and jurisdiction restrictions before interacting with its services. No investment outcomes are guaranteed.


## Offline StepAhead wallet forensics (optional, added October 28, 2026)

When you have collected an activity sample, run in a SECOND Terminal while the 24-hour paper monitor continues:

```bash
cd ~/Documents/WS-StepAhead-Lab
.venv/bin/stepahead audit --kind activity
.venv/bin/stepahead activity forensics --top 10
```

The report is written to `data/activity_forensics.json` (ignored by Git). It computes per-five-minute-window buy quantities, weighted acquisition prices, FIFO-matched UP/DOWN pair cost, the first-to-opposite-side timing gap, gross **pre-fee** pair edge and unmatched inventory. It flags partial boundary windows, excludes records with SELL activity or malformed trades from buy-only scenario rankings, and never labels those estimates as actual profit.

**Do not confuse this with a full StepAhead replica:** the matched-pair calculation ignores fees debited in shares, rebates, oracle settlement, unknown execution queue positions and any unobserved wallet cashflows. Wallet activity can show both sides purchased without their cost being below $1. The source may contain incomplete market windows at the beginning and end of a partial export.

The first export is deliberately frozen to the original `end` timestamp. Resume it with `.venv/bin/stepahead activity export --max-pages 30` (or `--max-pages 0` for potentially lengthy exhaustion); do not change the start/end/page-size parameters for that directory. This export is public GET-only but competes for API rate limits and consumes local disk space. It is not necessary to exhaust the wallet's entire history before running a 24-hour prospective paper observation.

**Mac run:** keep the Terminal open, the laptop connected to power and its lid open. Use `caffeinate -dimsu bash start.command` to prevent idle sleep. Leave the process and `data/` untouched until the prospective run is complete. Pushing GitHub source code never transfers the local history.

## Bounded paper observation (v1.1.3)

To run for a fixed 24 hours on your Mac, use `caffeinate -dimsu bash start.command --duration-hours 24`. The process prints an exact planned stop time in Madrid time and then exits automatically after the duration measured by a monotonic timer. For a specific end time, use `caffeinate -dimsu bash start.command --stop-at '2026-10-30T18:00:00+01:00'` (change the date as needed; UTC offset is mandatory). In-flight network calls may make the final exit a little later than the deadline, but no fresh poll begins after the deadline. Both options remain public-data/PAPER_ONLY. The launcher forwards command-line flags; do not start multiple instances on port 8765. Retain the existing ignored `data/` folder for later analyses.


## Optional research phase: record upcoming BTC 5-minute books (v1.2.0)

The first two 24-hour runs found zero qualifying simultaneous two-ask paper pairs, while the public wallet frequently bought both sides and often entered before the comparison window. The next collector observes the **next two listed BTC five-minute markets** before their event start, in addition to the existing current-window paper scanner:

\`\`\`bash
cd ~/Documents/WS-StepAhead-Lab
caffeinate -dimsu bash start.command --duration-hours 24 \
  --capture-upcoming --upcoming-windows 2 --upcoming-every 5
\`\`\`

The terminal prints the automatic Madrid stop time. For an exact wall-clock deadline, replace \`--duration-hours 24\` with a timezone-aware \`--stop-at\` argument. No live orders are possible. New evidence goes to ignored local files \`data/prewindow/snapshots.jsonl\`, \`data/prewindow/status.jsonl\` and \`data/prewindow/errors.jsonl\`, preserving earlier \`data/paper/\` snapshots. The dashboard exposes only the number of upcoming snapshots captured; these are NOT simulated trades, modeled fills or profit.

An upcoming market must have a canonical Gamma event, a verified condition ID, unambiguous UP/DOWN token IDs and metadata consistent with its five-minute start/end. Missing or unlisted markets are recorded separately, rather than fabricated. All records include REST response times, book skew and book ages. This feature makes extra public GETs and may run more slowly than the original collector. It does not assume that early public book quotes were executed by StepAhead or by us.

**Research standard:** do not tune a strategy using these snapshots and claim out-of-sample profit. Analyze the observed prewindow books alongside the wallet's public fill chronology; record fill assumptions, fees, rebates, queue priority and unavailable market coverage before building a paper execution model.
