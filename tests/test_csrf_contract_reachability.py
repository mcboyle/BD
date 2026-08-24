"""Current CSRF root-body probe and actionable-hint contract.

THE DEFECT, in two places that echo one deleted premise.

`capture.sh` step [3] printed two booleans -- `contains meta tag:` and
`contains marker  :` -- that probed for a `<meta name="csrf-token">` tag and an
unsubstituted `{{ csrf_token }}` Jinja marker in the body of GET /. The Jinja
shell that could produce either went at v3.66.334: `bulk_downloader/templates/`
no longer exists, and `/` is served either as the installer 503 or as the
static `frontend/dist/index.html`. Both booleans were therefore structural
constants False on BOTH reachable branches, and nothing read them --
`capture_verdict.py` consumes only the integer stage exit passed through
`--stage-exit "csrf=$CSRF_EXIT"`, never the log body. A probe with no
discriminating power cannot fail and cannot pass; in a bundle that is tarred
and shipped it reads as an alarm for a subsystem that is healthy. CLAUDE.md
section 0's inverse failure in its most durable form: too quiet to get switched
off, so it misleads forever.

The same premise was also on the WIRE. `bulk_downloader/app.py`'s CSRF
rejection returned `"hint": "send X-CSRF-Token header matching the value
embedded in the HTML meta tag"` in every 403 body -- sending an SPA developer,
extension author or API consumer hunting for a tag deleted 485 versions ago.
That copy reaches people who are not holding the repo.

WHY THESE ASSERTIONS ARE BICONDITIONAL, NOT STRING BANS.

A test that simply forbade the literal `<meta name="csrf-token"` in capture.sh
would be a presence scan: it would keep passing if the Jinja shell came back,
and it would forbid a probe that had become legitimate again. So each probe is
compared against MEASURED reachability -- the set of bodies GET / can actually
return, driven through the real app on both branches. The probe must be present
if and only if some reachable body can contain it. The same shape guards the
403 hint: every endpoint the hint names is EXTRACTED from the hint and then
CALLED, so the hint is proven by being followed rather than pinned by spelling.

ROW 201 CLOSED THE SPELLING GAP BEHAVIOURALLY. The exact extracted step [3]
program runs against two equal-length controlled GET / bodies that differ only
in whether they carry the retired contract. Equal lengths keep the legitimate
body-length diagnostic constant; a change in shipped output proves the step
observes the contract regardless of concatenation, quote style, `chr()`, or a
regex. The audit's computed spelling is a RED-first executable fixture.

TWO CRY-WOLF FIXES THAT ARE LOAD-BEARING, both measured before they were made.
(1) The "does the hint still push the client at HTML" arm must not trip on the
bare substring "meta" -- the truthful wording "...returns the token and its
metadata" contains it, and firing there would make the gate's own message
factually false about the input that produced it. It matches an HTML-meta shape
(`<meta`, "meta tag", "meta name"), not the letters. (2) The "does the hint name
a working token source" arm must try EVERY `/api/` candidate, not the first:
naming the rejected route first ("POST /api/... needs a token; GET /api/csrf
mints one") is the natural wording for a 403, and a first-match-only test fails
a hint that is entirely correct. A gate that fires on a truthful re-wording gets
switched off, and this project treats that as a soundness bug, not a safe
default.

UNKNOWN IS A THIRD STATE. The 200 branch is measured only against the real
`frontend/dist/index.html` when one is built. A clean source checkout measures
only its explicit frontend-not-built 503 branch; it never fabricates a built
artifact from Vite source inputs.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import types
from contextlib import ExitStack, redirect_stdout
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO / "capture.sh"

META_PROBE = '<meta name="csrf-token"'
JINJA_PROBE = "{{ csrf_token }}"

# CSRF-enforced and AUTH-exempt, so a 403 here is unambiguously the CSRF hook
# and not an auth rejection. tests/test_v3_66_764_contract_probe_csrf_derived.py
# pins exactly that property of this path.
GUARDED_PATH = "/api/secrets/extension/pair"


def _step3_program() -> str:
    """The exact Python capture.sh step [3] feeds to `python -c`.

    Undoes the two bash double-quote escapes so the result is the program the
    interpreter really sees.
    """
    src = CAPTURE_SH.read_text(encoding="utf-8")
    m = re.search(
        r'venv/bin/python -c "\n(.*?)\n" > "\$OUT/03_csrf_diag\.log"', src, re.S)
    assert m, "could not locate the step [3] `python -c` block in capture.sh"
    code = m.group(1).replace('\\"', '"').replace('\\$', '$')
    assert "c.get('/')" in code, (
        "the extracted block no longer requests GET / -- the anchor moved, and "
        "every assertion below would be made over the wrong text")
    return code


def _step3_output(program: str, body: bytes) -> str:
    """Run the extracted diagnostic against a controlled GET / response."""
    class Headers:
        @staticmethod
        def getlist(_name):
            return []

    class Response:
        status_code = 200
        headers = Headers()

        def __init__(self, data):
            self.data = data

        def get_data(self, as_text=False):
            return self.data.decode() if as_text else self.data

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        @staticmethod
        def get(path):
            assert path == "/", f"step [3] requested an unexpected path: {path}"
            return Response(body)

    class App:
        @staticmethod
        def test_client():
            return Client()

    fake = types.ModuleType("bulk_downloader.app")
    fake.app = App()
    import bulk_downloader
    missing = object()
    # RESTORE VIA `saved_modules` + `sys.modules.update(`, WHICH IS NOT STYLE.
    # tests/test_v3_66_1034's leaker census is a deliberately over-reporting
    # TEXT heuristic: it recognises `saved_modules`, `sys.modules.update(` and
    # `_restore*modules`, and says in its own docstring that "a file that
    # restores by an idiom not listed here reads as a leaker". An equivalent
    # restore written as a direct assignment is therefore counted as a leak and
    # consumes a slot in a ratchet whose whole value is that every entry is a
    # real one. Using the recognised idiom keeps the census honest instead of
    # buying silence with a budget bump.
    saved_modules = {name: sys.modules[name]
                     for name in ("bulk_downloader.app",)
                     if name in sys.modules}
    previous_attribute = getattr(bulk_downloader, "app", missing)
    sys.modules["bulk_downloader.app"] = fake
    bulk_downloader.app = fake
    output = io.StringIO()
    try:
        with redirect_stdout(output):
            exec(compile(program, "capture.sh step [3]", "exec"), {})
    finally:
        sys.modules.pop("bulk_downloader.app", None)
        sys.modules.update(saved_modules)
        if previous_attribute is missing:
            delattr(bulk_downloader, "app")
        else:
            bulk_downloader.app = previous_attribute
    return output.getvalue()


def _step3_observes_probe(program: str, probe: str) -> bool:
    """Whether step [3]'s shipped output changes only with probe presence."""
    encoded = probe.encode()
    absent = b"prefix:" + (b"x" * len(encoded)) + b":suffix"
    present = b"prefix:" + encoded + b":suffix"
    assert len(absent) == len(present), (
        "paired bodies differ in length, so the legitimate body-length "
        "diagnostic would masquerade as a contract probe")
    return _step3_output(program, absent) != _step3_output(program, present)


