"""Row 765: Scrapling 0.4's Selector capability remains usable."""
import importlib.machinery
import sys
import types

import pytest

from bulk_downloader import scrapling_adapter


BD_GATE_SCOPE = "repo-wide"


def test_transform_control_imports_adapter_without_driving_selector_resolution():
    assert scrapling_adapter.__name__.endswith("scrapling_adapter")


def _scrapling(*, selector=False, adaptor=False):
    module = types.ModuleType("scrapling")
    module.__spec__ = importlib.machinery.ModuleSpec("scrapling", loader=None)

    if selector or adaptor:
        # Only the module exports are simulated; parsing and the element API
        # come from the installed library, independently of the adapter.
        from scrapling import Selector

        if selector:
            module.Selector = Selector
        if adaptor:
            module.Adaptor = Selector
    return module


def test_selector_only_scrapling_is_available_for_fingerprint_and_recovery(monkeypatch):
    module = _scrapling(selector=True)
    assert hasattr(module, "Selector"), "precondition: 0.4 fixture exports Selector"
    assert not hasattr(module, "Adaptor"), "precondition: 0.4 fixture exports no Adaptor"
    monkeypatch.setitem(sys.modules, "scrapling", module)

    status = scrapling_adapter.capability_status()["adaptive_selectors"]
    html = "<html><body><main id='x' class='a b'>hi</main></body></html>"
    fingerprint = scrapling_adapter.build_fingerprint(html, "main")

    assert status["available"] is True
    assert scrapling_adapter.is_available() is True
    assert isinstance(fingerprint, dict)
    assert fingerprint["tag"] == "main"
    assert fingerprint["attrs"] == {"id": "x", "class": ["a", "b"]}
    assert fingerprint["text_preview"] == "hi"
    recovered = scrapling_adapter.recover_selector(html, fingerprint)
    assert recovered.ok is True
    assert recovered.selector == "main"
    assert recovered.error == ""


def test_real_selector_recovers_original_selector_from_independent_fingerprint():
    from hashlib import sha256
    from scrapling import Selector

    html = "<html><body><main id='x' class='a b'>hi</main></body></html>"
    assert len(Selector(html).css("main")) == 1
    fingerprint = {
        "tag": "main", "original_selector": "main",
        "text_hash": sha256(b"hi").hexdigest(),
        "attrs": {"id": "x", "class": ["a", "b"]},
    }
    recovered = scrapling_adapter.recover_selector(html, fingerprint)
    assert recovered.ok is True
    # Tag enumeration would synthesize #x: this pins the original-selector
    # accessor separately from fingerprint construction.
    assert recovered.selector == "main"
    assert recovered.candidates_considered == 1


def test_real_selector_uses_first_match_and_distinguishes_missing_element():
    from scrapling import Selector

    html = "<main id='first'>hi</main><main id='second'>bye</main>"
    assert len(Selector(html).css("main")) == 2
    fingerprint = scrapling_adapter.build_fingerprint(html, "main")
    assert isinstance(fingerprint, dict)
    assert fingerprint["attrs"]["id"] == "first"
    assert scrapling_adapter.build_fingerprint(html, "aside") is None
    recovered = scrapling_adapter.recover_selector("<aside>bye</aside>", fingerprint)
    assert recovered.ok is False
    assert recovered.error == "no_candidates_above_threshold"


def test_missing_scrapling_is_distinct_from_no_matching_candidate(monkeypatch):
    html = "<main id='x' class='a b'>hi</main>"
    fingerprint = scrapling_adapter.build_fingerprint(html, "main")
    assert isinstance(fingerprint, dict)
    assert scrapling_adapter.recover_selector(html, fingerprint).ok is True
    missing_element = scrapling_adapter.recover_selector("<aside>bye</aside>", fingerprint)
    assert missing_element.ok is False
    assert missing_element.error == "no_candidates_above_threshold"

    monkeypatch.setitem(sys.modules, "scrapling", None)
    assert scrapling_adapter.capability_status()["adaptive_selectors"] == {
        "available": False, "status": "unavailable", "reason": "scrapling_not_installed",
    }
    assert scrapling_adapter.build_fingerprint(html, "main") is None
    missing_library = scrapling_adapter.recover_selector(html, fingerprint)
    assert missing_library.ok is False
    assert missing_library.error == "scrapling_not_installed"
    assert missing_library.error != missing_element.error


def test_adaptor_only_scrapling_remains_available(monkeypatch):
    module = _scrapling(adaptor=True)
    assert hasattr(module, "Adaptor"), "precondition: legacy fixture exports Adaptor"
    assert not hasattr(module, "Selector"), "precondition: legacy fixture has no Selector"
    monkeypatch.setitem(sys.modules, "scrapling", module)

    assert scrapling_adapter.capability_status()["adaptive_selectors"]["available"] is True
    assert scrapling_adapter.is_available() is True


