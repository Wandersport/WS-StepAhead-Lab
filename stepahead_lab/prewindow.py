"""Optional public-data recorder for upcoming BTC five-minute markets.

Market discovery is restricted to the next N five-minute windows. Records are
observations of PUBLIC order books before the oracle comparison window starts.
No trade is submitted, and no fill or achievable arbitrage is inferred.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import time

from .config import BTC_MARKET_PREFIX, GAMMA_API
from .markets import parse_market, fetch_pair
from .paper import _append_jsonl


def record_upcoming(client, directory: str | Path, *,
                    windows: int = 2, now: float | None = None,
                    stop_at_monotonic: float | None = None) -> dict:
    """Record full pre-window L2 REST snapshots of at most three future markets.

    Missing Gamma listings are recorded separately from actually empty CLOBs.
    This function only returns observed evidence; it does not score fills.
    """
    if not 1 <= windows <= 3:
        raise ValueError("windows must be between 1 and 3")
    root = Path(directory)
    if now is None:
        client.ensure_clock()
        now = client.clock.now()
    slot = int(now // 300) * 300
    saved, missing, skipped_deadline = 0, 0, 0
    for ahead in range(1, windows+1):
        if stop_at_monotonic is not None and time.monotonic() >= stop_at_monotonic:
            skipped_deadline += windows - ahead + 1
            break
        start = slot + ahead * 300
        slug = f"{BTC_MARKET_PREFIX}{start}"
        events = client.get(f"{GAMMA_API}/events", {"slug": slug, "limit": 2})
        if not isinstance(events, list):
            raise ValueError("Unexpected Gamma event response")
        candidate = []
        for event in events:
            if not isinstance(event, dict) or event.get("slug") != slug:
                continue
            for item in event.get("markets") or []:
                if not isinstance(item, dict):
                    continue
                raw = {**item, "slug": item.get("slug") or slug,
                       "eventStartTime": event.get("eventStartTime") or item.get("eventStartTime")}
                market = parse_market(raw)
                if market is not None and market.start == start and now < market.start:
                    candidate.append(market)
        # Never guess a conditionId or outcome-token mapping.
        if len(candidate) != 1:
            _append_jsonl(root / "status.jsonl", {
                "kind": "prewindow_discovery_status", "slug": slug,
                "observed_venue_epoch": now,
                "reason": "not_listed_or_unverifiable" if not candidate else "ambiguous_market",
                "matching_markets": len(candidate)
            })
            missing += 1
            continue
        if stop_at_monotonic is not None and time.monotonic() >= stop_at_monotonic:
            skipped_deadline += 1
            break
        market = candidate[0]
        up, down = fetch_pair(client, market)
        if stop_at_monotonic is not None and time.monotonic() >= stop_at_monotonic:
            skipped_deadline += 1
            break
        # Discovery can finish before the start even if fetching the two
        # REST books crosses the five-minute boundary. Only receipt timestamps
        # from the calibrated venue clock establish pre-window observation.
        observed_epoch = max(up.observed_at, down.observed_at)
        if not observed_epoch < market.start:
            _append_jsonl(root / "status.jsonl", {
                "kind": "prewindow_discovery_status", "slug": slug,
                "observed_venue_epoch": observed_epoch,
                "reason": "books_received_after_window_start",
                "matching_markets": len(candidate),
            })
            missing += 1
            continue
        a, b = (up.asks[0] if up.asks else None,
                down.asks[0] if down.asks else None)
        record = {
            "kind": "prewindow_book_snapshot", "market": asdict(market),
            "venue_epoch_on_discovery": now,
            "book_observed_venue_epoch": observed_epoch,
            "seconds_until_window_start": market.start - observed_epoch,
            "up": asdict(up), "down": asdict(down),
            "up_best_ask": str(a.price) if a else None,
            "down_best_ask": str(b.price) if b else None,
            "up_ask_depth_shares": str(sum((x.size for x in up.asks), start=0)),
            "down_ask_depth_shares": str(sum((x.size for x in down.asks), start=0)),
            "book_response_skew_s": round(abs(up.observed_at-down.observed_at),3),
            "up_exchange_event_age_s": round(up.observed_at-up.source_ts,3)
                if up.source_ts is not None else None,
            "down_exchange_event_age_s": round(down.observed_at-down.source_ts,3)
                if down.source_ts is not None else None,
            "fills_observed": False, "orders_sent": 0, "paper_entry": False,
            "warning": "Upcoming books are public observations, not executed or guaranteed quotes."
        }
        _append_jsonl(root / "snapshots.jsonl", record)
        saved += 1
    return {
        "upcoming_snapshots": saved, "missing_or_ambiguous_markets": missing,
        "deadline_skips": skipped_deadline, "actual_orders": 0,
        "mode": "READ_ONLY_PREWINDOW"
    }
