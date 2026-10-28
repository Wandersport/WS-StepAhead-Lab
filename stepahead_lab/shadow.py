"""Delayed, *modelled* sequential taker fills from recorded public REST snapshots.

REST is not synchronized with matching-engine execution. This deliberately
models first-leg fills and unavailable second legs, but cannot certify any
real fill. No exchange endpoints are used here: pure offline replay.
"""
from __future__ import annotations

from collections import defaultdict, Counter
from decimal import Decimal as D
from pathlib import Path

from .paper import _read_jsonl, decode_snapshot
from .strategy import evaluate_pair, sweep_buy
from .config import CRYPTO_FEE_RATE


def _safe_book(market, book, t: float, *, guard: float = 15.,
               max_age: float = 4.) -> bool:
    if not market.start <= t < market.end - guard:
        return False
    if book.observed_at - book.request_started > max_age:
        return False
    if book.source_ts is not None and abs(t - book.source_ts) > max_age:
        return False
    return bool(book.asks)


def _try_buy(market, book, quantity: D, quoted_cash: D,
             max_slippage_per_share: D):
    if not _safe_book(market, book, book.observed_at):
        return None
    fee = CRYPTO_FEE_RATE if market.fees_enabled else D(0)
    fill = sweep_buy(book.asks, quantity, fee)
    if fill is None or fill.paid_usdc > quoted_cash + quantity * max_slippage_per_share:
        return None
    return fill


def _next_after(book_rows: list, timestamp: float):
    for idx, (market, up, down) in enumerate(book_rows):
        if max(up.observed_at, down.observed_at) >= timestamp:
            return idx, market, up, down
    return None


