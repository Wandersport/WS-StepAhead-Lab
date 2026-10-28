from __future__ import annotations
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from stepahead_lab.http import PublicHTTP, _retry_delay
from stepahead_lab.markets import (Book, Level, Market, parse_market, parse_book,
                                   parse_levels, discover_current)
from stepahead_lab.paper import observe, replay, snapshot_record, decode_snapshot
from stepahead_lab.strategy import sweep_buy, evaluate_pair
from stepahead_lab.wallet import export_wallet, analyze_wallet
from stepahead_lab.config import TARGET_WALLET


START = 1800000000  # UTC aligned to 5 minutes


def market(fees=True):
    return Market(f"btc-updown-5m-{START}", "0x" + "a" * 64, "111", "222",
                  START, START + 300, fees, "BTC up/down")


def book(token, price, size="100", now=START + 60, source=None):
    return Book(token, (), (Level(D(price), D(size)),), now - 0.15, now, source)


class APITest(unittest.TestCase):
    def test_market_mapping_and_validation(self):
        m = market()
        raw = {"slug": m.slug, "conditionId": m.condition_id,
               "outcomes": json.dumps(["Down", "Up"]),
               "clobTokenIds": json.dumps(["222", "111"]),
               "endDate": "2027-01-15T08:05:00Z", "acceptingOrders": True}
        # Use an unambiguous epoch-derived date in the fixture.
        from datetime import datetime, timezone
        raw["endDate"] = datetime.fromtimestamp(m.end, timezone.utc).isoformat()
        parsed = parse_market(raw)
        self.assertEqual(parsed.up_token, "111")
        self.assertEqual(parsed.down_token, "222")
        self.assertIsNone(parse_market({**raw, "outcomes": json.dumps(["Yes", "No"])}))
        self.assertIsNone(parse_market({**raw, "closed": True}))
        self.assertIsNone(parse_market({**raw, "endDate": None}))

    def test_book_unsorted_and_token_mismatch(self):
        raw = {"asset_id": "111", "asks": [{"price": "0.6", "size": "10"},
                    {"price": "0.4", "size": "2"}], "bids": [
                        {"price": "0.2", "size": "2"}, {"price": "0.3", "size": "2"}]}
        parsed = parse_book(raw, "111", START + 1, START + 1.1)
        self.assertEqual(parsed.asks[0].price, D("0.4"))
        self.assertEqual(parsed.bids[0].price, D("0.3"))
        with self.assertRaises(ValueError):
            parse_book(raw, "222", 1, 2)
        self.assertEqual(parse_levels([{"price": "0", "size": "50"}], False), ())

    def test_get_only_allowed_hosts(self):
        h = PublicHTTP(attempts=1)
        with self.assertRaises(ValueError):
            h.get("https://untrusted.example/exfil")
        with self.assertRaises(ValueError):
            h.get("http://data-api.polymarket.com/v2/status")
        self.assertGreater(_retry_delay("2", 0), 0)

    def test_discovery_uses_current_slug_exactly(self):
        class Fake:
            def get(self, url, params):
                self.params = params
                return [{"slug": market().slug, "markets": [{"slug": market().slug,
                    "conditionId": market().condition_id,
                    "clobTokenIds": '["111","222"]', "outcomes": '["Up","Down"]',
                    "endDate": START + 300}]}]
        m = discover_current(Fake(), now=START + 60)
        self.assertEqual(m.slug, market().slug)


class StrategyTest(unittest.TestCase):
    def test_fee_deducts_shares_not_cash(self):
        s = sweep_buy((Level(D("0.50"), D("10")),), D("10"), D("0.07"))
        self.assertEqual(s.paid_usdc, D("5"))
        self.assertEqual(s.buy_fee_usdc_equivalent, D("0.1750"))
        self.assertEqual(s.fee_shares, D("0.3500"))
        self.assertEqual(s.net_shares, D("9.6500"))

    def test_favorable_pair_both_winners_profitable(self):
        m = market()
        q = evaluate_pair(m, book("111", "0.40"), book("222", "0.52"),
                          quantity=D("25"), now=START + 60)
        self.assertTrue(q.valid)
        self.assertGreater(q.up_win_profit, 0)
        self.assertGreater(q.down_win_profit, 0)
        self.assertLess(q.minimum_profit, q.down_win_profit)

    def test_price_sum_under_one_is_not_enough(self):
        m = market()
        q = evaluate_pair(m, book("111", "0.45"), book("222", "0.52"),
                          quantity=D("25"), now=START + 60)
        self.assertFalse(q.valid)
        self.assertLess(q.minimum_profit, 0)

    def test_depth_sweep_and_conservative_buffer(self):
        m = market(False)
        up = Book("111", (), (Level(D("0.3"), D("10")),
                Level(D("0.4"), D("20"))), START + 59, START + 60, None)
        dn = book("222", "0.5")
        q = evaluate_pair(m, up, dn, quantity=D("25"), now=START + 60)
        self.assertEqual(q.up.paid_usdc, D("9"))
        self.assertEqual(q.up.levels_used, 2)
        self.assertTrue(q.valid)
        q2 = evaluate_pair(m, up, dn, quantity=D("25"), now=START + 60,
                           buffer_per_share_per_leg=D("0.20"))
        self.assertFalse(q2.valid)

    def test_reject_skew_stale_expiry_and_no_depth(self):
        m = market()
        a = book("111", "0.4")
        b = book("222", "0.5", now=START + 65)
        self.assertEqual(evaluate_pair(m, a, b, now=START + 65).reason, "snapshot_skew")
        b = book("222", "0.5", source=START + 45)
        self.assertEqual(evaluate_pair(m, a, b).reason, "stale_exchange_book")
        a2 = book("111", "0.4", now=START + 291)
        b2 = book("222", "0.5", now=START + 291)
        self.assertEqual(evaluate_pair(m, a2, b2).reason, "outside_safe_market_window")
        b3 = book("222", "0.5", size="5")
        self.assertEqual(evaluate_pair(m, a, b3).reason, "insufficient_visible_ask_depth")

    def test_recorded_snapshot_roundtrip(self):
        m = market()
        a, b = book("111", "0.4"), book("222", "0.52")
        q = evaluate_pair(m, a, b)
        import stepahead_lab.paper as pap
        rec = json.loads(json.dumps(pap._serialize(snapshot_record(m, a, b, q))))
        x, y, z = decode_snapshot(rec)
        self.assertEqual(x, m)
        self.assertEqual(y.asks[0].price, D("0.4"))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.jsonl"
            p.write_text(json.dumps(rec) + "\n")
            result = replay(p, D("25"), D("0.2"), D("0.002"))
            self.assertEqual(result["paper_signals"], 1)
            self.assertTrue(result["replay_is_not_a_fill_backtest"])


