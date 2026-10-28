"""Public GET-only HTTP client with bounded retries and request throttling."""
from __future__ import annotations
import email.utils
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import requests

from .config import CLOB_API, DATA_API, GAMMA_API
from .clock import VenueClock

_ALLOWED = frozenset({urlsplit(x).netloc for x in (DATA_API, CLOB_API, GAMMA_API)})


class PublicAPIError(RuntimeError):
    pass


class PublicHTTP:
    def __init__(self, session: requests.Session | None = None, timeout: float = 12,
                 min_interval: float = 0.12, attempts: int = 4):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "StepAheadLab/1.1.1 public-research-readonly",
                                     "Accept": "application/json"})
        self.timeout = timeout
        self.min_interval = min_interval
        self.attempts = attempts
        self._last = 0.0
        self.clock = VenueClock()

    def ensure_clock(self) -> dict:
        """Refresh a venue-time reference before any clock-sensitive research."""
        if self.clock.needs_sync():
            return self.clock.synchronize(lambda: self.get(f"{CLOB_API}/time"))
        return self.clock.diagnostic()

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        u = urlsplit(url)
        if u.scheme != "https" or u.netloc not in _ALLOWED:
            raise ValueError("Only hardcoded public Polymarket HTTPS API hosts are allowed")
        last_error = None
        for attempt in range(self.attempts):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout,
                                            allow_redirects=False)
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {response.status_code}: {response.text[:180]}"
                    if attempt + 1 == self.attempts:
                        break
                    retry = _retry_delay(response.headers.get("Retry-After"), attempt)
                    time.sleep(retry)
                    continue
                if response.status_code >= 300:
                    raise PublicAPIError(f"HTTP {response.status_code} from {u.netloc}: {response.text[:300]}")
                return response.json()
            except (requests.Timeout, requests.ConnectionError, ValueError) as exc:
                last_error = str(exc)
                if attempt + 1 == self.attempts:
                    break
                time.sleep(min(2 ** attempt, 8))
        raise PublicAPIError(f"GET failed after {self.attempts} attempts: {url}: {last_error}")


def _retry_delay(raw: str | None, attempt: int) -> float:
    if raw:
        try:
            return min(60., max(.1, float(raw)))
        except ValueError:
            try:
                dt = email.utils.parsedate_to_datetime(raw)
                return min(60., max(.1, (dt - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError):
                pass
    return min(2. ** attempt, 16.)
