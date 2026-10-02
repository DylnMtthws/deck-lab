# T03 — Scryfall-style syntax in the Research card search

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.

## Why
Moxfield and Archidekt users type `t:instant o:"draw a card" mv<=2` and get results. Deck Lab's
Research "Cards" tab has a rich filter form, but the search box `q` only matches names. Typing
syntax should fill the same filters.

## Allowed files
- `src/sabermetrics/ui/scryfall_query.py` (new): the pure parser, with no Flask imports.
- `src/sabermetrics/ui/research_routes.py`: only where the Cards tab builds its card filters from the request.
- `src/sabermetrics/ui/templates/deck_lab/research_fragment.html`: only to add the notices described below, inside the cards-tab branch.
- `tests/test_scryfall_query.py` (new), `tests/test_research_query_syntax.py` (new)
- `docs/specs/competitive-ux/reports/T03.md` (new)

First find how the Cards tab turns request args into `card_filters` (search `card_filters` in
`research_routes.py`). The parsed query must feed that SAME structure. Do not add a second filtering
path.

## Requirements
1. `parse_query(q: str) -> ParsedQuery` (frozen dataclass) with fields:
   - `filters: dict[str, object]`: keys and value shapes identical to the existing `card_filters` keys they map to.
   - `name_terms: tuple[str, ...]`
   - `unsupported: tuple[str, ...]`

   Tokenize on whitespace, respecting double-quoted values. Supported terms (case-insensitive keys):
   - `t:`/`type:` VALUE. If VALUE is a supertype in the form's supertype options, set the supertype filter. If it is a card type in the type options, set the card-type filter. Otherwise set the subtype filter. A leading `-` (e.g. `-t:creature`) sets the corresponding `*_op` to `not`.
   - `o:`/`oracle:` VALUE (quoted allowed) sets the oracle-text filter. If repeated, join with a single space.
   - `c:`/`color:` and `id:`/`identity:` with letters from `wubrgc`. Map onto the card color checkboxes and `color_mode`, using whichever existing mode best matches Scryfall: `c:` means include, `c=` means exact if an exact mode exists, otherwise include. Write the exact mapping you chose into the report. `id:` maps the same way unless an identity filter exists; if none exists, put the term in `unsupported`.
   - `mv`/`cmc` with `=`, `:`, `<`, `<=`, `>`, `>=` and an integer sets `mana_min`/`mana_max`. Strict `<N` becomes max `N-1`; strict `>N` becomes min `N+1`.
   - `pow`/`power` and `tou`/`toughness` with the same comparators set the power and toughness bounds.
   - `r:`/`rarity:` with `c`/`common`, `u`/`uncommon`, `r`/`rare` or `m`/`mythic` sets rarity.
   - A bare word or quoted phrase is added to `name_terms`.
   - Anything else (e.g. `is:commander`, `f:edh`, `set:mh3`, `or`, parentheses) is added to `unsupported` verbatim.
2. Merge rule in the route: explicit form query args WIN. Parsed values fill only filter keys that the request did not set. `name_terms` joined with spaces become the existing name search value. If the input has no recognised syntax, behaviour must be identical to today.
3. Template notices, inside the cards tab above the results:
   - If any syntax term was applied: `<p class="dl-muted" data-query-applied>Applied from search: …</p>`, listing the applied terms as typed.
   - If `unsupported` is not empty: `<p class="dl-notice" data-query-unsupported>Not supported yet: …</p>`, listing them. Never ignore an unsupported term silently.

## Acceptance criteria → required tests
In `tests/test_scryfall_query.py`, pure unit tests:
- AC-1 `test_parse_type_supertype_subtype_and_negation`
- AC-2 `test_parse_oracle_quoted_and_repeated`
- AC-3 `test_parse_mana_value_comparators` (all 6 comparators, including strict-inequality edges)
- AC-4 `test_parse_power_toughness`
- AC-5 `test_parse_colors_and_identity_mapping`
- AC-6 `test_parse_rarity_aliases`
- AC-7 `test_bare_words_become_name_terms`
- AC-8 `test_unsupported_terms_preserved_verbatim`
- AC-9 `test_plain_name_query_has_no_filters`

In `tests/test_research_query_syntax.py`, Flask test client with a populated card DB fixture. Reuse
an existing research test's DB setup; look at `tests/test_research*.py` and `tests/_populated_db.py`.
- AC-10 `test_syntax_query_filters_results`: `q=t:instant mv<=1` returns only instants with MV ≤ 1 from the fixture set, and the count differs from the unfiltered query.
- AC-11 `test_form_args_override_parsed_values`
- AC-12 `test_unsupported_notice_rendered`
- AC-13 `test_plain_query_unchanged`: results for a plain name query are identical with your change and with the parser bypassed. Monkeypatch the parser to return an empty `ParsedQuery` to compare.

## Out of scope
Boolean OR, parentheses, sets, formats, and changes to the commanders, meta or decks tabs.
