"""Tests for deterministic weighted reciprocal-rank fusion."""

from __future__ import annotations

import math

import pytest

from sabermetrics.substrate.fusion import (
    ChannelRank,
    RankedHit,
    weighted_reciprocal_rank_fusion,
)

CARD_A = "00000000-0000-0000-0000-000000000001"
CARD_B = "00000000-0000-0000-0000-000000000002"
CARD_C = "00000000-0000-0000-0000-000000000003"


def test_fused_score_uses_weighted_reciprocal_rank_formula_only():
    result = weighted_reciprocal_rank_fusion(
        {
            "lexical": [
                RankedHit(CARD_A, stage_score=1000.0),
                RankedHit(CARD_B, stage_score=-1000.0),
            ],
            "dense": [
                RankedHit(CARD_B, stage_score=math.nan),
                RankedHit(CARD_A, stage_score=math.inf),
            ],
        },
        {"lexical": 1.0, "dense": 1.0},
        rank_constant=60.0,
    )

    assert [hit.oracle_id for hit in result] == [CARD_A, CARD_B]
    assert result[0].fused_score == pytest.approx(1 / 61 + 1 / 62)
    assert result[1].fused_score == pytest.approx(1 / 61 + 1 / 62)
    assert result[0].channel_ranks == (
        ChannelRank("dense", 2, math.inf),
        ChannelRank("lexical", 1, 1000.0),
    )


def test_channel_weights_change_fused_order():
    result = weighted_reciprocal_rank_fusion(
        {
            "lexical": [RankedHit(CARD_A), RankedHit(CARD_B)],
            "dense": [RankedHit(CARD_B), RankedHit(CARD_A)],
        },
        {"lexical": 2.0, "dense": 1.0},
        rank_constant=60.0,
    )

    assert [hit.oracle_id for hit in result] == [CARD_A, CARD_B]
    assert result[0].fused_score == pytest.approx(2 / 61 + 1 / 62)
    assert result[1].fused_score == pytest.approx(2 / 62 + 1 / 61)


def test_duplicate_ids_contribute_once_at_first_occurrence():
    result = weighted_reciprocal_rank_fusion(
        {
            "lexical": [
                RankedHit(CARD_A, stage_score=0.9),
                RankedHit(CARD_A, stage_score=0.1),
                RankedHit(CARD_B, stage_score=0.8),
            ]
        },
        {"lexical": 1.0},
        rank_constant=10.0,
    )

    assert result[0].oracle_id == CARD_A
    assert result[0].fused_score == pytest.approx(1 / 11)
    assert result[0].channel_ranks == (ChannelRank("lexical", 1, 0.9),)
    assert result[1].fused_score == pytest.approx(1 / 13)
    assert result[1].channel_ranks == (ChannelRank("lexical", 3, 0.8),)


def test_ties_use_best_rank_before_oracle_id():
    result = weighted_reciprocal_rank_fusion(
        {
            "first": [RankedHit(CARD_C)],
            "second": [RankedHit(CARD_A), RankedHit(CARD_B)],
        },
        {"first": 1.0, "second": 1.5},
        rank_constant=1.0,
    )

    # CARD_C and CARD_B both score 0.5. CARD_C wins despite its larger UUID
    # because its best contributing rank is 1 rather than 2.
    assert [hit.oracle_id for hit in result] == [CARD_A, CARD_C, CARD_B]
    assert result[1].fused_score == result[2].fused_score == 0.5


def test_complete_ties_use_oracle_id():
    result = weighted_reciprocal_rank_fusion(
        {
            "zeta": [RankedHit(CARD_B)],
            "alpha": [RankedHit(CARD_A)],
        },
        {"zeta": 1.0, "alpha": 1.0},
        rank_constant=1.0,
    )

    assert [hit.oracle_id for hit in result] == [CARD_A, CARD_B]


def test_hit_absent_from_a_channel_has_no_rank_or_contribution():
    result = weighted_reciprocal_rank_fusion(
        {
            "lexical": [RankedHit(CARD_A)],
            "dense": [],
        },
        {"lexical": 2.0, "dense": 100.0},
        rank_constant=9.0,
    )

    assert len(result) == 1
    assert result[0].fused_score == pytest.approx(2 / 10)
    assert result[0].channel_ranks == (ChannelRank("lexical", 1, None),)


@pytest.mark.parametrize("weight", [0.0, -1.0, math.nan, math.inf, -math.inf, True])
def test_weights_must_be_positive_finite_numbers(weight):
    with pytest.raises((TypeError, ValueError), match="positive finite"):
        weighted_reciprocal_rank_fusion(
            {"lexical": [RankedHit(CARD_A)]},
            {"lexical": weight},
        )


def test_weight_channels_must_exactly_match_rankings():
    with pytest.raises(ValueError, match="missing weights"):
        weighted_reciprocal_rank_fusion(
            {"lexical": [RankedHit(CARD_A)]},
            {},
        )

    with pytest.raises(ValueError, match="unknown weights"):
        weighted_reciprocal_rank_fusion(
            {"lexical": [RankedHit(CARD_A)]},
            {"lexical": 1.0, "dense": 1.0},
        )


@pytest.mark.parametrize(
    "rank_constant", [0.0, -1.0, math.nan, math.inf, -math.inf, False]
)
def test_rank_constant_must_be_positive_and_finite(rank_constant):
    with pytest.raises((TypeError, ValueError), match="positive finite"):
        weighted_reciprocal_rank_fusion(
            {"lexical": [RankedHit(CARD_A)]},
            {"lexical": 1.0},
            rank_constant=rank_constant,
        )


@pytest.mark.parametrize(
    "oracle_id",
    [
        "",
        "not-a-uuid",
        "00000000000000000000000000000001",
        "00000000-0000-0000-0000-00000000000A",
    ],
)
def test_malformed_oracle_ids_are_rejected(oracle_id):
    with pytest.raises(ValueError, match="oracle_id"):
        weighted_reciprocal_rank_fusion(
            {"lexical": [RankedHit(oracle_id)]},
            {"lexical": 1.0},
        )


def test_channel_mapping_order_does_not_change_results():
    lexical = [RankedHit(CARD_A, 0.8), RankedHit(CARD_B, 0.7)]
    dense = [RankedHit(CARD_B, 0.6), RankedHit(CARD_C, 0.5)]

    forward = weighted_reciprocal_rank_fusion(
        {"lexical": lexical, "dense": dense},
        {"lexical": 2.0, "dense": 1.0},
    )
    reversed_channels = weighted_reciprocal_rank_fusion(
        {"dense": dense, "lexical": lexical},
        {"dense": 1.0, "lexical": 2.0},
    )

    assert forward == reversed_channels


def test_empty_rankings_return_no_hits():
    assert weighted_reciprocal_rank_fusion({}, {}) == ()
    assert (
        weighted_reciprocal_rank_fusion(
            {"lexical": [], "dense": []},
            {"lexical": 1.0, "dense": 1.0},
        )
        == ()
    )
