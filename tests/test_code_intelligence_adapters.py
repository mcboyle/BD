from dataclasses import dataclass
from pathlib import Path

import json
import math
import multiprocessing
from multiprocessing.reduction import ForkingPickler
import pickle
import re

import pytest

from tools.code_intelligence.adapters import (
    AdapterBudget,
    AdapterCase,
    AdapterContext,
    artifact_filename_component,
    clear_adapters_for_test,
    get_adapter,
    list_adapters,
    register_adapter,
)
from tools.code_intelligence.results import CheckResult, ResultState


@dataclass(frozen=True)
class _Adapter:
    name: str = "fixture-oracle"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("same", {"left": 1, "right": 1}),)

    def run(self, case, context):
        return CheckResult(self.name, ResultState.PASS, case.case_id, {"equal": True})


def setup_function():
    clear_adapters_for_test()


def test_registry_is_sorted_and_kind_filtered(tmp_path):
    register_adapter(_Adapter(name="zeta-oracle"))
    register_adapter(_Adapter(name="fixture-oracle"))
    register_adapter(_Adapter(name="alpha-oracle"))
    assert list_adapters() == ("alpha-oracle", "fixture-oracle", "zeta-oracle")
    assert list_adapters(kind="oracle") == ("alpha-oracle", "fixture-oracle", "zeta-oracle")
    assert list_adapters(kind="fuzz") == ()
    ctx = AdapterContext(
        tmp_path, tmp_path / "artifacts", tmp_path / "corpus", 7,
        AdapterBudget(1.0, 5, 4096),
    )
    assert get_adapter("fixture-oracle").cases(ctx)[0].case_id == "same"


def test_duplicate_adapter_name_is_rejected():
    register_adapter(_Adapter())
    with pytest.raises(ValueError, match="duplicate adapter: fixture-oracle"):
        register_adapter(_Adapter())


def test_invalid_budget_is_rejected():
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        AdapterBudget(0.0, 1, 1024)


def test_unknown_adapter_is_explicit():
    with pytest.raises(KeyError, match="unknown adapter: missing"):
        get_adapter("missing")


@pytest.mark.parametrize("timeout", [True, math.nan, math.inf, -math.inf])
def test_budget_rejects_nonfinite_or_boolean_timeout(timeout):
    with pytest.raises(ValueError, match="timeout_seconds must be positive and finite"):
        AdapterBudget(timeout, 1, 1024)


@pytest.mark.parametrize("field,value", [("max_cases", True), ("max_output_bytes", False)])
def test_budget_requires_exact_positive_integer_limits(field, value):
    with pytest.raises(ValueError, match=rf"{field} must be a positive integer"):
        AdapterBudget(1.0, value if field == "max_cases" else 1, value if field == "max_output_bytes" else 1024)


@pytest.mark.parametrize("name", ["", " whitespace ", "bad\x00name"])
def test_registration_rejects_invalid_adapter_names_without_echoing_them(name):
    with pytest.raises(ValueError, match="adapter name must be a nonempty safe identifier") as error:
        register_adapter(_Adapter(name=name))
    if name:
        assert name not in str(error.value)


def test_registration_rejects_unsupported_kind():
    raw_kind = "invalid-secret-kind"
    with pytest.raises(ValueError, match="unsupported adapter kind") as error:
        register_adapter(_Adapter(kind=raw_kind))
    assert raw_kind not in str(error.value)


@pytest.mark.parametrize(
    "case_id,payload",
    [("", {}), ("bad\x00id", {}), ("valid", {"value": math.nan}), ("valid", {"api_token": "not-for-fixtures"})],
)
def test_adapter_case_rejects_invalid_id_or_non_json_safe_payload(case_id, payload):
    with pytest.raises(ValueError):
        AdapterCase(case_id, payload)


def test_context_requires_path_seed_and_budget_types(tmp_path):
    budget = AdapterBudget(1.0, 1, 1024)
    with pytest.raises(TypeError, match="repo_root must be a Path"):
        AdapterContext(str(tmp_path), tmp_path, tmp_path, 1, budget)
    with pytest.raises(TypeError, match="seed must be an int"):
        AdapterContext(tmp_path, tmp_path, tmp_path, True, budget)
    with pytest.raises(TypeError, match="budget must be an AdapterBudget"):
        AdapterContext(tmp_path, tmp_path, tmp_path, 1, object())


