"""Resolve the public normalizer's deferred annotations at runtime."""
from typing import Any, get_type_hints

import pytest

from bulk_downloader import metadata_normalizer

BD_GATE_SCOPE = "module"


def test_normalize_record_annotation_resolves_to_any():
    assert get_type_hints(metadata_normalizer.normalize_record)["record"] is Any


def test_type_hint_resolution_positive_control():
    assert get_type_hints(metadata_normalizer.normalize)["name"] is str


def test_missing_any_is_reported_by_the_same_probe(monkeypatch):
    monkeypatch.delattr(metadata_normalizer, "Any", raising=False)
    with pytest.raises(NameError, match="name 'Any' is not defined"):
        get_type_hints(metadata_normalizer.normalize_record)
