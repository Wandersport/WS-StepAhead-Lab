"""Read-only public profile/time-series monitoring, locally persisted.

Only Polymarket API calls. `watch` runs in the invoking process for a
bounded number of iterations; no background service is installed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time

from .config import DATA_API, TARGET_WALLET
from .http import PublicHTTP
from .paper import _append_jsonl, _read_jsonl


def _n(v):
    try:
        if isinstance(v, bool):
            return None
        x = Decimal(str(v))
        return x if x.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def fetch_profile(client: PublicHTTP, wallet: str = TARGET_WALLET,
                  now: int | None = None) -> dict:
    """All fields passed through from canonical API: do not reinterpret basis."""
    if not (wallet.startswith("0x") and len(wallet) == 42 and
            all(c in "0123456789abcdefABCDEF" for c in wallet[2:])):
        raise ValueError("Invalid wallet")
    now = int(time.time()) if now is None else int(now)
    stats = client.get(f"{DATA_API}/v2/user-stats", {"user": wallet})
    volume = client.get(f"{DATA_API}/v2/user-volume", {"user": wallet})
    status = client.get(f"{DATA_API}/v2/status")
    if not isinstance(stats, dict) or not isinstance(stats.get("data"), dict):
        raise ValueError("Unexpected user-stats response")
    if not isinstance(volume, dict) or not isinstance(volume.get("data"), dict):
        raise ValueError("Unexpected user-volume response")
    if not isinstance(status, dict) or "data" not in status:
        raise ValueError("Unexpected status response")
    return {"source": "Polymarket Data API v2", "wallet": wallet,
            "observed_at": now, "observed_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "profile": stats["data"], "volume": volume["data"],
            "api_status": status["data"], "contains_live_orders": False}


def snapshot(client: PublicHTTP, output: str | Path, wallet: str = TARGET_WALLET,
             now: int | None = None) -> dict:
    output = Path(output)
    old = None
    if output.exists():
        for item in _read_jsonl(output):
            if item.get("wallet", "").lower() == wallet.lower():
                old = item
    latest = fetch_profile(client, wallet, now)
    differences = {}
    if old:
        # Only scalar fields that have identical labels, not inner PnL component
        # objects with potentially different definitions or observation times.
        for collection in ("profile", "volume"):
            for field, cur in latest[collection].items():
                prior = old.get(collection, {}).get(field)
                a, b = _n(cur), _n(prior)
                if a is not None and b is not None:
                    differences[f"{collection}.{field}"] = str(a - b)
                elif field == "all_time_pnl" and isinstance(cur, dict) and isinstance(prior, dict):
                    # Compare identical named component fields only, do not
                    # add together unverified accounting components.
                    for k, current_value in cur.items():
                        x, y = _n(current_value), _n(prior.get(k))
                        if x is not None and y is not None:
                            differences[f"{collection}.{field}.{k}"] = str(x - y)
    _append_jsonl(output, latest)
    return {"snapshot": latest, "delta_since_previous_observation": differences,
            "snapshot_file": str(output),
            "warning": "Observations are snapshots; different P&L component timestamps may differ. "
                       "No future.news scraping or live trade intent is inferred."}


def watch(client: PublicHTTP, output: str | Path, wallet: str,
          polls: int, interval: float) -> dict:
    if polls < 1 or polls > 10000 or interval < 5:
        raise ValueError("polls must be 1..10000 and interval at least 5 seconds")
    ok = 0
    failures = []
    for index in range(polls):
        try:
            snapshot(client, output, wallet)
            ok += 1
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append({"index": index, "error": str(exc)[:220]})
        if index + 1 < polls:
            time.sleep(interval)
    return {"successes": ok, "failures": failures, "public_profile_file": str(output),
            "monitor_is_running_after_command_returns": False}
