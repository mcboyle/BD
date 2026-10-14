"""dss-type-500s (deep-settings-secrets F6, F7): wrong-typed secrets fields get a named 400.

F6: change_password new_password int, secrets/delete site_id list/dict, import_apply
records/site_ids scalar, extension/pair pairing_token list and extension/revoke id
int/list all raised inside the route (HTTP 500). extension/pair also echoed the raw
request label (a dict, or 200k chars) while it stored the sanitized one.

F7: app.py's AttributeError handler answered "request body must be a JSON object"
for any AttributeError on /api/, so unlock password int/list/dict/bool,
change_password old_password int/list and fetch_one id int/list got a 400 that
blamed a well-formed JSON object and never named the field.

A non-string value must be refused by name BEFORE it reaches the backend.
The values below are fixed test strings, not credentials.
"""

from __future__ import annotations

import pytest
from flask import Flask

BD_GATE_SCOPE = "module"


class _Backend:
    """Records every call; a non-string argument reaching it is the defect."""

    name = "master_password"

    def __init__(self):
        self.calls = []

    def _rec(self, op, *args):
        self.calls.append((op, args))
        return True

    def unlock(self, password):
        return self._rec("unlock", password)

    def change_password(self, old, new):
        return self._rec("change_password", old, new)

    def delete(self, key):
        return self._rec("delete", key)

    def is_initialized(self):
        return True

    def is_unlocked(self):
        return True


@pytest.fixture
def client(monkeypatch):
    from bulk_downloader import app_secrets as AS
    from bulk_downloader import extension_vault as EV
    from bulk_downloader import secrets_store as SS

    backend = _Backend()
    monkeypatch.setattr(SS, "get_backend", lambda: backend)
    monkeypatch.setattr(AS, "_reject_if_vault_token", lambda *a, **k: None)
    monkeypatch.setattr(AS, "_require_vault_token", lambda *a, **k: ("vt", {}, None))
    monkeypatch.setattr(AS, "_app_s_cfg", dict)
    monkeypatch.setattr(
        EV, "redeem_pairing_token", lambda tok, extension_label="": "vt-dss"
    )
    monkeypatch.setattr(EV, "revoke_by_prefix", lambda prefix: False)
    app = Flask(__name__)
    app.register_blueprint(AS.secrets_bp)
    c = app.test_client()
    c.backend = backend
    return c


_CASES = [
    ("unlock", {"password": 123}, "password must be a string"),
    ("unlock", {"password": ["x"]}, "password must be a string"),
    ("unlock", {"password": {"a": 1}}, "password must be a string"),
    ("unlock", {"password": True}, "password must be a string"),
    (
        "change_password",
        {"old_password": 123, "new_password": "dss-new-pass-1"},
        "old_password must be a string",
    ),
    (
        "change_password",
        {"old_password": ["x"], "new_password": "dss-new-pass-1"},
        "old_password must be a string",
    ),
    (
        "change_password",
        {"old_password": "dss-old-pass", "new_password": 12345678},
        "new_password must be a string",
    ),
    ("delete", {"site_id": ["wow"]}, "site_id must be a non-empty string"),
    ("delete", {"site_id": {"a": 1}}, "site_id must be a non-empty string"),
    ("import_apply", {"records": 123}, "records must be a list"),
    ("import_apply", {"records": [], "site_ids": 123}, "site_ids must be a list"),
    ("import_apply", {"records": {}}, "records must be a list"),
    ("import_apply", {"records": [], "site_ids": {}}, "site_ids must be a list"),
    ("import_apply", {"records": False}, "records must be a list"),
    ("import_apply", {"records": [], "site_ids": False}, "site_ids must be a list"),
    ("import_apply", {"records": 0}, "records must be a list"),
    ("import_apply", {"records": [], "site_ids": 0}, "site_ids must be a list"),
    ("import_apply", {"records": ""}, "records must be a list"),
    ("import_apply", {"records": [], "site_ids": ""}, "site_ids must be a list"),
    ("extension/pair", {"pairing_token": ["x"]}, "pairing_token must be a string"),
    ("extension/revoke", {"id": 5}, "id must be a string"),
    ("extension/revoke", {"id": ["ab"]}, "id must be a string"),
    ("extension/fetch_one", {"id": 5}, "id must be a string"),
    ("extension/fetch_one", {"id": ["site:wow"]}, "id must be a string"),
]