class WalletTest(unittest.TestCase):
    def test_export_is_cursor_resumable_and_analysis_not_pnl(self):
        class Fake:
            def __init__(self): self.calls = []
            def get(self, url, params):
                self.calls.append(params)
                cursor = params.get("cursor")
                row = {"side": "BUY", "outcome": "Up" if cursor is None else "Down",
                       "size": 10, "price": 0.4 if cursor is None else 0.52,
                       "timestamp": START + 50, "event_slug": market().slug,
                       "condition_id": market().condition_id}
                return {"data": [row], "pagination": {"has_more": cursor is None,
                        "next_cursor": "PAGE2" if cursor is None else None}}
        with tempfile.TemporaryDirectory() as td:
            api = Fake()
            a = export_wallet(api, td, max_pages=1)
            self.assertEqual(a["status"], "partial")
            b = export_wallet(api, td, max_pages=1)
            self.assertEqual(b["status"], "complete")
            c = export_wallet(api, td, max_pages=1)
            self.assertEqual(c["new_pages"], 0)
            self.assertEqual(api.calls[1]["cursor"], "PAGE2")
            self.assertEqual(api.calls[0]["taker_only"], "false")
            metrics = analyze_wallet(td)
            self.assertEqual(metrics["btc_markets"], 1)
            self.assertEqual(metrics["markets_buying_both_outcomes"], 1)
            self.assertEqual(metrics["aggregate_end_of_trades_matched_shares"], "10")
            self.assertNotIn("realized_pnl", metrics)

    def test_bad_api_page_not_persisted(self):
        class Bad:
            def get(self, *a): return {"data": [], "pagination": {"has_more": True}}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                export_wallet(Bad(), td, max_pages=1)
            self.assertFalse(list(Path(td).glob("page_*.json")))


class PaperTest(unittest.TestCase):
    def test_paper_requires_2_confirmations_never_executes(self):
        m, a, b = market(), book("111", "0.4"), book("222", "0.52")
        with tempfile.TemporaryDirectory() as td:
            with patch("stepahead_lab.paper.discover_current", return_value=m), \
                 patch("stepahead_lab.paper.fetch_pair", return_value=(a, b)), \
                 patch("stepahead_lab.paper.time.sleep", return_value=None):
                result = observe(object(), polls=3, interval=0.5, quantity=D("25"),
                                 min_edge_pct=D("0.2"), buffer=D("0.002"),
                                 directory=Path(td), verbose=False)
                self.assertEqual(result["new_hypothetical_pairs"], 1)
                self.assertEqual(result["actually_executed_orders"], 0)
                result = observe(object(), polls=3, interval=0.5, quantity=D("25"),
                                 min_edge_pct=D("0.2"), buffer=D("0.002"),
                                 directory=Path(td), verbose=False)
                self.assertEqual(result["new_hypothetical_pairs"], 0)
            data = [json.loads(x) for x in (Path(td) / "paper_pairs.jsonl").read_text().splitlines()]
            self.assertEqual(len(data), 1)
            self.assertFalse(data[0]["actually_executed"])


class MakerTest(unittest.TestCase):
    def test_improve_bid_only_if_not_crossing(self):
        from stepahead_lab.maker import passive_price, assess_maker_candidate
        m = market()
        a = Book("111", (Level(D("0.38"), D("100")),),
                 (Level(D("0.40"), D("100")),), START + 59, START + 60, None)
        b = Book("222", (Level(D("0.51"), D("100")),),
                 (Level(D("0.52"), D("100")),), START + 59, START + 60, None)
        self.assertEqual(passive_price(a), D("0.39"))
        self.assertEqual(passive_price(b), D("0.51"))
        c = assess_maker_candidate(m, a, b, D("25"))
        self.assertTrue(c.candidate)
        self.assertLess(c.worst_only_one_leg_loss, 0)
        self.assertEqual(c.fills_observed, 0)
        self.assertFalse(c.tradable_without_execution_evidence)


if __name__ == "__main__":
    unittest.main()