def _root_bodies() -> dict[str, bytes]:
    """Every body GET / can return, MEASURED by driving the real app."""
    previous = os.environ.get("BD_DISABLE_KEEPALIVE")
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    try:
        import bulk_downloader.app as A
        saved = A._M2_DIST_ROOT
        out: dict[str, bytes] = {}
        with ExitStack() as stack:
            absent = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            A._M2_DIST_ROOT = absent / "no-such-dist"
            with A.app.test_client() as c:
                out["dist-absent-503"] = c.get("/").data
            built = REPO / "frontend" / "dist" / "index.html"
            if built.is_file():
                A._M2_DIST_ROOT = built.parent
                with A.app.test_client() as c:
                    out["built-dist"] = c.get("/").data
        return out
    finally:
        if "A" in locals() and "saved" in locals():
            A._M2_DIST_ROOT = saved
        if previous is None:
            os.environ.pop("BD_DISABLE_KEEPALIVE", None)
        else:
            os.environ["BD_DISABLE_KEEPALIVE"] = previous


def _root_evidence_is_complete() -> tuple[bool, str]:
    if (REPO / "frontend" / "dist" / "index.html").is_file():
        return True, "measured the real built dist/index.html"
    return True, "measured only the explicit frontend-not-built branch; no built artifact claimed"


def _reachable(probe: str) -> list[str]:
    return [k for k, b in _root_bodies().items() if probe.encode() in b]


