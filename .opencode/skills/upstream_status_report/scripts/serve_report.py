#!/usr/bin/env python3
"""Serve the generated report.html over HTTP so others can view it.

Serves the data directory on 0.0.0.0:<port> and prints shareable URLs
(all non-loopback IPv4 addresses of this host). Point people at
http://<your-ip>:<port>/report.html

Usage:
    python3 serve_report.py                 # port 8000
    python3 serve_report.py --port 9000
    python3 serve_report.py --report report.html
    python3 serve_report.py --bind 127.0.0.1  # local only
"""
import argparse, http.server, os, socket, functools

def local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    # also the address used to reach an external host (best public-facing guess)
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

    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    if not os.path.exists(args.report):
        print(f'! {args.report} not found in {here} — run build_report.sh first')

    Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=here)
    httpd = http.server.ThreadingHTTPServer((args.bind, args.port), Handler)

    print(f'serving {here} on port {args.port} (bind {args.bind})')
    print('shareable URLs:')
    print(f'  http://localhost:{args.port}/{args.report}')
    for ip in local_ips():
        print(f'  http://{ip}:{args.port}/{args.report}')
    print('\nfor people outside your network, expose with a tunnel, e.g.:')
    print(f'  ngrok http {args.port}        # or: cloudflared tunnel --url http://localhost:{args.port}')
    print('\nCtrl-C to stop.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped.')
        httpd.shutdown()

if __name__ == '__main__':
    main()
