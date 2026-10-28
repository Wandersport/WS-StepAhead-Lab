"""Regression suite for the reported Windows -25.93s clock discrepancy."""
from __future__ import annotations
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from stepahead_lab.clock import ClockCalibrationError, VenueClock
from stepahead_lab.dashboard import _tail, summarize_data, make_server
from stepahead_lab.markets import Book, Level, Market, fetch_book, discover_current
from stepahead_lab.paper import observe
from stepahead_lab.runner import run
from stepahead_lab.strategy import evaluate_pair
from stepahead_lab.config import CLOB_API, GAMMA_API, DATA_API

S = 1800000000
COND = '0x' + 'a'*64


class FakeTicks:
    def __init__(self, local=S+60-26):
        self.wall=local
        self.mono=1000.0

    def wall_time(self):return self.wall
    def monotonic(self):return self.mono
    def advance(self,seconds):
        self.wall+=seconds;self.mono+=seconds


class FakeRemote:
    """Exchange time is 26s ahead of Windows; books are actually fresh."""
    def __init__(self, ticks=None, *, stale_seconds=0):
        self.ticks=ticks or FakeTicks()
        self.clock=VenueClock(probes=3,monotonic=self.ticks.monotonic,
                              wall_time=self.ticks.wall_time)
        self.stale_seconds=stale_seconds
        self.calls=[]

    def ensure_clock(self):
        if self.clock.needs_sync():
            return self.clock.synchronize(lambda:self.get(f'{CLOB_API}/time'))
        return self.clock.diagnostic()

    def get(self, url, params=None):
        self.calls.append((url,params))
        if url.endswith('/time'):
            return int(self.ticks.wall+26)
        if url==f'{GAMMA_API}/events':
            start = int(self.clock.now()//300*300)
            slug=f'btc-updown-5m-{start}'
            rec={'slug':slug,'conditionId':COND,'clobTokenIds':'["111","222"]',
                'outcomes':'["Up","Down"]','endDate':start+300,
                'closed':False,'acceptingOrders':True}
            return [{'slug':slug,'markets':[rec]}]
        if url.endswith('/book'):
            token=params['token_id']
            price='0.40' if token=='111' else '0.52'
            return {'asset_id':token,'timestamp':str(int((self.clock.now()-self.stale_seconds)*1000)),
                    'bids':[{'price':'0.2','size':'100'}],
                    'asks':[{'price':price,'size':'100'}]}
        if url.endswith('/v2/user-stats'):
            return {'data':{'all_time_pnl':{'economic_pnl':50}}}
        if url.endswith('/v2/user-volume'):
            return {'data':{'volume_usdc':2000}}
        if url.endswith('/v2/status'):
            return {'data':{'state':'ok'}}
        raise AssertionError('Unexpected URL '+url)


class CalibrationTests(unittest.TestCase):
    def test_server_epoch_numeric_and_quoted(self):
        self.assertEqual(VenueClock.parse_server_time(str(S)),(S+0.5,0.5))
        self.assertEqual(VenueClock.parse_server_time(S),(S+0.5,0.5))
        self.assertEqual(VenueClock.parse_server_time(str(S)+'.250'),(S+0.25,0.002))

    def test_bad_server_values_fail_closed(self):
        for invalid in ({'timestamp':S}, [], 'NaN','Infinity',-1,True, None):
            with self.subTest(value=invalid), self.assertRaises(ClockCalibrationError):
                VenueClock.parse_server_time(invalid)

    def test_26_second_host_offset_is_recovered(self):
        ticks=FakeTicks(); c=VenueClock(monotonic=ticks.monotonic,wall_time=ticks.wall_time)
        result=c.synchronize(lambda:S+60)
        self.assertAlmostEqual(result['offset_venue_minus_host_seconds'],26.5,places=1)
        self.assertAlmostEqual(c.now(),S+60.5,places=1)
        ticks.advance(10)
        self.assertAlmostEqual(c.now(),S+70.5,places=1)

    def test_expired_clock_does_not_fall_back_to_windows(self):
        ticks=FakeTicks(); c=VenueClock(monotonic=ticks.monotonic,wall_time=ticks.wall_time,
                                     refresh_seconds=10)
        c.synchronize(lambda:S+60)
        ticks.advance(25)
        with self.assertRaises(ClockCalibrationError):c.now()
        self.assertTrue(c.needs_sync())

    def test_clock_unavailable_no_market_decisions(self):
        c=VenueClock()
        with self.assertRaises(ClockCalibrationError):c.now()
        with self.assertRaises(ClockCalibrationError):c.synchronize(lambda:'oops')
        self.assertIsNone(c.sample)

    def test_oversized_host_offset_rejected(self):
        t=FakeTicks(local=S-10000)
        c=VenueClock(monotonic=t.monotonic,wall_time=t.wall_time)
        with self.assertRaises(ClockCalibrationError):c.synchronize(lambda:S+60)

    def test_backends_disagree_fail_closed(self):
        t=FakeTicks(); c=VenueClock(monotonic=t.monotonic,wall_time=t.wall_time)
        data=iter([S+60,S+65,S+60])
        with self.assertRaises(ClockCalibrationError):c.synchronize(lambda:next(data))
        self.assertIsNone(c.sample)

    def test_rtt_exceeds_cap_rejected(self):
        t=FakeTicks()
        c=VenueClock(monotonic=t.monotonic,wall_time=t.wall_time,probes=1)
        def slow():
            t.advance(3)
            return S+61
        with self.assertRaises(ClockCalibrationError):c.synchronize(slow)

    def test_windows_clock_jump_during_probe_rejected(self):
        t=FakeTicks()
        c=VenueClock(monotonic=t.monotonic,wall_time=t.wall_time,probes=1)
        def jump():
            t.wall+=30
            return S+60
        with self.assertRaises(ClockCalibrationError):c.synchronize(jump)


class FullPipelineTests(unittest.TestCase):
    def test_mapped_market_and_fresh_pair_with_clock_correction(self):
        fake=FakeRemote()
        market=discover_current(fake)
        a=fetch_book(fake,market.up_token)
        b=fetch_book(fake,market.down_token)
        self.assertEqual(market.up_token,'111')
        self.assertAlmostEqual(a.observed_at-a.source_ts,0.0,places=1)
        quote=evaluate_pair(market,a,b,now=fake.clock.now())
        self.assertTrue(quote.valid)
        self.assertEqual(quote.reason,'positive_conservative_edge')
        self.assertTrue(any(url.endswith('/time') for url,_ in fake.calls))

    def test_real_stale_book_still_rejected(self):
        fake=FakeRemote(stale_seconds=25)
        market=discover_current(fake)
        a=fetch_book(fake,market.up_token)
        b=fetch_book(fake,market.down_token)
        quote=evaluate_pair(market,a,b,now=fake.clock.now())
        self.assertEqual(quote.reason,'stale_exchange_book')

    def test_venue_future_timestamp_after_calibration_rejected(self):
        fake=FakeRemote(stale_seconds=-25)
        market=discover_current(fake)
        a=fetch_book(fake,market.up_token)
        b=fetch_book(fake,market.down_token)
        quote=evaluate_pair(market,a,b,now=fake.clock.now())
        self.assertEqual(quote.reason,'stale_exchange_book')

    def test_paper_logs_a_hypothetical_pair_not_real_orders(self):
        fake=FakeRemote()
        with tempfile.TemporaryDirectory() as td,patch('stepahead_lab.paper.time.sleep',return_value=None):
            result=observe(fake,polls=3,interval=.5,quantity=D(25),
                           min_edge_pct=D('.2'),buffer=D('.002'),
                           directory=Path(td),verbose=False)
            self.assertEqual(result['snapshots_recorded'],3)
            self.assertEqual(result['new_hypothetical_pairs'],1)
            self.assertEqual(result['actually_executed_orders'],0)
            recs=[json.loads(x) for x in (Path(td)/'snapshots.jsonl').read_text().splitlines()]
            self.assertTrue(all(x['source_age_seconds']['up']<2 for x in recs))
            self.assertTrue(all(x['clock']['host_clock_action_recommended'] for x in recs))

    def test_service_cycle_and_dashboard_synthetic_end_to_end(self):
        fake=FakeRemote()
        with tempfile.TemporaryDirectory() as td,patch('stepahead_lab.paper.time.sleep',return_value=None):
            root=Path(td)
            result=run(root=root,cycles=1,polls_per_cycle=3,interval=.5,
                       dashboard=False,client=fake)
            self.assertEqual(result['public_book_snapshots'],3)
            self.assertEqual(result['new_counterfactual_pairs'],1)
            self.assertEqual(result['real_orders'],0)
            self.assertTrue((root/'clock_calibrations.jsonl').exists())
            view=summarize_data(root)
            self.assertEqual(view['snapshot_count'],3)
            self.assertEqual(view['last_decision'],'positive_conservative_edge')
            self.assertGreater(view['clock_offset_s'],25)
            self.assertEqual(view['paper_pair_count'],1)
            self.assertEqual(view['target_pnl'],50)

    def test_dashboard_port_is_exclusive_on_windows(self):
        import socket
        with tempfile.TemporaryDirectory() as td:
            server = make_server(Path(td), 0)
            try:
                self.assertFalse(server.allow_reuse_address)
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    self.assertEqual(server.socket.getsockopt(socket.SOL_SOCKET,
                                     socket.SO_EXCLUSIVEADDRUSE), 1)
            finally:
                server.server_close()

    def test_dashboard_ignores_partial_last_jsonl_write(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'half.jsonl'
            p.write_bytes(b'{"value":1}\n{"partial":')
            self.assertEqual(_tail(p,10),[{'value':1}])

    def test_dashboard_server_is_loopback_bound_and_detects_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            server=make_server(Path(td),0)
            try:
                self.assertEqual(server.server_address[0],'127.0.0.1')
                second = None
                try:
                    with self.assertRaises(OSError):
                        second = make_server(Path(td), server.server_address[1])
                finally:
                    # Close any unexpectedly constructed server, especially on
                    # Windows where SO_REUSEADDR may otherwise permit rebinding.
                    if second is not None:
                        second.server_close()
            finally:
                server.server_close()


if __name__ == '__main__':unittest.main()

class RealLocalDashboardSmokeTest(unittest.TestCase):
    def test_local_http_dashboard_serves_json_and_html(self):
        from threading import Thread
        import requests
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            server=make_server(root,0)
            thread=Thread(target=server.serve_forever,daemon=True)
            thread.start()
            url='http://127.0.0.1:'+str(server.server_address[1])
            try:
                a=requests.get(url+'/api/status',timeout=3)
                self.assertEqual(a.status_code,200)
                self.assertEqual(a.json()['mode'],'PAPER_ONLY')
                b=requests.get(url+'/',timeout=3)
                self.assertEqual(b.status_code,200)
                self.assertIn('Last quote decision',b.text)
                c=requests.get(url+'/unrecognized',timeout=3)
                self.assertEqual(c.status_code,404)
            finally:
                server.shutdown();thread.join(timeout=3);server.server_close()

    def test_snapshot_and_profile_tail_and_counts_are_incremental(self):
        from stepahead_lab.paper import _append_jsonl
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            p=root/'paper'/'snapshots.jsonl'
            for i in range(300):
                _append_jsonl(p,{'kind':'snapshot','quote':{'reason':'no_edge_after_costs' if i%2 else 'positive_conservative_edge'},
                                 'clock':{'offset_venue_minus_host_seconds':26.1},
                                 'source_age_seconds':{'up':0.3,'down':0.4}})
            a=summarize_data(root)
            self.assertEqual(a['snapshot_count'],300)
            self.assertEqual(a['clock_offset_s'],26.1)
            self.assertEqual(sum(a['recent_decisions'].values()),250)
            _append_jsonl(p,{'kind':'snapshot','quote':{'reason':'stale_exchange_book'}})
            b=summarize_data(root)
            self.assertEqual(b['snapshot_count'],301)
            self.assertEqual(b['last_decision'],'stale_exchange_book')

class SyntheticSoakTests(unittest.TestCase):
    def test_24h_clock_skew_market_rollover_soak(self):
        """Simulate 288 BTC 5m windows and 576 complete book pairs offline."""
        tick=FakeTicks(local=S+30-26)
        fake=FakeRemote(ticks=tick)
        windows=set(); book_count=0; clocks=0
        for i in range(288):
            for j in range(2):
                market=discover_current(fake)
                a=fetch_book(fake,market.up_token)
                b=fetch_book(fake,market.down_token)
                quote=evaluate_pair(market,a,b,now=fake.clock.now())
                self.assertNotIn(quote.reason,{'stale_exchange_book','snapshot_skew',
                                               'outside_safe_market_window'},(i,j,quote.reason))
                self.assertTrue(quote.valid)
                windows.add(market.slug); book_count+=2
                tick.advance(2)
            tick.advance(296)
        clocks=sum(1 for url,_ in fake.calls if url.endswith('/time'))
        self.assertEqual(len(windows),288)
        self.assertEqual(book_count,1152)
        self.assertGreater(clocks,500)  # 3 probes on each 90-second resync
        fake.ensure_clock()
        self.assertAlmostEqual(fake.clock.diagnostic()['offset_venue_minus_host_seconds'],26.5,places=1)

class LegacyDataMigrationTests(unittest.TestCase):
    def test_old_windows_clock_snapshots_archived_and_data_preserved(self):
        from stepahead_lab.paper import _separate_legacy_clockless_snapshots
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'snapshots.jsonl'
            prior=b'{"kind":"snapshot","up":{"source_ts":1800000080}}\n'
            p.write_bytes(prior)
            moved=_separate_legacy_clockless_snapshots(p)
            self.assertEqual(moved.name,'snapshots_v1_0_pre_clock_fix.jsonl')
            self.assertEqual(moved.read_bytes(),prior)
            self.assertFalse(p.exists())
            p.write_text('{"kind":"snapshot","clock":{"offset":26}}\n')
            self.assertIsNone(_separate_legacy_clockless_snapshots(p))

    def test_legacy_archive_collision_is_never_overwritten(self):
        from stepahead_lab.paper import _separate_legacy_clockless_snapshots
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'snapshots.jsonl'
            p.write_text('{"kind":"snapshot","up":{}}\n')
            arc=Path(td)/'snapshots_v1_0_pre_clock_fix.jsonl'
            arc.write_text('untouched')
            with self.assertRaises(ValueError):_separate_legacy_clockless_snapshots(p)
            self.assertEqual(arc.read_text(),'untouched')
            self.assertTrue(p.exists())
