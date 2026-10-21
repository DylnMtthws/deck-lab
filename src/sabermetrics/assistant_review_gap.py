"""Conservative evidence scope for the single corrective followthrough.

Text mentions establish a lower bound on dependencies, not an exhaustive closure.
Neither a card's absence from a parent part nor a recommendation packet's label
proves irrelevance. Until an upstream contract supplies a complete dependency
scope, retain the immutable full source table and research context. This also
retains counterevidence, cross-card antecedents and unrecognized source kinds.
"""

from __future__ import annotations

import json
from typing import Any


def corrective_scope(
    sources: list[str], research_context: dict[str, Any] | None
) -> tuple[set[int], dict[str, Any] | None, dict[str, Any]]:
    """Full fallback: the current receipt has no complete dependency certificate.

    Do not trim recommendation_packets independently of the evidence closure.
    Byte counts are actual UTF-8 sizes, not token estimates or claimed savings.
    """
    evidence_bytes = sum(len(source.encode()) for source in sources)
    context_bytes = len(json.dumps(research_context or {}, ensure_ascii=False).encode())
    return (
        set(range(len(sources))),
        research_context,
        {
            "status": "full_fallback",
            "reason": "unproven_complete_local_closure",
            "evidence_bytes_before": evidence_bytes,
            "evidence_bytes_after": evidence_bytes,
            "research_context_bytes_before": context_bytes,
            "research_context_bytes_after": context_bytes,
        },
    )
