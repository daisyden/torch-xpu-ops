# Troubleshooting

## `REAL CHANGE` equals `CHANGE`

The server is stale. `server.py`, `matcher.py` and `prdata.py` are loaded into
memory at startup, so a running process keeps serving old API responses even
though `static/*` (HTML/CSS/JS) is re-read from disk on every request — which
makes the frontend look updated while the API is hours behind.

```bash
./status.sh      # says STALE if the sources are newer than the process
./restart.sh     # replaces the process and probes the API to confirm
```

The UI also detects this and shows *"server is out of date"* in the status bar.
`restart.sh` verifies the port is actually free before binding and exits non-zero
if the server did not come up.

To confirm from the command line:

```bash
python3 dev/test_api_contract.py --port 8765
```

It asserts `REAL CHANGE < CHANGE` against the **running** server, which the
in-process tests structurally cannot do.

## "This site can't be reached" / connection refused

The server binds `0.0.0.0` by default, which covers loopback, the network IP and
the hostname. If it was started with `HOST=127.0.0.1` it will refuse connections
from other machines.

```bash
./status.sh      # prints an HTTP code for 127.0.0.1, the LAN IP and the FQDN
```

Prefer the FQDN in shared links: the IP is DHCP-assigned and can change.

## HTTP 403 on a URL that should work

A corporate proxy is intercepting localhost. On Linux:

```bash
export no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost
```

On Windows: Settings → Network & Internet → Proxy → Edit → add
`127.0.0.1;localhost` (plus the server's IP) to the exceptions. Chrome/Edge
usually bypass proxies for localhost automatically.

All the helper scripts pass `--noproxy '*'` to `curl` for this reason.

## `Address already in use`

Something still holds the port — often a previous instance that a `pkill` pattern
missed. The server runs as `python3 -W ignore server.py --host ...` from its own
directory, so its command line does **not** contain the directory name; matching
on `refactor-review/server.py` finds nothing.

```bash
./stop.sh                 # matches on script name + port, and on whoever holds the port
PORT=8899 ./stop.sh       # a specific port
./stop.sh --all           # every review server on this machine
ss -ltnp | grep 8765      # see what is actually listening
```

## Panes 2 and 3 are blank

Open DevTools (F12) → Console. An uncaught JavaScript error aborts the render
loop mid-build and leaves the panes empty while pane 1 still works.

If it is a script error, `./selftest.sh` reproduces it headlessly:
`dev/test_ui_e2e.js` loads the real `app.js` and fails on any uncaught error.

Also try a hard refresh (`Ctrl+Shift+R`) — the browser caches `app.js`.

## The Refactor map will not close

Fixed, but for the record: an author `display: flex` rule outranks the HTML
`hidden` attribute (which is only a UA-stylesheet `display: none`), so the box
stayed visible even though the close handler ran. `app.css` now contains
`[hidden] { display: none !important; }`. `dev/test_overlay.js` guards it.

## Analysis is slow on a huge file

Files like `common_methods_invocations.py` (27k lines) take a few seconds on the
first `/api/file` call; results are cached afterwards. Two mitigations:

- Set `REFACTOR_REVIEW_CLONE` so blobs come from `git` instead of the API.
- The line map is computed once per file and encoded compactly (one character per
  line for verdicts, word-segments only where lines differ), so metadata stays
  around 0.2 MB even for 27k lines.

## Line numbers look wrong / colours are nonsense

GitHub **truncates very large diffs**, leaving later hunks with line numbers that
no longer match the files. The tool detects this (`FileDiff.verify`) and rebuilds
the diff locally from the real base/head blobs (`prdata.rebuild_diff`), printing:

```
! diff for <path> is truncated/stale; rebuilding locally
```

If you see wrong numbers *without* that message, run
`python3 dev/_validate_linemap.py <pr>` — it checks array lengths, pointer
symmetry and that the diff's own context lines are marked identical.

## `gh` errors / rate limits

```bash
gh auth status
```

The tool caches PR metadata, diffs and blobs under
`~/.cache/pytorch-refactor-review` (override with `REFACTOR_REVIEW_CACHE`), so a
reopened PR costs no API calls. The `↻` button forces a re-fetch — note that with
several users sharing a server, one person's refresh re-fetches for everyone.

## Several users interfering with each other

They should not: the PR is in the URL and there is no per-user server state.

```bash
python3 dev/test_concurrency.py --port 8765
```

checks that concurrent users on different PRs get correct data, that a cached
request is not blocked by another user's heavy analysis, and that users do not
evict each other's cached work.

If several people routinely analyse *large* files simultaneously, the GIL makes
those cold analyses interleave rather than use multiple cores — run one server per
user on different ports instead.
