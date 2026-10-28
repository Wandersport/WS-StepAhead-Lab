"""Safe CLI. All commands are public-data reads or local file writes."""
from __future__ import annotations
import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .config import DATA_API, TARGET_WALLET
from .http import PublicHTTP
from .markets import discover_current, fetch_pair
from .paper import observe, replay
from .strategy import evaluate_pair
from .maker import assess_maker_candidate
from .wallet import analyze_wallet, export_wallet
from .activity import export_activity, profile_activity
from .monitor import snapshot as monitor_snapshot, watch as monitor_watch
from .quality import audit_pages
from .queue_replay import run_queue_replay
from .settlement import settle_file
from .dashboard import serve, summarize_data
from .runner import run as run_service
from .shadow import replay_delayed

D = Decimal


def _positive_dec(s: str) -> D:
    try:
        v = D(s)
        if v.is_finite() and v > 0:
            return v
    except InvalidOperation:
        pass
    raise argparse.ArgumentTypeError("Expected finite positive decimal")


def _nonnegative_dec(s: str) -> D:
    try:
        v = D(s)
        if v.is_finite() and v >= 0:
            return v
    except InvalidOperation:
        pass
    raise argparse.ArgumentTypeError("Expected finite nonnegative decimal")


def _evaluate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--quantity", type=_positive_dec, default=D("25"), help="hypothetical shares per outcome")
    parser.add_argument("--min-edge-pct", type=_nonnegative_dec, default=D("0.2"),
                        help="minimum worst-case payout edge after costs, percent")
    parser.add_argument("--buffer", type=_nonnegative_dec, default=D("0.002"),
                        help="execution-risk reserve in USDC per share per leg")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stepahead", description="StepAhead reverse-engineering lab: PAPER ONLY")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check public API compatibility and current market")
    w = sub.add_parser("wallet", help="public target wallet export and report")
    wsub = w.add_subparsers(dest="wallet_command", required=True)
    wexp = wsub.add_parser("export", help="resume cursor-paginated public trade export")
    wexp.add_argument("--wallet", default=TARGET_WALLET)
    wexp.add_argument("--out", type=Path, default=Path("data/wallet"))
    wexp.add_argument("--max-pages", type=int, default=5,
                      help="page budget this run, 0 = run until exhausted")
    wexp.add_argument("--page-size", type=int, default=1000)
    wrpt = wsub.add_parser("analyze", help="approximate behavior metrics (not P&L)")
    wrpt.add_argument("--data", type=Path, default=Path("data/wallet"))
    wrpt.add_argument("--out", type=Path, default=Path("data/wallet_summary.json"))
    a = sub.add_parser("activity", help="frozen UTC window account activity: splits/redeems/rebates")
    asub = a.add_subparsers(dest="activity_command", required=True)
    aexp = asub.add_parser("export", help="resumable read-only activity export")
    aexp.add_argument("--wallet", default=TARGET_WALLET)
    aexp.add_argument("--out", type=Path, default=Path("data/activity"))
    aexp.add_argument("--start", type=int, default=1, help="inclusive UTC epoch seconds")
    aexp.add_argument("--end", type=int, default=None, help="freeze at this UTC epoch")
    aexp.add_argument("--max-pages", type=int, default=5, help="0 = all pages")
    aexp.add_argument("--page-size", type=int, default=1000)
    arpt = asub.add_parser("analyze", help="activity types and non-PnL wallet inventory signals")
    arpt.add_argument("--data", type=Path, default=Path("data/activity"))
    arpt.add_argument("--out", type=Path, default=Path("data/activity_summary.json"))
    audit = sub.add_parser("audit", help="verify local cursor page continuity/completeness")
    audit.add_argument("--kind", choices=("trade", "activity"), default="trade")
    audit.add_argument("--data", type=Path, default=None)
    clock_diag = sub.add_parser("clock", help="verify CLOB venue time, Windows offset, and safety uncertainty")
    clock_diag.add_argument("--out", type=Path, default=Path("data/clock_calibrations.jsonl"))
    monitor = sub.add_parser("monitor", help="profile and volume watcher (read-only)")
    msub = monitor.add_subparsers(dest="monitor_command", required=True)
    monce = msub.add_parser("once", help="write one profile/volume snapshot and delta")
    mwatch = msub.add_parser("watch", help="bounded in-process watch, not a scheduled job")
    for m in (monce, mwatch):
        m.add_argument("--wallet", default=TARGET_WALLET)
        m.add_argument("--out", type=Path, default=Path("data/profile_snapshots.jsonl"))
    mwatch.add_argument("--polls", type=int, default=24)
    mwatch.add_argument("--interval", type=float, default=300)
    q = sub.add_parser("queue-replay", help="offline modelled passive fills from normalized public prints")
    q.add_argument("--orders", type=Path, required=True)
    q.add_argument("--prints", type=Path, required=True)
    q.add_argument("--latency-ms", type=int, default=100)
    q.add_argument("--queue-ahead", type=_nonnegative_dec, default=D("250"))
    scan = sub.add_parser("scan", help="single public-book paired opportunity check")
    _evaluate_args(scan)
    maker_scan = sub.add_parser("maker-scan", help="passive quote theory, no fills or rebate assumptions")
    maker_scan.add_argument("--quantity", type=_positive_dec, default=D("25"))
    maker_scan.add_argument("--tick", type=_positive_dec, default=D("0.01"))
    maker_scan.add_argument("--buffer", type=_nonnegative_dec, default=D("0.002"))
    paper = sub.add_parser("paper", help="observe public quotes and log hypothetical pairs")
    _evaluate_args(paper)
    paper.add_argument("--polls", type=int, default=30)
    paper.add_argument("--interval", type=float, default=3.0)
    paper.add_argument("--confirmations", type=int, default=2)
    paper.add_argument("--max-total-paper-cost", type=_positive_dec, default=D("1000"))
    paper.add_argument("--max-market-paper-cost", type=_positive_dec, default=D("100"))
    paper.add_argument("--out", type=Path, default=Path("data/paper"))
    rep = sub.add_parser("replay", help="replay recorded REST snapshots (NOT historical fills)")
    _evaluate_args(rep)
    rep.add_argument("--file", type=Path, default=Path("data/paper/snapshots.jsonl"))
    shadow = sub.add_parser("shadow-replay", help="OFFLINE delayed sequential two-leg execution risk model")
    _evaluate_args(shadow)
    shadow.add_argument("--file", type=Path, default=Path("data/paper/snapshots.jsonl"))
    shadow.add_argument("--latency-ms", type=int, default=500)
    shadow.add_argument("--max-slippage-per-share", type=_nonnegative_dec, default=D("0.01"))
    shadow.add_argument("--max-market-cost", type=_positive_dec, default=D("150"))
    shadow.add_argument("--max-total-cost", type=_positive_dec, default=D("2500"))
    settled = sub.add_parser("settle", help="verify winners and counterfactually settle paper pairs")
    settled.add_argument("--entries", type=Path, default=Path("data/paper/paper_pairs.jsonl"))
    settled.add_argument("--out", type=Path, default=Path("data/paper/settlements.json"))
    settled.add_argument("--max-markets", type=int, default=1000)
    dash = sub.add_parser("dashboard", help="read-only LOCAL browser dashboard")
    dash.add_argument("--data", type=Path, default=Path("data"))
    dash.add_argument("--port", type=int, default=8765)
    dash.add_argument("--no-browser", action="store_true")
    overview = sub.add_parser("overview", help="local data summary, works offline")
    overview.add_argument("--data", type=Path, default=Path("data"))
    service = sub.add_parser("start", help="one-command foreground paper monitor and local dashboard")
    service.add_argument("--data", type=Path, default=Path("data"))
    service.add_argument("--cycles", type=int, default=0, help="0 = run until Ctrl+C")
    service.add_argument("--polls-per-cycle", type=int, default=15)
    service.add_argument("--interval", type=float, default=4.)
    service.add_argument("--port", type=int, default=8765)
    service.add_argument("--no-dashboard", action="store_true")
    service.add_argument("--wallet", default=TARGET_WALLET)
    _evaluate_args(service)
    return p


