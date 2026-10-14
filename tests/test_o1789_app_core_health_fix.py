"""O1789 app-core-surface F1-F3 (health-core-fix r1).

F1  With auth configured, an unauthenticated GET /api/health answered the
    absolute sites_config path. The probe stays open (scripts/deploy.sh and
    install_service.sh read its counts / credentials / download_hold blocks
    with no token), so the fix withholds the path only, and only from a
    caller that presents no operator credential.
F2  A non-build install dir (no .git, no build_info.json) spawned
    ``git rev-parse`` on EVERY /api/health. A git-confirmed absence is now
    remembered while both markers stay absent; a failure is still never
    cached (row 521).
F3  The non-sites site-lifecycle stripe was acquired BEFORE _check_token, so
    an unauthenticated POST queued on it and then got 401.

F4  (r2) _attach_sites_config_health read flask.g unguarded; outside an
    app/request context (boot, config-load child) the proxy raised
    RuntimeError and the boot path died (T281i FIXFWD note). Without a
    context the flag is unset = full payload.

Every failure message starts with APPCORE-F<n>.
"""
import json
import subprocess

import pytest

BD_GATE_SCOPE = "module"
TOK = "appcore-health-fix-token"
AUTH = {"Authorization": f"Bearer {TOK}"}

# The keys scripts/deploy.sh (_vault_health_diagnosis and the
# locked-no-references check) and install_service.sh read off an
# UNAUTHENTICATED /api/health body. Withholding any of them fails a deploy.
DEPLOY_READS_TOP = ("ok", "db_ok", "queue_depth", "active_downloads",
                    "sites_loaded", "version", "vault_ready", "credentials",
                    "download_hold")
DEPLOY_READS_CREDENTIALS = ("backend", "ok", "state", "is_initialized",
                            "is_unlocked", "reference_count", "stored_count",
                            "unavailable_count", "missing_count",
                            "resolved_count")


@pytest.fixture
def client(fresh_app, monkeypatch, tmp_path):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.setenv("BD_AUTH_TOKEN", TOK)
    return fresh_app


def _strings(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{prefix}[{i}]")
    elif isinstance(obj, str):
        yield prefix, obj


def _sites_config_path():
    import bulk_downloader.app as A
    return str(A.SITES_FILE)


# ── F1 ────────────────────────────────────────────────────────────────────

def test_f1_unauthenticated_health_withholds_the_config_path(client):
    path = _sites_config_path()
    r = client.get("/api/health")
    body = r.get_json(silent=True)
    assert r.status_code in (200, 503), (r.status_code, body)
    assert isinstance(body, dict) and isinstance(body.get("sites_config"), dict), (
        "APPCORE-F1 no sites_config block to judge", body)
    shown = [p for p, v in _strings(body) if path in v]
    assert not shown, (
        f"APPCORE-F1 unauthenticated /api/health shows the config path at {shown}")
    assert "state" in body["sites_config"], body["sites_config"]


@pytest.mark.parametrize("headers", [AUTH, {"X-BD-Token": TOK}],
                         ids=["bearer", "x-bd-token"])
def test_f1_operator_credential_still_sees_the_path(client, headers):
    """Positive control: the same probe CAN say yes."""
    r = client.get("/api/health", headers=headers)
    body = r.get_json(silent=True) or {}
    assert body.get("sites_config", {}).get("path") == _sites_config_path(), (
        "APPCORE-F1-CONTROL credentialed caller lost the path", body.get("sites_config"))


def test_f1_wrong_token_is_the_unauthenticated_view(client):
    r = client.get("/api/health", headers={"Authorization": "Bearer nope"})
    body = r.get_json(silent=True) or {}
    assert r.status_code in (200, 503), (r.status_code, body)
    assert "path" not in body.get("sites_config", {}), (
        "APPCORE-F1 a wrong bearer was shown the config path", body.get("sites_config"))


def test_f1_auth_off_install_keeps_the_full_payload(fresh_app, monkeypatch, tmp_path):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    import bulk_downloader.app as A
    assert not A._accepted_tokens(), "precondition: auth not configured"
    body = fresh_app.get("/api/health").get_json(silent=True) or {}
    assert body.get("sites_config", {}).get("path") == _sites_config_path(), (
        "APPCORE-F1-CONTROL auth-off install lost the path", body.get("sites_config"))


def test_f1_unauthenticated_view_keeps_every_key_deploy_reads(client):
    """Negative control: the fix must not narrow what deploy.sh reads."""
    body = client.get("/api/health").get_json(silent=True) or {}
    missing = [k for k in DEPLOY_READS_TOP if k not in body]
    assert not missing, f"APPCORE-F1-DEPLOY unauthenticated body lost {missing}"
    cred = body["credentials"]
    missing = [k for k in DEPLOY_READS_CREDENTIALS if k not in cred]
    assert not missing, f"APPCORE-F1-DEPLOY credentials block lost {missing}"
    hold = body["download_hold"]
    assert {"state", "downloads_allowed"} <= set(hold), (
        "APPCORE-F1-DEPLOY download_hold lost a key", hold)


# ── F2 ────────────────────────────────────────────────────────────────────

class _Git:
    """Answers only build_identity's git calls against ``root``."""

    def __init__(self, root, *, rc, stderr):
        self.root, self.rc, self.stderr = str(root), rc, stderr
        self.calls = []
        self._real = subprocess.run

    def __call__(self, argv, *args, **kwargs):
        if not (isinstance(argv, (list, tuple)) and argv and argv[0] == "git"
                and str(kwargs.get("cwd")) == self.root):
            return self._real(argv, *args, **kwargs)
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), self.rc, "", self.stderr)


