"""deep-site-templates MED + deep-sites-lifecycle F1/F2: site config writes that report success they did not make.

  S27     PUT /api/sites/<sid> answered {"ok": true} when the config save failed; memory kept the edit, disk did not.
  SL-F2   DELETE (single and bulk) removed the queue rows BEFORE the config save and ignored its result: the site came
          back at restart with an empty queue.
  SL-F1   a sites_config.json that does not parse loaded 0 sites and the next save replaced the operator's file.
  S17     import skipped the selector check PUT runs (Row 722).
  S09-11  PUT stored a crawler_scene_patterns / login_url value /api/sites/validate rejects.
  S03     PUT accounts[].password was written plaintext into sites_config.json.
  LOW     S04 PUT name '' stored a nameless site; S06 a JSON array body was a 500; S15 any key was persisted.

Each case runs in a FRESH child process (scratch BD_HOME under tmp_path, Flask test_client, loopback only).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
REPO = Path(__file__).resolve().parents[1]
TAG = "DSTMED"

CHILD = r"""
import copy, io, json, os, sys, threading, contextlib
mode, home, arg = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
os.environ.update(BD_HOME=home, BD_INSTALL_DIR=home, BD_DISABLE_KEEPALIVE="1"); os.chdir(home)
from bulk_downloader import app as A, app_state as S, secrets_store as SS
A.SITES_FILE = A.Path(home) / "sites_config.json"
out = {}
vault = dict(arg.get("preload", {}))

class OpenVault:
    name = "master_password"
    def is_unlocked(self): return True
    def set(self, key, value): vault[key] = value
    def get(self, key): return vault.get(key)
    def delete(self, key): return vault.pop(key, None) is not None

class LockedVault(OpenVault):
    def is_unlocked(self): return False

if arg.get("vault") == "open":
    SS.get_backend = lambda: OpenVault()
elif arg.get("vault") == "locked":
    SS.get_backend = lambda: LockedVault()
elif arg.get("vault") == "plaintext":
    SS.get_backend = lambda: SS.PlaintextBackend()

def clean(sid):
    c = {k: A.DEFAULTS.get(k, "") for k in A.CFG_FIELDS}
    c.update(name=sid, download_dir=os.path.join(home, "dl", sid), cookie_file=os.path.join(home, sid + ".json"))
    return c

class FakeRunner:
    def __init__(self, sid): self.sid = sid; self.config = None; self.urls = []; self.jobs = {}; self._lock = threading.Lock()
    def update_config(self, cfg): self.config = copy.deepcopy(cfg)
    def set_cookies_from_file(self, *_a): pass
    def retire_scheduler(self, timeout=None): return True
    def retire_auto_retry(self, timeout=None): return True
    def retire_workers(self, timeout=None): return True

def add(sid, **over):
    c = dict(clean(sid), **over); S.s_cfg[sid] = c; S.s_meta[sid] = A._build_meta(c); S.runners[sid] = FakeRunner(sid)

def on_disk():
    return json.loads(A.SITES_FILE.read_text(encoding="utf-8"))

def block_saves():   # the save's temp path is a directory: tmp.write_text fails, _save_sites_config returns False
    os.makedirs(str(A.SITES_FILE.with_suffix(".json.tmp")), exist_ok=True)

client = A.app.test_client()

if mode == "put":
    add("keep"); add("c2", **arg.get("stored", {}))
    assert A._save_sites_config() is True
    if arg.get("block"): block_saves()
    before_file = A.SITES_FILE.read_bytes(); before_cfg = copy.deepcopy(S.s_cfg["c2"])
    if "raw" in arg:
        r = client.put("/api/sites/c2", data=arg["raw"], content_type="application/json")
    else:
        r = client.put("/api/sites/c2", json=arg["patch"])
    out.update(status=r.status_code, body=r.get_json(silent=True),
               file_changed=A.SITES_FILE.read_bytes() != before_file,
               memory_changed=S.s_cfg["c2"] != before_cfg,
               memory=S.s_cfg["c2"], meta_name=S.s_meta["c2"].get("name"),
               runner=S.runners["c2"].config, disk=on_disk().get("c2"),
               file_text=A.SITES_FILE.read_text(encoding="utf-8"), vault=vault)

