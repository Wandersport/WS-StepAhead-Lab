"""Offline proof that future-window observation is read-only and isolated."""
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from stepahead_lab.prewindow import record_upcoming
from stepahead_lab.cli import build_parser
from stepahead_lab.dashboard import summarize_data
from stepahead_lab.paper import observe

START = 1800000000

class Clock:
    def now(self): return START + 110

class API:
    def __init__(self, listed=True, swapped=False):
        self.clock = Clock()
        self.listed = listed
        self.swapped = swapped
        self.calls = []
    def ensure_clock(self): return {}
    def get(self, url, params=None):
        self.calls.append((url,params))
        if url.endswith("/events"):
            if not self.listed: return []
            slug=params["slug"]
            start=int(slug.rsplit("-",1)[1])
            outcomes=['Down','Up'] if self.swapped else ['Up','Down']
            tokenids=['222','111'] if self.swapped else ['111','222']
            market={
                "slug": slug, "conditionId":"0x"+"a"*64,
                "endDate":datetime.fromtimestamp(start+300,timezone.utc).isoformat(),
                "outcomes":json.dumps(outcomes), "clobTokenIds":json.dumps(tokenids),
                "acceptingOrders":True, "closed":False
            }
            return [{"slug":slug,"markets":[market]}]
        if url.endswith("/book"):
            return {"asset_id":params["token_id"],"timestamp":str(int(self.clock.now()*1000)),
                    "bids":[{"price":"0.40","size":"50"}],
                    "asks":[{"price":"0.55","size":"50"}]}
        raise AssertionError(url)

class PrewindowTests(unittest.TestCase):
    def test_records_two_future_books_zero_fills(self):
        with TemporaryDirectory() as td:
            client=API(swapped=True)
            future_root = Path(td) / "prewindow"
            r=record_upcoming(client,future_root,windows=2)
            self.assertEqual(r["upcoming_snapshots"],2)
            rows=[json.loads(x) for x in (future_root/"snapshots.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows),2)
            self.assertEqual([round(x["seconds_until_window_start"]) for x in rows],[190,490])
            self.assertEqual(rows[0]["market"]["up_token"],"111")
            self.assertTrue(all(x["orders_sent"]==0 and x["paper_entry"] is False for x in rows))
            self.assertEqual(rows[0]["kind"],"prewindow_book_snapshot")
            self.assertEqual(rows[0]["up_best_ask"],"0.55")
            self.assertEqual(summarize_data(Path(td))["upcoming_snapshot_count"],2)
            self.assertTrue(all("/order" not in u for u,_ in client.calls))

    def test_unlisted_separate_from_empty_book(self):
        with TemporaryDirectory() as td:
            r=record_upcoming(API(listed=False),td,windows=2)
            self.assertEqual(r["upcoming_snapshots"],0)
            self.assertEqual(r["missing_or_ambiguous_markets"],2)
            self.assertFalse((Path(td)/"snapshots.jsonl").exists())
            self.assertEqual(len((Path(td)/"status.jsonl").read_text().splitlines()),2)

    def test_expired_deadline_never_calls_api(self):
        with TemporaryDirectory() as td:
            client=API()
            r=record_upcoming(client,td,stop_at_monotonic=time.monotonic()-1)
            self.assertEqual(r["deadline_skips"],2)
            self.assertFalse(client.calls)

    def test_invalid_horizon_and_cli_flags(self):
        with TemporaryDirectory() as td:
            for n in [0,4]:
                with self.assertRaises(ValueError):
                    record_upcoming(API(),td,windows=n)
        args=build_parser().parse_args([
            "start","--capture-upcoming","--duration-hours","24",
            "--upcoming-windows","2","--upcoming-every","5"
        ])
        self.assertTrue(args.capture_upcoming)
        self.assertEqual(args.upcoming_windows,2)
        self.assertEqual(args.upcoming_every,5)

    def test_not_enabled_does_not_touch_future_api(self):
        with TemporaryDirectory() as td:
            m=object()
            with patch("stepahead_lab.paper.discover_current",side_effect=ValueError("not listed")):
                result=observe(API(listed=False),polls=1,interval=0.5,
                    quantity=Decimal("25"),min_edge_pct=Decimal("0.2"),
                    buffer=Decimal("0.002"),directory=Path(td)/"paper",verbose=False)
            self.assertEqual(result["upcoming_snapshots_recorded"],0)
            self.assertFalse((Path(td)/"prewindow").exists())

    def test_future_collector_error_does_not_abort_main(self):
        with TemporaryDirectory() as td:
            with patch("stepahead_lab.paper.discover_current",side_effect=ValueError("not listed")), \
                 patch("stepahead_lab.prewindow.record_upcoming",side_effect=ValueError("Gamma failed")):
                result=observe(API(),polls=1,interval=0.5,
                    quantity=Decimal("25"),min_edge_pct=Decimal("0.2"),
                    buffer=Decimal("0.002"),directory=Path(td)/"paper",verbose=False,
                    capture_upcoming=True)
            self.assertEqual(result["upcoming_recording_errors"],1)
            self.assertEqual(len((Path(td)/"prewindow"/"errors.jsonl").read_text().splitlines()),1)

if __name__=="__main__":
    unittest.main()
