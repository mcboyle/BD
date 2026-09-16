"""Row 769 -- LOGIN-LOGS-LACK-SITE-ID-UNDER-CONCURRENCY.

Every `login:` line the login submit path writes to stderr (and so into
logs/bulk_downloader.log) was emitted behind a bare `  login: ` prefix.  Site
lanes log concurrently into one stream, so the only way to say which site a
login line belonged to was to correlate it against neighbouring
`[<sid>][network]` lines -- a correlation that already produced one recorded
misattribution.  The runner's own lines have carried `[{self.site_id}]` since
they were written; the login lines carried nothing.

The row's acceptance is behavioural: capture stderr for a login submit under a
stub site id, and every "login:" line must carry that id.  Two limbs assert
exactly that -- one lane, then two lanes interleaved into one shared stream
with an exact line count and a per-lane attribution check.  A third limb makes
the property standing rather than a snapshot: no tracked source in the
application package may emit a `  login...` stderr line behind a bare literal
prefix, because such a prefix cannot carry the tag by construction.  A fourth
refuses the inert fix: a site id that no caller passes is a site id that is
never in the log, whatever the unit tests say.

BD_GATE_SCOPE is "repo-wide": the census denominator is every tracked
`bulk_downloader/**/*.py`, not one module, and an untagged login emitter added
anywhere in the package is the regression this gate exists to refuse.
"""
import ast
import pathlib
import subprocess
import sys
import threading

BD_GATE_SCOPE = "repo-wide"

_REPO = pathlib.Path(__file__).resolve().parents[1]

# The static skeleton of a write() argument, with every interpolated
# expression collapsed to this one character.  A prefix that begins with the
# marker CAN carry a site tag; one that begins with a literal cannot.
_HOLE = "\x00"
_LOGIN_LEAD = "  login"
_TAGGED_LEAD = f"  {_HOLE}login"


# ── the census probe, extracted so it can be driven with synthetic input ─────

def _skeleton(node: ast.AST) -> str | None:
    """The static text of a write() argument, holes for interpolations."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            piece.value
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
            else _HOLE
            for piece in node.values)
    return None


def _login_write_sites(source: str) -> list[tuple[int, str]]:
    """(line, kind) for every `sys.stderr.write` of a login-prefixed line.

    kind is "bare" when the prefix is a literal `  login...` -- the defect --
    and "tagged" when an interpolated expression stands ahead of it, the only
    shape that can carry the site id.  Extracted from the census below for the
    reason every probe in this repo is extracted: a comparison whose only
    assertions run against the tree it is checking is a detector with no
    detector, so the control underneath drives this with sources it owns.
    """
    sites: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write"):
            continue
        target = node.func.value
        if not (isinstance(target, ast.Attribute) and target.attr == "stderr"):
            continue
        if not node.args:
            continue
        skeleton = _skeleton(node.args[0])
        if skeleton is None:
            continue
        if skeleton.startswith(_TAGGED_LEAD):
            sites.append((node.lineno, "tagged"))
        elif skeleton.startswith(_LOGIN_LEAD):
            sites.append((node.lineno, "bare"))
    return sorted(sites)


def _do_login_call_sites(source: str) -> list[tuple[int, bool]]:
    """(line, passes_a_site_id) for every `do_login(...)` call in a source.

    The site id reaches do_login one of two ways: named explicitly by the
    caller, or already inside the config mapping it is handed.  Only the first
    is visible in a call expression, and both real callers hold the id in a
    variable beside the config, so that is what this asks for.
    """
    calls: list[tuple[int, bool]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.id if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute)
                else None)
        if name != "do_login":
            continue
        calls.append((node.lineno,
                      any(kw.arg == "site_id" for kw in node.keywords)))
    return sorted(calls)


def _tracked_package_sources() -> list[str]:
    """Denominator from the filesystem: tracked .py under bulk_downloader/."""
    out = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "--", "bulk_downloader"],
        capture_output=True, text=True, check=True).stdout.split("\n")
    return sorted(p for p in out if p.endswith(".py"))


# ── the behavioural fixture: a login submit that needs no browser ───────────

class _Sink:
    """A stderr stand-in shared by every lane, with the lock the real stream
    has and StringIO does not."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chunks: list[str] = []

    def write(self, text: str) -> int:
        with self._lock:
            self._chunks.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    def login_lines(self) -> list[str]:
        return [ln for ln in "".join(self._chunks).split("\n") if "login:" in ln]