def test_no_adaptive_selector_symbol_remains_unavailable(monkeypatch):
    module = _scrapling()
    assert not hasattr(module, "Selector") and not hasattr(module, "Adaptor"), (
        "precondition: fixture exports neither adaptive selector symbol"
    )
    monkeypatch.setitem(sys.modules, "scrapling", module)

    status = scrapling_adapter.capability_status()["adaptive_selectors"]
    assert status == {
        "available": False,
        "status": "unavailable",
        "reason": "adaptor_unavailable",
    }


def test_literal_minimal_score_contract_recovers_main():
    from scrapling import Selector

    html = "<html><body><main id='x' class='a b'>hi</main></body></html>"
    assert len(Selector(html).css("main")) == 1
    recovered = scrapling_adapter.recover_selector(
        html, {"tag": "main", "original_selector": "main"},
    )
    assert recovered.ok is True
    assert recovered.selector == "main"
    assert recovered.score == 1.0
    assert recovered.candidates_considered == 1


def test_missing_element_score_contract_s2():
    from scrapling import Selector

    html = "<aside id='x'>hi</aside>"
    page = Selector(html)
    assert len(page.css("aside")) == 1
    assert len(page.css("main")) == 0
    recovered = scrapling_adapter.recover_selector(
        html, {"tag": "main", "original_selector": "main"},
    )
    assert recovered.ok is False
    assert recovered.error == "no_candidates_above_threshold"
    assert recovered.candidates_considered == 0


def test_wrong_tag_score_contract_rejects_resolved_original():
    from scrapling import Selector

    html = "<aside id='x'>hi</aside>"
    assert Selector(html).css("#x")[0].tag == "aside"
    recovered = scrapling_adapter.recover_selector(
        html, {"tag": "main", "original_selector": "#x"},
    )
    assert recovered.ok is False
    assert recovered.error == "no_candidates_above_threshold"


@pytest.mark.parametrize("original", ["main", "#missing"])
def test_rich_threshold_score_contract_s1(original):
    from hashlib import sha256
    from scrapling import Selector

    html = "<main id='x' class='a b'>hi</main>"
    assert len(Selector(html).css("main")) == 1
    fingerprint = {
        "tag": "main", "original_selector": original,
        "text_hash": sha256(b"bye").hexdigest(), "text_preview": "bye",
        "attrs": {"id": "x", "class": ["a", "b"]},
    }
    # Tag/classes/id match, but the strongest supplied signal (text) misses.
    permissive = scrapling_adapter.recover_selector(html, fingerprint, min_score=0.45)
    assert permissive.ok is True
    assert permissive.score == pytest.approx(7 / 15)
    recovered = scrapling_adapter.recover_selector(html, fingerprint)
    assert recovered.ok is False
    assert recovered.error == "no_candidates_above_threshold"
    assert recovered.candidates_considered == 1


@pytest.mark.parametrize("original", ["main", "#missing"])
def test_conflicting_attributes_score_contract_s3(original):
    from scrapling import Selector

    html = "<main id='actual' class='actual'>hi</main>"
    assert Selector(html).css("main")[0].attrib["id"] == "actual"
    fingerprint = {
        "tag": "main", "original_selector": original,
        "attrs": {"id": "expected", "class": ["expected"]},
    }
    matching = scrapling_adapter.recover_selector(
        "<main id='expected' class='expected'>hi</main>", fingerprint,
    )
    assert matching.ok is True
    assert matching.score == 1.0
    # Supplied mismatches remain in the denominator: tag alone earns 3/7.
    permissive = scrapling_adapter.recover_selector(html, fingerprint, min_score=0.4)
    assert permissive.ok is True
    assert permissive.score == pytest.approx(3 / 7)
    for threshold in (0.45, 0.6):
        recovered = scrapling_adapter.recover_selector(html, fingerprint, min_score=threshold)
        assert recovered.ok is False
        assert recovered.error == "no_candidates_above_threshold"
        assert recovered.candidates_considered == 1


def test_preview_only_score_contract_normalizes_available_text_signal():
    from scrapling import Selector

    element = Selector("<main>hello world</main>").css("main")[0]
    assert scrapling_adapter._candidate_score(element, {"text_preview": "hello"}) == 1.0
    assert scrapling_adapter._candidate_score(element, {"text_preview": "bye"}) == 0.0


def test_empty_score_contract_has_no_evidence():
    from scrapling import Selector

    element = Selector("<main>hi</main>").css("main")[0]
    assert scrapling_adapter._candidate_score(element, {"tag": "main"}) == 1.0
    assert scrapling_adapter._candidate_score(element, {}) == 0.0
    assert scrapling_adapter._candidate_score(
        element, {"text_hash": "", "text_preview": "", "attrs": {}},
    ) == 0.0
