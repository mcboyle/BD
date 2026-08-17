"""The pre-share scrubber could not see BD's own auth cookie, and said SAFE.

`toolchain/bin/bd-log-sanitize` builds the share-safety verdict for six tools.
Its `RE_KV` key alternation was `(api[_-]?key|secret|token|password|
csrf[_-]?token)` behind a bare `\\b`. Two holes, measured at v3.66.819:

  (a) `session`, `sid` and `xsrf` were absent from the alternation entirely, so
      even the bare `session=<v>` and `sid=<v>` shapes leaked -- shapes
      `tools/capture_scrub.py` covered before its own fix;
  (b) `\\b` can never fall inside `bd_session=` because `_` is a word char, so
      every underscore-affixed variant of a key that WAS listed leaked too:
      `bd_token=`, `access_token=`, `x_api_key=`.

The consequence was not a missed redaction, it was an affirmative false verdict:
bd-share-safe wrote the bundle, stamped SHARE_MANIFEST.json `"scrubbed": 0` and
exited 0 with the live cookie value verbatim in the file it declared safe;
bd-wacz-scrub printed "verified clean"; bd-scrub-proof printed "SAFE TO SHARE".
CLAUDE.md section 0: a gate whose corpus structurally excludes its subject
reports clean, truthfully and uselessly, with a success exit code.

The fix must not swing the other way, and this file spends more of its length on
that than on the leak. A literal copy of capture_scrub's substring arm
(`[\\w-]*(?:session|sid|csrf|xsrf)[\\w-]*`) newly masks 23 distinct spans across
this repo's own tracked share-eligible text files -- CSS class names
(`Header-DesktopSidebar-Link-Icon:`), `Client-side:`, `inside:`, `residual =`.
A narrower widening that still lets the four LOW-SPECIFICITY bare arms
(`session|sid|csrf|xsrf`) take either separator is also over-sensitive, in a way
the repo differential structurally cannot see: it fires on ordinary `key: value`
operator input -- `session: filesystem_backed`, a systemd `SESSION: /dev/pts/0`,
`csrf: enabled_by_default`, and the CSS pseudo-class `.session:first-of-type`
(`.html` is in bd-share-safe:26 TEXT_EXT, so that corrupts a shared capture
mid-selector). Those shapes are in `REALISTIC_BENIGN` below precisely because
the repo corpus does not contain them. A redactor that cries wolf gets switched
off, so over-sensitivity is a soundness bug here, not a safe default.

The two guards therefore divide as follows, and neither can stand in for the
other: the inline corpora are the guard for log-shaped and config-shaped input,
and `test_the_widening_does_not_cry_wolf_over_this_repo` is the guard for the
repo's own markdown/JSON prose.

Nothing in tests/ exercised any of these six tools before this file.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "toolchain" / "bin"
SENTINEL = "8f3a9c1d4b6e2f70a1b2c3d4"   # fake, fixed, obviously synthetic


def _load(tool: str):
    """Load a toolchain/bin tool the way its five consumers do."""
    path = BIN / tool
    assert path.is_file(), f"{path} does not exist"
    modname = tool.replace("-", "_")
    spec = importlib.util.spec_from_loader(
        modname, importlib.machinery.SourceFileLoader(modname, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# corpora. Every value is fake and carries SENTINEL or an obvious marker.
# --------------------------------------------------------------------------

# Measured leaking at v3.66.819: scrub() returned these unchanged.
# All are `=`-separated (or a cookie/query shape that reduces to one). The
# quoted-JSON form `"session": "<v>"` is NOT here and is NOT fixed by this cut:
# `\s*[=:]\s*` cannot cross the closing quote, and today's regex misses
# `"api_key": "<v>"` for exactly the same reason, so that is pre-existing.
LEAKED_AT_HEAD = [
    f"bd_session={SENTINEL}",
    f"session={SENTINEL}",
    f"sessionid={SENTINEL}",
    f"session_id={SENTINEL}",
    f"sid={SENTINEL}",
    f"xsrf={SENTINEL}",
    f"x_csrf={SENTINEL}",
    f"bd_token={SENTINEL}",
    f"access_token={SENTINEL}",
    f"x_api_key={SENTINEL}",
    f"Cookie: bd_session={SENTINEL}",
    f"set-cookie: bd_session={SENTINEL}; Path=/; HttpOnly",
    f"GET /api/jobs?sid={SENTINEL} HTTP/1.1",
    f"SESSION_ID = {SENTINEL}",
    f"bd-session={SENTINEL}",
    f"session_key={SENTINEL}",
]

# Already redacted at v3.66.819. Regression guard: these must not regress.
COVERED_AT_HEAD = [
    f"api_key={SENTINEL}",
    f"token={SENTINEL}",
    f"password={SENTINEL}",
    f"secret={SENTINEL}",
    f"csrf_token={SENTINEL}",
    f"X-XSRF-TOKEN: {SENTINEL}",
]

# Cry-wolf corpus, part 1. PROVENANCE, stated because it bounds what this list
# can prove: every entry was drawn from a real span in THIS repo's own tracked
# text corpus that capture_scrub's substring arm masks. That makes it a good
# guard for repo-shaped prose and a useless one for anything the repo does not
# happen to contain -- which is the whole of part 2 below.
BENIGN_FROM_REPO_CORPUS = [
    f"tokenizer={SENTINEL}",
    "Read-side: 8-sample-average",
    "Client-side: 8-sample-average",
    "side_effect=RuntimeError_boom",
    "outside=tmp_path_fixture",
    "residual: known_documented_ok",
    "max_side=1280_by_720_scaled",
    "Header-DesktopSidebar-Link-Icon:nth-of-type",
    "Header-SidebarHover-InnerBox-BorderBox:nth-of-type",
    "sidebar__section:first-child",
    "sidebar-nav__item:first-child",
    "aside__menu__link:last-of-type",
    "inside: authenticated_area",
    "consider: something_else_here",
    "subsidiary=companyname_here",
    "presidential=candidate_list",
    "insider: trading_report_2026",
    "tokenized=representation_here",
    "sidecar: container_definition",
    "PipSession=connection_pool_x",
    "ManualLoginSession: headless_false",
    "PWD:/tmp/prestaged_site_packages",
    "user_sid_map=lookup_table_x",
    "not_a_secretive=value_here_ok",
]

# Cry-wolf corpus, part 2 -- THE ONE THAT MATTERS, and the one no differential
# over this repository can produce. bd-LOG-sanitize's declared inputs are logs,
# journal/systemd output, env dumps, YAML config, CDXJ/HAR JSON and shared HTML;
# the tracked corpus contains none of those extensions (measured breakdown in
# test_the_widening_does_not_cry_wolf_over_this_repo). These are `key: value`
# shapes with a low-specificity key and an 8+ char value: they fire on any
# design that lets `session|sid|csrf|xsrf` take a `:` separator, and they stay
# clean when those four bare arms are restricted to `=`.
REALISTIC_BENIGN = [
    "session: filesystem_backed",
    "SESSION: /dev/pts/0000000",
    "csrf: enabled_by_default",
    ".session:first-of-type { color: red }",
    "Session: 2026-07-21-template-host",
    "  sid: 000000000000",
    # env dumps: measured clean, pinned so a future widening cannot take them.
    "XDG_SESSION_ID=c2",
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
    "SESSION_MANAGER=local/test4:@/tmp/.ICE-unix/1234",
]

BENIGN = BENIGN_FROM_REPO_CORPUS + REALISTIC_BENIGN


# --------------------------------------------------------------------------
# 0. denominator canaries -- these run before the assertions they protect
# --------------------------------------------------------------------------

def test_the_scrubber_under_test_is_the_one_the_share_tools_load():
    """Derive the consumer set; never assert it from a comment.

    INSTRUMENT: ast over every file in toolchain/bin.
    PREDICATE: the module both constructs a SourceFileLoader and passes the
    string constant "bd-log-sanitize" as a call argument. A grep for the name
    alone is wrong in this tree -- bd-tool-lint and friends mention these names
    in plain lists and load nothing.
    """
    loaders, unparsed = [], []
    for entry in sorted(BIN.iterdir()):
        if not entry.is_file():
            continue
        src = entry.read_text(errors="ignore")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            unparsed.append(entry.name)
            continue
        if "SourceFileLoader" not in src or entry.name == "bd-log-sanitize":
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            args = list(node.args) + [kw.value for kw in node.keywords]
            if any(isinstance(a, ast.Constant) and a.value == "bd-log-sanitize"
                   for a in args):
                loaders.append(entry.name)
                break
    # unknown is a third state: a file this scan could not parse is not
    # evidence of absence. Resolve it -- every skip must be a shell script.
    for name in unparsed:
        first = (BIN / name).read_text(errors="ignore").splitlines()[:1]
        assert first and first[0].startswith("#!") and "python" not in first[0], (
            f"toolchain/bin/{name} did not ast.parse and is not a shell script; "
            f"this scan cannot say whether it loads bd-log-sanitize")
    assert sorted(loaders) == ["bd-scrub-proof", "bd-share-safe", "bd-wacz-scrub"], (
        f"direct loaders of bd-log-sanitize changed: {sorted(loaders)}. "
        f"If a tool was added or removed, extend this file's coverage in the "
        f"same cut -- otherwise the tests below stop covering a live path.")


def test_the_scrubber_is_neither_inert_nor_a_blanket():
    """A green sheet below must not come from a scrub() that changes nothing,
    nor from one that masks everything."""
    ls = _load("bd-log-sanitize")
    out, counts = ls.scrub(f"api_key={SENTINEL}")
    assert out != f"api_key={SENTINEL}" and counts, "scrub() redacts nothing"
    plain = "just a normal log line with no secrets"
    assert ls.scrub(plain)[0] == plain, "scrub() masks a line with no secret in it"
    assert ls.RE_KV.groups == 2, (
        f"RE_KV has {ls.RE_KV.groups} groups; bd-log-sanitize:scrub() builds the "
        f"replacement from group(1)+group(2). Any added arm must be non-capturing.")


# --------------------------------------------------------------------------
# 1. RED -- the defect
# --------------------------------------------------------------------------

@pytest.mark.parametrize("line", LEAKED_AT_HEAD)
def test_session_shaped_keys_are_redacted(line):
    ls = _load("bd-log-sanitize")
    out, _counts = ls.scrub(line)
    assert SENTINEL not in out, (
        f"bd-log-sanitize left a session-shaped credential verbatim: {out!r}")


def test_scrub_proof_does_not_call_a_session_cookie_log_safe(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(f"auth cookie bd_session={SENTINEL} issued for matt\n"
                   f"GET /api/jobs?sid=9a8b7c6d5e4f3a2b1c0d\n")
    sp = _load("bd-scrub-proof")
    res = sp.prove(str(log))
    assert res["safe"] is False and res["total_secrets"] >= 2, (
        f"bd-scrub-proof called a log holding a live session cookie SAFE TO "
        f"SHARE: {res}")


def test_share_safe_bundle_does_not_contain_the_cookie_it_declares_scrubbed(tmp_path):
    """bd-share-safe is the worst of the six: it WRITES the bundle and stamps a
    manifest asserting cleanliness. Assert over the FILE it wrote, not over its
    own report -- a tool's verdict about itself is not evidence.

    Subprocess, because bd-share-safe calls sys.exit(main()) at module level, so
    importing it runs the CLI.
    """
    log = tmp_path / "run.log"
    log.write_text(f"auth cookie bd_session={SENTINEL} issued for matt\n")
    out = tmp_path / "share"
    proc = subprocess.run(
        [sys.executable, str(BIN / "bd-share-safe"), str(log), "--out", str(out)],
        capture_output=True, text=True, timeout=120)
    bundled = (out / "run.log").read_text()
    manifest = json.loads((out / "SHARE_MANIFEST.json").read_text())
    entry = next(m for m in manifest["manifest"] if m["file"] == "run.log")
    assert SENTINEL not in bundled, (
        f"bd-share-safe exited {proc.returncode} and wrote a share bundle that "
        f"still contains the session cookie, while its manifest claims "
        f"scrubbed={entry['scrubbed']}: {bundled!r}")
    assert entry["scrubbed"] >= 1


def test_wacz_scrub_verified_clean_is_not_a_lie(tmp_path):
    src = tmp_path / "in.wacz"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("archive/data.cdxj",
                   'com,example)/ 20260730 {"cookie":"bd_session=%s"}\n' % SENTINEL)
        z.writestr("pages/img.png", b"\x89PNG\x00")
    dst = tmp_path / "out.wacz"
    proc = subprocess.run(
        [sys.executable, str(BIN / "bd-wacz-scrub"), str(src), "--out", str(dst)],
        capture_output=True, text=True, timeout=120)
    assert dst.is_file(), f"no output written (exit {proc.returncode}): {proc.stdout}"
    with zipfile.ZipFile(dst) as z:
        body = z.read("archive/data.cdxj").decode()
        # a broken zip fixture, not a scrubber hole, would fail this first
        assert z.read("pages/img.png").startswith(b"\x89PNG")
    assert SENTINEL not in body, (
        f"bd-wacz-scrub printed its verdict and left the cookie in place: "
        f"{proc.stdout.strip()!r} / member={body.strip()!r}")


def test_the_secret_fixture_cookie_kinds_survive_nothing():
    """bd-secret-fixture is the project's own negative control. bd-secret-canary
    feeds it only to tools/capture_scrub.py (bd-secret-canary:38), so this path
    was never covered by it.
    """
    fix = _load("bd-secret-fixture")
    ls = _load("bd-log-sanitize")
    leaking = set()
    for item in fix.corpus(2, fix.ALL_KINDS):
        if fix.SENTINEL in ls.scrub(item["value"])[0]:
            leaking.add(item["kind"])
    assert not (leaking & {"cookie", "bd_cookie"}), (
        f"bd-log-sanitize leaks the fixture's cookie kinds: {sorted(leaking)}")
    assert leaking <= {"privkey", "opaque"}, (
        f"bd-log-sanitize leaks fixture kinds beyond the two this cut declares "
        f"out of scope (no entropy/opaque backstop exists in this tool at all): "
        f"{sorted(leaking)}")


def test_the_two_copies_of_the_kv_key_list_agree():
    """toolchain/bin/bd-redaction-compiler:42 holds a second copy of the same
    key alternation in a file whose docstring calls its RULESET 'THE single
    source of truth'. Nothing compared them.

    INSTRUMENT: ast over both files (neither can be imported -- bd-log-sanitize
    guards __main__ but the compiler calls sys.exit(main()) at module level).
    PREDICATE: behavioural, not textual. The two literals cannot be identical:
    bd-log-sanitize captures group(1)=key and group(2)=separator, the compiler's
    is fully non-capturing and masks via group(0).

    This one PASSES on pristine -- both copies are identically broken. It is not
    a RED; it is the fold-in gate, and it goes red the moment one is fixed and
    the other is not.
    """
    ls_pat = _kv_pattern_of_log_sanitize()
    rc_pat = _kv_pattern_of_redaction_compiler()
    ls_rx, rc_rx = re.compile(ls_pat), re.compile(rc_pat)
    disagree = [line for line in LEAKED_AT_HEAD + COVERED_AT_HEAD + BENIGN
                if bool(ls_rx.search(line)) != bool(rc_rx.search(line))]
    assert not disagree, (
        "bd-log-sanitize RE_KV and bd-redaction-compiler RULESET['kv_secret'] "
        f"disagree on {len(disagree)} shape(s): {disagree}")


def _kv_pattern_of_log_sanitize() -> str:
    tree = ast.parse((BIN / "bd-log-sanitize").read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "RE_KV"):
            call = node.value
            assert isinstance(call, ast.Call), "RE_KV is no longer re.compile(...)"
            arg = call.args[0]
            assert isinstance(arg, ast.Constant), "RE_KV pattern is not a literal"
            return arg.value
    raise AssertionError("no RE_KV assignment found in bd-log-sanitize")


def _kv_pattern_of_redaction_compiler() -> str:
    tree = ast.parse((BIN / "bd-redaction-compiler").read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "RULESET"):
            for elt in node.value.elts:
                if (isinstance(elt, ast.Tuple) and isinstance(elt.elts[0], ast.Constant)
                        and elt.elts[0].value == "kv_secret"):
                    pat = elt.elts[1]
                    assert isinstance(pat, ast.Constant), (
                        "kv_secret pattern is not a single literal")
                    return pat.value
    raise AssertionError("no RULESET['kv_secret'] entry found in bd-redaction-compiler")


def test_the_tools_own_selftest_corpus_contains_the_shape_it_certifies():
    """bd-log-sanitize --selftest passed at v3.66.819 while the tool leaked,
    because its corpus (bd-log-sanitize:38) held no session shape. A gate whose
    corpus structurally excludes its subject reports clean.

    INSTRUMENT: ast over bd-log-sanitize; PREDICATE: the string constants
    reachable from the selftest() FunctionDef node.
    """
    tree = ast.parse((BIN / "bd-log-sanitize").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "selftest")
    corpus = "".join(
        node.value for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str))
    for shape in (r"(?i)bd_session\s*=", r"(?i)\bsid\s*="):
        assert re.search(shape, corpus), (
            f"bd-log-sanitize's own --selftest corpus contains no {shape!r} "
            f"case, so --selftest cannot see the hole this cut closed.")


# --------------------------------------------------------------------------
# 2. cry-wolf floors -- GREEN at v3.66.819, and they must stay green.
#    Over-sensitivity is a soundness bug: a redactor that mangles ordinary text
#    gets switched off, and then it protects nothing at all.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("line", COVERED_AT_HEAD)
def test_shapes_already_covered_stay_covered(line):
    ls = _load("bd-log-sanitize")
    assert SENTINEL not in ls.scrub(line)[0]


@pytest.mark.parametrize("line", BENIGN_FROM_REPO_CORPUS)
def test_the_scrubber_does_not_cry_wolf_on_repo_lookalikes(line):
    ls = _load("bd-log-sanitize")
    out, counts = ls.scrub(line)
    assert out == line, (
        f"over-redaction: {line!r} -> {out!r} ({counts}). A redactor that "
        f"cries wolf gets switched off.")


@pytest.mark.parametrize("line", REALISTIC_BENIGN)
def test_the_scrubber_does_not_cry_wolf_on_realistic_operator_input(line):
    """The guard the repo differential cannot be: `key: value` log, journal,
    YAML, env-dump and CSS shapes with a low-specificity key. No tracked file in
    this repository has these extensions, so only an inline corpus can pin them.
    """
    ls = _load("bd-log-sanitize")
    out, counts = ls.scrub(line)
    assert out == line, (
        f"over-redaction on realistic operator input: {line!r} -> {out!r} "
        f"({counts}). The four low-specificity bare arms (session|sid|csrf|xsrf) "
        f"must require '='; only the high-specificity arms may take ':'.")


# files the cut itself rewrites AFTER the band runs (CHANGELOG entry, then
# bd-regen-order). Scoring the ratchet against them makes the gate's own
# denominator move under it -- measured: a v3.66.820 CHANGELOG entry in the
# repo's house style alone adds 2 spans.
_DIFFERENTIAL_EXCLUDED = (
    "CHANGELOG.md",
    "PIN_INDEX.json",
    "DEPENDENCY_GRAPH.json",
    "DEPENDENCY_GRAPH.md",
    "ROUTE_INDEX.json",
)


def test_the_widening_does_not_cry_wolf_over_this_repo():
    """Differential over the repo's OWN share-eligible text corpus.

    DENOMINATOR, stated as measured rather than as intended: git-tracked files
    whose extension is in the union of the TEXT_EXT sets of bd-share-safe:26,
    bd-scrub-proof:29 and bd-wacz-scrub:29. At v3.66.819 that is 373 files, and
    the breakdown is 217 .md + 131 .json + 12 .html + 11 .txt + 2 yaml --
    ZERO .log, .csv, .cdx, .cdxj, .ndjson or .xml. The corpus that certifies
    bd-LOG-sanitize contains no log file at all. This differential is therefore
    the guard for markdown/JSON prose ONLY; REALISTIC_BENIGN above -- not this
    test -- is the guard for log-shaped, config-shaped and CSS input.

    Measured over that corpus: capture_scrub's substring arm would newly mask
    23 distinct spans, the shipped form 4.
    """
    text_ext = {".log", ".txt", ".json", ".md", ".csv", ".html", ".xml",
                ".yaml", ".yml", ".ndjson", ".cdx", ".cdxj"}
    tracked = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"],
                             capture_output=True, text=True, timeout=120).stdout.split("\n")
    rels = [rel for rel in tracked
            if rel and Path(rel).suffix.lower() in text_ext]
    # the exclusion must be visible, and it must not silently become a no-op if
    # one of these generated artifacts is renamed.
    missing = [name for name in _DIFFERENTIAL_EXCLUDED if name not in rels]
    assert not missing, (
        f"excluded-from-differential artifacts are no longer tracked under "
        f"these names: {missing}. Either the exclusion list is stale or the "
        f"artifact moved; an exclusion nobody can see is not an exclusion.")
    files = [REPO_ROOT / rel for rel in rels if rel not in _DIFFERENTIAL_EXCLUDED]
    files = [p for p in files if p.is_file()]
    assert len(files) >= 200, (
        f"only {len(files)} share-eligible tracked files found; this "
        f"differential has lost its denominator")
    rx = re.compile(_kv_pattern_of_log_sanitize())
    old = re.compile(r"(?i)\b(api[_-]?key|secret|token|password|csrf[_-]?token)"
                     r"(\s*[=:]\s*)[A-Za-z0-9_\-\.=/+]{8,}")
    newly, lost = set(), set()
    for p in files:
        text = p.read_text(errors="ignore")
        was = {m.group(0) for m in old.finditer(text)}
        now = {m.group(0) for m in rx.finditer(text)}
        newly |= (now - was)
        lost |= {s for s in was if not rx.search(s)}
    assert not lost, f"the new pattern stopped masking {len(lost)} shape(s): {sorted(lost)[:5]}"
    assert len(newly) <= 12, (
        f"the widened pattern newly masks {len(newly)} distinct spans of this "
        f"repo's own text (ceiling 12; measured 4 at v3.66.819, and 23 for a "
        f"literal copy of capture_scrub's substring arm). Excluded from this "
        f"count: {list(_DIFFERENTIAL_EXCLUDED)}. Over-sensitivity is a "
        f"soundness bug: {sorted(newly)[:15]}")


def test_the_tools_own_selftests_still_pass():
    for tool in ("bd-log-sanitize", "bd-scrub-proof", "bd-share-safe",
                 "bd-wacz-scrub", "bd-redaction-compiler"):
        proc = subprocess.run([sys.executable, str(BIN / tool), "--selftest"],
                              capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, f"{tool} --selftest exit={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        assert "SELFTEST PASS" in proc.stdout, f"{tool}: {proc.stdout}"


# --------------------------------------------------------------------------
# v3.66.863 -- a file the scanner COULD NOT READ counted as a file with
# nothing in it.
#
# `bdtools_sec.should_scan(name, data)` decides by CONTENT and throws the
# filename away. That is correct for the .warc gap it was written to close
# (v3.66.859): an extension allowlist is what let a WACZ payload through three
# tools at once. But it leaves one class no content rule can reach -- a file
# that IS the credential store rather than one that CONTAINS a secret. A
# password-manager export is a binary container: looks_binary sees NUL,
# refuses to regex it (rightly), the member lands in `binary_skipped`, and
# `binary_skipped` never touches `safe`.
#
# MEASURED on a real file: "Proton Pass_export_2026-07-19_<n>.xlsx" sat in an
# unencrypted snapshot on the operator's box from 2026-07-19 to 2026-08-04.
# The v2 archive inventory's credential patterns did not flag it, and neither
# did any tool here -- bd-scrub-proof returned exit 0, "SAFE TO SHARE (0 secret
# hit(s), 1 binary member(s) skipped)". bd-share-safe consumes that verdict to
# build share bundles.
#
# Both directions are pinned below. The obvious over-correction -- treat every
# unscannable member as unsafe -- would make the tool useless, since a WACZ is
# mostly binary by design.
# --------------------------------------------------------------------------

CRED_NAMES = [
    "Proton Pass_export_2026-07-19_1784474629.xlsx",
    "keepass-backup.kdbx",
    "bitwarden_export_20260101.json",
    "1password-vault.1pux",
    "lastpass_export.csv",
    "passwords.csv",
]

BENIGN_BINARY_NAMES = [
    "pages/screenshot.png",
    "archive/data.warc.gz",
    "assets/font.woff2",
    "media/clip.mp4",
    # near-misses: these name a concept, not a credential store
    "docs/password_policy.md",
    "src/session_manager.py",
    "reports/token_usage.json",
]


def _binary_blob() -> bytes:
    """A NUL-bearing blob so looks_binary() is PROVABLY true for it.

    Payload bytes are a zero-entropy repeat on purpose (CLAUDE.md section 7):
    a realistic-looking secret written into a tracked test turns the test file
    into a place the secret lives, and gitleaks scans the PR's whole range.
    """
    return b"PK\x03\x04\x00\x00" + b"a" * 512


def test_looks_binary_really_is_true_for_the_fixture():
    """Denominator canary. If the blob were scannable the two tests below
    would pass for the wrong reason -- they would be exercising the text path,
    not the binary-skip path that carries the defect."""
    sec = _load("bdtools_sec.py")
    assert sec.looks_binary(_binary_blob()) is True
    assert sec.should_scan("anything.xlsx", _binary_blob()) is False


@pytest.mark.parametrize("name", CRED_NAMES)
def test_a_credential_store_is_not_safe_to_share(name, tmp_path):
    """RED on v3.66.862: exit 0, 'SAFE TO SHARE', because the member was
    skipped as binary and skipping is silent."""
    z = tmp_path / "snapshot.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr(name, _binary_blob())
        f.writestr("readme.txt", "nothing sensitive here")
    sp = _load("bd-scrub-proof")
    res = sp.prove(str(z))
    assert res["safe"] is False, (
        f"bd-scrub-proof called an archive holding {name!r} SAFE TO SHARE. "
        f"No content rule can reach this file -- it IS the credential store, "
        f"and it is binary, so the only signal available is the name: {res}")


@pytest.mark.parametrize("name", BENIGN_BINARY_NAMES)
def test_ordinary_binaries_stay_safe(name, tmp_path):
    """The over-sensitive direction, and the one that would destroy the tool.

    A WACZ is mostly binary by design. A fix that flags every unscannable
    member -- or that matches 'password'/'token'/'session' as substrings --
    makes SAFE TO SHARE unreachable and the gate gets switched off. Section 0
    calls over-sensitivity a soundness bug, not a safe default."""
    z = tmp_path / "snapshot.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr(name, _binary_blob())
        f.writestr("readme.txt", "nothing sensitive here")
    sp = _load("bd-scrub-proof")
    res = sp.prove(str(z))
    assert res["safe"] is True, (
        f"bd-scrub-proof refused an archive whose only binary member is "
        f"{name!r}. That is an ordinary artifact, not a credential store: {res}")


def test_share_safe_refuses_to_bundle_a_credential_store(tmp_path):
    """RED on v3.66.862, and the reason it is a SEPARATE test from the
    bd-scrub-proof one above: fixing the shared library does NOT fix this.

    bd-share-safe does not call prove(); `build()` decides inclusion itself.
    MEASURED before the fix: exit 0, "1 included, 0 refused", and the export
    written into the bundle verbatim. A shared helper is not shared behaviour
    unless the consumer actually calls it -- the same lesson as the three
    divergent TEXT_EXT sets that hid the .warc gap in all of them at once.

    bd-share-safe is the tool that WRITES the artifact someone hands over, so
    it is the last place this can be caught.
    """
    cred = tmp_path / "Proton Pass_export_2026-07-19.xlsx"
    cred.write_bytes(b"PK\x03\x04\x00\x00" + b"a" * 512)
    out = tmp_path / "share"
    proc = subprocess.run(
        [sys.executable, str(BIN / "bd-share-safe"), str(cred), "--out", str(out)],
        capture_output=True, text=True, timeout=120)
    assert not (out / cred.name).exists(), (
        f"bd-share-safe exited {proc.returncode} and copied a password-manager "
        f"export into a share bundle it declares safe:\n{proc.stdout}")
    manifest = json.loads((out / "SHARE_MANIFEST.json").read_text())
    assert manifest["refused"], f"nothing refused: {manifest}"
    assert any("proton pass" in x["reason"].lower() for x in manifest["refused"]), (
        f"refused, but not for the right reason -- the manifest must say WHY: "
        f"{manifest['refused']}")


BD_GATE_SCOPE = "repo-wide"
