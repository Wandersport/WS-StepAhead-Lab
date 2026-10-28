"""Venue-clock calibration for READ-ONLY Polymarket market observations.

A CLOB book timestamps the venue's last change, not the Windows host clock.
Book freshness is meaningful only when the observer and venue share a time base.
This module obtains a bounded-RTT Unix clock from the public CLOB /time API.

Calibration is intentionally NOT a loophole for stale books: an uncalibrated
or unreliable clock must fail closed, and old/implausibly future venue events
remain ineligible for paper decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from decimal import Decimal, InvalidOperation
from typing import Callable


class ClockCalibrationError(RuntimeError):
    """Cannot establish an adequately precise public venue clock."""


@dataclass(frozen=True)
class ClockSample:
    server_epoch_mid: float
    local_wall_mid: float
    monotonic_mid: float
    rtt_seconds: float
    uncertainty_seconds: float

    @property
    def offset_seconds(self) -> float:
        return self.server_epoch_mid - self.local_wall_mid


class VenueClock:
    """Monotonic-anchored clock with bounded uncertainty and periodic resync."""

    def __init__(self, *, refresh_seconds: float = 90., max_rtt_seconds: float = 2.,
                 max_offset_seconds: float = 1800., probes: int = 3,
                 monotonic: Callable[[], float] | None = None,
                 wall_time: Callable[[], float] | None = None):
        if refresh_seconds < 5 or max_rtt_seconds <= 0 or max_offset_seconds <= 0 or not 1 <= probes <= 9:
            raise ValueError('Invalid clock calibration controls')
        self.refresh_seconds = refresh_seconds
        self.max_rtt_seconds = max_rtt_seconds
        self.max_offset_seconds = max_offset_seconds
        self.probes = probes
        self._mono = monotonic or time.monotonic
        self._wall = wall_time or time.time
        self.sample: ClockSample | None = None
        self.last_sync_result: dict | None = None

    @staticmethod
    def parse_server_time(payload: object) -> tuple[float, float]:
        """Return central epoch and rounding uncertainty for numeric /time reply."""
        if isinstance(payload, bool) or isinstance(payload, (list, dict)):
            raise ClockCalibrationError('Unexpected CLOB /time payload')
        try:
            d = Decimal(str(payload).strip())
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ClockCalibrationError('Non-numeric CLOB /time payload') from exc
        if not d.is_finite() or not Decimal('1600000000') <= d <= Decimal('4102444800'):
            raise ClockCalibrationError('Implausible CLOB /time epoch')
        resolution = 0.5 if d == d.to_integral_value() else 0.002
        return (float(d) + (0.5 if resolution == 0.5 else 0.0), resolution)

    def needs_sync(self) -> bool:
        return self.sample is None or self._mono() - self.sample.monotonic_mid > self.refresh_seconds

    def synchronize(self, reader: Callable[[], object]) -> dict:
        samples = []
        issues = []
        for _ in range(self.probes):
            m0 = self._mono(); w0 = self._wall()
            try:
                value = reader()
            except (RuntimeError, OSError, ValueError) as exc:
                issues.append(str(exc)[:160]); continue
            m1 = self._mono(); w1 = self._wall()
            rtt = m1 - m0
            if rtt < 0 or rtt > self.max_rtt_seconds:
                issues.append(f'unacceptable CLOB clock RTT: {rtt:.3f}s'); continue
            # Local-wall clock changes during a request cannot be reconciled
            # with monotonic elapsed time: discard that probe.
            if abs((w1 - w0) - rtt) > 0.3:
                issues.append('host clock jumped during CLOB probe'); continue
            epoch, server_resolution = self.parse_server_time(value)
            sample = ClockSample(epoch, (w0 + w1) / 2, (m0 + m1) / 2,
                                 rtt, server_resolution + rtt / 2)
            if abs(sample.offset_seconds) > self.max_offset_seconds:
                issues.append(f'host/venue offset exceeds safety cap: {sample.offset_seconds:.2f}s')
                continue
            samples.append(sample)
        if not samples:
            self.sample = None
            raise ClockCalibrationError('No reliable CLOB /time probes; ' + '; '.join(issues[-3:]))
        # Reject inconsistent venue time responses; do not quietly select a
        # convenient offset when backend servers disagree or local time jumps.
        offsets = [x.offset_seconds for x in samples]
        if len(samples) >= 2 and max(offsets) - min(offsets) > 2.0:
            self.sample = None
            raise ClockCalibrationError('CLOB time probes disagree by more than 2 seconds')
        chosen = min(samples, key=lambda x: x.uncertainty_seconds)
        self.sample = chosen
        self.last_sync_result = {
            'source': 'https://clob.polymarket.com/time',
            'state': 'calibrated',
            'offset_venue_minus_host_seconds': round(chosen.offset_seconds, 3),
            'rtt_seconds': round(chosen.rtt_seconds, 3),
            'uncertainty_seconds': round(chosen.uncertainty_seconds, 3),
            'probes_accepted': len(samples),
            'probes_attempted': self.probes,
            'host_clock_action_recommended': abs(chosen.offset_seconds) > 5,
            'warning': 'Public venue clock; not proof that any market orders would fill.'
        }
        return dict(self.last_sync_result)

    def now(self) -> float:
        if self.sample is None:
            raise ClockCalibrationError('Venue clock is uncalibrated; refusing market decisions')
        elapsed = self._mono() - self.sample.monotonic_mid
        if elapsed < -0.1 or elapsed > self.refresh_seconds + 10:
            raise ClockCalibrationError('Venue clock calibration expired; resync required')
        return self.sample.server_epoch_mid + elapsed

    def diagnostic(self) -> dict:
        if self.sample is None:
            return {'state': 'uncalibrated'}
        return {**(self.last_sync_result or {}),
                'current_venue_epoch': self.now(),
                'current_host_epoch': self._wall(),
                'sample_age_monotonic_seconds': round(self._mono() - self.sample.monotonic_mid, 3)}
