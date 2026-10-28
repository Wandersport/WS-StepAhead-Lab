from __future__ import annotations
import tempfile, unittest, json
from pathlib import Path
from decimal import Decimal as D
from stepahead_lab.markets import Market,Book,Level
from stepahead_lab.strategy import evaluate_pair
from stepahead_lab.paper import _append_jsonl, snapshot_record, _serialize
from stepahead_lab.shadow import replay_delayed

START=1800000000
M=Market(f'btc-updown-5m-{START}','0x'+'a'*64,'111','222',START,START+300,True,'BTC 5min')


def add(p,t,up_price='0.40',dn_price='0.52',up_depth='100',dn_depth='100'):
    a=Book('111',(),(Level(D(up_price),D(up_depth)),),t-.1,t,None)
    b=Book('222',(),(Level(D(dn_price),D(dn_depth)),),t-.1,t,None)
    q=evaluate_pair(M,a,b,now=t)
    _append_jsonl(p,_serialize(snapshot_record(M,a,b,q)))


class ShadowTests(unittest.TestCase):
    def test_two_distinct_lagged_books_model_both_legs(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'snap.jsonl'
            for x in (60,61,62,63):add(p,START+x)
            r=replay_delayed(p)
            self.assertEqual(r['modelled_two_leg_entries'],1)
            self.assertEqual(r['modelled_stranded_entries'],0)
            self.assertEqual(r['actual_executed_orders'],0)
            self.assertEqual(r['records'][0]['first_leg'],'down')
            self.assertEqual(r['records'][0]['state'],'two_legs_modelled')

    def test_second_leg_suddenly_unavailable_is_not_arb(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'snap.jsonl'
            add(p,START+60);add(p,START+61)
            add(p,START+62)
            add(p,START+63,up_depth='2')
            r=replay_delayed(p)
            self.assertEqual(r['modelled_two_leg_entries'],0)
            self.assertEqual(r['modelled_stranded_entries'],1)
            self.assertEqual(r['records'][0]['state'],'modelled_stranded_first_leg')
            self.assertLess(D(r['records'][0]['modelled_worst_case_pnl_usdc']),0)

    def test_missing_followup_does_not_claim_failure_or_fill(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'s.jsonl'
            for x in (60,61,62):add(p,START+x)
            r=replay_delayed(p)
            self.assertEqual(r['modelled_unobservable_second_legs'],1)
            self.assertEqual(r['modelled_stranded_entries'],0)
            self.assertEqual(r['actual_executed_orders'],0)

    def test_price_slippage_and_risk_limit(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'s.jsonl'
            for x in (60,61,62):add(p,START+x)
            add(p,START+63,up_price='0.90')
            r=replay_delayed(p)
            self.assertEqual(r['modelled_stranded_entries'],1)
            self.assertEqual(r['modelled_two_leg_entries'],0)
            with self.assertRaises(ValueError):replay_delayed(p,latency_ms=50)
            capped=replay_delayed(p,max_market_cost=D('3'))
            self.assertEqual(capped['modelled_entries'],0)
            self.assertEqual(capped['excluded'].get('risk_cap_first_leg'),1)

if __name__=='__main__':unittest.main()
