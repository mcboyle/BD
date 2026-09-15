"""Row 810 -- slice 2 of 3 of the whole-corpus spec collection walk.

This is v3.66.1184's test_every_tracked_spec_parses_and_declares_schema_band_and
_mutants over every third tracked tests/mutants/*.json spec (specs[2::3]), in
its own CI shard. That walk -- one `pytest --collect-only` per spec -- was the
whole of the mutation-tools shard's wall time (see the row-810 comment in
tests/test_v3_66_1184_mutation_specs_are_tracked.py for the measurement), and it
is not divisible by test id, only by population. The helpers, the worker sizing
and the failure reconciliation stay in v3.66.1184, whose partition test proves
the slice files on disk re-assemble the whole population.
"""
from __future__ import annotations

import pytest

from test_v3_66_1184_mutation_specs_are_tracked import (
    _git_paths,
    _tracked_spec_slice,
    _validate_tracked_specs_concurrently,
)

BD_GATE_SCOPE = "repo-wide"
_SPEC_SLICE = (2, 3)


@pytest.mark.timeout(900)
def test_every_tracked_spec_in_this_slice_parses_and_declares_schema_band_and_mutants():
    specs = _tracked_spec_slice(*_SPEC_SLICE)
    assert specs, "cannot validate a zero-spec slice"
    tracked = set(_git_paths())
    checked = _validate_tracked_specs_concurrently(specs, tracked)
    assert checked > 0 and checked == len(specs), (
        f"schema reader processed {checked} of {len(specs)} tracked specs"
    )
