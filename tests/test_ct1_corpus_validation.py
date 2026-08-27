"""C-T1 validation against the real (scrubbed) recon_corpus fixtures.

These six captures are real authenticated member sessions, scrubbed at
v3.66.33. Each is a different site with a single capture, so they can't
provide a same-site 2-capture diff — the synthesis *logic* is covered by
test_ct1_capture_synth.py with controlled synthetic captures. This file
covers the complementary dimension: does the synthesizer survive real,
messy, large captures without crashing or leaking, and do the posture
invariants hold on real redacted data?

Loaded lazily (a module-level cache) so importing this file costs
nothing — the fixtures total ~19 MB.
"""

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, unquote

import pytest

from bulk_downloader.capture_synth import synthesize, classify_value
from bulk_downloader.capture_redact import PLACEHOLDER, SENSITIVE_QS_KEY

# This gate judges every shipped real-corpus fixture, so fixture changes alter
# its denominator independently of which production module a diff touches.
BD_GATE_SCOPE = "repo-wide"

_CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "recon_corpus"
_FIXTURES = sorted(p.name for p in _CORPUS_DIR.glob("*.json"))
_CACHE: dict = {}

KNOWN_TYPES = {"uuid", "sha256", "md5", "iso8601", "unix_ts", "id", "jwt",
               "filename", "token", "opaque", "empty", "redacted"}


def _load(name: str) -> dict:
    if name not in _CACHE:
        _CACHE[name] = json.loads(
            (_CORPUS_DIR / name).read_text(encoding="utf-8"))
    return _CACHE[name]


def _query_values(cap: dict):
    for e in (cap.get("network_log") or []):
        url = e.get("url") or ""
        if "?" not in url:
            continue
        for pair in url.partition("?")[2].split("&"):
            if "=" in pair:
                yield pair.partition("=")[2]


def _credential_census() -> dict[str, int]:
    metrics = {
        "fixtures": 0,
        "requests": 0,
        "parameters": 0,
        "credential_parameters": 0,
        "redacted_parameters": 0,
        "signing_parameters": 0,
    }
    for name in _FIXTURES:
        syn = synthesize(_load(name), _load(name))
        metrics["fixtures"] += 1
        metrics["requests"] += len(syn["requests"])
        for request in syn["requests"]:
            metrics["parameters"] += len(request["params"])
            for param in request["params"]:
                if param["credential"]:
                    metrics["credential_parameters"] += 1
                if (str(param.get("value_a", "")).startswith(PLACEHOLDER)
                        or str(param.get("value_b", "")).startswith(PLACEHOLDER)):
                    metrics["redacted_parameters"] += 1
                if SENSITIVE_QS_KEY.search(param["key"]):
                    metrics["signing_parameters"] += 1
    return metrics


class TestCorpusPresent:

    def test_fixtures_exist(self):
        # Guard against silently testing nothing if the corpus moves.
        assert len(_FIXTURES) >= 5, _FIXTURES
        metrics = _credential_census()
        assert metrics["parameters"] > 0, (
            "real corpus produced zero synthesized parameters eligible for "
            f"credential assertions: {metrics}")
        assert metrics == {
            "fixtures": 6,
            "requests": 106,
            "parameters": 47,
            "credential_parameters": 47,
            "redacted_parameters": 39,
            "signing_parameters": 42,
        }, metrics

    def test_credential_census_visits_every_fixture(self, monkeypatch):
        assert len(_FIXTURES) == 6, _FIXTURES
        real_synthesize = synthesize
        synthesized = []

        def counted_synthesize(cap_a, cap_b):
            synthesized.append((cap_a, cap_b))
            return real_synthesize(cap_a, cap_b)

        monkeypatch.setitem(globals(), "synthesize", counted_synthesize)
        self.test_fixtures_exist()
        assert len(synthesized) == len(_FIXTURES), (
            f"credential census synthesized {len(synthesized)} fixtures; "
            f"expected {len(_FIXTURES)}")

    def test_credential_census_rejects_zero_eligible_parameters(
            self, monkeypatch):
        assert len(_FIXTURES) == 6, _FIXTURES
        synthesized = []
        no_eligible_parameters = {"requests": [{"params": []}]}
        assert len(no_eligible_parameters["requests"]) == 1
        assert no_eligible_parameters["requests"][0]["params"] == []

        def empty_parameter_synthesize(cap_a, cap_b):
            synthesized.append((cap_a, cap_b))
            return no_eligible_parameters

        monkeypatch.setitem(globals(), "synthesize", empty_parameter_synthesize)
        with pytest.raises(
                AssertionError,
                match="zero synthesized parameters eligible for credential assertions"):
            self.test_fixtures_exist()
        assert len(synthesized) == len(_FIXTURES), (
            f"negative control synthesized {len(synthesized)} fixtures; "
            f"expected {len(_FIXTURES)}")

    def test_credential_census_rejects_exact_metric_drift(self, monkeypatch):
        assert len(_FIXTURES) == 6, _FIXTURES
        real_synthesize = synthesize
        synthesized = []

        def extra_empty_request_synthesize(cap_a, cap_b):
            syn = real_synthesize(cap_a, cap_b)
            assert isinstance(syn["requests"], list)
            synthesized.append((cap_a, cap_b))
            return {**syn, "requests": [*syn["requests"], {"params": []}]}

        monkeypatch.setitem(
            globals(), "synthesize", extra_empty_request_synthesize)
        with pytest.raises(AssertionError) as raised:
            self.test_fixtures_exist()
        assert len(synthesized) == len(_FIXTURES), (
            f"metric-drift control synthesized {len(synthesized)} fixtures; "
            f"expected {len(_FIXTURES)}")
        failure = str(raised.value)
        assert "'requests': 112" in failure, failure
        assert "'parameters': 47" in failure, failure

    def test_transform_control_imports_census_without_asserting_behavior(self):
        module = __import__(__name__)
        getattr(module, "_credential_census")


