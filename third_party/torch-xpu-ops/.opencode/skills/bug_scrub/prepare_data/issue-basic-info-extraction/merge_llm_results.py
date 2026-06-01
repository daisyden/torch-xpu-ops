#!/usr/bin/env python3
"""Merge batch_NN.json files in $BUG_SCRUB_DATA_DIR/llm_results/ into
$BUG_SCRUB_DATA_DIR/llm_extracted.json, keyed by issue_id (as string)."""
import json
import os
import sys
from pathlib import Path

_COMMON_DIR = Path(__file__).resolve().parents[2] / "_common"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from paths import DATA_DIR  # type: ignore[reportMissingImports] # noqa: E402

RESULTS_DIR = str(DATA_DIR / "llm_results")
CACHE_PATH = str(DATA_DIR / "llm_extracted.json")

REQUIRED_KEYS = {'issue_id', 'body_hash', 'kind', 'test_cases', 'reproducer',
                 'error_message', 'traceback', 'notes'}

def main():
    try:
        with open(CACHE_PATH) as f:
            cache = json.load(f)
    except FileNotFoundError:
        cache = {}

    merged = 0
    skipped_bad = 0
    files = sorted(f for f in os.listdir(RESULTS_DIR) if f.endswith('.json'))
    for fname in files:
        path = os.path.join(RESULTS_DIR, fname)
        try:
            with open(path) as f:
                rows = json.load(f)
        except json.JSONDecodeError as e:
            print(f'  SKIP {fname}: bad JSON: {e}', file=sys.stderr)
            skipped_bad += 1
            continue
        if not isinstance(rows, list):
            print(f'  SKIP {fname}: not a list', file=sys.stderr)
            skipped_bad += 1
            continue
        for entry in rows:
            if not isinstance(entry, dict) or 'issue_id' not in entry:
                continue
            missing = REQUIRED_KEYS - set(entry.keys())
            if missing:
                entry.setdefault('test_cases', [])
                entry.setdefault('reproducer', '')
                entry.setdefault('error_message', '')
                entry.setdefault('traceback', '')
                entry.setdefault('notes', '')
                entry.setdefault('kind', 'other')
                entry.setdefault('body_hash', '')
            cache[str(entry['issue_id'])] = entry
            merged += 1

    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    print(f'Merged {merged} entries from {len(files)} files into {CACHE_PATH}')
    print(f'Cache now holds {len(cache)} issues')
    if skipped_bad:
        print(f'WARNING: {skipped_bad} files were skipped due to errors')


if __name__ == '__main__':
    main()
