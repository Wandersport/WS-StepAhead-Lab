"""No-network regression tests for public activity wallet forensics."""
import json
import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from stepahead_lab.forensics import profile_windows
from stepahead_lab.cli import build_parser

OLD="btc-updown-5m-1793199900"
NEW="btc-updown-5m-1793200200"

def trade(slug,side,outcome,size,price,t):
    return {"type":"TRADE","slug":slug,"timestamp":t,"side":side,
            "outcome":outcome,"size":size,"price":price}

def fixture(root,rows,end=1793200800,cursor="has_more"):
    Path(root,"activity_000000.json").write_text(json.dumps({
        "wallet":"0xe29042f5d913dcc4015aab3455c13c58514ca33f",
        "start":1,"end":end,"page_size":1000,"request_cursor":None,
        "next_cursor":cursor,"rows":rows}),encoding="utf8")

class ForensicsTest(unittest.TestCase):
    def test_pre_fee_pair_cost_fifo_and_exposure(self):
        with TemporaryDirectory() as d:
            fixture(d,[
                trade(NEW,"BUY","Up","10","0.45",1793200220),
                trade(NEW,"BUY","Up","10","0.70",1793200250),
                trade(NEW,"BUY","Down","10","0.52",1793200240),
                trade(OLD,"BUY","Up","4","0.50",1793200000),
                trade(OLD,"BUY","Down","4","0.50",1793200010)])
            r=profile_windows(d)
            self.assertEqual(r["coverage"]["boundary_partial_windows_count"],1)
            self.assertEqual(r["fully_observed_buy_only_eligible_windows"],1)
            self.assertEqual(r["eligible_positive_matched_gross_edge_before_fees"],1)
            q=next(x for x in r["per_window"] if x["slug"]==NEW)
            self.assertEqual(D(q["matched_contract_pairs"]),D(10))
            self.assertEqual(D(q["matched_fifo_purchase_cost_usdc"]),D("9.7"))
            self.assertEqual(D(q["matched_gross_edge_before_fees_and_rebates_usdc"]),D("0.3"))
            self.assertEqual(D(q["unmatched_up_shares"]),D("10"))
            self.assertEqual(D(q["buy_only_gross_profit_if_up_wins_usdc"]),D("3.3"))
            self.assertEqual(D(q["buy_only_gross_profit_if_down_wins_usdc"]),D("-6.7"))
            self.assertEqual(q["first_opposite_side_gap_seconds"],20)

    def test_sells_excluded_from_scenario(self):
        with TemporaryDirectory() as d:
            fixture(d,[trade(NEW,"BUY","Up",2,0.6,1793200240),
                       trade(NEW,"SELL","Down",1,0.8,1793200250),
                       trade(NEW,"BUY","Down",2,0.5,1793200260)],cursor=None)
            r=profile_windows(d)
            self.assertEqual(r["fully_observed_buy_only_eligible_windows"],0)
            self.assertEqual(r["classifications"]["with_sells_excluded_from_buy_only_scenarios"],1)
            self.assertIsNone(r["per_window"][0]["buy_only_gross_profit_if_up_wins_usdc"])

    def test_upper_boundary_incomplete(self):
        with TemporaryDirectory() as d:
            fixture(d,[trade(NEW,"BUY","Up",1,0.4,1793200230),
                       trade(NEW,"BUY","Down",1,0.5,1793200240)],
                    cursor=None,end=1793200250)
            self.assertEqual(profile_windows(d)["fully_observed_buy_only_eligible_windows"],0)

    def test_empty_missing_and_cli(self):
        with TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                profile_windows(d)
            fixture(d,[])
            self.assertEqual(profile_windows(d,top_n=0)["windows"],0)
            with self.assertRaises(ValueError):
                profile_windows(d,top_n=101)
        a=build_parser().parse_args(["activity","forensics","--top","9"])
        self.assertEqual(a.top,9)

if __name__=="__main__":
    unittest.main()
