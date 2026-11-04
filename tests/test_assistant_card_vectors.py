from types import SimpleNamespace

from sabermetrics import db
from scripts.prepare_assistant_card_vectors import prepare_vectors
from tests.test_assistant_deck_intelligence import prepared as _base_prepared

prepared = _base_prepared


def test_offline_vector_refresh_reuses_unchanged_facts_and_invalidates_edits(prepared):
    path, _ = prepared
    calls = []

    def embed(text):
        calls.append(text)
        return [0.5, 1.0]

    encoder = SimpleNamespace(_model_name="test-encoder", embed=embed)
    first = prepare_vectors(path, encoder)
    assert first["embedded"] > 0
    assert first["network_calls"] == 0
    assert first["training_performed"] is False
    calls.clear()
    second = prepare_vectors(path, encoder)
    assert second["embedded"] == 0
    assert second["reused"] == first["embedded"]
    assert not calls
    with db.connect(path) as conn:
        conn.execute("UPDATE cards SET oracle_text='Draw four cards.' WHERE id='draw'")
        conn.commit()
    third = prepare_vectors(path, encoder)
    assert third["embedded"] == 1
    assert third["reused"] == first["embedded"] - 1
