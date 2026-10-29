"""Public observation and strictly hypothetical paired fills; zero order submission."""
from __future__ import annotations
import json
import os
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Iterator
from .http import PublicHTTP
from .markets import Book, Level, Market, discover_current, fetch_pair
from .strategy import Quote, evaluate_pair
from .maker import MakerCandidate, assess_maker_candidate

D = Decimal


def _serialize(value):
    if isinstance(value, D):
        return str(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _append_jsonl(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf8") as f:
        f.write(json.dumps(_serialize(data), separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _load_decisions(path: Path) -> tuple[set[str], D]:
    slugs: set[str] = set()
    cost = D(0)
    if path.exists():
        for item in _read_jsonl(path):
            if item.get("kind") != "paper_pair":
                continue
            slugs.add(item["market_slug"])
            cost += D(str(item["all_in_cost"]))
    return slugs, cost


def _read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Corrupt JSONL {path}:{line_no}") from exc


def snapshot_record(market: Market, up: Book, down: Book, quote: Quote,
                    maker: MakerCandidate | None = None,
                    clock: dict | None = None) -> dict:
    return {"kind": "snapshot", "market": asdict(market), "up": asdict(up),
            "down": asdict(down), "quote": quote.to_dict(),
            "clock": clock,
            "source_age_seconds": {
                "up": round(up.observed_at - up.source_ts, 3) if up.source_ts is not None else None,
                "down": round(down.observed_at - down.source_ts, 3) if down.source_ts is not None else None,
            },
            "maker_candidate": maker.to_dict() if maker is not None else None}


def decode_snapshot(record: dict) -> tuple[Market, Book, Book]:
    m = record["market"]
    market = Market(**{**m, "start": float(m["start"]), "end": float(m["end"])})

    def book(data: dict) -> Book:
        return Book(str(data["token_id"]),
                    tuple(Level(D(p["price"]), D(p["size"])) for p in data["bids"]),
                    tuple(Level(D(p["price"]), D(p["size"])) for p in data["asks"]),
                    float(data["request_started"]), float(data["observed_at"]),
                    float(data["source_ts"]) if data.get("source_ts") is not None else None)
    return market, book(record["up"]), book(record["down"])


def replay(path: Path, quantity: D, min_edge_pct: D, buffer: D) -> dict:
    n = 0
    signals = 0
    maker_candidates = 0
    valid_sum = D(0)
    reasons: dict[str, int] = {}
    for row in _read_jsonl(path):
        if row.get("kind") != "snapshot":
            continue
        market, up, down = decode_snapshot(row)
        # Deterministic replay: evaluate at the recorded observation time, never wall clock.
        quote = evaluate_pair(market, up, down, quantity, min_edge_pct,
                              buffer, now=max(up.observed_at, down.observed_at))
        passive = assess_maker_candidate(market, up, down, quantity,
                                         buffer_per_leg=buffer,
                                         now=max(up.observed_at, down.observed_at))
        if passive.candidate:
            maker_candidates += 1
        n += 1
        reasons[quote.reason] = reasons.get(quote.reason, 0) + 1
        if quote.valid:
            signals += 1
            valid_sum += quote.minimum_profit or D(0)
    return {"snapshots": n, "paper_signals": signals,
            "positive_passive_maker_quote_candidates_NO_FILLS": maker_candidates,
            "sum_theoretical_minimum_profit_if_all_quotes_filled_usdc": str(valid_sum),
            "reasons": reasons, "replay_is_not_a_fill_backtest": True,
            "execution_model": "Non-atomic REST quote replay with fee-in-shares and explicit cost buffer"}


def _separate_legacy_clockless_snapshots(path: Path) -> Path | None:
    """Preserve v1.0 host-clock snapshots without mixing time bases in replay.

    The old data recorded Windows wall time but venue source times. A mixed
    same-market shadow replay could manufacture a nonsensical 26-second jump.
    Only an explicit old format is archived; an ambiguous/corrupt last record
    halts collection rather than erasing anything.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return None
    with path.open('rb') as f:
        size=path.stat().st_size
        f.seek(max(0,size-65536))
        block=f.read()
    lines=block.splitlines()
    if not lines or (not block.endswith(b'\n')):
        raise ValueError('Snapshot JSONL has incomplete final record; preserve and repair manually')
    try:
        last=json.loads(lines[-1])
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('Snapshot JSONL tail is corrupt; preserve and repair manually') from exc
    if last.get('kind') != 'snapshot':
        raise ValueError('Unknown snapshot file format; refusing to mix time bases')
    if 'clock' in last:
        return None
    archived=path.with_name('snapshots_v1_0_pre_clock_fix.jsonl')
    if archived.exists():
        raise ValueError(f'Legacy snapshot archive already exists: {archived}; manual reconciliation required')
    path.rename(archived)
    return archived


def observe(client: PublicHTTP, *, polls: int, interval: float, quantity: D,
            min_edge_pct: D, buffer: D, directory: Path,
            confirmations: int = 2, max_total_paper_cost: D = D("1000"),
            max_market_paper_cost: D = D("100"),
            verbose: bool = True, stop_at_monotonic: float | None = None) -> dict:
    if interval < 0.5:
        raise ValueError("interval must be >= 0.5 seconds")
    if confirmations < 2:
        raise ValueError("confirmations must be >= 2 to reduce snapshot artifacts")
    if polls < 1:
        raise ValueError("polls must be positive")
    if max_total_paper_cost <= 0 or max_market_paper_cost <= 0:
        raise ValueError("Paper budgets must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    snaps = directory / "snapshots.jsonl"
    archived = _separate_legacy_clockless_snapshots(snaps)
    if archived is not None and verbose:
        print(f"Archived legacy Windows-clock snapshots: {archived}",flush=True)
    decisions = directory / "paper_pairs.jsonl"
    already, spent = _load_decisions(decisions)
    previous_slug = ""
    consecutive = 0
    observed = 0
    recorded = 0
    errors = 0
    for iteration in range(polls):
        if stop_at_monotonic is not None and time.monotonic() >= stop_at_monotonic:
            break
        try:
            market = discover_current(client)
            up, down = fetch_pair(client, market)
            quote = evaluate_pair(market, up, down, quantity, min_edge_pct, buffer)
            passive = assess_maker_candidate(market, up, down, quantity,
                                             buffer_per_leg=buffer)
            clock = getattr(client, "clock", None)
            clock_meta = clock.diagnostic() if clock is not None else None
            if stop_at_monotonic is not None and time.monotonic() >= stop_at_monotonic:
                break
            _append_jsonl(snaps, snapshot_record(market, up, down, quote, passive, clock_meta))
            observed += 1
            if quote.valid:
                consecutive = consecutive + 1 if market.slug == previous_slug else 1
            else:
                consecutive = 0
            previous_slug = market.slug
            simulated = False
            if quote.valid and consecutive >= confirmations and market.slug not in already:
                assert quote.all_in_cost is not None
                if quote.all_in_cost <= max_market_paper_cost and spent + quote.all_in_cost <= max_total_paper_cost:
                    # This event does NOT mean real orders would have filled.
                    _append_jsonl(decisions, {"kind": "paper_pair", "market_slug": market.slug,
                        "condition_id": market.condition_id, "quote_observed_at": quote.timestamp,
                        "all_in_cost": str(quote.all_in_cost), "quantity": str(quantity),
                        "up_win_profit": str(quote.up_win_profit),
                        "down_win_profit": str(quote.down_win_profit),
                        "theoretical_worst_profit": str(quote.minimum_profit),
                        "assumed_executed": False, "assumed_both_filled_for_counterfactual": True,
                        "actually_executed": False,
                        "settled": False, "simulated_only": True,
                        "warning": "Two book reads are non-atomic; this is not proven executable alpha"})
                    already.add(market.slug)
                    spent += quote.all_in_cost
                    recorded += 1
                    simulated = True
            if verbose:
                v = f"{quote.minimum_edge_pct:.4f}%" if quote.minimum_edge_pct is not None else "n/a"
                print(f"[{iteration + 1}/{polls}] {market.slug} edge={v} "
                      f"decision={quote.reason} paper_logged={simulated}", flush=True)
        except (ValueError, OSError, RuntimeError) as exc:
            errors += 1
            consecutive = 0
            previous_slug = ""
            if verbose:
                print(f"[{iteration + 1}/{polls}] public observation failed: {exc}", flush=True)
        if iteration + 1 < polls:
            delay = interval if stop_at_monotonic is None else min(
                interval, max(0., stop_at_monotonic - time.monotonic()))
            if delay > 0:
                time.sleep(delay)
    return {"mode": "PAPER_ONLY", "snapshots_recorded": observed,
            "new_hypothetical_pairs": recorded, "rejected_or_network_errors": errors,
            "cumulative_hypothetical_reserved_cost_usdc": str(spent),
            "snapshots_file": str(snaps), "paper_pairs_file": str(decisions),
            "actually_executed_orders": 0,
            "stop_reached": stop_at_monotonic is not None and time.monotonic() >= stop_at_monotonic}