def replay_delayed(snapshot_path: Path, *, quantity: D = D('25'),
                   min_edge_pct: D = D('0.2'), buffer: D = D('0.002'),
                   latency_ms: int = 500, max_slippage_per_share: D = D('0.01'),
                   max_market_cost: D = D('150'), max_total_cost: D = D('2500')) -> dict:
    """One entry candidate per market, with two distinct lagged observations.

    A missing later book means UNOBSERVABLE, not a definite market non-fill.
    A matched visible depth at an observation is still a *modelled* fill.
    """
    if quantity <= 0 or min_edge_pct < 0 or buffer < 0 or latency_ms < 1 or \
            max_slippage_per_share < 0 or max_market_cost <= 0 or max_total_cost <= 0:
        raise ValueError('Invalid shadow replay controls')
    if latency_ms < 100:
        raise ValueError('Minimum model latency is 100ms (REST cannot prove faster execution)')
    if not snapshot_path.is_file():
        raise ValueError('Snapshot file not found')
    groups: dict[str,list] = defaultdict(list)
    for r in _read_jsonl(snapshot_path):
        if r.get('kind') != 'snapshot':
            continue
        m,up,down=decode_snapshot(r)
        groups[m.slug].append((m,up,down))
    records=[]; excluded=Counter(); exposure=D(0)
    for slug in sorted(groups):
        frames=sorted(groups[slug],key=lambda r:max(r[1].observed_at,r[2].observed_at))
        if any(x[0].condition_id!=frames[0][0].condition_id or
               x[0].up_token!=frames[0][0].up_token or x[0].down_token!=frames[0][0].down_token
               for x in frames):
            raise ValueError(f'Market metadata changed within recorded group {slug}')
        prior_good=False; decision=None; trigger_i=None
        prev_ts=None
        for i,(market,up,down) in enumerate(frames):
            t=max(up.observed_at,down.observed_at)
            if prev_ts is not None and t<=prev_ts:
                prior_good=False  # repeated timestamps cannot count as independent confirmations
                continue
            prev_ts=t
            quote=evaluate_pair(market,up,down,quantity,min_edge_pct,buffer,now=t)
            if quote.valid and prior_good:
                decision=quote;trigger_i=i;break
            prior_good=quote.valid
        if decision is None:
            excluded['no_2_persistent_edge_samples']+=1
            continue
        assert trigger_i is not None and decision.up is not None and decision.down is not None
        trigger=decision.timestamp
        later=_next_after(frames[trigger_i+1:],trigger + latency_ms/1000.)
        if later is None:
            excluded['no_later_snapshot_for_first_leg']+=1
            continue
        relative_i,market,up,down=later
        first_i=trigger_i+1+relative_i
        first_side='up' if decision.up.paid_usdc >= decision.down.paid_usdc else 'down'
        f_book=up if first_side=='up' else down
        f_plan=decision.up if first_side=='up' else decision.down
        first=_try_buy(market,f_book,quantity,f_plan.paid_usdc,max_slippage_per_share)
        if first is None:
            excluded['first_leg_does_not_survive_latency_slippage_depth']+=1
            continue
        if first.paid_usdc > max_market_cost or exposure + first.paid_usdc > max_total_cost:
            excluded['risk_cap_first_leg']+=1
            continue
        second_side='down' if first_side=='up' else 'up'
        s_plan=decision.down if second_side=='down' else decision.up
        next_row=_next_after(frames[first_i+1:],max(up.observed_at,down.observed_at) + latency_ms/1000.)
        if next_row is None:
            excluded['unobservable_second_leg_after_first_modelled_fill']+=1
            exposure+=first.paid_usdc
            records.append({'slug':slug,'condition_id':market.condition_id,
                'state':'unobservable_after_first_leg','first_leg':first_side,
                'first_cash_modelled':str(first.paid_usdc),
                'first_net_shares_modelled':str(first.net_shares),
                'actual_orders':0,'warning':'No subsequent book; no outcome inferred'})
            continue
        _,m2,u2,d2=next_row
        s_book=d2 if second_side=='down' else u2
        second=_try_buy(m2,s_book,quantity,s_plan.paid_usdc,max_slippage_per_share)
        if second is None:
            # We cannot assert the exchange would reject the second order;
            # merely show the conservative one-leg modelled exposure.
            records.append({'slug':slug,'condition_id':market.condition_id,
                'state':'modelled_stranded_first_leg','first_leg':first_side,
                'first_cash_modelled':str(first.paid_usdc),
                'first_net_shares_modelled':str(first.net_shares),
                'modelled_worst_case_pnl_usdc':str(-first.paid_usdc),
                'actual_orders':0,'warning':'Second leg not available within observed depth/slippage'})
            exposure+=first.paid_usdc
            continue
        total=first.paid_usdc+second.paid_usdc
        if total>max_market_cost or exposure+total>max_total_cost:
            excluded['risk_cap_second_leg']+=1
            # A realistic first-leg execution cannot be erased by a second-leg budget breach.
            records.append({'slug':slug,'condition_id':market.condition_id,
                'state':'modelled_stranded_budget','first_leg':first_side,
                'first_cash_modelled':str(first.paid_usdc),
                'modelled_worst_case_pnl_usdc':str(-first.paid_usdc),
                'actual_orders':0})
            exposure+=first.paid_usdc
            continue
        ups=first if first_side=='up' else second
        dns=second if first_side=='up' else first
        reserve=quantity*buffer*2
        worst=min(ups.net_shares,dns.net_shares)-total-reserve
        exposure+=total
        records.append({'slug':slug,'condition_id':market.condition_id,
            'state':'two_legs_modelled','first_leg':first_side,
            'up_net_shares_modelled':str(ups.net_shares),
            'down_net_shares_modelled':str(dns.net_shares),
            'modelled_cash_spent_usdc':str(total),
            'stress_buffer_usdc':str(reserve),
            'modelled_worst_case_pnl_usdc':str(worst),
            'modelled_best_case_pnl_usdc':str(max(ups.net_shares,dns.net_shares)-total-reserve),
            'actual_orders':0,
            'warning':'Both fills assumed on delayed public book observations, not proven executions'})
    return {'mode':'OFFLINE_SEQUENTIAL_SHADOW_MODEL',
        'markets_with_snapshots':len(groups), 'modelled_entries':len(records),
        'modelled_two_leg_entries':sum(r['state']=='two_legs_modelled' for r in records),
        'modelled_stranded_entries':sum(r['state'].startswith('modelled_stranded') for r in records),
        'modelled_unobservable_second_legs':sum(r['state']=='unobservable_after_first_leg' for r in records),
        'total_modelled_cost_reservation_usdc':str(exposure),
        'excluded':dict(excluded), 'records':records, 'actual_executed_orders':0,
        'warning':'Not execution-proof or profit validation; REST snapshots are non-atomic and observational'}
