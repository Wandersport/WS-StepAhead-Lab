"""Offline, print-driven *modeled* fill simulation for passive BUY orders.

Requires externally provided normalized, timestamped orders and taker prints.
Market and outcome are strict keys. No IO to a venue, no orders submitted.
A trade-through is only a counterfactual upper-bound signal: public prints
cannot prove which resting order was ahead or what would have happened had our
quote been present. Reports MUST retain modelled labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

D = Decimal


def _d(value) -> D:
    try:
        x = D(str(value))
        if not x.is_finite():
            raise ValueError("Nonfinite decimal")
        return x
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid decimal input") from exc


@dataclass
class RestingBuy:
    order_id: str
    market_slug: str
    outcome: str
    placed_ms: int
    price: D
    quantity: D
    queue_ahead: D
    filled: D = D(0)
    first_fill_ms: int | None = None


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise ValueError(f"File not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def model_passive_fills(orders: list[dict], prints: list[dict], *, latency_ms: int = 100,
                        queue_ahead: D = D("250")) -> dict:
    if latency_ms < 0 or queue_ahead < 0:
        raise ValueError("Invalid lag or queue setting")
    resting: dict[str, RestingBuy] = {}
    for raw in orders:
        key = str(raw["order_id"])
        if not key or key in resting:
            raise ValueError("Duplicate/empty order_id")
        outcome = str(raw["outcome"]).lower()
        if outcome not in {"up", "down"}:
            raise ValueError("Invalid outcome")
        px, qty = _d(raw["price"]), _d(raw["quantity"])
        if not (D(0) < px < D(1) and qty > 0):
            raise ValueError("Invalid modelled resting quote")
        order_queue = _d(raw.get("queue_ahead", queue_ahead))
        if order_queue < 0:
            raise ValueError("Invalid queue ahead")
        resting[key] = RestingBuy(key, str(raw["market_slug"]), outcome,
                                 int(raw["placed_ms"]) + latency_ms,
                                 px, qty, order_queue)
    prior_ms = None
    valid_prints = 0
    for p in sorted(prints, key=lambda p: int(p["ts_ms"])):
        stamp = int(p["ts_ms"])
        if prior_ms is not None and stamp < prior_ms:
            raise ValueError("Unsorted tape")
        prior_ms = stamp
        if str(p.get("taker_side", "")).upper() != "SELL":
            continue
        px, qty = _d(p["price"]), _d(p["size"])
        if not (D(0) < px < D(1) and qty > 0):
            continue
        valid_prints += 1
        group = str(p.get("market_slug", ""))
        outcome = str(p.get("outcome", "")).lower()
        available_from_print = qty
        # No single taker print can model-fill more shares than it traded.
        # FIFO here is a configurable assumption, not exchange queue truth.
        for o in sorted(resting.values(), key=lambda o: (o.placed_ms, o.order_id)):
            if available_from_print <= 0:
                break
            if o.market_slug != group or o.outcome != outcome or o.placed_ms > stamp or o.filled >= o.quantity:
                continue
            if px > o.price:
                continue
            # Same-price taker prints must clear modelled queue before us.
            # A trade-through can indicate queue cleared, but never proves it.
            available = available_from_print
            if o.queue_ahead > 0:
                consumed = min(available, o.queue_ahead)
                o.queue_ahead -= consumed
                available -= consumed
                available_from_print -= consumed
            if available > 0:
                fill = min(available, o.quantity - o.filled)
                o.filled += fill
                available_from_print -= fill
                if o.first_fill_ms is None:
                    o.first_fill_ms = stamp
    filled = []
    for o in resting.values():
        filled.append({"order_id": o.order_id, "market_slug": o.market_slug,
                       "outcome": o.outcome, "quote_price": str(o.price),
                       "requested": str(o.quantity), "MODELLED_filled": str(o.filled),
                       "MODELLED_remaining": str(o.quantity - o.filled),
                       "unconsumed_assumed_queue": str(o.queue_ahead),
                       "first_MODELLED_fill_ms": o.first_fill_ms})
    return {"orders": len(resting), "taker_sell_prints": valid_prints,
            "MODELLED_fully_filled_orders": sum(o.filled == o.quantity for o in resting.values()),
            "MODELLED_partially_filled_orders": sum(D(0) < o.filled < o.quantity for o in resting.values()),
            "fill_details": filled, "actually_executed_orders": 0,
            "warning": "Counterfactual print-queue model ONLY. Queue ahead is assumed, "
                       "simultaneous orders compete for the same tape, cancellations and hidden "
                       "liquidity are unobserved. Never classify modeled fills as realized P&L."}


def run_queue_replay(orders_file: Path, prints_file: Path, *, latency_ms: int,
                     queue_ahead: D) -> dict:
    return model_passive_fills(_load_jsonl(orders_file), _load_jsonl(prints_file),
                               latency_ms=latency_ms, queue_ahead=queue_ahead)
