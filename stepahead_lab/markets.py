"""Strict discovery of the currently active BTC Up/Down 5-minute market."""
from __future__ import annotations
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from .config import BTC_MARKET_PREFIX, BTC_SERIES_SLUG, CLOB_API, GAMMA_API
from .http import PublicHTTP


def _as_list(item: Any) -> list:
    if isinstance(item, list):
        return item
    if isinstance(item, str):
        try:
            result = json.loads(item)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _ts(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (float, int)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class Market:
    slug: str
    condition_id: str
    up_token: str
    down_token: str
    start: float
    end: float
    fees_enabled: bool
    title: str


def parse_market(data: dict) -> Market | None:
    """Refuse speculative market-to-token mapping; don't assume array order."""
    slug = str(data.get("slug") or "")
    match = re.fullmatch(r"btc-updown-5m-(\d{10})", slug)
    if not match:
        return None
    start = int(match.group(1))
    event_start = _ts(data.get("eventStartTime"))
    # Gamma startDate is frequently the market LISTING time, not its
    # crypto price-comparison window. It must not validate the slug epoch.
    end = _ts(data.get("endDate"))
    # The slug timestamps the oracle comparison window, not market listing.
    if end is None or abs(end - (start + 300)) > 2:
        return None
    if event_start is not None and abs(event_start - start) > 2:
        return None
    outcomes = [str(x).strip().lower() for x in _as_list(data.get("outcomes"))]
    ids = [str(x) for x in _as_list(data.get("clobTokenIds"))]
    if len(ids) != 2 or set(outcomes) != {"up", "down"} or len(outcomes) != 2:
        return None
    if not all(x.isdigit() and int(x) > 0 for x in ids):
        return None
    if data.get("closed") is True or data.get("acceptingOrders") is False or data.get("enableOrderBook") is False:
        return None
    condition = str(data.get("conditionId") or "")
    if not re.fullmatch("0x[0-9a-fA-F]{64}", condition):
        return None
    return Market(slug=slug, condition_id=condition,
                  up_token=ids[outcomes.index("up")], down_token=ids[outcomes.index("down")],
                  start=float(start), end=float(end),
                  fees_enabled=bool(data.get("feesEnabled", True)),
                  title=str(data.get("question") or slug))


def discover_current(client: PublicHTTP, now: float | None = None) -> Market:
    if now is None:
        synchronizer = getattr(client, "ensure_clock", None)
        if synchronizer is not None:
            synchronizer()
            now = client.clock.now()
        else:
            now = time.time()
    slot_start = int(now // 300 * 300)
    target_slug = f"{BTC_MARKET_PREFIX}{slot_start}"
    # Try direct event lookup first, then series listing. Fail closed on metadata ambiguity.
    events = client.get(f"{GAMMA_API}/events", {"slug": target_slug, "limit": 2})
    if not isinstance(events, list):
        raise ValueError("Gamma events API changed its response shape")
    matches = [event for event in events if event.get("slug") == target_slug]
    if not matches:
        events = client.get(f"{GAMMA_API}/events", {
            "series_slug": BTC_SERIES_SLUG, "closed": "false", "limit": 500,
            "order": "endDate", "ascending": "true"})
        if not isinstance(events, list):
            raise ValueError("Gamma series API changed its response shape")
        matches = [e for e in events if e.get("slug") == target_slug]
    for event in matches:
        for record in event.get("markets") or []:
            market = parse_market({**record, "slug": record.get("slug") or event.get("slug"),
                                   "eventStartTime": event.get("eventStartTime") or record.get("eventStartTime")})
            if market and market.start <= now < market.end:
                return market
    raise ValueError(f"BTC 5-minute market {target_slug} not discoverable/verified; no trade simulation")


@dataclass(frozen=True)
class Level:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class Book:
    token_id: str
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    request_started: float
    observed_at: float
    source_ts: float | None


def parse_levels(raw: Any, reverse: bool) -> tuple[Level, ...]:
    result = []
    if not isinstance(raw, list):
        raise ValueError("Malformed book levels")
    for level in raw:
        try:
            price = Decimal(str(level["price"]))
            size = Decimal(str(level["size"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Malformed order-book level") from exc
        if not (Decimal(0) < price < Decimal(1)) or size <= 0:
            continue
        result.append(Level(price, size))
    return tuple(sorted(result, key=lambda x: x.price, reverse=reverse))


def parse_book(raw: dict, token_id: str, t0: float, t1: float) -> Book:
    if not isinstance(raw, dict):
        raise ValueError("Malformed CLOB response")
    asset = raw.get("asset_id") or raw.get("token_id")
    if asset is not None and str(asset) != token_id:
        raise ValueError("Order book token mismatch")
    server_ts = raw.get("timestamp")
    source_ts = None
    if server_ts not in (None, ""):
        try:
            source_ts = float(server_ts)
            if source_ts > 1e11:
                source_ts /= 1000
        except (ValueError, TypeError):
            source_ts = None
    return Book(token_id, parse_levels(raw.get("bids"), reverse=True),
                parse_levels(raw.get("asks"), reverse=False), t0, t1, source_ts)


def fetch_book(client: PublicHTTP, token_id: str) -> Book:
    clock = getattr(client, "clock", None)
    if clock is not None:
        client.ensure_clock()
    started = clock.now() if clock is not None else time.time()
    response = client.get(f"{CLOB_API}/book", {"token_id": token_id})
    observed = clock.now() if clock is not None else time.time()
    return parse_book(response, token_id, started, observed)


def fetch_pair(client: PublicHTTP, market: Market) -> tuple[Book, Book]:
    return fetch_book(client, market.up_token), fetch_book(client, market.down_token)