def _run_login(site_name: str, site_id: str = "") -> None:
    """One login submit that returns before any browser is opened.

    A config with no login_url/username/password takes do_login's missing-
    credentials early return, which writes exactly one `  login:` line naming
    the site.  That is the whole browser-free surface the row needs: the line
    exists, and the only question is whether it says which site it is about.
    The id travels in the config here so that this fixture runs unchanged on a
    tree that has no site_id parameter at all.
    """
    from bulk_downloader.login_impl.submit import do_login
    config = {"name": site_name}
    if site_id:
        config["site_id"] = site_id
    ok, info, cookies = do_login(config)
    assert ok is False and cookies == [], (ok, info, cookies)
    assert "Missing credentials" in info, info


# ── limb 1: the row's own acceptance, one lane ──────────────────────────────

def test_a_login_submit_under_a_stub_site_id_tags_every_login_line(monkeypatch):
    sink = _Sink()
    monkeypatch.setattr(sys, "stderr", sink)
    _run_login("Alpha Archive", "stub-site-769")

    lines = sink.login_lines()
    assert lines, (
        "the fixture produced NO 'login:' line at all, so every assertion "
        "below would pass over an empty population -- do_login's browser-free "
        "early return stopped writing one and this gate measures nothing")
    untagged = [ln for ln in lines if "[stub-site-769]" not in ln]
    assert not untagged, (
        f"row 769: {len(untagged)} of {len(lines)} 'login:' line(s) emitted "
        f"under stub site id 'stub-site-769' carry no site tag, so a reader "
        f"of logs/bulk_downloader.log cannot say which site wrote them: "
        f"{untagged}")


# ── limb 2: the defect as the row states it -- lanes interleaved ────────────

def test_two_interleaved_login_lanes_are_each_attributable(monkeypatch):
    """The exact-count limb: two lanes, one stream, no misattribution."""
    sink = _Sink()
    monkeypatch.setattr(sys, "stderr", sink)

    lanes = [("Alpha Archive", "sid-alpha"), ("Beta Bazaar", "sid-beta")]
    errors: list[BaseException] = []

    def lane(name: str, sid: str) -> None:
        try:
            _run_login(name, sid)
        except BaseException as exc:      # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=lane, args=pair, name=f"login-{pair[1]}")
               for pair in lanes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, f"a login lane raised: {errors}"
    assert not [t for t in threads if t.is_alive()], "a login lane did not finish"

    lines = sink.login_lines()
    assert len(lines) == 2, (
        f"expected exactly one 'login:' line per lane from two lanes; the "
        f"shared stream holds {len(lines)}: {lines}")

    misattributed = []
    for name, sid in lanes:
        owned = [ln for ln in lines if repr(name) in ln]
        if len(owned) != 1 or f"[{sid}]" not in owned[0]:
            misattributed.append((name, sid, owned))
    assert not misattributed, (
        f"row 769: with two lanes interleaved into one stream a 'login:' line "
        f"cannot be attributed to the site that wrote it -- this is the "
        f"recorded misattribution, and only the [<sid>] tag closes it: "
        f"{misattributed}")


# ── limb 3: repo-wide census -- no bare login emitter anywhere ──────────────

