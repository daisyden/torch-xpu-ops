#!/usr/bin/env python3
"""Serve the generated report.html over HTTP so others can view it.

Serves the data directory on 0.0.0.0:<port> and prints shareable URLs
(all non-loopback IPv4 addresses of this host). Point people at
http://<your-ip>:<port>/report.html

The server also exposes a small refresh API used by the "Refresh data" button
in the report:
  POST /api/refresh          -> starts a full pipeline refresh (build_report.sh --all)
  GET  /api/refresh/status    -> {running, ok, started, finished, tail}

Usage:
    python3 serve_report.py                 # port 8000
    python3 serve_report.py --port 9000
    python3 serve_report.py --report report.html
    python3 serve_report.py --bind 127.0.0.1  # local only
"""
import argparse, http.server, os, socket, functools, json, threading, subprocess, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'refresh_web.log')

# refresh job state (single global job; only one refresh at a time)
_JOB = {'running': False, 'ok': None, 'started': None, 'finished': None, 'rc': None}
_PROC = None          # the running build_report.sh Popen (for liveness checks)
_LOCK = threading.Lock()


def _reconcile_locked():
    """Self-heal a stuck 'running' state: if we think a job is running but the
    child process is gone (killed/crashed with no exit captured), mark it failed
    so clients stop polling forever."""
    global _PROC
    if _JOB['running']:
        alive = _PROC is not None and _PROC.poll() is None
        if not alive:
            rc = _PROC.poll() if _PROC is not None else -1
            _JOB.update(running=False, ok=(rc == 0), rc=(rc if rc is not None else -1),
                        finished=datetime.datetime.now().isoformat(timespec='seconds'))


def _run_refresh():
    """Run the full pipeline (build_report.sh --all) capturing output to LOG."""
    global _JOB, _PROC
    with _LOCK:
        if _JOB['running']:
            return
        _JOB.update(running=True, ok=None, finished=None, rc=None,
                    started=datetime.datetime.now().isoformat(timespec='seconds'))
    try:
        with open(LOG, 'w') as fh:
            fh.write(f'=== refresh started {_JOB["started"]} ===\n')
            fh.flush()
            proc = subprocess.Popen(
                ['bash', os.path.join(HERE, 'build_report.sh'), '--all'],
                cwd=HERE, stdout=fh, stderr=subprocess.STDOUT,
                # force unbuffered child output so per-PR progress ([i/N] PR ...)
                # streams into the log live instead of sitting in a block buffer
                # (Python block-buffers stdout when it is a file, not a tty).
                env={**os.environ, 'PYTHONUNBUFFERED': '1'})
            with _LOCK:
                _PROC = proc
            rc = proc.wait()
        with _LOCK:
            _JOB.update(running=False, ok=(rc == 0), rc=rc,
                        finished=datetime.datetime.now().isoformat(timespec='seconds'))
    except Exception as e:
        with open(LOG, 'a') as fh:
            fh.write(f'\n!! refresh crashed: {e}\n')
        with _LOCK:
            _JOB.update(running=False, ok=False, rc=-1,
                        finished=datetime.datetime.now().isoformat(timespec='seconds'))


def _tail(path, n=25):
    try:
        with open(path) as fh:
            return ''.join(fh.readlines()[-n:])
    except FileNotFoundError:
        return ''


class Handler(http.server.SimpleHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip('/') == '/api/refresh':
            with _LOCK:
                already = _JOB['running']
            if not already:
                threading.Thread(target=_run_refresh, daemon=True).start()
                time.sleep(0.3)
            with _LOCK:
                st = dict(_JOB)
            return self._json({'started': not already, **st})
        self.send_error(404)

    def do_GET(self):
        if self.path.rstrip('/') == '/api/refresh/status':
            with _LOCK:
                _reconcile_locked()
                st = dict(_JOB)
            st['tail'] = _tail(LOG)
            return self._json(st)
        return super().do_GET()

    def end_headers(self):
        # report.html changes on refresh; discourage stale caching
        if self.path.endswith('.html'):
            self.send_header('Cache-Control', 'no-cache, must-revalidate')
        super().end_headers()


def local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith('127.'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--bind', default='0.0.0.0', help='interface to bind (default all)')
    ap.add_argument('--report', default='report.html', help='report file to highlight')
    args = ap.parse_args()

    os.chdir(HERE)
    if not os.path.exists(args.report):
        print(f'! {args.report} not found in {HERE} — run build_report.sh first')

    handler = functools.partial(Handler, directory=HERE)
    httpd = http.server.ThreadingHTTPServer((args.bind, args.port), handler)

    print(f'serving {HERE} on port {args.port} (bind {args.bind})')
    print('refresh API: POST /api/refresh, GET /api/refresh/status')
    print('shareable URLs:')
    print(f'  http://localhost:{args.port}/{args.report}')
    for ip in local_ips():
        print(f'  http://{ip}:{args.port}/{args.report}')
    print('\nCtrl-C to stop.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped.')
        httpd.shutdown()


if __name__ == '__main__':
    main()