class TestCorpusRobustness:

    @pytest.mark.parametrize("name", _FIXTURES)
    def test_self_diff_wellformed(self, name):
        cap = _load(name)
        syn = synthesize(cap, cap)
        for k in ("capture_synth_version", "synthesized", "needs_review",
                  "confidence", "host", "requests", "credentials_required",
                  "unresolved", "session_specific", "summary", "notes"):
            assert k in syn, (name, k)
        assert syn["confidence"] == "low"
        assert syn["needs_review"] is True
        assert isinstance(syn["summary"], str) and syn["summary"]
        # Self-diff: nothing genuinely varies, so EVERY param slot that
        # appears must be a (redacted) credential, never a varying param.
        for r in syn["requests"]:
            for p in r["params"]:
                assert p["credential"] is True, (name, r["key"], p)

    def test_cross_pair_no_crash(self):
        a = _load(_FIXTURES[0])
        b = _load(_FIXTURES[1])
        syn = synthesize(a, b)
        # Different sites → the bulk of requests are session/site-specific.
        ss = syn["session_specific"]
        assert isinstance(ss["only_in_a"], list)
        assert isinstance(ss["only_in_b"], list)
        assert isinstance(syn["requests"], list)


class TestCorpusPosture:

    @pytest.mark.parametrize("name", _FIXTURES)
    def test_credentials_never_inlined(self, name):
        # A synthesized template must never carry a scrubbed credential as
        # a literal — credentials are always slotted as {key}.
        syn = synthesize(_load(name), _load(name))
        for r in syn["requests"]:
            assert PLACEHOLDER not in r["url_template"], (name, r["key"])

    @pytest.mark.parametrize("name", _FIXTURES)
    def test_redacted_params_flagged_and_not_traced(self, name):
        syn = synthesize(_load(name), _load(name))
        for r in syn["requests"]:
            for p in r["params"]:
                redacted = (str(p.get("value_a", "")).startswith(PLACEHOLDER)
                            or str(p.get("value_b", "")).startswith(PLACEHOLDER))
                if redacted:
                    assert p["credential"] is True, (name, p)
                    assert p["type"] == "redacted", (name, p)
                    # Never traced to a real source.
                    assert p["source"] == "redacted_credential", (name, p)

    @pytest.mark.parametrize("name", _FIXTURES)
    def test_redacted_credentials_not_in_unresolved(self, name):
        # Required credentials are inputs, not dataflow gaps.
        syn = synthesize(_load(name), _load(name))
        cred_keys = {c.split(" ")[0] for c in syn["credentials_required"]}
        for u in syn["unresolved"]:
            assert u["param"] not in cred_keys, (name, u)


class TestSignedUrlNeverLeaks:
    """No CloudFront/short-lived signing *value* may survive into the
    synthesized output for ANY corpus fixture, after URL-decoding.

    We assert on the VALUES, not the marker key-names: a slot like
    ``Key-Pair-Id={Key-Pair-Id}`` in a url_template is the synthesizer
    correctly redacting the value into a named slot — the name is benign
    and tells the operator which credential the request needs. The
    guarantee is that the secret value behind it is gone. Holds whether or
    not the fixture is fully scrubbed; the defense-in-depth itself is
    unit-tested synthetically in test_ct1_capture_synth.py.
    """

    # Distinctive CloudFront signing params whose VALUES must never leak.
    _SIGN_VAL = re.compile(
        r'(?:Signature|Policy|Key-Pair-Id)=([^&\s"\'}]+)', re.I)

    @pytest.mark.parametrize("name", _FIXTURES)
    def test_no_signing_value_survives(self, name):
        cap = _load(name)
        secrets = set()
        for e in (cap.get("network_log") or []):
            # Double-unquote so a nested (URL-encoded) signed URL is seen.
            dec = unquote(unquote(e.get("url") or ""))
            for m in self._SIGN_VAL.finditer(dec):
                v = m.group(1)
                if (v and len(v) >= 8 and v != PLACEHOLDER
                        and not v.startswith("{")):
                    secrets.add(v)
        if not secrets:
            return  # fully scrubbed fixture — nothing to leak
        syn = synthesize(cap, cap)
        blob = unquote(unquote(json.dumps(syn)))
        leaked = sorted(s for s in secrets if s in blob)
        assert not leaked, f"{name}: signing value(s) leaked: {leaked[:2]}"

    @pytest.mark.parametrize("name", _FIXTURES)
    def test_signing_params_are_credentialed(self, name):
        # Any request carrying a signing param must mark it as a masked
        # credential (never an inlined constant).
        syn = synthesize(_load(name), _load(name))
        for r in syn["requests"]:
            for p in r["params"]:
                if SENSITIVE_QS_KEY.search(p["key"]):
                    assert p["credential"] is True, (name, r["key"], p["key"])
                    assert p["value_a"] in (PLACEHOLDER, "<signed_url>",
                                            "<credential>")

    def test_classify_returns_known_label_for_all_corpus_values(self):
        seen = Counter()
        total = 0
        for name in _FIXTURES:
            for v in _query_values(_load(name)):
                label = classify_value(v)
                assert label in KNOWN_TYPES, (name, v[:40], label)
                seen[label] += 1
                total += 1
        # Visible in -v output; not an assertion target (real data varies).
        print(f"\nclassified {total} real query values: {dict(seen)}")
        assert total > 0