@pytest.mark.parametrize(
    "route, body, error",
    _CASES,
    ids=[f"{r}-{e.split()[0]}-{i}" for i, (r, _, e) in enumerate(_CASES)],
)
def test_wrong_typed_field_gets_a_named_400(client, route, body, error):
    r = client.post(f"/api/secrets/{route}", json=body)
    leaked = [
        c for c in client.backend.calls if any(not isinstance(a, str) for a in c[1])
    ]
    assert not leaked, (
        f"DSS_NONSTR_REACHED_BACKEND: {route} {body!r} called the backend with {leaked!r}"
    )
    got = r.get_json(silent=True) or {}
    assert r.status_code == 400 and got.get("error") == error, (
        f"DSS_TYPE_NOT_NAMED: {route} {body!r} -> HTTP {r.status_code} {r.get_data(as_text=True)[:160]!r}, "
        f"expected 400 {error!r}"
    )


def test_delete_key_list_control(client):
    """Positive control (already right at BASE): the key field is refused by name."""
    r = client.post("/api/secrets/delete", json={"key": ["x"]})
    assert (
        r.status_code == 400 and "must be a non-empty string" in r.get_json()["error"]
    )


@pytest.mark.parametrize(
    "body", [{}, {"records": None}, {"records": [], "site_ids": None}]
)
def test_import_apply_absent_or_null_still_means_empty(client, body):
    """Over-correction control (r2): only a wrong-typed value is refused; an absent
    or null records/site_ids is still an empty import."""
    r = client.post("/api/secrets/import_apply", json=body)
    assert r.status_code == 200 and r.get_json().get("saved") == 0, (
        f"DSS_IMPORT_NULL_REFUSED: {body!r} -> {r.status_code} {r.get_data(as_text=True)[:120]!r}"
    )


def test_revoke_string_id_control(client):
    r = client.post("/api/secrets/extension/revoke", json={"id": "ab"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "removed": False}


@pytest.mark.parametrize("label", [{"a": 1}, "L" * 200_000, "Chrome <b>on</b> laptop"])
def test_pair_echoes_the_label_it_stores(client, label):
    from bulk_downloader import extension_vault as EV

    r = client.post(
        "/api/secrets/extension/pair",
        json={"pairing_token": "dss-pair", "label": label},
    )
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert r.get_json()["label"] == EV._sanitize_label(label), (
        f"DSS_PAIR_LABEL_ECHO: response label {str(r.get_json()['label'])[:60]!r} "
        f"(len {len(str(r.get_json()['label']))}) is not the stored {EV._sanitize_label(label)!r}"
    )


def _handler_message(json_body):
    from bulk_downloader import app as A

    with A.app.test_request_context(
        "/api/secrets/unlock", method="POST", json=json_body
    ):
        resp = A._on_attribute_error(
            AttributeError("'int' object has no attribute 'encode'")
        )
    if isinstance(resp, tuple):
        return resp[1], resp[0].get_json()
    return resp.status_code, resp.get_json()


def test_attribute_error_on_an_object_body_does_not_blame_the_body_shape():
    status, body = _handler_message({"password": 123})
    assert status == 400
    assert body.get("error") != "request body must be a JSON object", (
        f"DSS_HANDLER_BLAMES_BODY: a well-formed JSON object got {body!r}"
    )


@pytest.mark.parametrize("bad_body", [[1, 2], "text", 7])
def test_attribute_error_on_a_non_object_body_still_names_the_shape(bad_body):
    """Control: the handler's original job (array/scalar body) is unchanged."""
    status, body = _handler_message(bad_body)
    assert (
        status == 400 and body.get("error") == "request body must be a JSON object"
    ), body
