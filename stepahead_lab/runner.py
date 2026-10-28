"""One-command, foreground, paper-only research service.

All network interactions are public GET requests through the allowlisted client.
The local dashboard is a separate daemon thread bound to loopback.
"""
from __future__ import annotations

from pathlib import Path
from threading import Thread
import time

from .config import TARGET_WALLET
from .dashboard import make_server, serve_on
from .http import PublicHTTP
from .monitor import snapshot as monitor_snapshot
from .paper import observe
from .settlement import settle_file
from .paper import _append_jsonl
from decimal import Decimal as D


def run(*, root: Path, cycles: int = 0, polls_per_cycle: int = 15,
        interval: float = 4.0, quantity: D = D('25'), min_edge_pct: D = D('0.2'),
        buffer: D = D('0.002'), port: int = 8765,
        dashboard: bool = True, wallet: str = TARGET_WALLET,
        client: PublicHTTP | None = None) -> dict:
    """Run indefinitely when cycles=0, or a bounded number for smoke tests.

    Never silently places any order. Existing counterfactual paper budget persists.
    """
    if cycles < 0 or polls_per_cycle < 2 or interval < 0.5:
        raise ValueError('Invalid service limits')
    if quantity <= 0 or min_edge_pct < 0 or buffer < 0:
        raise ValueError('Invalid model configuration')
    if not (1024 <= port <= 65535):
        raise ValueError('Bad local dashboard port')
    root.mkdir(parents=True, exist_ok=True)
    server = None
    if dashboard:
        try:
            server = make_server(root, port)
        except OSError as exc:
            raise RuntimeError(f'Cannot bind dashboard to 127.0.0.1:{port}. '
                               'Another StepAhead instance may be running; stop the old instance '
                               'or use --port to select another port.') from exc
        Thread(target=serve_on, args=(server,port), kwargs={'open_browser':True}, daemon=True).start()
    client = client or PublicHTTP()
    completed=0; errors=[]; total_snap=0; total_pairs=0
    print('StepAhead Lab service: public reads + paper hypotheses ONLY. Ctrl+C to stop.',flush=True)
    last_clock_sync = None
    while cycles==0 or completed<cycles:
        if hasattr(client, 'ensure_clock'):
            try:
                clock_report = client.ensure_clock()
                if last_clock_sync != getattr(client.clock, 'sample', None):
                    last_clock_sync = client.clock.sample
                    _append_jsonl(root/'clock_calibrations.jsonl', clock_report)
                    print('Venue clock:', clock_report, flush=True)
                    if abs(clock_report.get('offset_venue_minus_host_seconds', 0)) > 5:
                        print('CLOCK WARNING: Windows time differs materially from venue. '
                              'Research uses a calibrated venue clock; fix Windows Time sync.',flush=True)
            except (OSError,RuntimeError,ValueError) as exc:
                errors.append('clock: '+str(exc)[:200])
                print('Venue clock unavailable; paper collector will fail closed:',str(exc)[:200],flush=True)
                # The profile is still public but market decisions without
                # venue time must not proceed in the same cycle.
                completed += 1
                time.sleep(max(5., interval))
                continue
        try:
            data = monitor_snapshot(client, root/'profile_snapshots.jsonl',wallet)
            apnl = data.get('snapshot',{}).get('profile',{}).get('all_time_pnl')
            val = apnl.get('economic_pnl') if isinstance(apnl,dict) else None
            print('Target public wallet economic P&L (not bot):',val,flush=True)
        except (OSError,RuntimeError,ValueError) as exc:
            errors.append('profile: '+str(exc)[:200])
            print('Profile sample unavailable:',str(exc)[:200],flush=True)
        try:
            result=observe(client, polls=polls_per_cycle, interval=interval, quantity=quantity,
                           min_edge_pct=min_edge_pct, buffer=buffer,
                           directory=root/'paper',verbose=True)
            total_snap += result['snapshots_recorded']
            total_pairs += result['new_hypothetical_pairs']
        except (OSError,RuntimeError,ValueError) as exc:
            errors.append('paper: '+str(exc)[:200])
            print('Paper collector unavailable:',str(exc)[:200],flush=True)
        try:
            report=settle_file(client,root/'paper'/'paper_pairs.jsonl',root/'paper'/'settlements.json')
            print('Counterfactual settlement:',report,flush=True)
        except (OSError,RuntimeError,ValueError) as exc:
            errors.append('settlement: '+str(exc)[:200])
            print('Settlement check unavailable:',str(exc)[:200],flush=True)
        completed+=1
        if cycles==0 and total_snap==0:
            # If API has been unreachable, be gentle rather than retrying in a hot loop.
            time.sleep(max(5., interval))
    return {'mode':'PAPER_ONLY','completed_cycles':completed,'public_book_snapshots':total_snap,
            'new_counterfactual_pairs':total_pairs,'errors':errors[-15:],
            'real_orders':0,'dashboard':f'http://127.0.0.1:{port}/' if dashboard else None}