elif mode == "delete":
    from bulk_downloader import db
    db.db_init()
    add("keep"); add("c2")
    assert A._save_sites_config() is True
    for sid in ("keep", "c2"):
        for i in range(3):
            db.queue_upsert(sid, "https://example.invalid/%s/%d" % (sid, i), status="queued")
    if arg.get("block"): block_saves()
    if arg.get("bulk"):
        r = client.post("/api/sites/v2/bulk", json={"action": "delete", "site_ids": ["c2"]})
    else:
        r = client.delete("/api/sites/c2")
    out.update(status=r.status_code, body=r.get_json(silent=True),
               memory=sorted(S.s_cfg), meta=sorted(S.s_meta), runners=sorted(S.runners),
               disk=sorted(on_disk()), rows={sid: len(db.queue_load(sid) or []) for sid in ("keep", "c2")})

elif mode == "import":
    before = sorted(S.s_cfg)
    r = client.post("/api/sites/import", json=arg["payload"])
    out.update(status=r.status_code, body=r.get_json(silent=True), created=sorted(set(S.s_cfg) - set(before)))

elif mode == "load":
    A.SITES_FILE.write_text(arg["text"], encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        A._load_sites_config()
        add("fresh")
        out["saved"] = A._save_sites_config()
    out.update(stderr=err.getvalue(), sites=sorted(S.s_cfg), reach=A._sites_config_reachability(),
               file_text=A.SITES_FILE.read_text(encoding="utf-8"),
               copies={p: open(os.path.join(home, p), encoding="utf-8").read()
                       for p in sorted(os.listdir(home)) if p.startswith("sites_config.json.malformed")})
    if arg.get("then_valid"):   # the operator repairs the file; a reload accepts it and saves work again
        A.SITES_FILE.write_text(json.dumps({"fixed": clean("fixed")}), encoding="utf-8")
        S.s_cfg.clear(); S.s_meta.clear(); S.runners.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            A._load_sites_config()
            out["saved_after_repair"] = A._save_sites_config()
        out["sites_after_repair"] = sorted(on_disk())

print("DSTMED-RESULT " + json.dumps(out, default=str), flush=True)
os._exit(0)
"""


def _child(tmp_path: Path, mode: str, arg: dict) -> dict:
    script = tmp_path / "dstmed_child.py"
    script.write_text(CHILD, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    r = subprocess.run(
        [sys.executable, "-B", str(script), mode, str(home), json.dumps(arg)],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=90, check=False,
    )
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("DSTMED-RESULT ")]
    assert lines, f"{TAG}-CHILD-NO-RESULT mode={mode} rc={r.returncode}: {r.stdout[-400:]} {r.stderr[-1200:]}"
    return json.loads(lines[-1][len("DSTMED-RESULT "):])


# ------------------------------------------------------------------ S27 PUT save result


def test_s27_put_with_a_failed_save_is_not_ok_and_rolls_memory_back(tmp_path):
    got = _child(tmp_path, "put", {"patch": {"username": "after-ro"}, "block": True})
    assert got["status"] >= 500 and (got["body"] or {}).get("ok") is not True, (
        f"{TAG}-S27-PUT-OK-ON-FAILED-SAVE status={got['status']} body={got['body']}"
    )
    assert not got["memory_changed"] and got["memory"]["username"] == "", (
        f"{TAG}-S27-MEMORY-KEPT-UNSAVED-EDIT username={got['memory']['username']!r}"
    )
    assert got["runner"]["username"] == "" and not got["file_changed"], (
        f"{TAG}-S27-RUNNER-OR-FILE-DIVERGED runner={got['runner']['username']!r} file_changed={got['file_changed']}"
    )


def test_s27_control_a_writable_put_saves(tmp_path):
    got = _child(tmp_path, "put", {"patch": {"username": "writable"}})
    assert got["status"] == 200 and got["disk"]["username"] == "writable", got["body"]


# ------------------------------------------------------------------ SL-F2 delete order


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
def test_sl_f2_delete_with_a_failed_save_keeps_the_queue_and_the_site(tmp_path, bulk):
    got = _child(tmp_path, "delete", {"block": True, "bulk": bulk})
    body = got["body"] or {}
    reported_ok = body.get("ok") is True and (not bulk or body.get("applied_to") == 1)
    assert not reported_ok, f"{TAG}-SL-F2-DELETE-OK-ON-FAILED-SAVE bulk={bulk} status={got['status']} body={body}"
    assert got["rows"]["c2"] == 3, f"{TAG}-SL-F2-QUEUE-ROWS-DELETED-BEFORE-SAVE bulk={bulk} rows={got['rows']}"
    assert "c2" in got["memory"] and "c2" in got["meta"] and "c2" in got["runners"], (
        f"{TAG}-SL-F2-MEMORY-NOT-RESTORED bulk={bulk} memory={got['memory']} runners={got['runners']}"
    )
    assert "c2" in got["disk"], got["disk"]


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
def test_sl_f2_control_a_normal_delete_removes_only_that_sites_rows(tmp_path, bulk):
    got = _child(tmp_path, "delete", {"bulk": bulk})
    assert got["status"] == 200 and (got["body"] or {}).get("ok") is True, got["body"]
    assert got["rows"] == {"keep": 3, "c2": 0}, got["rows"]
    assert got["disk"] == ["keep"] and got["memory"] == ["keep"], (got["disk"], got["memory"])


# ------------------------------------------------------------------ SL-F1 malformed file


@pytest.mark.parametrize("text", ["{", "[1, 2]"], ids=["unparseable", "not-an-object"])
def test_sl_f1_a_malformed_file_is_copied_and_never_overwritten(tmp_path, text):
    got = _child(tmp_path, "load", {"text": text, "then_valid": True})
    assert got["file_text"] == text, f"{TAG}-SL-F1-MALFORMED-FILE-OVERWRITTEN saved={got['saved']} now={got['file_text'][:80]!r}"
    assert got["saved"] is False, f"{TAG}-SL-F1-SAVE-NOT-REFUSED saved={got['saved']}"
    assert list(got["copies"].values()) == [text], f"{TAG}-SL-F1-NO-BACKUP-COPY copies={sorted(got['copies'])}"
    lines = got["stderr"].splitlines()
    malformed = [ln for ln in lines if "sites_config.json malformed, ignoring:" in ln]
    assert len(malformed) == 1 and next(iter(got["copies"])) in malformed[0], lines
    assert got["reach"]["state"] == "reachable", got["reach"]
    assert got["saved_after_repair"] is True and got["sites_after_repair"] == ["fixed"], (
        f"{TAG}-SL-F1-REPAIRED-FILE-STILL-REFUSED {got['saved_after_repair']} {got['sites_after_repair']}"
    )


def test_sl_f1_control_one_bad_entry_still_loads_the_rest_and_saves(tmp_path):
    # FIXED-ON-MAIN half (s28): one non-object entry is skipped, the rest load, a save keeps it.
    good = {k: "" for k in ("name",)}
    got = _child(tmp_path, "load", {"text": json.dumps({"ok1": good, "bad": "x"})})
    assert got["saved"] is True and got["copies"] == {}, (got["saved"], got["copies"])
    assert sorted(json.loads(got["file_text"])) == ["bad", "fresh", "ok1"], got["file_text"][:200]


# ------------------------------------------------------------------ S17 import selector check


def test_s17_import_refuses_a_selector_put_refuses(tmp_path):
    cfg = {"name": "imp", "start_url": "https://example.invalid/", "dl_selector": "a[href*=2160p]"}
    got = _child(tmp_path, "import", {"payload": cfg})
    assert got["status"] == 400 and not got["created"], (
        f"{TAG}-S17-IMPORT-STORED-BAD-SELECTOR status={got['status']} created={got['created']}"
    )
    assert any("invalid selector 'dl_selector'" in e for e in got["body"]["errors"]), got["body"]


def test_s17_control_import_of_a_valid_selector_creates_the_site(tmp_path):
    cfg = {"name": "imp", "start_url": "https://example.invalid/", "dl_selector": "a[href*='2160p']"}
    got = _child(tmp_path, "import", {"payload": cfg})
    assert got["status"] == 200 and len(got["created"]) == 1, got["body"]


# ------------------------------------------------------------------ S09-S11 PUT validate parity


@pytest.mark.parametrize(
    "patch,needle",
    [
        ({"crawler_scene_patterns": "(["}, "'crawler_scene_patterns' line '([' is not a valid regex"),
        ({"crawler_scene_patterns": "a" * 600}, "'crawler_scene_patterns' has a line over 512 characters."),
        ({"login_url": "not a url"}, "'login_url' does not look like a valid URL"),
    ],
    ids=["s09-bad-regex", "s10-long-line", "s11-login-url"],
)
def test_s09_s11_put_refuses_what_validate_refuses(tmp_path, patch, needle):
    got = _child(tmp_path, "put", {"patch": patch})
    assert got["status"] == 400 and needle in (got["body"] or {}).get("error", ""), (
        f"{TAG}-S09-S11-PUT-STORED-INVALID {sorted(patch)} status={got['status']} body={got['body']}"
    )
    assert not got["file_changed"] and not got["memory_changed"], (got["file_changed"], got["memory_changed"])


def test_s09_s11_control_valid_values_still_save(tmp_path):
    patch = {"crawler_scene_patterns": "/scene/\\d+\n/video/[a-z-]+", "login_url": "https://example.invalid/login"}
    got = _child(tmp_path, "put", {"patch": patch})
    assert got["status"] == 200 and got["disk"]["login_url"] == patch["login_url"], got["body"]


# ------------------------------------------------------------------ S03 account passwords


def test_s03_account_password_goes_to_the_vault_not_the_file(tmp_path):
    patch = {"accounts": [{"username": "u1", "password": "ACCT-PW-PLAIN"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "open"})
    assert "ACCT-PW-PLAIN" not in got["file_text"], f"{TAG}-S03-ACCOUNT-PASSWORD-PLAINTEXT-ON-DISK"
    assert got["status"] == 200, got["body"]
    ref = got["disk"]["accounts"][0]["password"]
    assert ref == "@cred:bulkdl-site-c2-account-0" and got["vault"] == {"bulkdl-site-c2-account-0": "ACCT-PW-PLAIN"}, (
        ref, got["vault"]
    )


def test_s03_locked_vault_keeps_the_old_password_and_says_so(tmp_path):
    stored = {"accounts": [{"username": "u1", "password": "@cred:bulkdl-site-c2-account-0"}]}
    patch = {"accounts": [{"username": "u1", "password": "ACCT-PW-PLAIN"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "locked", "stored": stored})
    assert "ACCT-PW-PLAIN" not in got["file_text"], f"{TAG}-S03-LOCKED-ACCOUNT-PASSWORD-PLAINTEXT-ON-DISK"
    assert got["status"] == 200 and got["body"].get("secrets_locked") is True, got["body"]
    assert got["disk"]["accounts"][0]["password"] == "@cred:bulkdl-site-c2-account-0", got["disk"]["accounts"]


def test_s03_a_new_password_never_reuses_a_key_another_account_holds(tmp_path):
    stored = {"accounts": [{"username": "a", "password": "@cred:bulkdl-site-c2-account-0"},
                           {"username": "b", "password": "@cred:bulkdl-site-c2-account-1"}]}
    # "a" removed: "b" moves to index 0 keeping its account-1 ref; a new "c" lands at index 1.
    patch = {"accounts": [{"username": "b"}, {"username": "c", "password": "C-PW"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "open", "stored": stored})
    refs = [a["password"] for a in got["disk"]["accounts"]]
    assert refs[0] == "@cred:bulkdl-site-c2-account-1" and refs[1] not in (refs[0], ""), refs
    assert "bulkdl-site-c2-account-1" not in got["vault"], f"{TAG}-S03-OTHER-ACCOUNT-SECRET-OVERWRITTEN {got['vault']}"


_AB = {"accounts": [{"username": "a", "password": "@cred:bulkdl-site-c2-account-0"},
                    {"username": "b", "password": "@cred:bulkdl-site-c2-account-1"}]}
_AB_VAULT = {"bulkdl-site-c2-account-0": "PA", "bulkdl-site-c2-account-1": "PB"}


def test_s03_s27_failed_save_never_leaves_an_account_on_another_accounts_password(tmp_path):
    # "a" removed, "b" (now row 0) retyped; the save fails, so the rows come back.
    patch = {"accounts": [{"username": "b", "password": "PB2"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "open", "stored": _AB, "preload": _AB_VAULT,
                                   "block": True})
    assert got["status"] == 500, got["body"]
    refs = {a["username"]: a["password"] for a in got["memory"]["accounts"]}
    assert refs == {a["username"]: a["password"] for a in _AB["accounts"]}, refs
    held = {u: got["vault"].get(r[len("@cred:"):]) for u, r in refs.items()}
    assert held == {"a": "PA", "b": "PB"}, f"{TAG}-F1-ROLLBACK-CRED-CLOBBERED {held} vault={got['vault']}"


def test_s03_s27_control_the_same_retype_with_a_good_save_lands(tmp_path):
    patch = {"accounts": [{"username": "b", "password": "PB2"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "open", "stored": _AB, "preload": _AB_VAULT})
    assert got["status"] == 200, got["body"]
    (row,) = got["disk"]["accounts"]
    assert row["username"] == "b" and got["vault"].get(row["password"][len("@cred:"):]) == "PB2", (row, got["vault"])


def _held_by(got):
    return {a["username"]: got["vault"].get(a["password"][len("@cred:"):]) for a in got["memory"]["accounts"]}


def test_a2_save_ok_deletes_the_prior_account_key(tmp_path):
    # "a" removed, "b" retyped: b lands on a free key; neither old key is referenced any more.
    patch = {"accounts": [{"username": "b", "password": "PB2"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "open", "stored": _AB, "preload": _AB_VAULT})
    assert got["status"] == 200, got["body"]
    assert _held_by(got) == {"b": "PB2"}, got["vault"]
    assert set(got["vault"]) == {got["disk"]["accounts"][0]["password"][len("@cred:"):]}, (
        f"{TAG}-A2-ORPHAN-ACCOUNT-KEY-LEFT {sorted(got['vault'])}")
    assert sorted(got["body"].get("vault_removed") or []) == sorted(_AB_VAULT), got["body"]


def test_a2_control_failed_save_keeps_the_prior_key(tmp_path):
    patch = {"accounts": [{"username": "b", "password": "PB2"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "open", "stored": _AB, "preload": _AB_VAULT,
                                   "block": True})
    assert got["status"] == 500, got["body"]
    assert _held_by(got) == {"a": "PA", "b": "PB"}, f"{TAG}-A2-ROLLBACK-KEY-REAPED {got['vault']}"


def test_a2_a_key_another_remaining_row_references_is_not_deleted(tmp_path):
    # Reorder only: every prior key is still referenced, by another row index than before.
    patch = {"accounts": [{"username": "b"}, {"username": "a"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "open", "stored": _AB, "preload": _AB_VAULT})
    assert got["status"] == 200, got["body"]
    assert _held_by(got) == {"a": "PA", "b": "PB"}, f"{TAG}-A2-REFERENCED-KEY-REAPED {got['vault']}"
    assert not got["body"].get("vault_removed"), got["body"]


def test_a2_plaintext_backend_delete_is_best_effort_and_flagged(tmp_path):
    # "a" removed on a plaintext backend: its key cannot be deleted; the save still lands and says so.
    patch = {"accounts": [{"username": "b"}]}
    got = _child(tmp_path, "put", {"patch": patch, "vault": "plaintext", "stored": _AB})
    assert got["status"] == 200 and got["body"].get("ok") is True, got["body"]
    assert [a["username"] for a in got["disk"]["accounts"]] == ["b"], got["disk"]["accounts"]
    kept = [r.get("key") for r in got["body"].get("vault_retained") or []]
    assert kept == ["bulkdl-site-c2-account-0"], f"{TAG}-A2-UNREAPED-KEY-NOT-FLAGGED {got['body']}"


# ------------------------------------------------------------------ LOW: S04 S06 S15


def test_s04_put_blank_name_is_refused(tmp_path):
    got = _child(tmp_path, "put", {"patch": {"name": "   "}})
    assert got["status"] == 400 and "'name' is required" in (got["body"] or {}).get("error", ""), (
        f"{TAG}-S04-BLANK-NAME-STORED status={got['status']} disk={got['disk']['name']!r}"
    )
    assert got["disk"]["name"] == "c2" and got["meta_name"] == "c2", (got["disk"]["name"], got["meta_name"])


@pytest.mark.parametrize("raw", ["[]", "[1, 2]", '["username", "x"]'], ids=["empty", "ints", "strs"])
def test_s06_put_array_body_is_400_not_500(tmp_path, raw):
    got = _child(tmp_path, "put", {"raw": raw})
    assert got["status"] == 400 and "request body must be a JSON object" in (got["body"] or {}).get("error", ""), (
        f"{TAG}-S06-ARRAY-BODY status={got['status']} body={got['body']}"
    )
    assert not got["file_changed"], got["file_changed"]


def test_s15_put_unknown_keys_are_dropped_not_persisted(tmp_path):
    patch = {"username": "kept", "bad\u0000key": 1, "k" * 10000: 2, "no_such_field": 3}
    got = _child(tmp_path, "put", {"patch": patch})
    assert got["status"] == 200 and got["disk"]["username"] == "kept", got["body"]
    leaked = sorted(k[:20] for k in got["disk"] if k not in got["memory"] or k.startswith(("bad", "kkk", "no_such")))
    assert not leaked and "no_such_field" not in got["memory"], f"{TAG}-S15-UNKNOWN-KEY-PERSISTED {leaked}"
    assert got["body"].get("ignored_fields") == 3, got["body"]
