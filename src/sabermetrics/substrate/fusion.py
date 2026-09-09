"""Deterministic weighted reciprocal-rank fusion for card retrieval."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real


@dataclass(frozen=True)
class RankedHit:
    """One card at one position in a retriever's ranking.

    Attributes:
        oracle_id: Canonical Scryfall oracle UUID.
        stage_score: Optional score emitted by the retriever. It is retained for
            provenance but never used by reciprocal-rank fusion.
    """

    oracle_id: str
    stage_score: float | None = None


@dataclass(frozen=True)
class ChannelRank:
    """One fused card's position and original score in a retrieval channel.

    Attributes:
        channel: Retriever name.
        rank: One-based position of the card's first occurrence.
        stage_score: Optional score attached to that first occurrence.
    """

    channel: str
    rank: int
    stage_score: float | None = None


@dataclass(frozen=True)
class FusedHit:
    """A card ranked by weighted reciprocal-rank fusion.

    Attributes:
        oracle_id: Canonical Scryfall oracle UUID.
        fused_score: Sum of ``weight / (rank_constant + rank)`` over channels
            containing the card.
        channel_ranks: Channel details, sorted by channel name.
    """

    oracle_id: str
    fused_score: float
    channel_ranks: tuple[ChannelRank, ...]


def weighted_reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[RankedHit]],
    weights: Mapping[str, float],
    *,
    rank_constant: float = 60.0,
) -> tuple[FusedHit, ...]:
    """Fuse named retriever rankings using weighted reciprocal rank.

    A channel contributes ``weight / (rank_constant + rank)`` for a card.
    Retriever scores are carried into the result for provenance, but only the
    card's one-based rank affects fusion. Repeated oracle IDs in a channel
    contribute once, at their first occurrence and with that occurrence's
    optional stage score.

    Args:
        rankings: Retriever name to its ordered hits.
        weights: One positive finite weight for every retriever in ``rankings``.
            Extra weights are rejected as unknown channels.
        rank_constant: Positive finite denominator offset.

    Returns:
        Fused hits ordered by descending fused score, then lowest contributing
        rank, then oracle ID. Empty rankings produce an empty tuple.

    Raises:
        TypeError: If a weight, ``rank_constant``, or oracle ID has the wrong
            runtime type.
        ValueError: If channel names or oracle IDs are malformed, the weight
            channels do not exactly match the rankings, or a weight or
            ``rank_constant`` is not positive and finite.
    """
    channel_names = _validate_channels(rankings, weights)
    constant = _positive_finite(rank_constant, name="rank_constant")
    validated_weights = {
        channel: _positive_finite(weights[channel], name=f"weight[{channel!r}]")
        for channel in channel_names
    }

    ranks_by_oracle_id: dict[str, list[ChannelRank]] = defaultdict(list)
    contributions_by_oracle_id: dict[str, list[float]] = defaultdict(list)

    for channel in channel_names:
        seen: set[str] = set()
        for rank, hit in enumerate(rankings[channel], start=1):
            oracle_id = _validate_oracle_id(hit.oracle_id)
            if oracle_id in seen:
                continue
            seen.add(oracle_id)
            ranks_by_oracle_id[oracle_id].append(
                ChannelRank(
                    channel=channel,
                    rank=rank,
                    stage_score=hit.stage_score,
                )
            )
            contributions_by_oracle_id[oracle_id].append(
                validated_weights[channel] / (constant + rank)
            )

    fused = [
        FusedHit(
            oracle_id=oracle_id,
            fused_score=math.fsum(contributions_by_oracle_id[oracle_id]),
            channel_ranks=tuple(ranks),
        )
        for oracle_id, ranks in ranks_by_oracle_id.items()
    ]
    fused.sort(
        key=lambda hit: (
            -hit.fused_score,
            min(channel_rank.rank for channel_rank in hit.channel_ranks),
            hit.oracle_id,
        )
    )
    return tuple(fused)


def _validate_channels(
    rankings: Mapping[str, Sequence[RankedHit]],
    weights: Mapping[str, float],
) -> tuple[str, ...]:
    ranking_channels = set(rankings)
    weight_channels = set(weights)
    if any(not isinstance(channel, str) or not channel for channel in ranking_channels):
        raise ValueError("ranking channel names must be non-empty strings")
    if any(not isinstance(channel, str) or not channel for channel in weight_channels):
        raise ValueError("weight channel names must be non-empty strings")

    missing = ranking_channels - weight_channels
    unknown = weight_channels - ranking_channels
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing weights: {sorted(missing)!r}")
        if unknown:
            details.append(f"unknown weights: {sorted(unknown)!r}")
        raise ValueError("; ".join(details))
    return tuple(sorted(ranking_channels))


def _positive_finite(value: float, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a positive finite number")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")
    return converted


def _validate_oracle_id(oracle_id: str) -> str:
    if not isinstance(oracle_id, str):
        raise TypeError("oracle_id must be a canonical UUID string")
    try:
        parsed = uuid.UUID(oracle_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"malformed oracle_id: {oracle_id!r}") from exc
    if str(parsed) != oracle_id:
        raise ValueError(f"malformed oracle_id: {oracle_id!r}")
    return oracle_id