def test_no_tracked_source_emits_a_bare_login_prefixed_stderr_line():
    sources = _tracked_package_sources()
    assert sources, "no tracked bulk_downloader sources; the census is vacuous"

    bare: dict[str, list[int]] = {}
    tagged_total = 0
    for rel in sources:
        sites = _login_write_sites((_REPO / rel).read_text(encoding="utf-8"))
        lines = [ln for ln, kind in sites if kind == "bare"]
        tagged_total += sum(1 for _, kind in sites if kind == "tagged")
        if lines:
            bare[rel] = lines

    assert tagged_total or bare, (
        f"the census found no login-prefixed stderr write at all across "
        f"{len(sources)} tracked sources, so it would report a clean tree "
        f"whatever the tree held -- the probe, not the package, is broken")
    assert not bare, (
        f"login-prefixed stderr line(s) written behind a bare literal prefix, "
        f"which cannot carry the site id under concurrent lanes (row 769): "
        f"{bare}")


# ── limb 4: the correction must not be inert in production ─────────────────

def test_every_do_login_caller_names_the_site_it_is_logging_in():
    """A site id no caller passes is a site id that never reaches the log."""
    callers: dict[str, list[tuple[int, bool]]] = {}
    for rel in _tracked_package_sources():
        if rel.endswith("login_impl/submit.py"):
            continue                      # the definition, not a call site
        sites = _do_login_call_sites((_REPO / rel).read_text(encoding="utf-8"))
        if sites:
            callers[rel] = sites

    total = sum(len(v) for v in callers.values())
    assert total == 2, (
        f"expected exactly the two known do_login call sites (the site runner "
        f"and the keeper relogin path); found {total}: {callers}")
    silent = {rel: [ln for ln, named in sites if not named]
              for rel, sites in callers.items()}
    silent = {rel: lines for rel, lines in silent.items() if lines}
    assert not silent, (
        f"do_login call site(s) that name no site id, so every line that call "
        f"emits is untagged in production however the unit limbs above look: "
        f"{silent}")


# ── controls ───────────────────────────────────────────────────────────────

def test_the_census_probe_reports_a_bare_emitter_it_is_shown():
    """Negative control: the probe says YES to the defect before it says NO.

    Driven with sources this test owns, so it fails for the intended reason on
    a tree where the census above is green.
    """
    bare_src = 'import sys\nsys.stderr.write(f"  login: {info}\\n")\n'
    tagged_src = ('import sys\n'
                  'sys.stderr.write(f"  {_tag()}login: {info}\\n")\n')
    unrelated_src = 'import sys\nsys.stderr.write(f"  banner: {info}\\n")\n'

    assert _login_write_sites(bare_src) == [(2, "bare")], (
        "the census cannot see a bare login emitter, so its zero over the "
        "tree would mean nothing")
    assert _login_write_sites(tagged_src) == [(2, "tagged")], (
        "the census calls the corrected shape a defect, so it would stay red "
        "after the fix and be switched off")
    assert _login_write_sites(unrelated_src) == [], (
        "the census claims a non-login stderr line, so its population is not "
        "the one row 769 is about")


def test_the_caller_probe_reports_a_call_that_names_no_site():
    """Negative control for limb 4, on sources this test owns."""
    silent_src = "do_login(self.config, allow_manual_takeover=True)\n"
    named_src = "do_login(self.config, site_id=self.site_id)\n"
    assert _do_login_call_sites(silent_src) == [(1, False)]
    assert _do_login_call_sites(named_src) == [(1, True)]
    assert _do_login_call_sites("other_call(site_id=1)\n") == []


def test_a_login_line_with_no_site_id_in_scope_stays_untagged(monkeypatch):
    """Control for the behavioural limbs: with no site id anywhere the line is
    emitted untagged, which is exactly what limb 1 refuses.  Without this, the
    tag assertion could be passing because nothing is emitted at all."""
    sink = _Sink()
    monkeypatch.setattr(sys, "stderr", sink)
    _run_login("No Lane")

    lines = sink.login_lines()
    assert len(lines) == 1, f"expected one untagged login line, got {lines}"
    assert "[" not in lines[0].split("login:")[0], (
        f"a site tag appeared with no site id in scope, so limb 1 could pass "
        f"on a stream that names no site: {lines[0]!r}")
