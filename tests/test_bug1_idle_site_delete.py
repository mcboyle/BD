"""BUG-1 -- DELETE /api/sites/<sid> is a no-op for idle (never-started) sites.

api_delete nests the entire teardown (config removal + _save_sites_config) inside
`if sid in runners:`. A site created-but-never-started has no runner, so nothing
is removed -- yet the handler still returns {"ok": True}, so the UI shows a
successful delete while the site persists.

Fix: config/meta removal (and queue cleanup + save) must be UNCONDITIONAL; only
the runner teardown is conditional. A truly-absent id returns 404.

Direct-call test (no CSRF/before_request) via app_state + a request context.
"""
import os
import tempfile

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import bulk_downloader.app_state as st
from bulk_downloader.app import app
from bulk_downloader.app_sites_id_core import api_delete


def _status(resp):
    return resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)


def test_delete_idle_site_removes_config():
    sid = "idle_probe_bug1"
    st.s_cfg[sid] = {"url": "http://example.com", "name": "idle"}
    st.s_meta[sid] = {"status": "idle"}
    st.runners.pop(sid, None)          # idle: no runner
    with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
        api_delete(sid)
    assert sid not in st.s_cfg, "BUG-1: idle site was NOT removed from s_cfg"
    assert sid not in st.s_meta, "BUG-1: idle site was NOT removed from s_meta"


def test_delete_absent_site_returns_404():
    sid = "totally_absent_bug1"
    st.s_cfg.pop(sid, None); st.s_meta.pop(sid, None); st.runners.pop(sid, None)
    with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
        resp = api_delete(sid)
    assert _status(resp) == 404, f"absent site should 404, got {_status(resp)}"


def test_delete_running_site_still_works():
    # a site WITH a runner must still be fully torn down (no regression)
    sid = "running_probe_bug1"

    class _FakeRunner:
        def stop(self): pass
        def _stop_auto_retry(self): pass
    st.s_cfg[sid] = {"url": "http://example.com", "name": "run"}
    st.s_meta[sid] = {"status": "ready"}
    st.runners[sid] = _FakeRunner()
    with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
        api_delete(sid)
    assert sid not in st.s_cfg and sid not in st.runners, "running site teardown regressed"


if __name__ == "__main__":
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
