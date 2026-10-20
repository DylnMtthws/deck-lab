"""Versioned projections of supplied card facts; generated labels stay separate."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sabermetrics.assistant_strategy import draw_routes, strategy_capabilities

VIEW_VERSION = "assistant-card-views.v1"


def build_card_views(card: dict[str, Any]) -> dict[str, Any]:
    """Project exact supplied text and bounded inferred roles/prerequisites.

    Hash includes all supplied facts, source version and projection version. No
    embedding/model work occurs here; retrieval uses the corpus's pinned vectors.
    Missing provenance remains missing rather than becoming an official source.
    """
    source = {
        key: card.get(key)
        for key in (
            "oracle_id",
            "name",
            "oracle_text",
            "type_line",
            "mana_cost",
            "cmc",
            "mana_value",
            "card_faces",
            "source_url",
            "source_version",
            "oracle_version",
            "oracle_text_truncated",
            "layout",
            "power",
            "toughness",
            "loyalty",
            "defense",
            "keywords",
            "color_identity",
        )
    }
    canonical = json.dumps(
        {"version": VIEW_VERSION, "source": source},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    incomplete = bool(card.get("oracle_text_truncated")) or card.get("layout") not in (
        None,
        "",
        "normal",
        "token",
    )
    routes = [] if incomplete else [route.model_dump() for route in draw_routes(card)]
    views: dict[str, dict[str, Any]] = {
        "printed_effect": {"evidence_class": "supplied_source", "facts": source},
        "functional_role": {
            "evidence_class": "generated_inference",
            "roles": [] if incomplete else sorted(strategy_capabilities(card)),
        },
        "prerequisite": {
            "evidence_class": "generated_inference",
            "draw_routes": routes,
        },
    }
    for name, view in views.items():
        view["view_hash"] = hashlib.sha256(
            (VIEW_VERSION + name + digest).encode()
        ).hexdigest()
    return {
        "version": VIEW_VERSION,
        "source_hash": digest,
        "views": views,
        "limitations": [
            "Supplied text is not independently refreshed or certified.",
            "Role and draw prerequisite recognizers are incomplete; absent labels mean unknown.",
            "Ingredients do not prove availability, legal execution, a loop or deck quality.",
        ],
    }


def retrieve_card_views(
    cards: list[dict[str, Any]],
    query: str,
    *,
    limit: int = 8,
    vector_artifact: dict[str, Any] | None = None,
    encoder: Any = None,
    all_matches: bool = False,
) -> dict[str, Any]:
    """Bounded lexical retrieval across printed text and generated role labels.

    Caller supplies the already legal/current shortlist, not an entire catalog.
    No inferred label is represented as printed evidence or a quality score.
    """
    import re

    if not 1 <= limit <= 8 or len(cards) > 64 or not query.strip() or len(query) > 320:
        raise ValueError(
            "provide at most 64 current cards, 1–320 query characters and limit 1–8"
        )
    terms = set(re.findall(r"\w+", query.casefold()))
    ranked = []
    limitations = [
        "Caller must retain current legal/owner/revision guards; labels are incomplete generated inference."
    ]
    vector_rows = {}
    query_vector = None
    semantic_used = False
    if vector_artifact:
        try:
            from sabermetrics.reference_layer.strategy_corpus import (
                local_embedding_service,
            )

            service = encoder if encoder is not None else local_embedding_service()
            if (
                service is None
                or vector_artifact.get("version") != VIEW_VERSION
                or vector_artifact.get("artifact_version") != "card-view-vectors.v1"
                or vector_artifact.get("embedding_model") != service._model_name
            ):
                raise ValueError("incompatible local view index")
            query_vector = [float(value) for value in service.embed(query)]
            if not _valid_vector(query_vector):
                raise ValueError("invalid query vector")
            vector_rows = {
                (row["source_hash"], row["view"]): row
                for row in vector_artifact["records"]
            }
        except (ImportError, OSError, RuntimeError, ValueError, KeyError, TypeError):
            limitations.append(
                "View vectors unavailable or incompatible; lexical fallback retained."
            )
            query_vector = None
    for card in cards:
        views = build_card_views(card)
        printed = views["views"]["printed_effect"]["facts"]
        source_text = " ".join(
            str(printed.get(key) or "") for key in ("name", "oracle_text", "type_line")
        )
        generated = views["views"]["functional_role"]["roles"]
        lexical = len(terms & set(re.findall(r"\w+", source_text.casefold())))
        role = len(
            terms & set(re.findall(r"\w+", " ".join(generated).replace("_", " ")))
        )
        similarities = {}
        if query_vector is not None:
            import math

            for name, view in views["views"].items():
                # Empty generated projections contain no retrieval information.
                # Embedding "[]" must not nominate an unrelated card.
                if name == "functional_role" and not view["roles"]:
                    continue
                if name == "prerequisite" and not view["draw_routes"]:
                    continue
                row = vector_rows.get((views["source_hash"], name), {})
                vector = row.get("vector")
                text_hash = hashlib.sha256(_view_text(views, name).encode()).hexdigest()
                if (
                    row.get("view_hash") != view["view_hash"]
                    or row.get("text_sha256") != text_hash
                    or not _valid_vector(vector)
                    or len(vector) != len(query_vector)
                ):
                    continue
                denom = math.sqrt(
                    sum(x * x for x in vector) * sum(x * x for x in query_vector)
                )
                similarities[name] = max(
                    0.0,
                    min(1.0, sum(a * b for a, b in zip(vector, query_vector)) / denom),
                )
                semantic_used = True
        semantic = max(similarities.values(), default=0.0)
        # Cosine is a ranking signal, not a calibrated probability. Callers
        # bound nominations globally after combining the independent batches.
        if lexical or role or semantic > 0:
            ranked.append(
                (
                    0.5 * (lexical + role) / (1 + lexical + role) + 0.5 * semantic,
                    str(card.get("oracle_id") or card.get("name") or ""),
                    {
                        "card_views": views,
                        "view_cosines": similarities,
                        "matched_channels": [
                            name
                            for name, score in (
                                ("printed_effect", lexical),
                                ("generated_functional_role", role),
                            )
                            if score
                        ]
                        + [
                            "semantic:" + name
                            for name, score in similarities.items()
                            if score > 0
                        ],
                    },
                )
            )
    ranked.sort(key=lambda row: (-row[0], row[1]))
    retained_limit = len(cards) if all_matches else limit
    return {
        "version": VIEW_VERSION,
        "mode": "hybrid" if semantic_used else "lexical",
        "results": [row[2] for row in ranked[:retained_limit]],
        "omitted_matches": max(0, len(ranked) - retained_limit),
        "limitations": limitations
        + (
            []
            if semantic_used
            else ["No compatible view vectors; lexical retrieval only."]
        ),
    }


def _view_text(views: dict[str, Any], name: str) -> str:
    view = views["views"][name]
    if name == "printed_effect":
        return "\n".join(
            str(view["facts"].get(key) or "")
            for key in ("name", "type_line", "oracle_text", "card_faces")
        )
    if name == "functional_role":
        return " ".join(view["roles"]).replace("_", " ")
    return json.dumps(view["draw_routes"], sort_keys=True, ensure_ascii=False)


def prepare_card_view_vectors(
    cards: list[dict[str, Any]], *, encoder: Any = None
) -> dict[str, Any]:
    """Explicit offline preparation: one independently encoded vector per view.

    No embedding occurs on import or in build_card_views. Operators control this
    potentially expensive step. Encoder must be pinned/local; default uses the
    existing local-only corpus infrastructure. Artifacts are safe to omit.
    """
    from sabermetrics.reference_layer.strategy_corpus import local_embedding_service

    service = encoder if encoder is not None else local_embedding_service()
    if service is None:
        raise ValueError("configure a pinned local encoder before offline preparation")
    records = []
    texts = []
    for card in cards:
        views = build_card_views(card)
        for name, view in views["views"].items():
            text = _view_text(views, name)
            records.append(
                {
                    "oracle_id": card.get("oracle_id"),
                    "source_hash": views["source_hash"],
                    "view": name,
                    "view_hash": view["view_hash"],
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
            texts.append(text)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 16):
        batch = texts[start : start + 16]
        encoded = (
            service.embed_many(batch)
            if hasattr(service, "embed_many")
            else [service.embed(text) for text in batch]
        )
        vectors.extend([float(value) for value in vector] for vector in encoded)
    if len(vectors) != len(records):
        raise ValueError("encoder returned incomplete view vectors")
    for record, vector in zip(records, vectors):
        if not _valid_vector(vector):
            raise ValueError("encoder returned invalid view vector")
        record["vector"] = vector
    return {
        "version": VIEW_VERSION,
        "artifact_version": "card-view-vectors.v1",
        "embedding_model": service._model_name,
        "records": records,
    }


def _valid_vector(vector: Any) -> bool:
    import math

    return (
        isinstance(vector, list)
        and bool(vector)
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in vector
        )
        and sum(value * value for value in vector) > 0
    )
