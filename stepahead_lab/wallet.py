"""Resumable full-wallet page export from public /v2/trades (no credentials).

Every page is an atomic JSON file; resume follows the last durable cursor.
An incomplete page is never committed. No trading functions exist.
"""
from __future__ import annotations
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator
from .config import DATA_API, TARGET_WALLET
from .http import PublicHTTP

D = Decimal


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def export_wallet(client: PublicHTTP, directory: str | Path, wallet: str = TARGET_WALLET,
                  max_pages: int = 0, page_size: int = 1000) -> dict:
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet):
        raise ValueError("Invalid wallet address")
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be 1..1000")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    page_files = sorted(directory.glob("page_[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    if page_files:
        if any(int(f.stem.split("_")[1]) != i for i, f in enumerate(page_files)):
            raise ValueError("Non-contiguous wallet pages; refusing unsafe resume")
        with open(page_files[0], encoding="utf8") as f:
            first = json.load(f)
        if first["wallet"].lower() != wallet.lower() or first["page_size"] != page_size:
            raise ValueError("Wallet or page_size differs from saved export")
        with open(page_files[-1], encoding="utf8") as f:
            last = json.load(f)
        cursor = last.get("next_cursor")
        if cursor is None:
            return {"status": "complete", "pages": len(page_files), "new_pages": 0, "directory": str(directory)}
        if not cursor or not isinstance(cursor, str):
            raise ValueError("Invalid resume cursor")
    else:
        cursor = None
    idx = len(page_files)
    new_pages = 0
    seen_cursors: set[str] = set()
    # Persisted cursor continuity matters across separate invocations too.
    for f in page_files:
        body = json.loads(f.read_text(encoding="utf8"))
        requested = body.get("request_cursor")
        if requested is not None:
            if requested in seen_cursors:
                raise ValueError("Repeated saved cursor")
            seen_cursors.add(requested)
    # The final next cursor is our resume point, so never count it twice.
    while max_pages == 0 or new_pages < max_pages:
        if cursor is not None and cursor in seen_cursors and new_pages > 0:
            raise ValueError("API returned a repeated cursor")
        if cursor is not None:
            seen_cursors.add(cursor)
        params: dict[str, Any] = {"user": wallet, "limit": page_size,
                                  "start": 1, "taker_only": "false"}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(f"{DATA_API}/v2/trades", params)
        if not isinstance(response, dict) or not isinstance(response.get("data"), list) \
                or not isinstance(response.get("pagination"), dict):
            raise ValueError("Unexpected V2 trades API shape; no page saved")
        rows = response["data"]
        pagination = response["pagination"]
        next_cursor = pagination.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ValueError("Unexpected next cursor")
        if next_cursor is not None and next_cursor in seen_cursors:
            raise ValueError("Repeated cursor")
        if pagination.get("has_more") is True and not next_cursor:
            raise ValueError("API says more pages but returned no cursor")
        if rows and not all(isinstance(r, dict) for r in rows):
            raise ValueError("Invalid trade rows")
        _atomic_json(directory / f"page_{idx:06d}.json", {
            "wallet": wallet, "page_size": page_size, "page": idx,
            "request_cursor": cursor, "next_cursor": next_cursor,
            "rows": rows})
        idx += 1
        new_pages += 1
        cursor = next_cursor
        if next_cursor is None:
            return {"status": "complete", "pages": idx, "new_pages": new_pages, "directory": str(directory)}
    return {"status": "partial", "pages": idx, "new_pages": new_pages,
            "directory": str(directory), "resumable": True}


def iter_trades(directory: str | Path) -> Iterator[dict]:
    files = sorted(Path(directory).glob("page_[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    if not files:
        raise ValueError("No wallet pages found; run 'wallet export' first")
    for path in files:
        with open(path, encoding="utf8") as f:
            body = json.load(f)
        for item in body["rows"]:
            yield item


def analyze_wallet(directory: str | Path, top_n: int = 15) -> dict:
    """Avoid claiming P&L from wallet trades: settlement/rebates not in feed.

    Aggregated BUY/SELL and matched inventory are observable proxies only.
    """
    by_market: dict[str, dict] = defaultdict(lambda: {"up_buy": D(0), "down_buy": D(0),
        "up_sell": D(0), "down_sell": D(0), "up_paid": D(0), "down_paid": D(0),
        "up_proceeds": D(0), "down_proceeds": D(0), "fills": 0,
        "first_up_buy": None, "first_down_buy": None, "buy_usdc": D(0),
        "first_ts": None, "last_ts": None})
    by_hour = Counter()
    by_day = Counter()
    side_totals = Counter()
    window_offsets = Counter()
    total = 0
    skipped = 0
    first_ts = None
    last_ts = None
    for trade in iter_trades(directory):
        total += 1
        try:
            raw_side = str(trade.get("side", "")).upper()
            raw_outcome = str(trade.get("outcome", "")).lower()
            side = "buy" if raw_side == "BUY" else "sell" if raw_side == "SELL" else ""
            outcome = "up" if raw_outcome == "up" else "down" if raw_outcome == "down" else ""
            size, price = D(str(trade["size"])), D(str(trade["price"]))
            ts = int(trade["timestamp"])
            slug = str(trade.get("event_slug") or trade.get("slug") or "")
        except (ValueError, TypeError, KeyError, ArithmeticError):
            skipped += 1
            continue
        if not slug.startswith("btc-updown-5m-") or side == "" or outcome == "" \
                or not (size > 0 and D(0) < price < D(1)):
            skipped += 1
            continue
        s = by_market[str(trade.get("condition_id") or trade.get("conditionId") or slug)]
        s[f"{outcome}_{side}"] += size
        s[f"{outcome}_{'paid' if side == 'buy' else 'proceeds'}"] += size * price
        s["fills"] += 1
        s["first_ts"] = ts if s["first_ts"] is None else min(ts, s["first_ts"])
        s["last_ts"] = ts if s["last_ts"] is None else max(ts, s["last_ts"])
        if side == "buy":
            s["buy_usdc"] += size * price
            first_buy_key = f"first_{outcome}_buy"
            s[first_buy_key] = ts if s[first_buy_key] is None else min(ts, s[first_buy_key])
        try:
            window_start = int(slug.rsplit("-", 1)[-1])
            offset = ts - window_start
            bucket = ("pre_window" if offset < 0 else
                      "0_59s" if offset < 60 else "60_119s" if offset < 120 else
                      "120_179s" if offset < 180 else "180_239s" if offset < 240 else
                      "240_299s" if offset < 300 else "post_window")
            window_offsets[bucket] += 1
        except ValueError:
            pass
        by_hour[datetime.fromtimestamp(ts, timezone.utc).hour] += 1
        by_day[datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")] += 1
        side_totals[side] += 1
        first_ts = min(ts, first_ts) if first_ts is not None else ts
        last_ts = max(ts, last_ts) if last_ts is not None else ts
    aggregate_pair_shares = D(0)
    markets_both_buy = 0
    markets_first_opposite_buy_within_5s = 0
    markets_first_opposite_buy_within_30s = 0
    markets_net_paired = 0
    markets_net_directional = 0
    gross_buys_usdc = D(0)
    market_rows = []
    for condition, s in by_market.items():
        net_up = s["up_buy"] - s["up_sell"]
        net_down = s["down_buy"] - s["down_sell"]
        paired = max(D(0), min(net_up, net_down))
        aggregate_pair_shares += paired
        gross_buys_usdc += s["buy_usdc"]
        if paired > 0:
            markets_net_paired += 1
        if net_up > paired or net_down > paired:
            markets_net_directional += 1
        if s["first_up_buy"] is not None and s["first_down_buy"] is not None:
            lag = abs(s["first_up_buy"] - s["first_down_buy"])
            if lag <= 5:
                markets_first_opposite_buy_within_5s += 1
            if lag <= 30:
                markets_first_opposite_buy_within_30s += 1
        if s["up_buy"] > 0 and s["down_buy"] > 0:
            markets_both_buy += 1
        up_vwap = s["up_paid"] / s["up_buy"] if s["up_buy"] else None
        down_vwap = s["down_paid"] / s["down_buy"] if s["down_buy"] else None
        market_rows.append({"market": condition, "fills": s["fills"],
                            "net_up_shares": str(net_up), "net_down_shares": str(net_down),
                            "end_of_trades_matched_shares": str(paired),
                            "gross_buy_notional_usdc": str(s["buy_usdc"]),
                            "first_buy_opposite_leg_lag_seconds": (
                                abs(s["first_up_buy"] - s["first_down_buy"])
                                if s["first_up_buy"] is not None and s["first_down_buy"] is not None
                                else None),
                            "up_buy_vwap": str(up_vwap) if up_vwap is not None else None,
                            "down_buy_vwap": str(down_vwap) if down_vwap is not None else None,
                            "combined_buy_vwap": str(up_vwap + down_vwap) if up_vwap is not None and down_vwap is not None else None})
    # Not deduplicating by tx hash: a transaction can legitimately have multiple fills.
    return {"source": str(directory), "parsed_btc_fills": sum(x["fills"] for x in by_market.values()),
            "total_rows_seen": total, "skipped_other_or_malformed": skipped,
            "first_timestamp_utc": datetime.fromtimestamp(first_ts, timezone.utc).isoformat() if first_ts else None,
            "last_timestamp_utc": datetime.fromtimestamp(last_ts, timezone.utc).isoformat() if last_ts else None,
            "btc_markets": len(by_market), "markets_buying_both_outcomes": markets_both_buy,
            "aggregate_end_of_trades_matched_shares": str(aggregate_pair_shares),
            "gross_btc_buy_notional_usdc": str(gross_buys_usdc),
            "markets_with_positive_net_pair_inventory": markets_net_paired,
            "markets_with_positive_directional_net_inventory": markets_net_directional,
            "markets_first_opposite_buy_within_5s": markets_first_opposite_buy_within_5s,
            "markets_first_opposite_buy_within_30s": markets_first_opposite_buy_within_30s,
            "fills_by_market_window_offset": dict(window_offsets),
            "fills_by_side": dict(side_totals), "fills_by_utc_hour": dict(sorted(by_hour.items())),
            "fills_by_utc_day": dict(sorted(by_day.items())),
            "top_active_markets": sorted(market_rows, key=lambda x: x["fills"], reverse=True)[:top_n],
            "limitations": [
                "This is NOT realized P&L: settlement payouts, fees, and rebates are absent.",
                "A buy on both outcomes does NOT prove the legs were an arbitrage pair.",
                "First opposite-side buy timing does not prove leg execution was simultaneous.",
                "Maker/taker classification is not available in this normalized public feed.",
                "End-of-trades matched shares are net-flow proxies, not true inventory after redemption.",
                "Coincident fills and cancellations cannot be reconstructed from this feed."]}