def test_registration_requires_protocol_shape():
    with pytest.raises(TypeError, match="adapter must provide name, kind, cases, and run"):
        register_adapter(object())


def _nested_payload(depth):
    payload = 0
    for _ in range(depth):
        payload = [payload]
    return payload


def test_payload_depth_limit_is_value_free_and_not_recursive():
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free") as error:
        AdapterCase("deep/corpus.case", _nested_payload(1_000))
    assert "[" not in str(error.value)


def test_payload_cycle_is_value_free_and_not_recursive():
    payload = []
    payload.append(payload)
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free") as error:
        AdapterCase("cycle.case", payload)
    assert "payload" in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [10 ** 5_000, complex(1, 2), ("not", "a", "list"), {"nested": {1, 2}}],
    ids=["huge-int", "complex", "tuple", "set"],
)
def test_payload_rejects_non_serializable_values_without_contents(payload):
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free") as error:
        AdapterCase("numeric.case", payload)
    assert "5000" not in str(error.value)


@pytest.mark.parametrize(
    "key",
    [
        "cookie",
        "sessionCookie",
        "signed_query",
        "authorizationHeader",
        "privateKey",
        "accessToken",
        "raw_body",
    ],
)
def test_secret_key_variants_are_rejected_without_echoing_values(key):
    secret_value = "do-not-echo-this-secret"
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free") as error:
        AdapterCase("secret.case", {key: secret_value})
    assert secret_value not in str(error.value)
    assert key not in str(error.value)


def test_only_explicit_redaction_markers_and_counts_are_allowed_for_secret_metadata():
    case = AdapterCase(
        "redaction.case",
        {
            "authorizationHeaderRedacted": True,
            "private_key_redacted": "<redacted>",
            "access-token-redacted": "[REDACTED]",
            "cookieCount": 2,
            "signed_query_count": 0,
            "rawBodyCount": 1,
        },
    )
    assert json.loads(json.dumps(case.payload)) == {
        "authorizationHeaderRedacted": True,
        "private_key_redacted": "<redacted>",
        "access-token-redacted": "[REDACTED]",
        "cookieCount": 2,
        "signed_query_count": 0,
        "rawBodyCount": 1,
    }


@pytest.mark.parametrize(
    "payload",
    [{"accessTokenRedacted": "actual-secret"}, {"cookieCount": True}, {"raw_body_count": -1}],
)
def test_secret_metadata_rejects_non_redacted_content(payload):
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free"):
        AdapterCase("metadata.case", payload)


def test_case_payload_is_a_deep_immutable_json_snapshot():
    source = {"items": [{"name": "original"}]}
    case = AdapterCase("snapshot.case", source)
    source["items"][0]["name"] = "changed"
    source["items"].append({"name": "injected"})
    assert case.payload == {"items": [{"name": "original"}]}
    with pytest.raises(TypeError):
        case.payload["items"].append({"name": "blocked"})
    with pytest.raises(TypeError):
        case.payload["items"][0]["name"] = "blocked"
    assert json.loads(json.dumps(case.payload)) == {"items": [{"name": "original"}]}


@pytest.mark.parametrize("identifier", ["Adapter_Name.2", "Corpus/Case_1.v2", "UPPER.CASE"])
def test_printable_bounded_identifiers_support_corpus_conventions(identifier):
    case = AdapterCase(identifier, {"value": 1})
    assert case.case_id == identifier


@pytest.mark.parametrize("name", ["Adapter_Name.2", "UPPER.CASE", "adapter-name"])
def test_adapter_names_use_a_safe_echoable_identifier_grammar(name):
    register_adapter(_Adapter(name=name))
    assert get_adapter(name).name == name


@pytest.mark.parametrize("name", ["path/name", r"path\\name", "colon:name", "white space", "bad\x00name"])
def test_adapter_names_reject_path_or_unsafe_characters_without_echoing(name):
    with pytest.raises(ValueError, match="adapter name must be a nonempty safe identifier") as error:
        register_adapter(_Adapter(name=name))
    assert name not in str(error.value)


class _MutableAdapter:
    def __init__(self):
        self.name = "mutable.adapter"
        self.kind = "oracle"

    def cases(self, context):
        return (AdapterCase("mutable.case", {"stable": True}),)

    def run(self, case, context):
        return CheckResult("delegated", ResultState.PASS, case.case_id, {"stable": True})


