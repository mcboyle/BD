"""s28 (finder S02-C, deep-site-templates S28, HIGH): one PUT made the next restart load NO sites.

PUT /api/sites/<sid> {"accounts": "x"} answered 200 and stored the string; SiteRunner.__init__ then failed on it and
_load_sites_config, all-or-nothing, took every other site down with it.

Two halves, each proved in a FRESH process per case (a restart is a new process; the loader builds the REAL SiteRunner):
  PUT   a wrongly typed "accounts" / container field is refused 400 and nothing is written (memory, file, runner)
  LOAD  a file that already holds a bad entry loads every other site; the bad one is skipped, logged, listed in the
        /api/health sites_config block, and still in the file after the next save
Scratch BD_HOME under tmp_path only; Flask test_client; no network, no browser.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
REPO = Path(__file__).resolve().parents[1]

CHILD = r"""
import copy, io, json, os, sys, threading, contextlib
mode, home, arg = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
os.environ.update(BD_HOME=home, BD_INSTALL_DIR=home, BD_DISABLE_KEEPALIVE="1"); os.chdir(home)
from bulk_downloader import app as A, app_state as S
A.SITES_FILE = A.Path(home) / "sites_config.json"
out = {}

def _expand(v):          # {"$rep": [s, n]} -> s * n ; {"$big": n} -> 10 ** n ; {"$locked": name} -> unreadable path
    if isinstance(v, dict) and set(v) == {"$rep"}:
        return v["$rep"][0] * v["$rep"][1]
    if isinstance(v, dict) and set(v) == {"$big"}:
        return 10 ** v["$big"]
    if isinstance(v, dict) and set(v) == {"$home"}:      # a path under BD_HOME (inside the default path allowlist)
        return os.path.join(home, _expand(v["$home"]))
    if isinstance(v, dict) and set(v) == {"$locked"}:
        d = os.path.join(home, "locked-" + v["$locked"]); os.makedirs(d, exist_ok=True); os.chmod(d, 0)
        return os.path.join(d, "sub", "c.json")
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_expand(x) for x in v]
    return v

def clean(sid):
    c = {k: A.DEFAULTS.get(k, "") for k in A.CFG_FIELDS}
    c.update(name=sid, download_dir=os.path.join(home, "dl", sid), cookie_file=os.path.join(home, sid + ".json"))
    return c

if arg.get("sites"):        # write the config file: every id in arg["sites"] clean, arg["bad"] patched over it
    data = {sid: clean(sid) for sid in arg["sites"]}
    for sid, patch in arg.get("bad", {}).items():
        patch = _expand(patch)
        data[sid] = dict(data[sid], **patch) if isinstance(patch, dict) else patch
    A.SITES_FILE.write_text(json.dumps(data), encoding="utf-8")

if mode == "put":         # one live app, two sites; PUT each patch at c2 and measure what it touched
    class FakeRunner:
        def __init__(self, sid): self.sid = sid; self.urls = []; self.jobs = {}; self._lock = threading.Lock(); self.updates = 0
        def update_config(self, cfg): self.updates += 1
        def set_cookies_from_file(self, *_a): pass
    def add(sid, **over):
        c = dict(clean(sid), **over); S.s_cfg[sid] = c; S.s_meta[sid] = A._build_meta(c); S.runners[sid] = FakeRunner(sid)
    client = A.app.test_client()
    out["rows"] = []
    for patch in arg["patches"]:
        patch = _expand(patch)
        S.s_cfg.clear(); S.s_meta.clear(); S.runners.clear()
        add("keep"); add("c2", **arg.get("stored", {}))
        A._save_sites_config()
        before_file = A.SITES_FILE.read_bytes(); before_cfg = copy.deepcopy(S.s_cfg["c2"])
        r = client.put("/api/sites/c2", json=patch)
        out["rows"].append({
            "euid": os.geteuid(),
            "status": r.status_code, "error": (r.get_json(silent=True) or {}).get("error", ""),
            "file_changed": A.SITES_FILE.read_bytes() != before_file, "memory_changed": S.s_cfg["c2"] != before_cfg,
            "runner_updates": S.runners["c2"].updates,
            "stored_accounts": json.loads(A.SITES_FILE.read_text(encoding="utf-8"))["c2"].get("accounts"),
        })

