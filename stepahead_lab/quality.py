"""Integrity audit of local paged snapshots: refuse unsupported completeness."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


def audit_pages(directory: str | Path, kind: str = "trade") -> dict:
    if kind not in {"trade", "activity"}:
        raise ValueError("kind must be trade or activity")
    prefix = "page" if kind == "trade" else "activity"
    paths = sorted(Path(directory).glob(f"{prefix}_[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    if not paths:
        raise ValueError("No page files")
    errors = []
    totals = Counter()
    headers = set()
    last_next = None
    seen_requests = set()
    first_cursor = None
    final_cursor = None
    for idx, path in enumerate(paths):
        try:
            page = json.loads(path.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: unreadable {exc}")
            continue
        if path.name != f"{prefix}_{idx:06d}.json":
            errors.append(f"gap or duplicate page index at {path.name}")
        headers.add((page.get("wallet", "").lower(), page.get("page_size"),
                     page.get("start") if kind == "activity" else 1,
                     page.get("end") if kind == "activity" else None))
        current = page.get("request_cursor")
        following = page.get("next_cursor")
        if idx and current != last_next:
            errors.append(f"Cursor continuity failed at {path.name}")
        if current is not None and current in seen_requests:
            errors.append(f"Repeated request cursor at {path.name}")
        seen_requests.add(current)
        last_next = following
        if idx == 0:
            first_cursor = current
            if current is not None:
                errors.append("First page has a cursor")
        final_cursor = following
        rows = page.get("rows")
        if not isinstance(rows, list):
            errors.append(f"{path.name}: no valid rows")
        else:
            totals["rows"] += len(rows)
            totals["pages"] += 1
            totals["empty_pages"] += int(not bool(rows))
    if len(headers) != 1:
        errors.append("Page metadata cohort mismatch")
    complete = bool(paths) and not errors and final_cursor is None
    return {"kind": kind, "pages": totals["pages"], "rows": totals["rows"],
            "empty_pages": totals["empty_pages"], "complete": complete,
            "issues": errors, "directory": str(directory),
            "warning": "A continuous cursor chain does not verify all upstream records exist; "
                       "compare API aggregates, time bounds and raw unique fill identities separately."}
