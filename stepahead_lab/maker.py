"""Passive order-placement HYPOTHESES, with no fill inference or execution.

StepAhead may benefit from maker rebates, but public wallet fills do not disclose
its unfilled/cancelled orders, queue state, or actual fee-rebate allocation.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Any
from .markets import Book, Market

D = Decimal


@dataclass(frozen=True)
class MakerCandidate:
    market_slug: str
    quantity: D
    up_post_only_bid: D | None
    down_post_only_bid: D | None
    both_sides_hypothetical_cost: D | None
    both_sides_fill_gross_payout: D | None
    both_sides_fill_gross_profit: D | None
    worst_only_one_leg_loss: D | None
    fees_if_genuinely_maker: D
    maker_rebates_assumed: D
    candidate: bool
    reason: str
    fills_observed: int = 0
    tradable_without_execution_evidence: bool = False

    def to_dict(self) -> dict[str, Any]:
        def cast(value):
            if isinstance(value, D):
                return str(value)
            if isinstance(value, dict):
                return {k: cast(v) for k, v in value.items()}
            return value
        return cast(asdict(self))


def passive_price(book: Book, tick: D = D("0.01")) -> D | None:
    if not book.bids or not book.asks:
        return None
    highest_bid = max(x.price for x in book.bids)
    lowest_ask = min(x.price for x in book.asks)
    if highest_bid >= lowest_ask:
        return None
    # Improve bid only when doing so leaves at least one tick to the ask.
    price = min(highest_bid + tick, lowest_ask - tick)
    if price < highest_bid:
        price = highest_bid
    if not (D(0) < price < lowest_ask < D(1)):
        return None
    return price


def assess_maker_candidate(market: Market, up: Book, down: Book,
                           quantity: D, tick: D = D("0.01"),
                           buffer_per_leg: D = D("0.002"), now: float | None = None,
                           max_skew_s: float = 1.5, max_age_s: float = 4.,
                           expiry_guard_s: float = 15.) -> MakerCandidate:
    if quantity <= 0 or tick <= 0 or buffer_per_leg < 0:
        raise ValueError("Invalid passive quote configuration")
    def no(reason):
        return MakerCandidate(market.slug, quantity, None, None, None, None,
                              None, None, D(0), D(0), False, reason)
    now = max(up.observed_at, down.observed_at) if now is None else now
    if up.token_id != market.up_token or down.token_id != market.down_token:
        return no("token_mapping_mismatch")
    if not market.start <= now < market.end - expiry_guard_s:
        return no("outside_safe_market_window")
    if abs(up.observed_at - down.observed_at) > max_skew_s:
        return no("snapshot_skew")
    for b in (up, down):
        if b.observed_at - b.request_started > max_age_s or \
                (b.source_ts is not None and abs(now - b.source_ts) > max_age_s):
            return no("stale_book")
    pu, pd = passive_price(up, tick), passive_price(down, tick)
    if pu is None or pd is None:
        return no("insufficient_or_crossed_book")
    cash = (pu + pd) * quantity
    profit = quantity - cash - 2 * buffer_per_leg * quantity
    # If ONLY one BUY maker quote gets hit, in worst case the other outcome wins.
    one_leg_loss = -max(pu, pd) * quantity
    return MakerCandidate(market.slug, quantity, pu, pd, cash, quantity, profit,
                          one_leg_loss, D(0), D(0), profit > 0,
                          "theoretical_positive_if_both_fill" if profit > 0 else
                          "no_two_leg_edge_after_buffer")
