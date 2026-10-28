import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from stepahead_lab.activity import export_activity, iter_activity, normalize, profile_activity
from stepahead_lab.monitor import snapshot, fetch_profile, watch
from stepahead_lab.quality import audit_pages
from stepahead_lab.queue_replay import model_passive_fills
from stepahead_lab.config import TARGET_WALLET


class ActivityTests(unittest.TestCase):
    def test_frozen_window_and_crash_resume(self):
        class MockAPI:
            def __init__(self): self.calls=[]
            def get(self, url, params):
                self.calls.append(dict(params))
                cur=params.get('cursor')
                return {'data':[{'timestamp':200,'type':'TRADE','slug':'btc-updown-5m-1800000000',
                                  'side':'BUY','outcome':'Up' if cur is None else 'Down','price':'0.4',
                                  'size':'10','usdc_size':'4'}],
                        'pagination':{'has_more':cur is None,'next_cursor':'end2' if cur is None else None}}
        with tempfile.TemporaryDirectory() as td:
            f=MockAPI()
            x=export_activity(f,td,start=1,end=1000,max_pages=1)
            self.assertEqual(x['status'],'partial')
            with self.assertRaises(ValueError):
                export_activity(f,td,start=1,end=2000,max_pages=1)
            y=export_activity(f,td,start=1,max_pages=1)
            self.assertEqual(y['status'],'complete')
            self.assertEqual(f.calls[0]['end'],f.calls[1]['end'])
            self.assertEqual(len(list(iter_activity(td))),2)
            audit=audit_pages(td,'activity')
            self.assertTrue(audit['complete'], audit)
            rpt=profile_activity(td)
            self.assertEqual(rpt['window_behavior_counts']['observed_buy_both'],1)
            self.assertNotIn('realized_pnl',rpt)

    def test_bad_payload_does_not_commit_and_rebate_normalization(self):
        class Bad:
            def get(self,*args):return {'data':{},'pagination':{}}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                export_activity(Bad(),td,start=1,end=2)
            self.assertFalse(list(Path(td).glob('activity_*.json')))
        r=normalize({'type':'MAKER_REBATE','transaction_hash':'0x001','usdc_size':'0.55'})
        self.assertEqual(r['usdc_size'],'0.55')
        self.assertEqual(r['transaction_hash'],'0x001')

    def test_cursors_detect_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            root.joinpath('page_000000.json').write_text(json.dumps({'wallet':TARGET_WALLET,'page_size':1000,
                'request_cursor':None,'next_cursor':'a','rows':[]}))
            root.joinpath('page_000001.json').write_text(json.dumps({'wallet':TARGET_WALLET,'page_size':1000,
                'request_cursor':'wrong','next_cursor':None,'rows':[]}))
            result=audit_pages(root)
            self.assertFalse(result['complete'])
            self.assertTrue(any('continuity' in x.lower() for x in result['issues']))


class MonitorTests(unittest.TestCase):
    def test_profile_deltas(self):
        class MockAPI:
            def __init__(self):self.n=0
            def get(self,url,params=None):
                if url.endswith('/v2/user-stats'):
                    self.n+=1
                    return {'data':{'trades':20+self.n,'all_time_pnl':{'net':'x'}}}
                if url.endswith('/v2/user-volume'):
                    return {'data':{'volume_usdc':'1000'}}
                return {'data':{'lag':0}}
        with tempfile.TemporaryDirectory() as td:
            f=MockAPI()
            a=snapshot(f,Path(td)/'snap.jsonl',now=1)
            b=snapshot(f,Path(td)/'snap.jsonl',now=2)
            self.assertEqual(b['delta_since_previous_observation']['profile.trades'],'1')
            self.assertEqual(b['delta_since_previous_observation']['volume.volume_usdc'],'0')
            with self.assertRaises(ValueError):
                watch(f,Path(td)/'snap.jsonl',TARGET_WALLET,2,1)


class QueueTests(unittest.TestCase):
    def test_queue_latency_and_partial_fill(self):
        orders=[{'order_id':'a','market_slug':'x','outcome':'Up','placed_ms':100,
                 'price':'0.5','quantity':'10'}]
        prints=[{'market_slug':'x','outcome':'up','taker_side':'SELL','ts_ms':101,
                 'price':'0.5','size':'500'},
                {'market_slug':'x','outcome':'up','taker_side':'SELL','ts_ms':300,
                 'price':'0.5','size':'253'},
                {'market_slug':'x','outcome':'up','taker_side':'SELL','ts_ms':301,
                 'price':'0.5','size':'7'}]
        r=model_passive_fills(orders,prints,latency_ms=100,queue_ahead=D('250'))
        self.assertEqual(r['MODELLED_fully_filled_orders'],1)
        self.assertEqual(r['fill_details'][0]['first_MODELLED_fill_ms'],300)
        self.assertEqual(r['actually_executed_orders'],0)

    def test_opposite_trade_cannot_fill(self):
        orders=[{'order_id':'a','market_slug':'x','outcome':'Up','placed_ms':10,
                 'price':'0.5','quantity':'2'}]
        prints=[{'market_slug':'x','outcome':'down','taker_side':'SELL','ts_ms':100,
                 'price':'0.2','size':'99'},
                {'market_slug':'x','outcome':'Up','taker_side':'BUY','ts_ms':101,
                 'price':'0.5','size':'99'}]
        r=model_passive_fills(orders,prints,latency_ms=0,queue_ahead=D('0'))
        self.assertEqual(r['MODELLED_fully_filled_orders'],0)


if __name__ == '__main__': unittest.main()

class QueueConservationTests(unittest.TestCase):
    def test_shared_print_cannot_double_allocate(self):
        orders=[{'order_id':k,'market_slug':'a','outcome':'up','placed_ms':1,'price':'0.50','quantity':'5','queue_ahead':'0'} for k in ('a','b')]
        p=[{'ts_ms':100,'market_slug':'a','outcome':'up','taker_side':'SELL','price':'0.49','size':'6'}]
        r=model_passive_fills(orders,p,latency_ms=0,queue_ahead=D('0'))
        self.assertEqual(sum(D(z['MODELLED_filled']) for z in r['fill_details']),D('6'))
