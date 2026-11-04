#!/usr/bin/env python3
"""E002b experimental negative-control prototype. Never used by the application."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile

import benchmark_research_currentness as base

SELECT = """SELECT s.payload_json, j.state, j.lease_owner, j.lease_deadline
FROM deck_documents d LEFT JOIN build_assistant_sessions s
ON s.deck_id=d.id AND s.owner_id=d.owner_id
LEFT JOIN build_assistant_jobs j ON j.deck_id=d.id AND j.owner_id=d.owner_id AND j.id=?
WHERE d.id=? AND d.owner_id=?"""
# Extracted values deliberately cannot authorize currentness: Python JSON semantics
# (including duplicate object keys and exceptions) remain authoritative.
PROJECTION = SELECT.replace('s.payload_json,', "s.payload_json, json_extract(s.payload_json, '$.busy') AS busy_projection, json_extract(s.payload_json, '$.turn_id') AS turn_projection,")


def active_factory(path, original):
    def active(owner, deck, token):
        from sabermetrics.assistant_jobs import PROCESS_ID, expired
        from sabermetrics.assistant_store import DeckNotFound
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(PROJECTION, (token, deck, owner)).fetchone()
            except sqlite3.OperationalError as error:
                if 'malformed JSON' not in str(error):
                    raise
                # Preserve Python JSONDecodeError, including its position/message.
                row = conn.execute(SELECT, (token, deck, owner)).fetchone()
            if row is None:
                raise DeckNotFound('Deck not found.')
            if row['payload_json'] is None:
                return False
            payload = json.loads(row['payload_json'])
            return bool(payload['busy'] and payload['turn_id'] == token
                        and row['state'] in {'queued', 'running'} and not expired(row)
                        and (row['state'] == 'queued' or row['lease_owner'] == PROCESS_ID))
    return active


def benchmark():
    if any(n.startswith('sabermetrics') for n in sys.modules):
        raise RuntimeError('Fresh interpreter required')
    sys.path.insert(0, str(base.PIN / 'src'))
    from sabermetrics import db
    names = ['assistant_store.py', 'assistant_jobs.py', 'db.py', 'assistant_runtime.py',
             'ui/assistant_routes.py', 'assistant_deck_inference.py',
             'assistant_inference_facts.py', 'assistant_inference_search.py']
    commit = '12af094c1a5adeeb2a90949b3a00f530b75f82f0'
    hashes = {}
    for name in names + ['../../../docs/specs/research-assistant/deck-inference-v1.json']:
        relative = 'docs/specs/research-assistant/deck-inference-v1.json' if name.startswith('../') else 'src/sabermetrics/' + name
        data = (base.PIN / relative).read_bytes()
        assert data == subprocess.check_output(['git', 'show', commit + ':' + relative]), relative
        hashes[relative] = hashlib.sha256(data).hexdigest()
    gate = '>=50% median preparation reduction for each matched payload/cache fixture, with no semantic violations; unsupported equivalence prevents acceptance'
    runs = []
    with tempfile.TemporaryDirectory(prefix='e002b-') as directory:
        for size in (29000, 1000000, 5000000):
            payload = dict(busy=True, turn_id='turn', messages=[dict(role='assistant', content='')])
            payload['messages'][0]['content'] = 'x' * (size - len(json.dumps(payload).encode()))
            path = Path(directory) / (str(size) + '.sqlite')
            base.fixture(path, payload)
            with db.connect(path):
                pass
            for repeat in range(-1, 3):
                for warm in (False, True):
                    modes = ['full', 'prototype', 'noop']
                    # Rotate order: each mode occupies each position once in measured repeats.
                    offset = repeat % 3
                    for mode in modes[offset:] + modes[:offset]:
                        row = base.run_one(path, base.inputs(), warm, False, mode == 'noop',
                                           active_factory if mode == 'prototype' else None)
                        if repeat >= 0:
                            runs.append(dict(mode=mode, repeat=repeat, payload_bytes=size, **row))
    summaries = []
    for size in (29000, 1000000, 5000000):
        for warm in (False, True):
            group = [r for r in runs if r['payload_bytes'] == size and r['warm'] == warm]
            for key in ('manifest_hash', 'callbacks', 'compiler_cache_callbacks', 'search_callbacks', 'cache_hit', 'bundle_coverage'):
                assert len({json.dumps(r[key], sort_keys=True) for r in group}) == 1, key
            medians = {mode: statistics.median(r['wall'] for r in group if r['mode'] == mode) for mode in ('full', 'prototype', 'noop')}
            reduction = 1 - medians['prototype'] / medians['full']
            summaries.append(dict(payload_bytes=size, warm=warm, median_seconds=medians,
                                  reduction_fraction=reduction, numerical_gate_pass=reduction >= .5))
    return dict(experiment='E002b', gate_predeclared=gate, accepted=False,
                acceptance_reason='Fresh Opus review pending; general semantic equivalence is unproven.',
                pinned_commit=commit, source_hashes=hashes,
                prototype_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                harness_hash=hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest(),
                input_hash=base.digest(dict(cards=base.inputs(), rules={'sources': []}, sizes=[29000,1000000,5000000])),
                order='Sequential; one discarded cold/warm pass per payload and mode; three measured repeats with cyclic balanced mode order. Cold is compiler cache only, not disk cache.',
                limitations=['Synthetic E002a fixture, not E001 input or QA 15.4s cause; no product gain claim.',
                             'Projection retains Python full decode for exact payload semantics; json_extract still parses/reads JSON and adds work.',
                             'Only active read connection changed to fresh read-only; both original revision checks and all compiler/search callbacks retained.',
                             'Missing database, locking, SQLite JSON extensions/nonstandard JSON, concurrent deletion/replacement between the original three reads remain unproven equivalence boundaries.',
                             'Fresh connection observes replacement between callbacks; database identity detection and canonical-source validation are separate unresolved critical checks.',
                             'No runtime integration. No automatic acceptance. No children/network/model calls; requested fresh Opus adversarial review remains pending.'],
                fixture_cleanup=not Path(directory).exists(), summaries=summaries, runs=runs)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = benchmark()
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summaries'], indent=2))
