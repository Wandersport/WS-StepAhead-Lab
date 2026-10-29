"""No-network regression tests for bounded paper observation."""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from stepahead_lab.cli import build_parser
from stepahead_lab.paper import observe
from stepahead_lab.runner import run


class TimedServiceTests(unittest.TestCase):
    def test_launcher_forwards_flags(self):
        script = Path(__file__).resolve().parents[1] / "start.command"
        self.assertIn('exec .venv/bin/stepahead start "$@"', script.read_text())

    def test_cli_duration_and_aware_time(self):
        cli = build_parser()
        a = cli.parse_args(["start", "--duration-hours", "24"])
        self.assertEqual(a.duration_hours, Decimal("24"))
        self.assertIsNone(a.stop_at)
        b = cli.parse_args(["start", "--stop-at", "2026-10-30T18:00:00+01:00"])
        self.assertEqual(b.stop_at, 1793379600.0)
        with self.assertRaises(SystemExit):
            cli.parse_args(["start", "--duration-hours", "24", "--stop-at", "2026-10-30T18:00:00+01:00"])
        with self.assertRaises(SystemExit):
            cli.parse_args(["start", "--stop-at", "2026-10-30T18:00:00"])

    def test_reject_past_stop(self):
        with TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "future"):
                run(root=Path(td), dashboard=False, stop_at_epoch=time.time() - 10)

    def test_reject_bad_duration(self):
        with TemporaryDirectory() as td:
            for v in (Decimal(0), Decimal("-1"), Decimal("NaN"), Decimal(169)):
                with self.subTest(duration=v), self.assertRaises(ValueError):
                    run(root=Path(td), dashboard=False, duration_hours=v)

    def test_expired_deadline_stops_without_network(self):
        with TemporaryDirectory() as td:
            result = observe(None, polls=15, interval=4.0, quantity=Decimal("25"),
                min_edge_pct=Decimal("0.2"), buffer=Decimal("0.002"),
                directory=Path(td), stop_at_monotonic=time.monotonic()-1,
                verbose=False)
            self.assertEqual(result["snapshots_recorded"], 0)
            self.assertTrue(result["stop_reached"])
            self.assertFalse((Path(td) / "snapshots.jsonl").exists())

    def test_short_run_stops_without_network(self):
        with TemporaryDirectory() as td:
            # Ensure no public network calls even if an OS scheduler enters the
            # short-lived loop before the monotonic deadline has elapsed.
            with patch("stepahead_lab.runner.monitor_snapshot", return_value={"snapshot": {}}), \
                 patch("stepahead_lab.runner.observe", return_value={
                     "snapshots_recorded": 0, "new_hypothetical_pairs": 0}), \
                 patch("stepahead_lab.runner.settle_file", return_value={}):
                result = run(root=Path(td), dashboard=False, client=object(),
                             duration_hours=Decimal("0.000000001"))
            self.assertTrue(result["stop_reached"])
            self.assertEqual(result["real_orders"], 0)
            self.assertIsNotNone(result["stop_at_utc"])
            self.assertEqual(result["public_book_snapshots"], 0)


if __name__ == "__main__":
    unittest.main()
