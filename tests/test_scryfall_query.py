"""Pure unit tests for the Scryfall-style query parser.

See AC-1 through AC-9 in T03-research-scryfall-syntax.md.
"""

from __future__ import annotations

from sabermetrics.ui.scryfall_query import parse_query


class TestParseTypeSupertypeSubtypeAndNegation:
    """AC-1: t:/type: VALUE sets supertype, card-type, or subtype filters."""

    def test_legendary_supertype(self):
        result = parse_query("t:legendary")
        assert result.filters.get("super_type") == "Legendary"
        assert result.filters.get("super_op") == "is"
        assert not result.unsupported

    def test_creature_type(self):
        result = parse_query("t:creature")
        assert result.filters.get("card_type") == "Creature"
        assert result.filters.get("type_op") == "is"
        assert not result.unsupported

    def test_subtype_goblin(self):
        result = parse_query("t:goblin")
        assert result.filters.get("sub_type") == "goblin"
        assert result.filters.get("sub_op") == "is"
        assert not result.unsupported

    def test_negated_type(self):
        result = parse_query("-t:creature")
        assert result.filters.get("card_type") == "Creature"
        assert result.filters.get("type_op") == "not"
        assert not result.unsupported

    def test_negated_supertype(self):
        result = parse_query("-t:legendary")
        assert result.filters.get("super_type") == "Legendary"
        assert result.filters.get("super_op") == "not"
        assert not result.unsupported

    def test_type_alias(self):
        result = parse_query("type:instant")
        assert result.filters.get("card_type") == "Instant"
        assert result.filters.get("type_op") == "is"


class TestParseOracleQuotedAndRepeated:
    """AC-2: o:/oracle: VALUE with quoted support and repeated-join."""

    def test_simple_oracle(self):
        result = parse_query('o:"draw a card"')
        assert result.filters.get("oracle_text") == "draw a card"

    def test_single_word_oracle(self):
        result = parse_query("o:draw")
        assert result.filters.get("oracle_text") == "draw"

    def test_repeated_oracle_joins_with_space(self):
        result = parse_query('o:"draw a card" o:draw')
        assert result.filters.get("oracle_text") == "draw a card draw"

    def test_oracle_alias(self):
        result = parse_query('oracle:"draw a card"')
        assert result.filters.get("oracle_text") == "draw a card"


class TestParseManaValueComparators:
    """AC-3: All 6 comparators for mv/cmc with strict-inequality edges."""

    def test_mv_equals(self):
        result = parse_query("mv=2")
        assert result.filters.get("mana_min_bound") == 2
        assert result.filters.get("mana_max_bound") == 2

    def test_mv_colon_equals(self):
        result = parse_query("mv:2")
        assert result.filters.get("mana_min_bound") == 2
        assert result.filters.get("mana_max_bound") == 2

    def test_mv_less_than(self):
        result = parse_query("mv<3")
        assert result.filters.get("mana_max_bound") == 2
        assert result.filters.get("mana_min_bound") is None

    def test_mv_less_than_or_equal(self):
        result = parse_query("mv<=3")
        assert result.filters.get("mana_max_bound") == 3
        assert result.filters.get("mana_min_bound") is None

    def test_mv_greater_than(self):
        result = parse_query("mv>5")
        assert result.filters.get("mana_min_bound") == 6
        assert result.filters.get("mana_max_bound") is None

    def test_mv_greater_than_or_equal(self):
        result = parse_query("mv>=5")
        assert result.filters.get("mana_min_bound") == 5
        assert result.filters.get("mana_max_bound") is None

    def test_cmc_alias(self):
        result = parse_query("cmc<=2")
        assert result.filters.get("mana_max_bound") == 2
        assert result.filters.get("mana_min_bound") is None

    def test_strict_less_than_zero_edge(self):
        result = parse_query("mv<1")
        assert result.filters.get("mana_max_bound") == 0
        assert result.filters.get("mana_min_bound") is None


class TestParsePowerToughness:
    """AC-4: pow/power and tou/toughness with comparators."""

    def test_power_gte(self):
        result = parse_query("pow>=3")
        assert result.filters.get("power_min_bound") == 3
        assert result.filters.get("power_max_bound") is None

    def test_power_alias(self):
        result = parse_query("power>=3")
        assert result.filters.get("power_min_bound") == 3

    def test_toughness_equals(self):
        result = parse_query("tou=2")
        assert result.filters.get("toughness_min_bound") == 2
        assert result.filters.get("toughness_max_bound") == 2

    def test_toughness_alias(self):
        result = parse_query("toughness=2")
        assert result.filters.get("toughness_min_bound") == 2
        assert result.filters.get("toughness_max_bound") == 2

    def test_power_less_than(self):
        result = parse_query("pow<3")
        assert result.filters.get("power_max_bound") == 2
        assert result.filters.get("power_min_bound") is None


