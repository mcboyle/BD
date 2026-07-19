"""v3.66.468 WS3: before_capture / after_capture lifecycle wiring.

The two capture-path lifecycle points were defined + fireable since 465 but
their core firing points were unwired. WS3 wires them at the manual capture
boundary (non-guard runner_manual: `before_capture` after the live page is
ready in _launch; `after_capture` over the harvested artifact at finalize)
through a small gated, exception-isolated seam: `_fire_capture_lifecycle`.

This test exercises the SEAM (the live _launch/finalize call sites need a real
browser and validate on-stash). It asserts: the seam exists; it no-ops with
the full-access gate off; it fires registered before_capture/after_capture
hooks with the passed-through live objects when the gate is on; and an
after_capture hook can annotate the artifact dict in place (the documented
contract). A throwing hook must not propagate.

Runner-safe: zero-arg test fns, no pytest builtins, module globals restored.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402
from bulk_downloader import runner_manual as RM  # noqa: E402


def test_seam_exists():
    assert hasattr(RM, "_fire_capture_lifecycle"), dir(RM)


def test_before_capture_gated_off():
    P.reset()
    seen = []

    @P.lifecycle("before_capture")
    def hk(context, page, site_id):
        seen.append((context, page, site_id))

    try:
        # gate OFF -> fire_lifecycle no-ops even though the hook is registered.
        n = RM._fire_capture_lifecycle("before_capture", "CTX", "PAGE", "demo")
        assert n == 0, n
        assert seen == [], seen
    finally:
        P.reset()


def test_before_capture_fires_when_gate_on():
    P.reset()
    P.set_full_access(True)
    seen = []

    @P.lifecycle("before_capture")
    def hk(context, page, site_id):
        seen.append((context, page, site_id))

    try:
        n = RM._fire_capture_lifecycle("before_capture", "CTX", "PAGE", "demo")
        assert n == 1, n
        assert seen == [("CTX", "PAGE", "demo")], seen
    finally:
        P.reset()


def test_after_capture_annotates_artifact():
    P.reset()
    P.set_full_access(True)
    artifact = {"cookies": [], "recordings": {"clicks": []}}

    @P.lifecycle("after_capture")
    def hk(art, site_id):
        art["annotated_by"] = site_id

    try:
        n = RM._fire_capture_lifecycle("after_capture", artifact, "demo")
        assert n == 1, n
        assert artifact.get("annotated_by") == "demo", artifact
    finally:
        P.reset()


def test_throwing_hook_is_isolated():
    P.reset()
    P.set_full_access(True)

    @P.lifecycle("before_capture")
    def boom(context, page, site_id):
        raise RuntimeError("nope")

    try:
        # must not propagate; seam swallows + returns a count (0 invoked-ok)
        n = RM._fire_capture_lifecycle("before_capture", "CTX", "PAGE", "demo")
        assert isinstance(n, int), n
    finally:
        P.reset()