elif mode in ("load", "boot"):   # a restart over the file in <home>
    retired = {}
    def _spy(name):
        real = getattr(A.SiteRunner, name)
        def wrapped(self, *a, **k):
            retired.setdefault(self.site_id, []).append(name)
            return False if arg.get("stuck") else real(self, *a, **k)
        setattr(A.SiteRunner, name, wrapped)
    for _n in ("retire_scheduler", "retire_auto_retry", "retire_workers"):
        _spy(_n)
    if arg.get("plant"):     # the runner of one site cannot start: not a fault of its entry
        _real_runner = A.SiteRunner
        def _planted(site_id, config):
            if site_id == arg["plant"]["sid"]:
                raise {"RuntimeError": RuntimeError, "OSError": OSError}[arg["plant"]["exc"]]("planted")
            return _real_runner(site_id, config)
        A.SiteRunner = _planted
    err = io.StringIO()
    file_before = json.loads(A.SITES_FILE.read_text(encoding="utf-8")) if A.SITES_FILE.exists() else None
    try:
        with contextlib.redirect_stderr(err):
            if mode == "boot":   # the boot caller, its dependent services stubbed
                for name in ("_start_session_keepers", "_start_watch_folder_threads", "_start_watcher",
                             "_start_background_services"):
                    if hasattr(A, name): setattr(A, name, lambda *a, **k: None)
                A._init_vpn_runtime = lambda: {"ok": True}
                A._activate_configured_runtime_once(str(A.SITES_FILE))
            else:
                A._load_sites_config()
        out["raised"] = ""
    except Exception as e:
        out["raised"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    out["sites"] = sorted(S.s_cfg); out["runners"] = sorted(S.runners); out["meta"] = sorted(S.s_meta)
    out["real_runners"] = all(type(r).__name__ == "SiteRunner" for r in S.runners.values())
    out["stderr"] = err.getvalue()
    out["threads"] = sorted(t.name for t in threading.enumerate())
    out["euid"] = os.geteuid()
    out["retired"] = retired
    from bulk_downloader import app_health as H
    payload = {"ok": True}; H._attach_sites_config_health(payload); out["health"] = payload
    if arg.get("save"):
        if arg.get("other_file"):
            A.SITES_FILE = A.Path(home) / "other_sites_config.json"
        out["saved"] = A._save_sites_config()
        out["file_after"] = json.loads(A.SITES_FILE.read_text(encoding="utf-8"))
    out["file_before"] = file_before

print("S28-RESULT " + json.dumps(out), flush=True)
os._exit(0)
"""


def _child(tmp_path: Path, mode: str, home: Path, arg: dict) -> dict:
    script = tmp_path / "s28_child.py"
    script.write_text(CHILD, encoding="utf-8")
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
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("S28-RESULT ")]
    assert lines, (
        f"S28-CHILD-NO-RESULT mode={mode} rc={r.returncode}: {r.stdout[-400:]} {r.stderr[-800:]}"
    )
    return json.loads(lines[-1][len("S28-RESULT ") :])


# ------------------------------------------------------------------ the finder's repro: PUT, then restart


def test_finder_repro_put_accounts_string_then_restart_keeps_every_site(tmp_path):
    home = tmp_path / "home"
    put = _child(tmp_path, "put", home, {"patches": [{"accounts": "x"}]})["rows"][0]
    after = _child(tmp_path, "load", home, {})
    assert after["raised"] == "" and after["sites"] == ["c2", "keep"], (
        f"S28-RESTART-LOADED-NO-SITES after PUT accounts='x' (status {put['status']}): "
        f"raised={after['raised']!r} sites={after['sites']}"
    )
    assert put["status"] == 400 and "accounts must be a list" in put["error"], (
        f"S28-BAD-ACCOUNTS-ACCEPTED {put}"
    )
    assert put["stored_accounts"] == [], (
        f"S28-BAD-ACCOUNTS-STORED {put['stored_accounts']!r}"
    )


# ------------------------------------------------------------------ PUT half

BAD_PUTS = [
    ({"accounts": "x"}, "accounts must be a list"),
    ({"accounts": 7}, "accounts must be a list"),
    ({"accounts": True}, "accounts must be a list"),
    ({"accounts": {"username": "u"}}, "accounts must be a list"),
    ({"accounts": [1]}, "accounts[0] must be an object"),
    ({"accounts": [{"username": "u"}, "x"]}, "accounts[1] must be an object"),
    (
        {"accounts": [{"username": "u", "cooldown_until": "soon"}]},
        "accounts[0].cooldown_until must be a number",
    ),
    (
        {"accounts": [{"username": "u", "cooldown_until": [1]}]},
        "accounts[0].cooldown_until must be a number",
    ),
    (
        {"accounts": [{"username": "u", "cooldown_until": True}]},
        "accounts[0].cooldown_until must be a number",
    ),
    ({"accounts": [{"username": ["u"]}]}, "accounts[0].username must be a string"),
    (
        {"accounts": [{"username": "u", "password": {"p": 1}}]},
        "accounts[0].password must be a string",
    ),
    (
        {"accounts": [{"username": "u", "cookie_file": [1]}]},
        "accounts[0].cookie_file must be a string",
    ),
    (
        {"accounts": [{"username": "u", "label": {"a": 1}}]},
        "accounts[0].label must be a string",
    ),
    (
        {"accounts": [{"username": "u", "last_failure": [1]}]},
        "accounts[0].last_failure must be a string",
    ),
    ({"post_download_pipeline": "x"}, "post_download_pipeline must be a list"),
    ({"subscriptions": {"a": 1}}, "subscriptions must be a list"),
    ({"subtitle_languages": "en"}, "subtitle_languages must be a list"),
    ({"url_fingerprint": [1]}, "url_fingerprint must be an object"),
    ({"pre_scrape_action": "click"}, "pre_scrape_action must be an object"),
    ({"learned": "junk"}, "learned must be an object"),
    ({"learned": []}, "learned must be an object"),
    ({"draft_test_override": 7}, "draft_test_override must be an object"),
    # r2 (lens R22-B F1): an account cookie_file gets the site cookie_file's path check, and the OS's own answer
    (
        {"accounts": [{"username": "u", "cookie_file": {"$rep": ["A", 5000]}}]},
        "accounts[0].cookie_file must be an absolute path",
    ),
    (
        {"accounts": [{"username": "u", "cookie_file": "rel/c.json"}]},
        "accounts[0].cookie_file must be an absolute path",
    ),
    (
        {"accounts": [{"username": "u", "cookie_file": "/x/../c.json"}]},
        "accounts[0].cookie_file cannot contain '..' segments",
    ),
    (
        {
            "accounts": [
                {"username": "u", "cookie_file": {"$home": {"$rep": ["A/", 2500]}}}
            ]
        },
        "accounts[0].cookie_file is not a path this host can read (OSError)",
    ),
    (
        {
            "accounts": [
                {"username": "u"},
                {"username": "v", "cookie_file": {"$locked": "p"}},
            ]
        },
        "accounts[1].cookie_file is not a path this host can read (PermissionError)",
    ),
    (
        {"cookie_file": {"$home": {"$rep": ["A/", 2500]}}},
        "cookie_file is not a path this host can read (OSError)",
    ),
    (
        {"cookie_file": {"$locked": "t"}},
        "cookie_file is not a path this host can read (PermissionError)",
    ),
    # r2 (F2): every account element field is typed, scalars included
    (
        {"accounts": [{"username": "u", "cookie_file": 7}]},
        "accounts[0].cookie_file must be a string",
    ),
    (
        {"accounts": [{"username": "u", "cookie_file": True}]},
        "accounts[0].cookie_file must be a string",
    ),
    ({"accounts": [{"username": 7}]}, "accounts[0].username must be a string"),
    (
        {"accounts": [{"username": "u", "password": 7}]},
        "accounts[0].password must be a string",
    ),
    (
        {"accounts": [{"username": "u", "label": True}]},
        "accounts[0].label must be a string",
    ),
    (
        {"accounts": [{"username": "u", "last_failure": 1.5}]},
        "accounts[0].last_failure must be a string",
    ),
    # r2 (F3): cooldown_until is a finite number
    (
        {"accounts": [{"username": "u", "cooldown_until": {"$big": 400}}]},
        "accounts[0].cooldown_until must be a number",
    ),
    (
        {"accounts": [{"username": "u", "cooldown_until": "nan"}]},
        "accounts[0].cooldown_until must be a number",
    ),
    (
        {"accounts": [{"username": "u", "cooldown_until": "inf"}]},
        "accounts[0].cooldown_until must be a number",
    ),
    (
        {"accounts": [{"username": "u", "cooldown_until": []}]},
        "accounts[0].cooldown_until must be a number",
    ),
    (
        {"accounts": [{"username": "u", "cooldown_until": {}}]},
        "accounts[0].cooldown_until must be a number",
    ),
    # a bad field beside a good one: the good one is not written either
    ({"wait": 9, "accounts": "x"}, "accounts must be a list"),
]
GOOD_PUTS = [
    {"accounts": []},
    {"accounts": None},
    {"accounts": ""},
    {"accounts": [{"username": "u", "password": "p", "cooldown_until": 0}]},
    {
        "accounts": [
            {"username": "u", "cooldown_until": "12.5", "label": None},
            {"username": "v", "cooldown_until": None},
        ]
    },
    {"post_download_pipeline": [], "subscriptions": [], "subtitle_languages": ["en"]},
    {
        "url_fingerprint": {},
        "pre_scrape_action": {"selector": "#a"},
        "learned": {"download": {}},
    },
    {"draft_test_override": {"persist": True}},
    {"learned": None, "subtitle_languages": ""},
    {"wait": 9},
    # r2 controls: a cookie path under BD_HOME that does not exist yet is fine, for the site and for an account
    {
        "accounts": [
            {
                "username": "u",
                "cookie_file": {"$home": "not-yet/dir/c.json"},
                "cooldown_until": 5,
            }
        ]
    },
    {"cookie_file": {"$home": "not-yet/dir/site.json"}},
    {
        "accounts": [
            {
                "username": "u",
                "cookie_file": "",
                "label": "",
                "last_failure": "",
                "password": "",
            }
        ]
    },
]


@pytest.fixture(scope="module")
def put_rows(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("s28put")
    patches = [p for p, _ in BAD_PUTS] + GOOD_PUTS
    rows = _child(tmp, "put", tmp / "home", {"patches": patches})["rows"]
    assert len(rows) == len(patches)
    return {json.dumps(p, sort_keys=True): r for p, r in zip(patches, rows)}


@pytest.mark.parametrize(
    "patch,message", BAD_PUTS, ids=[json.dumps(p)[:80] for p, _ in BAD_PUTS]
)
def test_put_refuses_a_wrongly_typed_field_and_writes_nothing(put_rows, patch, message):
    row = put_rows[json.dumps(patch, sort_keys=True)]
    if "PermissionError" in message and row["euid"] == 0:
        pytest.skip("root reads every path: no unreadable path to refuse")
    assert row["status"] == 400, f"S28-BAD-TYPE-ACCEPTED {patch} -> {row['status']}"
    assert message in row["error"], (
        f"S28-BAD-TYPE-WRONG-REASON {patch} -> {row['error']!r}"
    )
    assert not row["file_changed"], f"S28-REFUSED-PUT-WROTE-THE-FILE {patch}"
    assert not row["memory_changed"], f"S28-REFUSED-PUT-CHANGED-MEMORY {patch}"
    assert row["runner_updates"] == 0, f"S28-REFUSED-PUT-REACHED-THE-RUNNER {patch}"


@pytest.mark.parametrize("patch", GOOD_PUTS, ids=[json.dumps(p) for p in GOOD_PUTS])
def test_put_still_accepts_well_typed_fields(put_rows, patch):
    row = put_rows[json.dumps(patch, sort_keys=True)]
    assert row["status"] == 200, (
        f"S28-GOOD-PUT-REFUSED {patch} -> {row['status']} {row['error']!r}"
    )
    assert row["runner_updates"] == 1, f"S28-GOOD-PUT-NOT-APPLIED {patch}"
    assert isinstance(row["stored_accounts"], list), (
        f"S28-ACCOUNTS-NOT-A-LIST-ON-DISK {row['stored_accounts']!r}"
    )


def test_put_accounts_keeps_the_stored_password_when_sent_blank(tmp_path):
    """positive control of the merge the check sits in front of"""
    stored = {
        "accounts": [
            {
                "label": "",
                "username": "u",
                "password": "kept",
                "cookie_file": "",
                "cooldown_until": 0,
                "last_failure": "",
            }
        ]
    }
    row = _child(
        tmp_path,
        "put",
        tmp_path / "home",
        {
            "stored": stored,
            "patches": [{"accounts": [{"username": "u", "password": ""}]}],
        },
    )["rows"][0]
    assert row["status"] == 200 and row["stored_accounts"][0]["password"] == "kept", (
        f"S28-ACCOUNT-MERGE-BROKEN {row}"
    )


@pytest.mark.parametrize("junk", ["x", [1, "y"], {"a": 1}, 7])
def test_put_can_repair_accounts_already_stored_wrong(tmp_path, junk):
    """A value stored before the check is still in memory until a restart; the repairing PUT must not be a 500."""
    row = _child(
        tmp_path,
        "put",
        tmp_path / "home",
        {
            "stored": {"accounts": junk},
            "patches": [{"accounts": [{"username": "u", "password": "p"}]}],
        },
    )["rows"][0]
    assert row["status"] == 200, (
        f"S28-REPAIR-PUT-FAILED stored={junk!r} -> {row['status']} {row['error']!r}"
    )
    assert row["stored_accounts"] == [
        {
            "label": "",
            "username": "u",
            "password": "p",
            "cookie_file": "",
            "cooldown_until": 0,
            "last_failure": "",
        }
    ], row["stored_accounts"]


# ------------------------------------------------------------------ LOAD half

BAD_ENTRIES = [
    {"accounts": "x"},
    {"accounts": 7},
    {"accounts": [1]},
    {"accounts": {"a": 1}},
    {"accounts": [{"username": "u", "cooldown_until": "soon"}]},
    {"cookie_file": 7},
    {"cookie_file": ["x"]},
    "not an object",
    [1],
    None,
    # r2 (lens R22-B): F1's two bodies as stored rows, F3, and the site-level twins; F5's _-key
    {
        "accounts": [
            {"username": "u", "password": "p", "cookie_file": {"$rep": ["A", 5000]}}
        ]
    },
    {"accounts": [{"username": "u", "password": "p", "cookie_file": {"$locked": "a"}}]},
    {"cookie_file": {"$rep": ["/A", 2500]}},
    {"cookie_file": {"$locked": "s"}},
    {"accounts": [{"username": "u", "cooldown_until": {"$big": 400}}]},
    {"accounts": "x", "_keepme": 1},
]


# every bad entry through the loader; two of them also through the boot caller that used to retire every runner
LOAD_CASES = [("load", b) for b in BAD_ENTRIES] + [
    ("boot", b) for b in BAD_ENTRIES[:1] + BAD_ENTRIES[7:8] + BAD_ENTRIES[10:12]
]


def _cookie_path_entry(bad) -> bool:
    """the entry fails in the cookie-path block, i.e. AFTER its runner was published"""
    if not isinstance(bad, dict):
        return False
    accts = bad.get("accounts")
    return "cookie_file" in bad or (
        isinstance(accts, list)
        and any(isinstance(a, dict) and "cookie_file" in a for a in accts)
    )


@pytest.mark.parametrize(
    "mode,bad", LOAD_CASES, ids=[f"{m}:{json.dumps(b)[:70]}" for m, b in LOAD_CASES]
)
def test_a_bad_entry_is_skipped_and_every_other_site_loads(tmp_path, bad, mode):
    home = tmp_path / "home"
    # the bad entry sits BETWEEN two good ones: sites before and after it must both load
    got = _child(
        tmp_path,
        mode,
        home,
        {"sites": ["a-good", "m-bad", "z-good"], "bad": {"m-bad": bad}, "save": True},
    )
    if "$locked" in json.dumps(bad) and got["euid"] == 0:
        pytest.skip("root reads every path: this entry is not bad for root")
    assert got["raised"] == "" and got["sites"] == ["a-good", "z-good"], (
        f"S28-ONE-BAD-SITE-TOOK-ALL-SITES-DOWN [{mode}] bad={json.dumps(bad)[:80]}: raised={got['raised'][:120]!r} sites={got['sites']}"
    )
    assert not [t for t in got["threads"] if "m-bad" in t], (
        f"S28-SKIPPED-SITE-LEFT-A-THREAD {got['threads']}"
    )
    assert (
        got["runners"] == ["a-good", "z-good"]
        and got["meta"] == ["a-good", "z-good"]
        and got["real_runners"]
    ), got
    sc = got["health"]["sites_config"]
    assert [s["id"] for s in sc.get("skipped_sites", [])] == ["m-bad"], (
        f"S28-SKIPPED-SITE-NOT-LISTED {sc}"
    )
    assert " at " in sc["skipped_sites"][0]["error"], sc
    # a runner published before the failure (cookie_file) is retired, all three generations; no other runner is touched
    late = _cookie_path_entry(bad)
    want = (
        {"m-bad": ["retire_scheduler", "retire_auto_retry", "retire_workers"]}
        if late
        else {}
    )
    assert got["retired"] == want, (
        f"S28-SKIPPED-RUNNER-NOT-RETIRED-ALONE [{mode}] bad={json.dumps(bad)[:80]}: {got['retired']}"
    )
    assert "site m-bad NOT loaded, skipped" in got["stderr"], (
        f"S28-SKIP-NOT-LOGGED {got['stderr'][-300:]!r}"
    )
    assert (
        "1 of 3 site(s)" in got["stderr"] and "restored 2 site(s)" in got["stderr"]
    ), got["stderr"][-400:]
    # the skipped entry is the operator's data: the next save must not erase it
    assert got["saved"] is True
    assert got["file_after"].get("m-bad", "MISSING") == got["file_before"]["m-bad"], (
        f"S28-SKIPPED-SITE-ERASED-BY-SAVE [{mode}] {sorted(got['file_after'])}"
    )
    assert sorted(got["file_after"]) == ["a-good", "m-bad", "z-good"]


def test_a_published_runner_that_cannot_be_proved_stopped_is_never_dropped(tmp_path):
    """negative control of the skip: the all-or-nothing rule still holds for a runner whose generations do not stop"""
    got = _child(
        tmp_path,
        "load",
        tmp_path / "home",
        {
            "sites": ["a-good", "m-bad"],
            "bad": {"m-bad": {"cookie_file": 7}},
            "stuck": True,
        },
    )
    assert got["raised"].startswith("TypeError"), (
        f"S28-UNSTOPPED-RUNNER-DROPPED raised={got['raised']!r}"
    )
    assert "m-bad" in got["runners"], (
        f"S28-UNSTOPPED-RUNNER-DROPPED runners={got['runners']}"
    )
    assert got["retired"] == {"m-bad": ["retire_scheduler"]}, got["retired"]


@pytest.mark.parametrize("exc", ["RuntimeError", "OSError"])
def test_a_runner_that_cannot_start_costs_only_its_own_site(tmp_path, exc):
    """r2 (PM 19:20Z): ANY exception out of one site's load is that site's alone (r1 let these fail the whole load)"""
    got = _child(
        tmp_path,
        "load",
        tmp_path / "home",
        {"sites": ["a-good", "m-ok", "z-good"], "plant": {"sid": "m-ok", "exc": exc}},
    )
    assert got["raised"] == "" and got["sites"] == ["a-good", "z-good"], (
        f"S28-ONE-BAD-SITE-TOOK-ALL-SITES-DOWN start failure {exc}: raised={got['raised']!r} sites={got['sites']}"
    )
    listed = got["health"]["sites_config"].get("skipped_sites", [])
    assert [x["id"] for x in listed] == ["m-ok"] and listed[0]["error"].startswith(
        f"{exc} at "
    ), listed


def test_the_listed_and_logged_error_never_echoes_a_value_of_the_entry(tmp_path):
    """r2 (lens F4): float("<value>") puts the value in the exception message; health and the log carry class + line only"""
    bad = {
        "password": "S28MARK-PW",
        "accounts": [
            {"username": "u", "password": "S28MARK-APW", "cooldown_until": "S28MARK-CD"}
        ],
    }
    got = _child(
        tmp_path,
        "load",
        tmp_path / "home",
        {"sites": ["a-good", "m-bad"], "bad": {"m-bad": bad}},
    )
    assert got["sites"] == ["a-good"], got["sites"]
    err = got["health"]["sites_config"]["skipped_sites"][0]["error"]
    assert err.startswith("ValueError at runner.py:") and err.endswith(" __init__"), (
        f"S28-SKIP-ERROR-SHAPE {err!r}"
    )
    leaked = [
        m
        for m in ("S28MARK-PW", "S28MARK-APW", "S28MARK-CD")
        if m in json.dumps(got["health"]) + got["stderr"]
    ]
    assert not leaked, f"S28-SKIP-ERROR-ECHOES-ENTRY-VALUE {leaked}"


def test_clean_file_loads_every_site_and_lists_nothing(tmp_path):
    """positive control: the skip path is silent on a good file"""
    home = tmp_path / "home"
    got = _child(tmp_path, "load", home, {"sites": ["a", "b", "c"], "save": True})
    assert (
        got["raised"] == ""
        and got["sites"] == ["a", "b", "c"]
        and got["runners"] == ["a", "b", "c"]
    )
    assert "skipped_sites" not in got["health"]["sites_config"], (
        f"S28-CLEAN-FILE-LISTS-A-SKIP {got['health']}"
    )
    assert got["health"]["ok"] is True and "NOT loaded" not in got["stderr"]
    assert "restored 3 site(s)" in got["stderr"]
    assert sorted(got["file_after"]) == ["a", "b", "c"]


def test_every_entry_bad_loads_nothing_but_does_not_raise_and_keeps_the_file(tmp_path):
    home = tmp_path / "home"
    got = _child(
        tmp_path,
        "load",
        home,
        {
            "sites": ["p", "q"],
            "bad": {"p": {"accounts": "x"}, "q": "junk"},
            "save": True,
        },
    )
    assert got["raised"] == "" and got["sites"] == [] and got["runners"] == []
    assert [s["id"] for s in got["health"]["sites_config"]["skipped_sites"]] == [
        "p",
        "q",
    ]
    assert got["file_after"] == got["file_before"], (
        "S28-SKIPPED-SITE-ERASED-BY-SAVE all-bad"
    )


def test_skipped_entries_are_not_written_into_a_different_config_file(tmp_path):
    """the carried entries belong to the file they were read from"""
    home = tmp_path / "home"
    got = _child(
        tmp_path,
        "load",
        home,
        {
            "sites": ["a", "m"],
            "bad": {"m": {"accounts": "x"}},
            "save": True,
            "other_file": True,
        },
    )
    assert got["saved"] is True and sorted(got["file_after"]) == ["a"], (
        f"S28-SKIPPED-SITE-LEAKED-INTO-ANOTHER-FILE {sorted(got['file_after'])}"
    )


@pytest.mark.parametrize("top", ["[]", '"x"', "7", "null"])
def test_a_top_level_that_is_not_an_object_is_malformed_not_a_crash(tmp_path, top):
    home = tmp_path / "home"
    home.mkdir()
    (home / "sites_config.json").write_text(top, encoding="utf-8")
    got = _child(tmp_path, "load", home, {})
    assert got["raised"] == "", f"S28-NON-OBJECT-FILE-RAISED {top}: {got['raised']}"
    assert got["sites"] == [] and "malformed, ignoring" in got["stderr"], got["stderr"][
        -300:
    ]