NOT_A_REPO = "fatal: not a git repository (or any of the parent directories): .git"


@pytest.fixture
def plain_root(client, monkeypatch, tmp_path):
    from bulk_downloader import app_health
    root = tmp_path / "plain-install"
    root.mkdir()
    monkeypatch.setenv("BD_INSTALL_DIR", str(root))
    # raising=False: BASE has no such attribute, and must fail on the
    # spawn count, not on fixture setup.
    monkeypatch.setattr(app_health, "_NOT_A_BUILD", set(), raising=False)
    monkeypatch.setattr(app_health, "_BUILD_IDENTITY_CACHE", {})
    return root


def test_f2_non_build_dir_spawns_git_once(client, plain_root, monkeypatch):
    git = _Git(plain_root, rc=128, stderr=NOT_A_REPO)
    monkeypatch.setattr(subprocess, "run", git)
    for _ in range(5):
        body = client.get("/api/health").get_json(silent=True) or {}
        assert "build" not in body, body.get("build")
    assert len(git.calls) == 1, (
        f"APPCORE-F2 5 /api/health on a non-build dir spawned git "
        f"{len(git.calls)}x", git.calls)


def test_f2_real_git_answer_is_recognised(client, plain_root, monkeypatch):
    """The remembered answer is real git's own text, not the fake's."""
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(plain_root.parent))
    seen = []
    real = subprocess.run

    def spy(argv, *a, **k):
        if (isinstance(argv, (list, tuple)) and argv and argv[0] == "git"
                and str(k.get("cwd")) == str(plain_root)):
            seen.append(list(argv))
        return real(argv, *a, **k)
    monkeypatch.setattr(subprocess, "run", spy)
    for _ in range(3):
        client.get("/api/health")
    assert len(seen) == 1, (
        f"APPCORE-F2 real git: 3 requests spawned git {len(seen)}x", seen)


def test_f2_a_build_marker_appearing_is_measured(client, plain_root, monkeypatch):
    git = _Git(plain_root, rc=128, stderr=NOT_A_REPO)
    monkeypatch.setattr(subprocess, "run", git)
    client.get("/api/health")
    (plain_root / "build_info.json").write_text(
        json.dumps({"sha": "abc123def456", "built_at": "2026-10-14T00:00:00Z"}))
    body = client.get("/api/health").get_json(silent=True) or {}
    assert (body.get("build") or {}).get("sha") == "abc123def456", (
        "APPCORE-F2-CONTROL build_info.json dropped in place was not read",
        body.get("build"))


