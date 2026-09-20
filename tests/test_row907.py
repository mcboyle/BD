"""row907: bulk_downloader.metadata_normalizer -- canonical record
attribution and metadata normalization.

Maps raw publisher/series/author strings, however inconsistently spelled by
different upstream sources, to one canonical filesystem-safe path component:
exact alias match first, then a bounded Levenshtein fuzzy match against the
same alias table, else a plain filesystem-safe normalization of the input.

Negative control: test_normalize_does_not_collapse_distinct_publishers_
without_an_alias proves the canonicalization does not simply map every
input to one constant -- without it, every "normalizes to X" assertion in
this file would pass even if normalize() were a stub returning a fixed
string.
"""
import sys
import time
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import metadata_normalizer as MN


# --------------------------------------------------------- filesystem-safe
def test_to_filesystem_component_strips_unsafe_chars_and_collapses_whitespace():
    assert MN.to_filesystem_component('Pub: "Weird"  /Name\\*?<>|') == "Pub Weird Name"


def test_to_filesystem_component_strips_trailing_dots_and_spaces():
    # Windows treats a trailing dot/space as illegal in a path component.
    assert MN.to_filesystem_component("Acme Corp. . ") == "Acme Corp"


def test_to_filesystem_component_empty_or_all_illegal_becomes_unknown():
    assert MN.to_filesystem_component("") == "unknown"
    assert MN.to_filesystem_component('///:::***') == "unknown"


def test_to_filesystem_component_caps_length():
    long_name = "A" * 500
    result = MN.to_filesystem_component(long_name)
    assert len(result) == 200


# --------------------------------------------------------------- exact alias
def test_alias_table_exact_match_normalizes_variant_to_canonical():
    table = MN.AliasTable({"OReilly Media": "O'Reilly Media"})
    result = table.lookup("  oreilly   media  ")
    assert result.canonical == "O'Reilly Media"
    assert result.matched_alias is True
    assert result.fuzzy is False


def test_alias_table_exact_match_ignores_leading_the_and_punctuation():
    table = MN.AliasTable({"Beatles": "The Beatles"})
    result = table.lookup("The, Beatles!")
    assert result.canonical == "The Beatles"
    assert result.matched_alias is True


# --------------------------------------------------------------- fuzzy match
def test_alias_table_fuzzy_match_within_distance_normalizes_typo():
    table = MN.AliasTable({"Packt Publishing": "Packt Publishing Ltd"}, fuzzy_max_distance=2)
    # "Pactk Publishing" is a 2-character transposition/edit away from the
    # folded key "packt publishing".
    result = table.lookup("Pactk Publishing")
    assert result.canonical == "Packt Publishing Ltd"
    assert result.matched_alias is True
    assert result.fuzzy is True
    assert 0 < result.distance <= 2


def test_alias_table_fuzzy_match_respects_max_distance_threshold():
    table = MN.AliasTable({"Packt Publishing": "Packt Publishing Ltd"}, fuzzy_max_distance=2)
    # Far beyond the threshold -- must NOT be coerced onto an unrelated alias.
    result = table.lookup("Completely Different Name Co")
    assert result.matched_alias is False
    assert result.canonical == "Completely Different Name Co"


def test_alias_table_fuzzy_disabled_when_max_distance_zero():
    table = MN.AliasTable({"Packt Publishing": "Packt Publishing Ltd"}, fuzzy_max_distance=0)
    result = table.lookup("Pactk Publishing")
    assert result.matched_alias is False
    assert result.fuzzy is False


# --------------------------------------------------------------- no match
def test_alias_table_no_match_falls_back_to_filesystem_safe_of_input():
    table = MN.AliasTable({"Known Publisher": "Known Publisher Canonical"})
    result = table.lookup('Totally: Unknown/Name')
    assert result.matched_alias is False
    assert result.canonical == "Totally UnknownName"


def test_normalize_without_aliases_is_plain_filesystem_safe():
    assert MN.normalize('Weird: "Name"') == MN.to_filesystem_component('Weird: "Name"')


def test_normalize_with_aliases_uses_the_alias_table_not_the_plain_fallback():
    table = MN.AliasTable({"OReilly Media": "O'Reilly Media"})
    assert MN.normalize("oreilly media", aliases=table) == "O'Reilly Media"
    # Proves the `aliases is None` branch is not always taken: a raw variant
    # that WOULD normalize differently via the plain fallback must instead
    # come back as the alias table's canonical value.
    assert MN.normalize("oreilly media", aliases=table) != MN.to_filesystem_component("oreilly media")


# --------------------------------------------------------- negative control
def test_normalize_does_not_collapse_distinct_publishers_without_an_alias():
    """Proves normalize() is not a stub that maps every input to one
    constant: two genuinely different, alias-table-absent names must
    normalize to two genuinely different canonical strings."""
    a = MN.normalize("Acme Publishing House")
    b = MN.normalize("Zephyr Books Incorporated")
    assert a != b


# --------------------------------------------------------- per-field scoping
def test_metadata_normalizer_keeps_publisher_series_author_tables_separate():
    norm = MN.build_normalizer(
        publisher_aliases={"Acme": "Acme Publishing"},
        series_aliases={"Acme": "Acme Chronicles"},
        author_aliases={},
    )
    assert norm.normalize_publisher("acme") == "Acme Publishing"
    assert norm.normalize_series("acme") == "Acme Chronicles"
    # No author alias for "Acme" -- falls back to filesystem-safe of input,
    # not one of the other two fields' canonical values.
    assert norm.normalize_author("Acme") == "Acme"


# --------------------------------------------------------------- determinism
def test_normalize_is_deterministic_across_repeated_calls():
    table = MN.AliasTable({"Packt Publishing": "Packt Publishing Ltd"}, fuzzy_max_distance=2)
    inputs = ["Pactk Publishing", "packt publishing", "Unknown Co", ""]
    first_pass = [table.lookup(n).canonical for n in inputs]
    for _ in range(50):
        assert [table.lookup(n).canonical for n in inputs] == first_pass


# --------------------------------------------------------- alias performance
def test_alias_table_exact_lookup_performance_scales_with_large_table():
    """Exact lookups are a single folded dict get, independent of table
    size -- proven by building a 5000-entry table and doing 2000 exact
    lookups (never triggering the O(n) fuzzy scan) well under a generous
    wall-clock budget."""
    aliases = {f"Publisher Variant {i}": f"Publisher Canonical {i}" for i in range(5000)}
    table = MN.AliasTable(aliases)

    start = time.perf_counter()
    for i in range(2000):
        result = table.lookup(f"publisher variant {i}")
        assert result.canonical == f"Publisher Canonical {i}"
        assert result.matched_alias is True
        assert result.fuzzy is False
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, (
        f"2000 exact alias lookups against a 5000-entry table took "
        f"{elapsed:.3f}s -- expected well under 2s for O(1) dict lookups; "
        "this likely means lookup() is re-folding the whole table per call "
        "instead of the fuzzy scan being skipped on exact hits"
    )
