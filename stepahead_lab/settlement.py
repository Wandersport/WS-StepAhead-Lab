"""Strict oracle-result lookup and settlement of explicitly hypothetical paper entries.

Historical quote observations do not establish executable fills. Settlement
reports remain counterfactual even when the winning outcome is verified.
"""
from __future__ import annotations

import json
import re
import hashlib
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .config import GAMMA_API
from .http import PublicHTTP
from .paper import _read_jsonl
from .wallet import _atomic_json

D = Decimal
SLUG = re.compile(r"btc-updown-5m-\d{10}\Z")


def _array(obj: object) -> list:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def resolved_outcome(event: dict, slug: str, condition_id: str | None = None) -> str | None:
    """Require CLOSED and exact [0,1] binary prices; never guess winners."""
    if not SLUG.fullmatch(slug) or event.get("slug") != slug:
        return None
    choices: set[str] = set()
    for market in event.get("markets") or []:
        if not isinstance(market, dict):
            continue
        if market.get("slug") not in (None, "", slug):
            continue
        if condition_id and str(market.get("conditionId", "")).lower() != condition_id.lower():
            continue
        if market.get("closed") is not True:
            continue
        outcomes = [str(x).strip().lower() for x in _array(market.get("outcomes"))]
        prices = _array(market.get("outcomePrices"))
        if len(outcomes) != 2 or set(outcomes) != {"up", "down"} or len(prices) != 2:
            continue
        try:
            p = [D(str(x)) for x in prices]
        except (InvalidOperation, TypeError, ValueError):
            continue
        if sorted(p) != [D(0), D(1)]:
            continue
        choices.add(outcomes[p.index(D(1))])
    return next(iter(choices)) if len(choices) == 1 else None


def lookup_outcome(client: PublicHTTP, slug: str, condition_id: str | None = None) -> str | None:
    if not SLUG.fullmatch(slug):
        raise ValueError("Bad BTC market slug")
    response = client.get(f"{GAMMA_API}/events", {"slug": slug, "limit": 2})
    if not isinstance(response, list):
        raise ValueError("Unexpected Gamma events payload")
    winners = {winner for row in response if isinstance(row, dict)
               if (winner := resolved_outcome(row, slug, condition_id)) is not None}
    return next(iter(winners)) if len(winners) == 1 else None


def _fingerprint(entry: dict) -> str:
    """Detect changes to all economic assumptions for a settled paper record."""
    keys = ('kind','market_slug','condition_id','actually_executed',
            'all_in_cost','quantity','up_win_profit','down_win_profit')
    canonical = json.dumps({k: entry.get(k) for k in keys}, sort_keys=True,
                           separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf8')).hexdigest()


def calculate_counterfactual(entry: dict, winner: str) -> dict:
    if winner not in {"up", "down"}:
        raise ValueError("winner must be up or down")
    if not entry.get("actually_executed") is False:
        raise ValueError("Paper entry must explicitly show zero real execution")
    pkey = "up_win_profit" if winner == "up" else "down_win_profit"
    try:
        pnl = D(str(entry[pkey]))
        cost = D(str(entry["all_in_cost"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Incomplete counterfactual entry") from exc
    if not pnl.is_finite() or not cost.is_finite() or cost <= 0:
        raise ValueError("Non-finite / invalid counterfactual accounting")
    return {"market_slug": entry["market_slug"], "condition_id": entry.get("condition_id"),
            "source_entry_sha256": _fingerprint(entry),
            "winner": winner, "stress_adjusted_counterfactual_profit_usdc": str(pnl),
            "paper_cost_reservation_usdc": str(cost), "actually_executed_orders": 0,
            "execution_proven": False, "rebates_assumed_usdc": "0",
            "note": "Both fills assumed for counterfactual settlement; not earned profit"}


def settle_file(client: PublicHTTP, decisions: Path, output: Path,
                *, max_markets: int = 1000, timestamp: int | None = None) -> dict:
    if not decisions.is_file():
        return {"state": "no_entries", "settled": 0, "pending": 0, "file": str(output)}
    if max_markets < 1:
        raise ValueError("max_markets must be positive")
    saved = {}
    if output.exists():
        doc = json.loads(output.read_text(encoding="utf8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("settlements"), dict):
            raise ValueError("Corrupt settlement cache")
        saved = doc["settlements"]
    entries: dict[str, dict] = {}
    for e in _read_jsonl(decisions):
        if e.get("kind") not in {"paper_pair", "paper_counterfactual_pair"}:
            continue
        slug = e.get("market_slug")
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise ValueError("Invalid paper entry slug")
        if slug in entries:
            raise ValueError("Duplicate paper entry; stop for review")
        entries[slug] = e
    if set(saved) - set(entries):
        raise ValueError("Settlement cache contains markets absent from input; refusing mixed accounting")
    if len(entries) > max_markets:
        raise ValueError("Too many markets for one reconciliation; raise max_markets deliberately")
    errors: dict[str, str] = {}
    n_new = 0
    for slug, entry in sorted(entries.items()):
        if slug in saved:
            # Detect edited source entries rather than silently reusing a stale settlement.
            if saved[slug].get("source_entry_sha256") != _fingerprint(entry):
                raise ValueError(f"Source paper entry changed for {slug}; manually isolate old cache")
            continue
        try:
            winner = lookup_outcome(client, slug, entry.get("condition_id"))
            if winner is None:
                continue
            saved[slug] = calculate_counterfactual(entry, winner)
            n_new += 1
        except (OSError, RuntimeError, ValueError) as exc:
            errors[slug] = str(exc)[:250]
    total = sum((D(row["stress_adjusted_counterfactual_profit_usdc"]) for row in saved.values()), D(0))
    _atomic_json(output, {"observed_at_utc": datetime.fromtimestamp(
        timestamp if timestamp is not None else time.time(), timezone.utc).isoformat(),
        "settlements": saved, "total_stress_adjusted_COUNTERFACTUAL_profit_usdc": str(total),
        "actual_executed_orders": 0, "source": "Gamma closed markets + local hypothetical entries",
        "warning": "P&L is NOT earned or executable; both-leg fills are assumed."})
    return {"new_settlements": n_new, "settled": len(saved),
            "pending": len(entries) - len(saved), "errors": errors,
            "stress_adjusted_COUNTERFACTUAL_profit_usdc": str(total),
            "file": str(output), "actual_trades": 0}
