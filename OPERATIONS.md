## Publishing and cloning 1.1.2

On Windows, the publisher runs a noninteractive staged-file listing, refuses unapproved
staged files, and performs an ordinary `git push -u origin main` with no force.
It may ask for GitHub authentication. The literal `:` seen in the older
publisher was a Git pager prompt; the new script avoids it.

Once the repository contains source code, on the Mac:

```bash
mkdir -p ~/Documents
cd ~/Documents
git clone https://github.com/Wandersport/WS-StepAhead-Lab.git
cd WS-StepAhead-Lab
bash start.command
```

After later changes stop the service, `git pull --ff-only`, and restart.
The Mac's local data/ is never fetched from GitHub. Transfer it separately
if you want historical continuity; otherwise it starts clean.

---

## v1.1.1 Windows startup / repository repair

The failure `AssertionError: OSError not raised` was a **test-level duplicate-port problem**, not a Polymarket API failure. Winsock's port reuse differs from Linux; the dashboard now requests Windows `SO_EXCLUSIVEADDRUSE` and a non-reusable loopback socket. The test ensures that an unexpected second server is always closed to prevent ResourceWarning. The 26-second skew was already successfully calibrated. Avoid mistaking printed fixture `economic_pnl=50` and `edge=3.6797%` for live P&L/signals: the test suite prints these simulated values.

`publish-to-github.bat` no longer fails just because global Git author settings are absent; it sets repository-local author metadata for `Wandersport` when needed, preserving any already-set identity. The initial `git init` from the failed run is harmless. The publishing script pushes only explicitly allowlisted project code, and never data/. Since the GitHub repository is public, inspect staged source before upload and keep private files ignored. Authentication to GitHub is still required and cannot be bypassed.

For Windows host clock correction, use Settings > Time & language > Date & time > Sync now. If necessary run `w32tm /resync` in an elevated terminal. This does not replace venue-clock calibration.

---

## Windows v1.1 operational recovery (2026-10-28)

1. Stop old v1.0 command window with Ctrl+C. Preserve all `data/` files.
2. Extract v1.1 over the same project folder. Run `diagnose-clock.bat` (after `.venv` exists); record offset, RTT and uncertainty.
3. `start.bat` runs offline tests and starts localhost dashboard. If the dashboard port is occupied by an older instance it **fails visibly** instead of silently displaying old data.
4. The updater archives existing v1.0 host-clock `snapshots.jsonl` to `snapshots_v1_0_pre_clock_fix.jsonl` on first new collection. It does NOT alter that archive or delete other `data/` files. New clocks are written to a fresh snapshots file, preventing mixed timestamps in shadow replay.
5. Expect clock log lines (`Venue clock: ...`). A ~+26 second offset corroborates the original -25.93s CLOB event ages. Venue time is then used for market discovery and source-age checks. Values remaining near -26s **after** calibration indicate independent clock-domain/schema disagreement; stop and investigate rather than raising limits.
6. Dashboard shows the current venue-minus-Windows offset, UP/DOWN event ages and decision counts; the old snapshots will retain their original `stale_exchange_book` reason, because their historic timing cannot be retrospectively certified.
7. Run a bounded `stepahead start --cycles 1 --no-dashboard` on a separate stopped instance for integration checks. `stepahead clock` is a quick standalone remote probe.
8. Use `publish-to-github.bat` for the verified existing repository. Verify the GitHub Actions matrix after pushing. No pushing of generated logs or trading secrets.

**Validation:** The 58 offline tests include a synthetic end-to-end 26-second host-offset reproduction, real-stale and future-stamped rejection, shell-free read-only service cycles, and dashboard safety. The current sandbox cannot resolve external Polymarket DNS, so a real venue handshake and 24h live collection cannot be claimed as tested here. The Windows machine must provide that live integration evidence.

---

# Operator runbook

## Prerequisites

- Python >=3.11, no private key, no account login.
- Public Polymarket API reachable and legal to access in your location.
- Enough local storage for activity export; the seller claims hundreds of thousands of fills.
- This code never places an order, signs a payload or accesses an exchange wallet.

## Install and test

```bash
python3 -m venv .venv
source .venv/bin/activate         # PowerShell: .venv\\Scripts\\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
stepahead --help
stepahead doctor
```

`doctor` intentionally fails if public API data is missing or Gamma metadata doesn't prove the current BTC event. That is safer than silently guessing. If it fails, investigate the live response contract before patching.

## One-time wallet research

```bash
stepahead wallet export --max-pages 5
stepahead audit --kind trade
stepahead wallet export --max-pages 0
stepahead audit --kind trade
stepahead wallet analyze

stepahead activity export --max-pages 5
stepahead audit --kind activity
stepahead activity export --max-pages 0
stepahead audit --kind activity
stepahead activity analyze
```

**Warning:** Running `--max-pages 0` can download many hundreds of thousands of records. The exports are crash-resumable and freeze the activity `end` timestamp at the *first* request. The trades endpoint is not a live collector after the original export completes. For subsequent activity, use a **new directory with an explicit UTC start/end**; do not delete or combine partial page cohorts.

## Monitoring current profile

```bash
stepahead monitor once
stepahead monitor watch --polls 12 --interval 300
```

These commands take official `v2/user-stats`, `v2/user-volume`, and `v2/status` snapshots, compute only like-for-like scalar deltas, and write `data/profile_snapshots.jsonl`. `watch` runs locally until its bounded number of polls completes. It does **not** install a service, collect from future.news, or run when your Mac is asleep/offline.

