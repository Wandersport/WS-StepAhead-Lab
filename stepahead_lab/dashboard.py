"""Loopback-only, read-only research dashboard; no remote network listener."""
from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from pathlib import Path
from collections import Counter
from threading import Thread
import webbrowser

from .paper import _read_jsonl

DASHBOARD_HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>StepAhead Lab | Paper Research</title>
<style>
:root{color-scheme:dark}body{font-family:system-ui,-apple-system,sans-serif;background:#0b101a;color:#e4edf9;max-width:1120px;margin:auto;padding:28px 20px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:2rem}.muted{color:#99a8bd}#status{font-weight:650;color:#58d9ba}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:22px 0}
.card,.section{background:#151f30;border:1px solid #293955;border-radius:12px;padding:20px}
.number{font-size:1.55rem;font-weight:750;overflow-wrap:anywhere;margin-top:12px}
small{font-size:.78rem}.section{margin:18px 0}h2{margin:0 0 12px;font-size:1.25rem}
table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #2b374b;text-align:left}th{font-size:.85rem;color:#9fb2d1}
.chart{width:100%;height:150px;display:block}a{color:#8ac7ff}
.warning{background:#302419;border:1px solid #81612c;padding:15px;border-radius:10px;color:#ffe0aa}
</style></head><body><header><div><h1>StepAhead Lab</h1><div class="muted">Independent Polymarket research dashboard</div></div><div id="status">PAPER ONLY · NO ORDERS</div></header>
<p class="warning">All local trade signals and settlements are counterfactual. Zero real executions; not a verified profitable clone. Displayed public wallet P&L is the target trader's, not yours.</p>
<div class="cards"><div class="card"><small class="muted">Target reported cumulative P&L</small><div class="number" id="pnl">—</div></div><div class="card"><small class="muted">Target trading volume (USDC)</small><div class="number" id="vol">—</div></div><div class="card"><small class="muted">Book snapshots captured</small><div class="number" id="snaps">0</div></div><div class="card"><small class="muted">Hypothetical pairs logged</small><div class="number" id="pairs">0</div></div></div>
<div class="section"><h2>Target P&L observations</h2><svg id="chart" class="chart" viewBox="0 0 800 150" preserveAspectRatio="none" role="img" aria-label="Target cumulative PNL chart"></svg><p class="muted" id="history">No observations yet.</p></div>
<div class="section"><h2>Research status</h2><table><tbody><tr><th>Verified settlement outcomes</th><td id="settled">0</td></tr><tr><th>Unresolved paper markets</th><td id="pending">0</td></tr><tr><th>Stress-adjusted counterfactual P&L</th><td id="theory">—</td></tr><tr><th>Observed at</th><td id="time">—</td></tr><tr><th>Venue minus Windows clock</th><td id="clock">—</td></tr><tr><th>Last UP/DOWN venue event age</th><td id="ages">—</td></tr><tr><th>Last quote decision</th><td id="reason">—</td></tr><tr><th>Upcoming-market snapshots captured</th><td id="upcoming">0</td></tr><tr><th>Recent decision distribution</th><td id="reasons">—</td></tr></tbody></table></div>
<div class="muted">Local dashboard · refreshes every 15 seconds · public GET-only API collectors · read-only browser endpoints</div>
<script>
function set(id,value){document.getElementById(id).textContent=value}
function fmt(n){if(n===null||n===undefined||!Number.isFinite(Number(n)))return '—';return '$'+Number(n).toLocaleString('en-US',{maximumFractionDigits:2})}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw new Error(r.status);const x=await r.json();
set('pnl',fmt(x.target_pnl));set('vol',fmt(x.target_volume));set('snaps',x.snapshot_count);set('pairs',x.paper_pair_count);set('settled',x.settled_count);set('pending',x.pending_count);set('theory',fmt(x.theoretical_settled_profit));set('time',x.latest_observed_utc||'—');
set('clock',x.clock_offset_s===null||x.clock_offset_s===undefined?'—':Number(x.clock_offset_s).toFixed(2)+'s');
set('ages',(x.last_book_ages?.up??'—')+'s / '+(x.last_book_ages?.down??'—')+'s');
set('reason',x.last_decision||'—');set('upcoming',x.upcoming_snapshot_count??0);
set('reasons',Object.entries(x.recent_decisions||{}).sort((a,b)=>b[1]-a[1]).slice(0,5).map(p=>p[0]+' '+p[1]).join(' · ')||'—');
const y=x.pnl_series||[]; const chart=document.getElementById('chart');chart.replaceChildren();
if(y.length>1){let vals=y.map(v=>v[1]);let a=Math.min(...vals),b=Math.max(...vals);if(b===a)b+=1;
let pts=y.map((v,i)=>[10+i*780/(y.length-1),135-(v[1]-a)*120/(b-a)]);
const p=document.createElementNS('http://www.w3.org/2000/svg','polyline');p.setAttribute('points',pts.map(v=>v.join(',')).join(' '));p.setAttribute('fill','none');p.setAttribute('stroke','#58d9ba');p.setAttribute('stroke-width','3');chart.append(p);
set('history',y.length+' saved samples · min '+fmt(a)+' · max '+fmt(Math.max(...vals)))}else set('history',y.length+' saved sample(s)');}
catch(e){set('history','Connection interrupted (automatic 15s retry): '+e.message)}}
refresh();setInterval(refresh,15000);
</script></body></html>'''


def _iter(path: Path):
    """Only parse complete JSONL records, so concurrent writes cannot break UI."""
    if not path.is_file():
        return
    with path.open('rb') as f:
        for line in f:
            if not line.endswith(b'\n'):
                break
            if line.strip():
                yield json.loads(line)


def _tail(path: Path, count: int = 300):
    """Read recent complete records without loading multi-gigabyte history."""
    if not path.is_file():
        return []
    with path.open('rb') as f:
        f.seek(0, 2)
        size = f.tell()
        pos = size
        blocks = []
        n_newlines = 0
        while pos and n_newlines <= count:
            n = min(65536, pos)
            pos -= n
            f.seek(pos)
            chunk = f.read(n)
            blocks.append(chunk)
            n_newlines += chunk.count(b'\n')
    data = b''.join(reversed(blocks))
    lines = data.split(b'\n')
    # If a file ends midway through a record, exclude its incomplete tail.
    if data.endswith(b'\n'):
        lines.pop()
    else:
        lines.pop()
    if pos > 0:
        lines = lines[1:]  # first record may start part-way through a line
    result=[]
    for line in lines[-count:]:
        if line.strip():
            result.append(json.loads(line))
    return result


_COUNTER_CACHE: dict[str, tuple[int,int,int]] = {}


def _count_lines(path: Path) -> int:
    """Cache append-only file count, reading only bytes added since last call."""
    if not path.is_file():
        return 0
    st = path.stat()
    key = str(path.resolve())
    offset, total, mtime_ns = _COUNTER_CACHE.get(key, (0, 0, 0))
    if st.st_size < offset or (st.st_size == offset and st.st_mtime_ns != mtime_ns):
        offset, total = 0, 0
    if st.st_size > offset:
        with path.open('rb') as f:
            f.seek(offset)
            while True:
                chunk = f.read(1024 * 512)
                if not chunk: break
                total += chunk.count(b'\n')
        offset = st.st_size
    _COUNTER_CACHE[key] = offset, total, st.st_mtime_ns
    return total


def _pnl(profile: dict):
    raw = profile.get('profile', {}).get('all_time_pnl', {})
    if isinstance(raw, dict):
        for key in ('economic_pnl','realized_pnl','position_pnl'):
            if raw.get(key) is not None:
                try: return float(raw[key])
                except (TypeError, ValueError): pass
    return None


def summarize_data(root: Path) -> dict:
    profile_path = root / 'profile_snapshots.jsonl'
    history = _tail(profile_path, 300)
    latest = history[-1] if history else {}
    series = [(int(x.get('observed_at',0)), v) for x in history if (v:=_pnl(x)) is not None]
    snapshot_path = root/'paper'/'snapshots.jsonl'
    count_snap = _count_lines(snapshot_path)
    pair_count = _count_lines(root/'paper'/'paper_pairs.jsonl')
    upcoming_count = _count_lines(root/'prewindow'/'snapshots.jsonl')
    recent = _tail(snapshot_path, 250)
    last_book = recent[-1] if recent else {}
    decision_counts = Counter(x.get('quote', {}).get('reason', 'unknown') for x in recent)
    clock = last_book.get('clock') or {}
    last_ages = last_book.get('source_age_seconds') or {}
    state = {}
    settlements_path = root/'paper'/'settlements.json'
    if settlements_path.exists():
        state = json.loads(settlements_path.read_text(encoding='utf8'))
    records = state.get('settlements',{})
    if not isinstance(records,dict): records={}
    return {"target_pnl": _pnl(latest), "target_volume":latest.get('volume',{}).get('volume_usdc'),
        "snapshot_count":count_snap,"paper_pair_count":pair_count,
        "upcoming_snapshot_count":upcoming_count,"settled_count":len(records),
        "pending_count":max(0,pair_count-len(records)),
        "clock_offset_s":clock.get('offset_venue_minus_host_seconds'),
        "last_book_ages":last_ages,
        "last_decision":last_book.get('quote',{}).get('reason'),
        "recent_decisions":dict(decision_counts),
        "theoretical_settled_profit":state.get('total_stress_adjusted_COUNTERFACTUAL_profit_usdc'),
        "latest_observed_utc":latest.get('observed_at_utc'),"pnl_series":series,
        "mode":"PAPER_ONLY","actual_orders":0}


def make_server(root: Path, port: int = 8765) -> ThreadingHTTPServer:
    if port != 0 and not 1024 <= port <= 65535:
        raise ValueError('Port must be 1024..65535 (or 0 for tests)')
    root = root.resolve()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                body = DASHBOARD_HTML.encode('utf8'); kind='text/html; charset=utf-8'
            elif self.path == '/api/status':
                try:
                    body=json.dumps(summarize_data(root),allow_nan=False).encode('utf8')
                except (OSError,ValueError) as exc:
                    self.send_error(500, 'Unable to read local data');return
                kind='application/json; charset=utf-8'
            else:
                self.send_error(404);return
            self.send_response(200)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            self.end_headers();self.wfile.write(body)
        def log_message(self, format, *args):
            pass
    class ExclusiveLoopbackHTTPServer(ThreadingHTTPServer):
        """Reserve the dashboard port exclusively across Windows and POSIX.

        Winsock SO_REUSEADDR does *not* have POSIX semantics and may allow
        another listener to bind an already used port. Windows explicitly
        supports SO_EXCLUSIVEADDRUSE, which must be set before bind().
        """
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET,
                                       socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    return ExclusiveLoopbackHTTPServer(('127.0.0.1',port),Handler)


def serve_on(server: ThreadingHTTPServer, port: int, *, open_browser: bool = True) -> None:
    url=f'http://127.0.0.1:{port}/'
    print('Local read-only dashboard:',url,flush=True)
    if open_browser:
        Thread(target=lambda:webbrowser.open(url), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def serve(root: Path, port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(root, port)
    serve_on(server, port, open_browser=open_browser)
