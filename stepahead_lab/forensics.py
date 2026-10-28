"""Read-only BTC 5-minute public-wallet inventory forensics.

NOT a P&L reconciliation. Public activity does not establish fees, queue,
real executions, or actual cashflows. All quoted edges below are pre-fee
purchase-price-times-size proxies, never earned profits.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import re
from .activity import iter_activity, normalize

D = Decimal
SLUG = re.compile(r"btc-updown-5m-(\d{10})\Z")


def _dec(value):
    try:
        d = D(str(value))
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _lot_totals(lots):
    return sum((q for _, _, q, _ in lots), D(0)), sum((q*p for _, _, q, p in lots), D(0))


def _fifo_cost(lots, amount):
    remaining, result = amount, D(0)
    for _, _, quantity, price in sorted(lots):
        fill = min(remaining, quantity)
        result += fill * price
        remaining -= fill
        if remaining <= 0:
            break
    if remaining > 0:
        raise ValueError("Insufficient lots")
    return result


def profile_windows(directory: str | Path, *, top_n: int = 20) -> dict:
    """Produce per-window pre-fee inventory metrics without inventing realized P&L."""
    if not 0 <= top_n <= 100:
        raise ValueError("top_n must be 0..100")
    directory = Path(directory)
    pages = sorted(directory.glob("activity_[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    if not pages:
        raise ValueError("No activity pages; run 'activity export' first")
    first = json.loads(pages[0].read_text(encoding="utf8"))
    last = json.loads(pages[-1].read_text(encoding="utf8"))
    if first["end"] != last["end"]:
        raise ValueError("Frozen activity end differs between pages")
    frozen_end = first["end"]
    complete = last.get("next_cursor") is None
    books = defaultdict(lambda: {"up": [], "down": [], "sells": 0, "bad": 0, "trades": 0})
    invalid = Counter()
    min_ts = max_ts = None
    events = 0
    for idx, raw in enumerate(iter_activity(directory)):
        events += 1
        r = normalize(raw)
        if r["type"] != "TRADE":
            continue
        slug = str(r["slug"] or "")
        if not SLUG.fullmatch(slug):
            continue
        book = books[slug]
        book["trades"] += 1
        try:
            timestamp = int(r["timestamp"])
            if timestamp <= 0:
                raise ValueError("Bad time")
        except (ValueError, TypeError, OverflowError):
            book["bad"] += 1
            invalid["invalid_timestamp"] += 1
            continue
        min_ts = timestamp if min_ts is None else min(min_ts, timestamp)
        max_ts = timestamp if max_ts is None else max(max_ts, timestamp)
        if r["side"] == "SELL":
            book["sells"] += 1
            continue
        quantity, price = _dec(r["size"]), _dec(r["price"])
        side = str(r["outcome"] or "").lower()
        if (r["side"] != "BUY" or side not in ("up", "down")
                or quantity is None or price is None
                or quantity <= 0 or not D(0) < price < D(1)):
            book["bad"] += 1
            invalid["invalid_or_unknown_trade"] += 1
            continue
        book[side].append((timestamp, idx, quantity, price))

    rows, classes = [], Counter()
    paired_total = cash_total = D(0)
    truncated = 0
    for slug, book in sorted(books.items()):
        start = int(SLUG.fullmatch(slug).group(1))
        up, upcash = _lot_totals(book["up"])
        down, downcash = _lot_totals(book["down"])
        qty = min(up, down)
        cost = (_fifo_cost(book["up"], qty) + _fifo_cost(book["down"], qty)) if qty else D(0)
        paired_total += qty
        cash_total += cost
        up_first = min((x[0] for x in book["up"]), default=None)
        down_first = min((x[0] for x in book["down"]), default=None)
        if up and down:
            classes["both_outcomes_bought"] += 1
        elif up or down:
            classes["one_outcome_bought"] += 1
        else:
            classes["no_valid_buys"] += 1
        if book["sells"]:
            classes["with_sells_excluded_from_buy_only_scenarios"] += 1
        if book["bad"]:
            classes["with_invalid_records"] += 1
        boundary = ((not complete and min_ts is not None and start <= min_ts < start+300)
                    or (isinstance(frozen_end, int) and start <= frozen_end < start+300))
        truncated += int(boundary)
        rows.append({
            "slug": slug,
            "market_start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
            "trades": book["trades"], "sell_count": book["sells"], "invalid_records": book["bad"],
            "up_bought_shares": str(up), "down_bought_shares": str(down),
            "up_cost_price_times_size_usdc": str(upcash),
            "down_cost_price_times_size_usdc": str(downcash),
            "up_weighted_entry_price": str(upcash/up) if up else None,
            "down_weighted_entry_price": str(downcash/down) if down else None,
            "matched_contract_pairs": str(qty),
            "matched_fifo_purchase_cost_usdc": str(cost),
            "matched_gross_edge_before_fees_and_rebates_usdc": str(qty-cost),
            "matched_gross_edge_per_pair": str((qty-cost)/qty) if qty else None,
            "unmatched_up_shares": str(up-qty),
            "unmatched_down_shares": str(down-qty),
            "first_up_buy_second_into_window": up_first-start if up_first is not None else None,
            "first_down_buy_second_into_window": down_first-start if down_first is not None else None,
            "first_opposite_side_gap_seconds": abs(up_first-down_first) if up_first is not None and down_first is not None else None,
            "boundary_partial_possible": boundary,
            "buy_only_gross_profit_if_up_wins_usdc": str(up-upcash-downcash) if not book["sells"] else None,
            "buy_only_gross_profit_if_down_wins_usdc": str(down-upcash-downcash) if not book["sells"] else None,
        })
    eligible = [r for r in rows if D(r["matched_contract_pairs"]) > 0
                and not r["boundary_partial_possible"] and not r["sell_count"] and not r["invalid_records"]]
    ordered = sorted(eligible, key=lambda r: D(r["matched_gross_edge_per_pair"]))
    positives = sum(D(r["matched_gross_edge_before_fees_and_rebates_usdc"]) > 0 for r in eligible)
    negatives = sum(D(r["matched_gross_edge_before_fees_and_rebates_usdc"]) < 0 for r in eligible)
    return {
        "method": "Activity BUY inventory, Decimal size*price, per-outcome FIFO pairing, pre-fee and pre-rebate; not profit",
        "coverage": {
            "frozen_end_epoch": frozen_end, "export_complete": complete, "pages": len(pages),
            "events_read": events, "oldest_trade_timestamp": min_ts, "newest_trade_timestamp": max_ts,
            "boundary_partial_windows_count": truncated,
            "warning": "A partial export excludes older markets; edge windows may lack fills."
        },
        "windows": len(rows), "classifications": dict(classes), "invalid_records": dict(invalid),
        "matched_contract_pairs_ALL_CAPTURED_WINDOWS": str(paired_total),
        "matched_purchase_cost_ALL_CAPTURED_WINDOWS_pre_fee_usdc": str(cash_total),
        "matched_gross_edge_ALL_CAPTURED_WINDOWS_pre_fee_usdc": str(paired_total-cash_total),
        "fully_observed_buy_only_eligible_windows": len(eligible),
        "eligible_positive_matched_gross_edge_before_fees": positives,
        "eligible_negative_matched_gross_edge_before_fees": negatives,
        "eligible_zero_matched_gross_edge_before_fees": len(eligible)-positives-negatives,
        "worst_eligible_windows": ordered[:top_n],
        "best_eligible_windows": list(reversed(ordered[-top_n:])) if top_n else [],
        "per_window": rows,
        "cautions": [
            "Both outcomes bought does not establish arbitrage: matched cost plus fees must be under net settlement value.",
            "FIFO pairing is an inventory convention, not proof of simultaneous orders or attainable execution.",
            "Share fees, rebates, redemptions, transfers and queue priority are absent; gross edges are NOT earned profits.",
            "Sell activity and invalid records are excluded from buy-only eligible comparisons.",
            "Gross win-conditional scenarios assume all bought shares are held; they are not realized profit.",
            "Boundary windows and earlier periods can be incomplete; do not use this to reconcile account P&L."
        ]
    }