## BTC 5-minute market research

```bash
stepahead scan --quantity 25 --buffer 0.002
stepahead maker-scan --quantity 25
stepahead paper --polls 300 --interval 3 --quantity 25
stepahead replay --file data/paper/snapshots.jsonl
```

`paper_pairs.jsonl` records hypothetical paired fills as research assumptions only; no account wallet, live order, settlement or realized P&L exists. Snapshot replay recomputes quote-based signals without implying queue execution. Maker research explicitly assumes no filled orders or rebates.

## Offline queue-replay data contract

The normalized orders file must be JSONL objects of the form:

```json
{"order_id":"o1","market_slug":"btc-updown-5m-1800000000","outcome":"up","placed_ms":1800000010000,"price":"0.45","quantity":"10","queue_ahead":"250"}
```

The prints file must have historical, chronological taker prints of the **same** market and outcome:

```json
{"market_slug":"btc-updown-5m-1800000000","outcome":"up","taker_side":"SELL","ts_ms":1800000010600,"price":"0.45","size":"40"}
```

Execute:

```bash
stepahead queue-replay --orders data/model_orders.jsonl --prints data/model_prints.jsonl --queue-ahead 250 --latency-ms 100
```

Only taker SELL prints can match a resting hypothetical BUY. The model respects activation latency and consumes assumed queue-ahead depth. No transaction can model-fill more quantity than observed in a single print; simultaneous order priority is a model assumption. This is **not** a calibrated exchange queue simulator; it cannot claim realizable P&L.

## Incident/safety handling

If API schema changes, exporter errors or timestamps don't line up, **halt and inspect**; do not fill missing fields with zeros. The GitHub Actions job is offline unit tests only; it contains no credentials, scheduled trading or production deployment. `data/` and `.env` should remain untracked. Neither WS-X nor WS-Jev is a dependency or writable target.

## Publish an isolated private repository (user-initiated)

The GitHub connection available to this assistant can edit existing repositories but cannot
create a new repository. **Do not publish this code inside WS-X, WS-Jev, Codex, or another
unrelated repository.** To create an independent private repository from the unpacked ZIP,
run these commands on a machine with `git` and GitHub CLI (`gh`) installed:

```bash
# Run from the stepahead-lab/ folder after reading README.md
# `gh auth login` is necessary only if not already authenticated.
gh auth status
git init -b main
git add .
git commit -m 'Initial StepAhead paper-only research lab'
gh repo create Wandersport/stepahead-lab --private --source=. --remote=origin --push
```

If `Wandersport/stepahead-lab` already exists, **stop** rather than overwrite it. Keep the
repository private, and keep generated `data/` files outside Git. No bot is running on GitHub
just because source was uploaded. The currently defined test-only GitHub Actions workflow
performs offline checks on commits and does not place trades or monitor wallets.

## Monitoring schedule limitations

`stepahead monitor watch --polls 24 --interval 300` runs 24 read-only checks over roughly two
hours *only while the process stays open*. `stepahead monitor once` performs one check and
writes to a gitignored local JSONL history. Neither command registers a macOS LaunchAgent,
cron schedule, hosted service, or ChatGPT task. Set one up separately if recurring checks
are desired; verify provider rate limits and do not assume a program that has exited remains
running.

## 1.0 service and dashboard

Extract archive and use `start.command` (macOS), `start.bat` (Windows), or `sh start.sh` (Linux). The launcher sets up a project-local `.venv`, verifies unit tests, and calls `stepahead start` (foreground, until Ctrl+C). The read-only dashboard is served on **127.0.0.1:8765** and is not exposed on the LAN. No account credentials, OAuth or exchange auth is required.

Use `stepahead start --cycles 1 --no-dashboard` as a bounded integration smoke check. This performs public GETs only; errors are logged and reported. If public DNS or Polymarket is unavailable, no fills or trades will be invented, and the dashboard may remain empty.

To look at the account and paper data without any network connection, run `stepahead overview` or `stepahead dashboard`. This won't generate synthetic trading history: an empty dashboard represents genuinely missing observations.

## Counterfactual settlement (new in v1.0)

The service periodically checks Gamma for binary closed-market outcomes. Run `stepahead settle` separately if desired. This writes `data/paper/settlements.json` and verifies the same `conditionId`, exact outcome names and price vector `[1,0]` or `[0,1]` from a CLOSED market. Unavailable or ambiguous outcomes stay unresolved. The report is **counterfactual on both legs hypothetically filling**; it includes a stress reserve, does not include imaginary rebates, and is never realized P&L. The settlement cache stores a SHA256 of each input decision and refuses to reuse outcomes if assumptions are edited.

## Local watch versus hosted service

The `start` command is an in-process monitoring loop (not a macOS LaunchAgent, Windows service, or hosted GitHub Action). If the computer sleeps or you close the terminal, it stops. `start --cycles 0` is intentionally infinite until interrupted. It does not create a scheduled trading workflow and its safety boundary cannot be lifted using any flag.

## Sequential shadow replay (v1.0)

`stepahead shadow-replay --latency-ms 500 --max-slippage-per-share 0.01` replays recorded REST snapshots in chronological order, requires two independent qualifying signal observations and later book observations for **each** leg, and models a stranded one-leg position when a later quoted second-leg order becomes unavailable within the slippage/depth limit. `modelled_stranded_*` is a risk stress scenario, **not** a confirmed fill; absent snapshots are labeled `unobservable`. There are **zero live orders**, and the output cannot establish a queue position or executable P&L.
