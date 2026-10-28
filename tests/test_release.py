"""Release-level boundaries, settlement accounting and zero-live-trading checks."""
from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from stepahead_lab.settlement import resolved_outcome, calculate_counterfactual, settle_file
from stepahead_lab.dashboard import summarize_data
from stepahead_lab.runner import run
from stepahead_lab.cli import main
from stepahead_lab.http import PublicHTTP
from stepahead_lab.paper import _append_jsonl

SLUG = 'btc-updown-5m-1800000000'
COND = '0x' + 'a'*64


def event(*, closed=True, prices='["1","0"]', slug=SLUG, condition=COND):
    return {'slug':slug, 'markets':[{'slug':slug, 'conditionId':condition, 'closed':closed,
             'outcomes':'["Up","Down"]', 'outcomePrices':prices}]}


def entry(*, slug=SLUG, condition=COND, cost='20'):
    return {'kind':'paper_pair','market_slug':slug,'condition_id':condition,
            'actually_executed':False,'all_in_cost':cost,
            'up_win_profit':'2.2','down_win_profit':'1.1'}


class SettlementTests(unittest.TestCase):
    def test_winner_requires_closed_exact_binary_and_matching_condition(self):
        self.assertEqual(resolved_outcome(event(), SLUG, COND), 'up')
        self.assertEqual(resolved_outcome(event(prices='["0","1"]'), SLUG, COND), 'down')
        self.assertIsNone(resolved_outcome(event(closed=False), SLUG, COND))
        self.assertIsNone(resolved_outcome(event(prices='["0.999","0.001"]'), SLUG, COND))
        self.assertIsNone(resolved_outcome(event(prices='["1","1"]'), SLUG, COND))
        self.assertIsNone(resolved_outcome(event(slug=SLUG+'x'), SLUG, COND))
        self.assertIsNone(resolved_outcome(event(), SLUG, '0x'+'b'*64))

    def test_reject_real_execution_claim_and_malformed_cost(self):
        self.assertEqual(calculate_counterfactual(entry(), 'up')['stress_adjusted_counterfactual_profit_usdc'],'2.2')
        with self.assertRaises(ValueError):
            calculate_counterfactual({**entry(),'actually_executed':True},'up')
        with self.assertRaises(ValueError):
            calculate_counterfactual(entry(cost='NaN'),'down')

    def test_settlement_resumes_without_network_replay(self):
        class Fake:
            def __init__(self):self.n=0
            def get(self,url,params):
                self.n+=1;return [event()]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);entries=root/'decisions.jsonl';out=root/'results.json'
            _append_jsonl(entries,entry())
            fake=Fake();r=settle_file(fake,entries,out)
            self.assertEqual((r['settled'],r['pending'],r['actual_trades']), (1,0,0))
            self.assertEqual(r['stress_adjusted_COUNTERFACTUAL_profit_usdc'],'2.2')
            r=settle_file(fake,entries,out)
            self.assertEqual(r['new_settlements'],0)
            self.assertEqual(fake.n,1)
            self.assertEqual(json.loads(out.read_text())['actual_executed_orders'],0)

    def test_unresolved_stays_pending(self):
        class Fake:
            def get(self,*args):return [event(closed=False)]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);ent=root/'paper.jsonl';out=root/'out.json'
            _append_jsonl(ent,entry())
            result=settle_file(Fake(),ent,out)
            self.assertEqual((result['pending'],result['settled']),(1,0))
            self.assertEqual(result['stress_adjusted_COUNTERFACTUAL_profit_usdc'],'0')

    def test_duplicate_entries_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'pairs.jsonl';out=Path(td)/'results.json'
            _append_jsonl(f,entry());_append_jsonl(f,entry())
            with self.assertRaises(ValueError):settle_file(object(),f,out)


class DashboardTests(unittest.TestCase):
    def test_empty_dashboard_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            summary=summarize_data(Path(td))
            self.assertEqual(summary['actual_orders'],0)
            self.assertIsNone(summary['target_pnl'])
            self.assertEqual(summary['paper_pair_count'],0)

    def test_reads_target_and_counterfactual_separately(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            _append_jsonl(root/'profile_snapshots.jsonl',{'observed_at':123,'observed_at_utc':'x',
                'profile':{'all_time_pnl':{'economic_pnl':120.3}},
                'volume':{'volume_usdc':5000}})
            _append_jsonl(root/'paper'/'paper_pairs.jsonl',entry())
            (root/'paper'/'settlements.json').write_text(json.dumps({'settlements':{SLUG:calculate_counterfactual(entry(),'down')},
                'total_stress_adjusted_COUNTERFACTUAL_profit_usdc':'1.1'}))
            r=summarize_data(root)
            self.assertEqual(r['target_pnl'],120.3)
            self.assertEqual(r['theoretical_settled_profit'],'1.1')
            self.assertEqual(r['settled_count'],1)
            self.assertEqual(r['pending_count'],0)


class ReleaseBoundaryTests(unittest.TestCase):
    def test_start_finite_cycle_no_order_access(self):
        with tempfile.TemporaryDirectory() as td,\
             patch('stepahead_lab.runner.monitor_snapshot',return_value={}) as prof,\
             patch('stepahead_lab.runner.observe',return_value={'snapshots_recorded':2,'new_hypothetical_pairs':0}) as obs,\
             patch('stepahead_lab.runner.settle_file',return_value={}) as settled:
            r=run(root=Path(td),cycles=2, dashboard=False, client=object())
            self.assertEqual(r['real_orders'],0)
            self.assertEqual(r['public_book_snapshots'],4)
            self.assertEqual(prof.call_count,2)
            self.assertEqual(obs.call_count,2)
            self.assertEqual(settled.call_count,2)

    def test_cli_offline_overview_is_usable(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(main(['overview','--data',td]),0)

    def test_no_live_order_client_transport(self):
        self.assertFalse(hasattr(PublicHTTP,'post'))
        self.assertFalse(hasattr(PublicHTTP,'delete'))
        self.assertFalse(hasattr(PublicHTTP,'put'))
        with self.assertRaises(ValueError):PublicHTTP().get('https://polymarket.com/anything')


if __name__=='__main__':unittest.main()

class IntegrityTests(unittest.TestCase):
    def test_reconciliation_rejects_modified_entry_after_cache(self):
        class Fake:
            def get(self,*a):return [event()]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);e=root/'entries.jsonl';o=root/'settlements.json'
            _append_jsonl(e,entry())
            settle_file(Fake(),e,o)
            e.write_text(json.dumps(entry(cost='21'))+'\n')
            with self.assertRaises(ValueError):settle_file(Fake(),e,o)

    def test_empty_settlement_dir_still_reports_no_real_trade(self):
        with tempfile.TemporaryDirectory() as td:
            r=settle_file(object(),Path(td)/'absent',Path(td)/'out')
            self.assertEqual(r['settled'],0)
