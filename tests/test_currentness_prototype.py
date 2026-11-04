"""Small differential fixtures only; the explicit CLI owns the full sweep."""

import subprocess
import sys
from pathlib import Path


def test_small_adversarial_fixtures():
    root = Path(__file__).resolve().parents[1]
    code = r"""
import sys, json, sqlite3, tempfile, os
from pathlib import Path
sys.path.insert(0, 'scripts')
import benchmark_currentness_prototype as p
sys.path.insert(0, str(p.base.PIN / 'src'))
from sabermetrics.assistant_store import AssistantStore
from sabermetrics.assistant_jobs import PROCESS_ID
from sabermetrics import db

def outcome(fn):
    try: return ('value', fn('owner','deck','turn'))
    except Exception as e: return (type(e).__name__, str(e))

with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'test.sqlite'
    def reset(payload={'busy':True,'turn_id':'turn'}):
        for suffix in ('','-wal','-shm'):
            Path(str(path)+suffix).unlink(missing_ok=True)
        p.base.fixture(path,payload)
        with db.connect(path): pass
    def compare():
        store=object.__new__(AssistantStore); store.db_path=path
        candidate=p.active_factory(path,store.active)
        assert outcome(store.active)==outcome(candidate), (outcome(store.active),outcome(candidate))
        return candidate
    def mutate(sql, values=()):
        with sqlite3.connect(path) as conn: conn.execute(sql,values)
    for payload in ({'busy':False,'turn_id':'turn'}, {'busy':True,'turn_id':'other'},
                    {'busy':1,'turn_id':'turn'}, {'busy':[]}, {}, [], None):
        reset(payload); compare()
    for raw in ('{', '{"busy":false,"busy":true,"turn_id":"turn"}',
                '{"busy":true,"turn_id":"turn","turn_id":"other"}'):
        reset(); mutate('UPDATE build_assistant_sessions SET payload_json=?',(raw,)); compare()
    for sql in (
        'DELETE FROM build_assistant_sessions', 'DELETE FROM deck_documents',
        "UPDATE deck_documents SET owner_id='other'",
        "UPDATE build_assistant_jobs SET owner_id='other'",
        "UPDATE build_assistant_jobs SET id='other'",
        "UPDATE build_assistant_jobs SET state='cancelled'",
        "UPDATE build_assistant_jobs SET state='completed'",
        "UPDATE build_assistant_jobs SET state='running',lease_owner='dead:owner',lease_deadline=9999999999",
        "UPDATE deck_documents SET revision=2",
    ):
        reset(); mutate(sql); compare()
    reset(); mutate("UPDATE build_assistant_jobs SET state='running',lease_owner=?,lease_deadline=9999999999",(str(os.getpid())+':wrong-identity',)); compare()
    reset(); mutate("UPDATE build_assistant_jobs SET lease_owner='dead',lease_deadline=0"); compare()
    for deadline in (0,9999999999):
        reset(); mutate("UPDATE build_assistant_jobs SET state='running',lease_owner=?,lease_deadline=?",(PROCESS_ID,deadline)); compare()
    # One retained callable must see every committed transition and replacement.
    reset(); candidate=compare(); assert candidate('owner','deck','turn')
    mutate("UPDATE build_assistant_jobs SET state='cancelled'")
    assert not candidate('owner','deck','turn')
    mutate("UPDATE build_assistant_jobs SET state='queued'")
    assert candidate('owner','deck','turn')
    mutate('CREATE TABLE unrelated(value TEXT)'); mutate("INSERT INTO unrelated VALUES ('x')")
    assert candidate('owner','deck','turn')
    replacement=Path(directory)/'replacement.sqlite'
    p.base.fixture(replacement,{'busy':False,'turn_id':'turn'})
    os.replace(replacement,path)
    assert not candidate('owner','deck','turn')
    reset()
    # Actual compiler/search checkpoints retained, counts and manifests matched.
    rows=[p.base.run_one(path,p.base.inputs(),warm,False,False,factory)
          for warm in (False,True) for factory in (None,p.active_factory)]
    for a,b in (rows[:2],rows[2:]):
        assert a['manifest_hash']==b['manifest_hash']
        assert a['callbacks']==b['callbacks']
    assert rows[0]['callbacks']==445 and rows[2]['callbacks']==73
    reset(); mutate('UPDATE deck_documents SET revision=2')
    for factory in (None,p.active_factory):
        try: p.base.run_one(path,p.base.inputs(),False,False,False,factory)
        except RuntimeError as e: assert str(e)=='revision changed'
        else: raise AssertionError('revision ignored')
    # Cancel at an original callback: same stop point and exception behavior.
    for factory in (None,p.active_factory):
        reset(); seen=[0]
        def cancelling(path,original):
            guard=factory(path,original) if factory else original
            def call(*args):
                seen[0]+=1
                if seen[0]==3: mutate("UPDATE build_assistant_jobs SET state='cancelled'")
                return guard(*args)
            return call
        try: p.base.run_one(path,p.base.inputs(),False,False,False,cancelling)
        except RuntimeError as e: assert str(e)=='inactive fixture'
        else: raise AssertionError('cancellation ignored')
        assert seen[0]==3
"""
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)