class TestParseColorsAndIdentityMapping:
    """AC-5: c:/c= and id:/id= map onto color checkboxes and color_mode."""

    def test_c_include(self):
        result = parse_query("c:wu")
        assert result.filters.get("colors") == ["W", "U"]
        assert result.filters.get("color_mode") == "include"

    def test_c_exact(self):
        result = parse_query("c=ubr")
        assert result.filters.get("colors") == ["U", "B", "R"]
        assert result.filters.get("color_mode") == "exactly"

    def test_c_negated(self):
        result = parse_query("-c:g")
        assert result.filters.get("colors") == ["G"]
        assert result.filters.get("color_mode") == "exclude"

    def test_id_include(self):
        result = parse_query("id:wubrg")
        assert result.filters.get("colors") == ["W", "U", "B", "R", "G"]
        assert result.filters.get("color_mode") == "include"

    def test_c_colorless(self):
        result = parse_query("c:c")
        assert result.filters.get("colors") == ["C"]
        assert result.filters.get("color_mode") == "include"

    def test_identity_colon(self):
        result = parse_query("id:g")
        assert result.filters.get("colors") == ["G"]
        assert result.filters.get("color_mode") == "include"


class TestParseRarityAliases:
    """AC-6: r:/rarity: with aliases."""

    def test_rare(self):
        result = parse_query("r:r")
        assert result.filters.get("rarity") == "rare"

    def test_mythic_long(self):
        result = parse_query("r:mythic")
        assert result.filters.get("rarity") == "mythic"

    def test_common(self):
        result = parse_query("rarity:common")
        assert result.filters.get("rarity") == "common"

    def test_uncommon_alias(self):
        result = parse_query("r:u")
        assert result.filters.get("rarity") == "uncommon"

    def test_unknown_rarity_is_unsupported(self):
        result = parse_query("r:special")
        assert "rarity" not in result.filters
        assert result.unsupported
        assert any("r:special" in u for u in result.unsupported)

    def test_mythic_via_m(self):
        result = parse_query("r:m")
        assert result.filters.get("rarity") == "mythic"


class TestBareWordsBecomeNameTerms:
    """AC-7: Bare words and quoted phrases become name_terms."""

    def test_single_word(self):
        result = parse_query("Sol Ring")
        assert result.name_terms == ("Sol", "Ring")
        assert not result.filters

    def test_quoted_phrase(self):
        result = parse_query('"Sol Ring"')
        assert result.name_terms == ("Sol Ring",)
        assert not result.filters

    def test_mixed_syntax_and_name(self):
        result = parse_query('t:creature "Serra Angel"')
        assert result.filters.get("card_type") == "Creature"
        assert result.name_terms == ("Serra Angel",)


class TestUnsupportedTermsPreservedVerbatim:
    """AC-8: Unsupported syntax terms are collected verbatim."""

    def test_is_commander(self):
        result = parse_query("is:commander")
        assert "is:commander" in result.unsupported

    def test_set_code(self):
        result = parse_query("set:mh3")
        assert "set:mh3" in result.unsupported

    def test_format(self):
        result = parse_query("f:edh")
        assert "f:edh" in result.unsupported

    def test_set_e_alias(self):
        result = parse_query("e:mh3")
        assert "e:mh3" in result.unsupported

    def test_or_and_parentheses(self):
        result = parse_query("t:creature or t:artifact")
        assert result.unsupported == ("or",)
        assert result.filters.get("card_type") == "Artifact"

    def test_multiple_unsupported(self):
        result = parse_query("is:commander set:mh3 f:edh")
        assert len(result.unsupported) == 3

    def test_negated_unsupported_operator(self):
        result = parse_query("-is:commander")
        assert len(result.unsupported) >= 1
        assert any("-is:commander" in u for u in result.unsupported)


class TestPlainNameQueryHasNoFilters:
    """AC-9: A plain name-only query produces empty filters."""

    def test_empty_string(self):
        result = parse_query("")
        assert not result.filters
        assert not result.name_terms
        assert not result.unsupported

    def test_only_whitespace(self):
        result = parse_query("   ")
        assert not result.filters
        assert not result.name_terms

    def test_bare_name(self):
        result = parse_query("Kinnan")
        assert not result.filters
        assert result.name_terms == ("Kinnan",)

    def test_multiple_names(self):
        result = parse_query("Sol Ring Mana Crypt")
        assert not result.filters
        assert result.name_terms == ("Sol", "Ring", "Mana", "Crypt")


class TestEdgeCases:
    """Additional edge-case coverage beyond the named ACs."""

    def test_mixed_case_keys(self):
        result = parse_query("T:Instant MV<=3")
        assert result.filters.get("card_type") == "Instant"
        assert result.filters.get("mana_max_bound") == 3

    def test_power_and_toughness_together(self):
        result = parse_query("pow>=2 tou<=4")
        assert result.filters.get("power_min_bound") == 2
        assert result.filters.get("toughness_max_bound") == 4

    def test_complex_query(self):
        result = parse_query('t:creature o:"whenever" mv<=3 r:rare -c:g')
        assert result.filters.get("card_type") == "Creature"
        assert result.filters.get("oracle_text") == "whenever"
        assert result.filters.get("mana_max_bound") == 3
        assert result.filters.get("rarity") == "rare"
        assert result.filters.get("colors") == ["G"]
        assert result.filters.get("color_mode") == "exclude"
        assert not result.unsupported

    def test_applied_terms_tracks_parsed_tokens(self):
        result = parse_query("t:creature o:draw mv<=3")
        assert len(result.applied_terms) == 3

    def test_unsupported_colors_empty(self):
        result = parse_query("c:")
        assert "colors" not in result.filters
        assert result.unsupported
