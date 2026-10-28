"""Public account-activity acquisition and strictly observational profiling.

v2/activity is the necessary supplement to v2/trades for SPLIT/MERGE/
REDEEM and rebates. Cached pages are immutable snapshots of a *fixed* UTC
window. Resuming a partial scan does not widen its date range.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import re
import time
from typing import Iterator

from .config import DATA_API, TARGET_WALLET
from .http import PublicHTTP
from .wallet import _atomic_json

D = Decimal
_WALLET = re.compile(r"0x[0-9a-fA-F]{40}\Z")


def export_activity(client: PublicHTTP, directory: str | Path,
                    wallet: str = TARGET_WALLET, *, start: int = 1,
                    end: int | None = None, max_pages: int = 5,
                    page_size: int = 1000) -> dict:
    if not _WALLET.fullmatch(wallet):
        raise ValueError("Invalid wallet address")
    if start < 1 or (end is not None and end < start):
        raise ValueError("Invalid UTC date bounds")
    if not 1 <= page_size <= 1000 or max_pages < 0:
        raise ValueError("Invalid pagination options")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    paths = sorted(root.glob("activity_[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    if paths:
        if any(int(p.stem.rsplit("_", 1)[-1]) != i for i, p in enumerate(paths)):
            raise ValueError("Non-contiguous activity pages")
        first = json.loads(paths[0].read_text(encoding="utf8"))
        last = json.loads(paths[-1].read_text(encoding="utf8"))
        for p in (first, last):
            if p["wallet"].lower() != wallet.lower() or p["start"] != start or p["page_size"] != page_size:
                raise ValueError("Activity resume cohort mismatch")
        frozen_end = first["end"]
        if end is not None and frozen_end != end:
            raise ValueError("End timestamp differs from frozen activity window")
        cursor = last["next_cursor"]
        if cursor is None:
            return {"status": "complete", "pages": len(paths), "new_pages": 0,
                    "frozen_end": frozen_end, "directory": str(root)}
        if not isinstance(cursor, str) or not cursor:
            raise ValueError("Bad activity resume cursor")
        seen = {json.loads(p.read_text(encoding="utf8")).get("request_cursor") for p in paths}
        if cursor in seen:
            raise ValueError("Activity pagination cursor cycle")
    else:
        frozen_end = int(time.time()) if end is None else end
        cursor = None
        seen = set()
    count = 0
    while max_pages == 0 or count < max_pages:
        params = {"user": wallet, "limit": page_size, "start": start, "end": frozen_end}
        if cursor:
            params["cursor"] = cursor
        body = client.get(f"{DATA_API}/v2/activity", params)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list) or not isinstance(body.get("pagination"), dict):
            raise ValueError("Unrecognized v2/activity response; refusing to persist")
        pagination = body["pagination"]
        rows = body["data"]
        nxt = pagination.get("next_cursor")
        if nxt is not None and (not isinstance(nxt, str) or not nxt or nxt == cursor or nxt in seen):
            raise ValueError("Invalid or cycling activity cursor")
        if pagination.get("has_more") is True and nxt is None:
            raise ValueError("Activity API reports more data but omits cursor")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("Malformed activity row")
        _atomic_json(root / f"activity_{len(paths) + count:06d}.json", {
            "wallet": wallet, "page_size": page_size, "start": start, "end": frozen_end,
            "request_cursor": cursor, "next_cursor": nxt, "rows": rows,
        })
        count += 1
        seen.add(cursor)
        if nxt is None:
            return {"status": "complete", "pages": len(paths) + count,
                    "new_pages": count, "frozen_end": frozen_end, "directory": str(root)}
        cursor = nxt
    return {"status": "partial", "pages": len(paths) + count,
            "new_pages": count, "frozen_end": frozen_end, "directory": str(root)}


def iter_activity(directory: str | Path) -> Iterator[dict]:
    paths = sorted(Path(directory).glob("activity_[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    if not paths:
        raise ValueError("No activity pages; run 'activity export' first")
    for p in paths:
        for row in json.loads(p.read_text(encoding="utf8"))["rows"]:
            yield row


def normalize(row: dict) -> dict:
    """Preserve original raw record; only project fields with explicit provenance."""
    def get(*keys):
        for k in keys:
            if row.get(k) is not None:
                return row[k]
        return None
    return {
        "type": str(get("type", "activity_type") or "UNKNOWN").upper(),
        "timestamp": get("timestamp", "created_at", "createdAt"),
        "slug": get("event_slug", "slug"),
        "condition_id": get("condition_id", "conditionId"),
        "asset": get("token_id", "asset"),
        "side": str(get("side", "taker_side", "takerSide") or "").upper(),
        "outcome": get("outcome"),
        "price": get("price"),
        "size": get("size"),
        "usdc_size": get("usdc_size", "usdcSize"),
        "transaction_hash": get("transaction_hash", "transactionHash"),
    }


def _finite_positive(v) -> D | None:
    try:
        d = D(str(v))
        return d if d.is_finite() and d > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def profile_activity(directory: str | Path) -> dict:
    """Counts and inventory proxies, never labels profits without settlement proof."""
    counts = Counter()
    by_day = defaultdict(Counter)
    by_window = defaultdict(Counter)
    skipped = Counter()
    refunds = D(0)
    rebate_records_with_amount = 0
    first = last = None
    rows = 0
    for raw in iter_activity(directory):
        rows += 1
        r = normalize(raw)
        kind = r["type"]
        counts[kind] += 1
        try:
            ts = int(r["timestamp"])
            if ts <= 0:
                raise ValueError("bad time")
            day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        except (ValueError, OverflowError, TypeError, OSError):
            skipped["invalid_timestamp"] += 1
            continue
        first = ts if first is None else min(first, ts)
        last = ts if last is None else max(last, ts)
        by_day[day][kind] += 1
        slug = str(r["slug"] or "")
        if slug.startswith("btc-updown-5m-"):
            group = by_window[slug]
            group[kind] += 1
            if kind == "TRADE":
                side = r["side"]
                outcome = str(r["outcome"] or "").lower()
                size = _finite_positive(r["size"])
                if size is not None and side in {"BUY", "SELL"} and outcome in {"up", "down"}:
                    group[f"{outcome}_{side.lower()}_shares"] += size
                else:
                    group["unclassified_trade"] += 1
        if kind in {"MAKER_REBATE", "TAKER_REBATE"}:
            # Treat explicit *_usdc as payout only if API declares activity type;
            # it remains an *observed activity field*, not reconciled account P&L.
            amount = _finite_positive(r["usdc_size"])
            if amount is not None:
                refunds += amount
                rebate_records_with_amount += 1
            else:
                skipped["rebate_amount_unavailable"] += 1
    classified = Counter()
    for d in by_window.values():
        both = d["up_buy_shares"] > 0 and d["down_buy_shares"] > 0
        if both and not d["up_sell_shares"] and not d["down_sell_shares"]:
            classified["observed_buy_both"] += 1
        elif d["up_buy_shares"] > 0 or d["down_buy_shares"] > 0:
            classified["other_buy_activity"] += 1
        else:
            classified["no_classifiable_buy"] += 1
    return {
        "record_count": rows, "event_types": dict(counts),
        "first_timestamp": first, "last_timestamp": last,
        "btc_5m_windows": len(by_window), "window_behavior_counts": dict(classified),
        "by_utc_day": {k: dict(v) for k,v in sorted(by_day.items())},
        "explicit_positive_rebate_activity_rows": rebate_records_with_amount,
        "sum_explicit_rebate_activity_usdc_UNRECONCILED": str(refunds),
        "data_quality": dict(skipped),
        "warning": "Activity fields alone cannot establish realized P&L, fees or exact queue execution. "
                   "Do not sum activity and trading P&L without an account-level reconciliation.",
    }