def test_registry_snapshots_identity_but_delegates_behavior(tmp_path):
    source = _MutableAdapter()
    register_adapter(source)
    registered = get_adapter("mutable.adapter")
    source.name = "changed.adapter"
    source.kind = "fuzz"
    assert registered.name == "mutable.adapter"
    assert registered.kind == "oracle"
    assert list_adapters(kind="oracle") == ("mutable.adapter",)
    assert list_adapters(kind="fuzz") == ()
    context = AdapterContext(tmp_path, tmp_path, tmp_path, 1, AdapterBudget(1.0, 1, 1024))
    case = registered.cases(context)[0]
    assert registered.run(case, context).summary == "mutable.case"


def _spawn_case_round_trip(serialized_case, connection):
    case = pickle.loads(serialized_case)
    immutable = False
    try:
        case.payload["nested"].append("blocked")
    except TypeError:
        immutable = True
    connection.send((case.case_id, json.loads(json.dumps(case.payload)), immutable))
    connection.close()


def test_frozen_payloads_survive_pickle_and_forking_pickler_round_trips():
    case = AdapterCase("pickle.case", {"nested": [{"value": 1}]})
    for serialized in (pickle.dumps(case), ForkingPickler.dumps(case)):
        restored = pickle.loads(serialized)
        assert restored == case
        assert json.loads(json.dumps(restored.payload)) == {"nested": [{"value": 1}]}
        with pytest.raises(TypeError):
            restored.payload["nested"].append("blocked")


def test_frozen_payloads_survive_a_real_spawn_process_round_trip():
    case = AdapterCase("spawn.case", {"nested": [{"value": 1}]})
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_spawn_case_round_trip, args=(pickle.dumps(case), child))
    process.start()
    child.close()
    result = parent.recv()
    process.join(timeout=10)
    assert process.exitcode == 0
    assert result == ("spawn.case", {"nested": [{"value": 1}]}, True)


@pytest.mark.parametrize(
    "key",
    [
        "cookies",
        "credentials",
        "passwords",
        "secrets",
        "tokens",
        "privateKeys",
        "accessTokens",
        "signedQueries",
        "rawBodies",
    ],
)
def test_plural_secret_keys_reject_unrestricted_values(key):
    secret_value = "do-not-echo-this-secret"
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free") as error:
        AdapterCase("plural-secret.case", {key: secret_value})
    assert secret_value not in str(error.value)
    assert key not in str(error.value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("cookies_redacted", True),
        ("credentials_count", 2),
        ("passwords_redacted", "<redacted>"),
        ("secrets_count", 0),
        ("tokens_redacted", "[REDACTED]"),
        ("privateKeys_count", 1),
        ("accessTokens_redacted", True),
        ("signedQueries_count", 3),
        ("rawBodies_redacted", True),
    ],
)
def test_plural_secret_metadata_allows_only_exact_redactions_or_counts(key, value):
    case = AdapterCase("plural-metadata.case", {key: value})
    assert json.loads(json.dumps(case.payload)) == {key: value}


@pytest.mark.parametrize(
    "key,value",
    [("cookies_redacted", "actual-secret"), ("credentials_count", True), ("rawBodies_count", -1)],
)
def test_plural_secret_metadata_rejects_non_safe_values(key, value):
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free"):
        AdapterCase("plural-invalid.case", {key: value})


class _HostileList(list):
    def __len__(self):
        raise RuntimeError("hostile-list-secret")

    def __iter__(self):
        raise RuntimeError("hostile-list-secret")


class _HostileDict(dict):
    def __len__(self):
        raise RuntimeError("hostile-dict-secret")

    def items(self):
        raise RuntimeError("hostile-dict-secret")


class _HostileString(str):
    def __str__(self):
        raise RuntimeError("hostile-string-secret")


@pytest.mark.parametrize("payload", [_HostileList(), _HostileDict(), _HostileString("value")])
def test_hostile_json_subclasses_are_rejected_without_leaking_errors(payload):
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free") as error:
        AdapterCase("hostile.case", payload)
    assert "hostile" not in str(error.value)