def _csrf_403_hint() -> str:
    previous = os.environ.get("BD_DISABLE_KEEPALIVE")
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    try:
        import bulk_downloader.app as A
        with A.app.test_client() as c:
            c.get("/")                       # warm the bd_session cookie
            r = c.post(GUARDED_PATH, json={})
            assert r.status_code == 403, (
                f"{GUARDED_PATH} answered {r.status_code}, not the CSRF 403 this "
                f"test reads its subject from")
            return (r.get_json() or {}).get("hint", "")
    finally:
        if previous is None:
            os.environ.pop("BD_DISABLE_KEEPALIVE", None)
        else:
            os.environ["BD_DISABLE_KEEPALIVE"] = previous


@pytest.mark.parametrize("probe", [META_PROBE, JINJA_PROBE])
def test_step3_probes_only_contracts_the_root_can_actually_serve(probe):
    ok, why = _root_evidence_is_complete()
    assert ok, f"cannot establish what GET / can serve, so UNKNOWN and FAIL: {why}"
    reachable = _reachable(probe)
    probed = _step3_observes_probe(_step3_program(), probe)
    assert probed == bool(reachable), (
        f"capture.sh step [3] and the app disagree about {probe!r}: "
        f"probed={probed}, reachable branches={reachable} ({why}). "
        f"A probe no reachable body can satisfy is a constant, not a check; a "
        f"reachable contract nobody probes is an unwatched one.")


def test_step3_probe_verdict_rejects_a_computed_spelling(tmp_path, monkeypatch):
    """ROW 201 RED: a real computed probe must not escape literal matching."""
    source = CAPTURE_SH.read_text(encoding="utf-8")
    anchor = "   print('body length:', len(r.data))"
    assert source.count(anchor) == 1, (
        "the evasion was not planted exactly once; capture step [3] moved")
    computed_probe = (
        anchor + "\n"
        "   print('computed meta probe:', "
        "('<meta name=' + chr(34) + 'csrf-token' + chr(34)) "
        "in r.data.decode())"
    )
    candidate = tmp_path / "capture.sh"
    candidate.write_text(source.replace(anchor, computed_probe), encoding="utf-8")

    syntax = subprocess.run(
        ["bash", "-n", str(candidate)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr
    monkeypatch.setattr(sys.modules[__name__], "CAPTURE_SH", candidate)
    compile(_step3_program(), str(candidate), "exec")

    with pytest.raises(AssertionError, match="disagree"):
        test_step3_probes_only_contracts_the_root_can_actually_serve(META_PROBE)


def test_csrf_403_hint_names_a_token_source_that_actually_works():
    """The hint is proven by being FOLLOWED, not by its spelling.

    Every `/api/` path the hint names is a candidate, because naming the
    rejected route alongside the token source is a natural and more helpful
    403 wording. The test fails only if NONE of them yields a token that
    unblocks the request.
    """
    hint = _csrf_403_hint()
    cands = [m.rstrip(".,;)") for m in re.findall(r"/api/[A-Za-z0-9_./-]+", hint)]
    assert cands, f"the 403 hint names no endpoint a client can call: {hint!r}"
    import bulk_downloader.app as A
    tried = []
    for endpoint in cands:
        with A.app.test_client() as c:
            c.get("/")
            tr = c.get(endpoint)
            tok = (tr.get_json() or {}).get("csrf_token") if tr.status_code == 200 else None
            if not tok:
                tried.append((endpoint, tr.status_code))
                continue
            r2 = c.post(GUARDED_PATH, json={}, headers={"X-CSRF-Token": tok})
            if r2.status_code != 403:
                return
            tried.append((endpoint, "token rejected"))
    assert False, (
        f"no endpoint named in the 403 hint yields a token that works: "
        f"{tried!r}. hint={hint!r}")


def test_csrf_403_hint_does_not_point_at_an_unservable_html_contract():
    ok, why = _root_evidence_is_complete()
    assert ok, f"cannot establish what GET / can serve, so UNKNOWN and FAIL: {why}"
    hint = _csrf_403_hint()
    # An HTML-meta SHAPE, not the letters "meta": "metadata" is a truthful word
    # and a gate that fires on it would be making a false statement about its
    # own input.
    if not re.search(r"<meta\b|\bmeta[ _-]?tag\b|\bmeta[ _-]?name\b", hint, re.I):
        return
    assert _reachable(META_PROBE), (
        f"the CSRF 403 hint tells the client to read the token from an HTML "
        f"meta tag, but no reachable GET / body can contain one ({why}). "
        f"hint={hint!r}")