def _print(result: dict):
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "wallet" and args.wallet_command == "analyze":
            result = analyze_wallet(args.data)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2), encoding="utf8")
        elif args.command == "activity" and args.activity_command == "analyze":
            result = profile_activity(args.data)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2), encoding="utf8")
        elif args.command == "queue-replay":
            result = run_queue_replay(args.orders, args.prints,
                                      latency_ms=args.latency_ms, queue_ahead=args.queue_ahead)
        elif args.command == "audit":
            directory = args.data or Path("data/activity" if args.kind == "activity" else "data/wallet")
            result = audit_pages(directory, args.kind)
        elif args.command == "shadow-replay":
            result = replay_delayed(args.file, quantity=args.quantity,
                                    min_edge_pct=args.min_edge_pct, buffer=args.buffer,
                                    latency_ms=args.latency_ms,
                                    max_slippage_per_share=args.max_slippage_per_share,
                                    max_market_cost=args.max_market_cost,
                                    max_total_cost=args.max_total_cost)
        elif args.command == "replay":
            result = replay(args.file, args.quantity, args.min_edge_pct, args.buffer)
        elif args.command == "dashboard":
            serve(args.data, args.port, not args.no_browser)
            return 0
        elif args.command == "overview":
            result = summarize_data(args.data)
        elif args.command == "start":
            result = run_service(root=args.data, cycles=args.cycles,
                                 polls_per_cycle=args.polls_per_cycle, interval=args.interval,
                                 quantity=args.quantity, min_edge_pct=args.min_edge_pct,
                                 buffer=args.buffer, port=args.port,
                                 dashboard=not args.no_dashboard, wallet=args.wallet)
        else:
            client = PublicHTTP()
            if args.command == "settle":
                result = settle_file(client, args.entries, args.out, max_markets=args.max_markets)
            elif args.command == "monitor":
                if args.monitor_command == "once":
                    result = monitor_snapshot(client, args.out, args.wallet)
                else:
                    result = monitor_watch(client, args.out, args.wallet, args.polls, args.interval)
            elif args.command == "activity":
                result = export_activity(client, args.out, args.wallet, start=args.start,
                                         end=args.end, max_pages=args.max_pages,
                                         page_size=args.page_size)
            elif args.command == "clock":
                result = client.ensure_clock()
                from .paper import _append_jsonl
                _append_jsonl(args.out, result)
            elif args.command == "doctor":
                result = {"mode": "PUBLIC_READ_ONLY", "trading_enabled": False,
                          "venue_clock": client.ensure_clock(),
                          "data_api_status": client.get(f"{DATA_API}/v2/status")}
                market = discover_current(client)
                up, down = fetch_pair(client, market)
                result.update({"market": market.slug, "up_token": market.up_token,
                               "down_token": market.down_token,
                               "up_asks": len(up.asks), "down_asks": len(down.asks)})
            elif args.command == "wallet":
                result = export_wallet(client, args.out, args.wallet,
                                       max_pages=args.max_pages, page_size=args.page_size)
            elif args.command == "scan":
                market = discover_current(client)
                up, down = fetch_pair(client, market)
                quote = evaluate_pair(market, up, down, args.quantity,
                                      args.min_edge_pct, args.buffer)
                result = quote.to_dict()
                result["actually_executed_orders"] = 0
            elif args.command == "maker-scan":
                market = discover_current(client)
                up, down = fetch_pair(client, market)
                result = assess_maker_candidate(market, up, down, args.quantity,
                                                tick=args.tick,
                                                buffer_per_leg=args.buffer).to_dict()
                result["actually_executed_orders"] = 0
            elif args.command == "paper":
                result = observe(client, polls=args.polls, interval=args.interval,
                                 quantity=args.quantity, min_edge_pct=args.min_edge_pct,
                                 buffer=args.buffer, confirmations=args.confirmations,
                                 directory=args.out,
                                 max_total_paper_cost=args.max_total_paper_cost,
                                 max_market_paper_cost=args.max_market_paper_cost)
            else:
                raise ValueError("Unexpected CLI command")
        _print(result)
        return 0
    except KeyboardInterrupt:
        print("Stopped. No live orders were ever possible.", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