@pytest.mark.parametrize("make_git_dir, stderr", [
    (True, NOT_A_REPO),
    (False, "fatal: detected dubious ownership in repository"),
], ids=["git-marker-present", "refusal-is-not-absence"])
def test_f2_a_failure_is_never_remembered(client, plain_root, monkeypatch,
                                          make_git_dir, stderr):
    if make_git_dir:
        (plain_root / ".git").mkdir()
    git = _Git(plain_root, rc=128, stderr=stderr)
    monkeypatch.setattr(subprocess, "run", git)
    for _ in range(3):
        client.get("/api/health")
    # >= not ==: one request may measure more than once; remembering would
    # make it 1.
    assert len(git.calls) >= 3, (
        f"APPCORE-F2-ROW521 a refused measurement was remembered "
        f"({len(git.calls)} spawns for 3 requests)", git.calls)


# ── F3 ────────────────────────────────────────────────────────────────────

@pytest.fixture
def stripe(client, monkeypatch):
    """A non-sites POST <sid> route with its view stubbed (no route side
    effects), a known site id, and a spy on the stripe factory."""
    import bulk_downloader.app as A
    from bulk_downloader import app_state
    rules = sorted(
        (r for r in A.app.url_map.iter_rules()
         if "<sid>" in r.rule and "POST" in r.methods and r.rule.startswith("/api/")
         and r.endpoint.split(".")[0] not in A._SITE_TRANSACTION_BLUEPRINT_EXCLUSIONS),
        key=lambda r: r.rule)
    assert rules, "APPCORE-F3 no non-sites POST <sid> route to probe"
    rule = rules[0]
    monkeypatch.setitem(A.app.view_functions, rule.endpoint,
                        lambda **_kw: ("stub", 200))
    monkeypatch.setitem(A.s_cfg, "appcoresite", {})
    taken = []
    real = app_state.site_lifecycle_lock

    def spy(sid):
        taken.append(sid)
        return real(sid)
    monkeypatch.setattr(app_state, "site_lifecycle_lock", spy)
    url = rule.rule.replace("<sid>", "appcoresite")
    import re
    return re.sub(r"<[^>]+>", "x", url), taken


def test_f3_unauthenticated_post_takes_no_site_stripe(client, stripe):
    url, taken = stripe
    r = client.post(url, json={})
    assert r.status_code == 401, (url, r.status_code)
    assert taken == [], (
        f"APPCORE-F3 unauthenticated POST {url} acquired the site stripe "
        f"{len(taken)}x before its 401")


def test_f3_authenticated_post_still_takes_the_stripe(client, stripe):
    """Positive control: the spy sees the acquisition when auth passes."""
    url, taken = stripe
    r = client.post(url, json={}, headers=AUTH)
    assert r.status_code == 200, (url, r.status_code)
    assert taken == ["appcoresite"], (
        f"APPCORE-F3-CONTROL authenticated POST {url} took {taken}")


# ── F4 (r2) ───────────────────────────────────────────────────────────────

def test_f4_health_outside_an_app_context_does_not_raise(monkeypatch):
    from flask import has_app_context
    import bulk_downloader.app_health as H
    assert not has_app_context(), "APPCORE-F4 precondition: test must run with no app context pushed"
    monkeypatch.setattr(H, "_app_sites_config_reachability",
                        lambda: {"ok": True, "path": "/srv/bd/sites.json"})
    payload = {"ok": True}
    try:
        H._attach_sites_config_health(payload)
    except RuntimeError as exc:
        pytest.fail(f"APPCORE-F4 health outside an app context raised: {exc}")
    assert payload.get("sites_config", {}).get("path") == "/srv/bd/sites.json", (
        "APPCORE-F4 outside a context the flag is unset = full payload; "
        f"path withheld: {payload!r}")
    assert payload["ok"] is True, f"APPCORE-F4 reachable config marked not ok: {payload!r}"
