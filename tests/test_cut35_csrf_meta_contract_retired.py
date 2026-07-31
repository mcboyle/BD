"""The deleted csrf-meta-tag contract must not be probed OR advertised.

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

UNKNOWN IS A THIRD STATE. The 200 branch is measured against the real
`frontend/dist/index.html` when one is built. When it is not, the vite SOURCE
index stands in, and that is only faithful if the build has no HTML-injection
hook -- so `_dist_standin_is_faithful` proves that from `vite.config.ts` and
FAILS when it cannot.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

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


def _root_bodies() -> dict[str, bytes]:
    """Every body GET / can return, MEASURED by driving the real app."""
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    import bulk_downloader.app as A
    saved = A._M2_DIST_ROOT
    out: dict[str, bytes] = {}
    try:
        A._M2_DIST_ROOT = Path(tempfile.mkdtemp()) / "no-such-dist"
        with A.app.test_client() as c:
            out["dist-absent-503"] = c.get("/").data
        built = REPO / "frontend" / "dist" / "index.html"
        if built.is_file():
            A._M2_DIST_ROOT, label = built.parent, "built-dist"
        else:
            d = Path(tempfile.mkdtemp())
            shutil.copy(REPO / "frontend" / "index.html", d / "index.html")
            A._M2_DIST_ROOT, label = d, "vite-source-index-standin"
        with A.app.test_client() as c:
            out[label] = c.get("/").data
    finally:
        A._M2_DIST_ROOT = saved
    return out


def _dist_standin_is_faithful() -> tuple[bool, str]:
    if (REPO / "frontend" / "dist" / "index.html").is_file():
        return True, "measured the real built dist/index.html"
    cfg = REPO / "frontend" / "vite.config.ts"
    if not cfg.is_file():
        return False, "frontend/vite.config.ts is missing"
    if "transformIndexHtml" in cfg.read_text(encoding="utf-8"):
        return False, "vite.config.ts declares a transformIndexHtml hook"
    return True, "no built dist; vite declares no HTML-transform hook"


def _reachable(probe: str) -> list[str]:
    return [k for k, b in _root_bodies().items() if probe.encode() in b]


def _csrf_403_hint() -> str:
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    import bulk_downloader.app as A
    with A.app.test_client() as c:
        c.get("/")                       # warm the bd_session cookie
        r = c.post(GUARDED_PATH, json={})
        assert r.status_code == 403, (
            f"{GUARDED_PATH} answered {r.status_code}, not the CSRF 403 this "
            f"test reads its subject from")
        return (r.get_json() or {}).get("hint", "")


@pytest.mark.parametrize("probe", [META_PROBE, JINJA_PROBE])
def test_step3_probes_only_contracts_the_root_can_actually_serve(probe):
    ok, why = _dist_standin_is_faithful()
    assert ok, f"cannot establish what GET / can serve, so UNKNOWN and FAIL: {why}"
    reachable = _reachable(probe)
    probed = probe in _step3_program()
    assert probed == bool(reachable), (
        f"capture.sh step [3] and the app disagree about {probe!r}: "
        f"probed={probed}, reachable branches={reachable} ({why}). "
        f"A probe no reachable body can satisfy is a constant, not a check; a "
        f"reachable contract nobody probes is an unwatched one.")


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
    ok, why = _dist_standin_is_faithful()
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
