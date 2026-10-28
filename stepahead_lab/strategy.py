"""Execution-aware *hypothetical* paired-outcome taker test.

This is NOT guaranteed arbitrage: two public book requests are non-atomic and
fills cannot be inferred from quotes. Execution labels are always simulated.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from .config import CRYPTO_FEE_RATE
from .markets import Book, Level, Market

D = Decimal


@dataclass(frozen=True)
class Sweep:
    requested: D
    filled: D
    paid_usdc: D
    buy_fee_usdc_equivalent: D
    fee_shares: D
    net_shares: D
    vwap: D
    levels_used: int


def sweep_buy(asks: tuple[Level, ...], quantity: D, fee_rate: D) -> Sweep | None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    remaining = quantity
    cash = D(0)
    fee_cash = D(0)
    fee_shares = D(0)
    used = 0
    for level in sorted(asks, key=lambda x: x.price):
        if remaining <= 0:
            break
        n = min(remaining, level.size)
        if n <= 0:
            continue
        cash += n * level.price
        # Polymarket crypto taker fee: C*rate*p*(1-p) in USDC equivalent.
        # Buy-side taker fees are DEBITED IN OUTCOME SHARES, not cash.
        # Fee shares = fee USDC equivalent / execution price.
        fee = n * fee_rate * level.price * (D(1) - level.price)
        fee_cash += fee
        fee_shares += fee / level.price
        remaining -= n
        used += 1
    if remaining > D("0.00000001"):
        return None
    return Sweep(quantity, quantity, cash, fee_cash, fee_shares,
                 quantity - fee_shares, cash / quantity, used)


@dataclass(frozen=True)
class Quote:
    market_slug: str
    condition_id: str
    timestamp: float
    quantity: D
    up: Sweep | None
    down: Sweep | None
    all_in_cost: D | None
    worst_case_payout: D | None
    up_win_profit: D | None
    down_win_profit: D | None
    minimum_profit: D | None
    minimum_edge_pct: D | None
    valid: bool
    reason: str
    hypothetical: bool = True

    def to_dict(self) -> dict[str, Any]:
        def cast(value: Any) -> Any:
            if isinstance(value, D):
                return str(value)
            if isinstance(value, dict):
                return {k: cast(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [cast(v) for v in value]
            return value
        return cast(asdict(self))


def evaluate_pair(market: Market, up_book: Book, down_book: Book,
                  quantity: D = D("25"), min_edge_pct: D = D("0.2"),
                  buffer_per_share_per_leg: D = D("0.002"),
                  max_book_age_s: float = 4., max_snapshot_skew_s: float = 1.5,
                  expiry_guard_s: float = 15., now: float | None = None) -> Quote:
    if quantity <= 0 or quantity > D("100000"):
        raise ValueError("Invalid test quantity")
    if buffer_per_share_per_leg < 0 or min_edge_pct < 0:
        raise ValueError("Negative cost buffer/edge")
    now = max(up_book.observed_at, down_book.observed_at) if now is None else now

    def rejected(reason: str) -> Quote:
        return Quote(market.slug, market.condition_id, now, quantity, None, None,
                     None, None, None, None, None, None, False, reason)

    if up_book.token_id != market.up_token or down_book.token_id != market.down_token:
        return rejected("token_mapping_mismatch")
    if not (market.start <= now < market.end - expiry_guard_s):
        return rejected("outside_safe_market_window")
    if abs(up_book.observed_at - down_book.observed_at) > max_snapshot_skew_s:
        return rejected("snapshot_skew")
    for book in (up_book, down_book):
        if (book.observed_at - book.request_started) > max_book_age_s:
            return rejected("slow_http_response")
        if book.source_ts is not None and abs(now - book.source_ts) > max_book_age_s:
            return rejected("stale_exchange_book")
    if not up_book.asks or not down_book.asks:
        return rejected("empty_book")

    fee_rate = CRYPTO_FEE_RATE if market.fees_enabled else D(0)
    up = sweep_buy(up_book.asks, quantity, fee_rate)
    down = sweep_buy(down_book.asks, quantity, fee_rate)
    if up is None or down is None:
        return rejected("insufficient_visible_ask_depth")

    # Conservative hypothetical reserve against sequential execution risk.
    reserve = quantity * buffer_per_share_per_leg * 2
    total_cost = up.paid_usdc + down.paid_usdc + reserve
    up_profit = up.net_shares - total_cost
    down_profit = down.net_shares - total_cost
    worst = min(up.net_shares, down.net_shares)
    minimum_profit = min(up_profit, down_profit)
    edge_pct = (minimum_profit / total_cost) * 100 if total_cost > 0 else D(-100)
    valid = edge_pct >= min_edge_pct and minimum_profit > 0
    return Quote(market.slug, market.condition_id, now, quantity, up, down,
                 total_cost, worst, up_profit, down_profit, minimum_profit,
                 edge_pct, valid, "positive_conservative_edge" if valid else "no_edge_after_costs")