@pytest.mark.parametrize(
    "logical_id",
    ["../escape", r"..\\escape", "colon:value", ".", "..", "CON", "Café/東京", "token:do-not-echo-this-secret"],
)
def test_artifact_filename_component_is_safe_and_does_not_expose_raw_logical_ids(logical_id):
    component = artifact_filename_component(logical_id)
    assert component == artifact_filename_component(logical_id)
    assert component not in {".", ".."}
    assert len(component) <= 64
    assert len(component.removeprefix("artifact-")) >= 32
    assert re.fullmatch(r"[a-z0-9-]+", component)
    assert not any(character in component for character in "\\/:.")
    assert "do-not-echo-this-secret" not in component


def test_artifact_filename_component_uses_a_stable_hash_to_avoid_collisions():
    assert artifact_filename_component("Corpus/Case_1.v2") != artifact_filename_component("Corpus:Case_1.v2")


@pytest.mark.parametrize(
    "key",
    [
        "apiKeys",
        "api_keys",
        "myApiKey",
        "myapikey",
        "sshPrivateKey",
        "sshPrivateKeys",
        "sshprivatekeys",
        "capturedRawBody",
        "capturedrawbody",
        "requestSignedQuery",
        "requestsignedquery",
    ],
)
def test_compound_prefixed_and_plural_secret_keys_reject_unrestricted_values(key):
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free"):
        AdapterCase("compound-secret.case", {key: "do-not-echo-this-secret"})


@pytest.mark.parametrize(
    "key,value",
    [
        ("myApiKeyRedacted", True),
        ("my_api_key_redacted", "<redacted>"),
        ("myapikeyredacted", "[REDACTED]"),
        ("myApiKeyCount", 2),
        ("sshPrivateKeys_count", 1),
        ("capturedRawBodyRedacted", True),
        ("requestSignedQuery_count", 0),
    ],
)
def test_compound_secret_metadata_requires_exact_safe_markers_or_counts(key, value):
    case = AdapterCase("compound-metadata.case", {key: value})
    assert json.loads(json.dumps(case.payload)) == {key: value}


@pytest.mark.parametrize(
    "key,value",
    [
        ("myApiKeyRedacted", "actual-secret"),
        ("myApiKeyCount", True),
        ("sshPrivateKeys_count", -1),
        ("capturedRawBodyRedacted", False),
    ],
)
def test_compound_secret_metadata_rejects_unrestricted_values(key, value):
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free"):
        AdapterCase("compound-invalid.case", {key: value})


@pytest.mark.parametrize("key", ["monkey", "tokenizer"])
def test_non_sensitive_words_do_not_trigger_secret_policy(key):
    assert AdapterCase("false-positive.case", {key: "ordinary"}).payload == {key: "ordinary"}


@pytest.mark.parametrize("key", ["passwordAccount", "secretAccount", "accessTokenDiscount"])
def test_sensitive_tokens_are_not_lost_to_embedded_compact_metadata_suffixes(key):
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free"):
        AdapterCase("sensitive-boundary.case", {key: 1})


@pytest.mark.parametrize(
    "key",
    [
        "privateKeyboardLayout",
        "apiTokenizer",
        "accessTokenizer",
        "authorizationHeaderlessMode",
        "rawBodyguard",
        "signedQueryable",
    ],
)
def test_benign_compound_words_are_not_secret_key_matches(key):
    assert AdapterCase("benign-boundary.case", {key: 1}).payload == {key: 1}


@pytest.mark.parametrize("key", ["proxyAuthorization", "requestAuthorization", "authorizations"])
def test_terminal_authorization_forms_are_sensitive(key):
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free"):
        AdapterCase("authorization-boundary.case", {key: 1})


def test_terminal_authorization_redaction_rejects_unsafe_marker():
    with pytest.raises(ValueError, match="payload must be JSON-safe and secret-free"):
        AdapterCase("authorization-boundary.case", {"requestAuthorizationRedacted": "actual-secret"})


@pytest.mark.parametrize(
    "key,value",
    [
        ("proxyAuthorizationRedacted", True),
        ("requestAuthorizationCount", 1),
        ("authorizations_redacted", "<redacted>"),
    ],
)
def test_terminal_authorization_metadata_accepts_exact_safe_values(key, value):
    case = AdapterCase("authorization-metadata.case", {key: value})
    assert json.loads(json.dumps(case.payload)) == {key: value}
