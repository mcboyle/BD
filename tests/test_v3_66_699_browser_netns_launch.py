"""699 (v3.66.699, F5 Phase 2 / fork 2b) -- launch the BROWSER inside a
per-capture netns [safety].

Phase 1 (689) routed the yt-dlp / gallery-dl SUBPROCESS fallbacks through the
netns engine, but the browser -- the primary capture path -- still egressed
un-isolated, because Playwright spawns Chromium ITSELF (in-process API:
launch_persistent_context / chromium.launch), so there is no argv for a caller
to wrap with ``ip netns exec``.

FORK RESOLUTION (operator confirmed 2b): rather than an in-process ``setns``
(2a, which would need the much heavier CAP_SYS_ADMIN), exploit Playwright's
``executable_path`` seam -- point it at a tiny SHIM that re-execs the real
browser binary inside the namespace:

    #!/bin/sh
    exec ip netns exec "$NETNS_NS" "$NETNS_BROWSER_BIN" "$@"

Playwright spawns the shim believing it is the browser; the shim re-execs
Chromium in the ns, passing argv through untouched (Playwright passes ~40 args,
so byte-exact passthrough is load-bearing). Needs only CAP_NET_ADMIN.

Verified live against a real kernel before this cut: a process launched through
the shim reports the target netns identity, sees only ``lo`` (no eth0), and
receives its argv unmodified.

RED-first on pristine v3.66.698: ``netns_browser_shim`` /
``write_browser_shim`` / ``browser_launch_env`` do not exist.

Pure/unit -- the shim script is generated + its content asserted; nothing here
launches a real browser (that live exercise is the operator's, on stash).
"""
import os
import stat
import subprocess

from bulk_downloader import netns_isolation as ni


# ── shim content ──────────────────────────────────────────────────────
def test_shim_reexecs_into_netns_with_argv_passthrough():
    src = ni.netns_browser_shim()
    assert src.startswith("#!/bin/sh")
    # re-exec (not fork) so Playwright's process handle IS the browser
    assert "exec ip netns exec" in src
    # argv passthrough must be quoted "$@" -- unquoted $@ would re-split args
    assert '"$@"' in src, "argv must pass through as quoted \"$@\""
    # ns + real binary come from the environment (never interpolated as text)
    assert "$NETNS_NS" in src and "$NETNS_BROWSER_BIN" in src


def test_shim_does_not_interpolate_untrusted_values():
    """The ns name / browser path are read from env at run time, so the shim
    body itself carries no caller-supplied text."""
    src = ni.netns_browser_shim()
    assert "bd_cap_" not in src
    assert "chrome" not in src.lower()


# ── write_browser_shim ────────────────────────────────────────────────
def test_write_browser_shim_is_executable(tmp_path):
    p = ni.write_browser_shim(str(tmp_path))
    assert os.path.exists(p)
    mode = os.stat(p).st_mode
    assert mode & stat.S_IXUSR, "shim must be executable (Playwright execs it)"
    assert ni.netns_browser_shim() == open(p).read()


def test_write_browser_shim_is_idempotent(tmp_path):
    a = ni.write_browser_shim(str(tmp_path))
    b = ni.write_browser_shim(str(tmp_path))
    assert a == b and os.path.exists(a)


def test_written_shim_actually_runs(tmp_path):
    """Execute the real generated shim with a harmless NETNS_BROWSER_BIN to prove the
    exec line + argv passthrough are syntactically correct (no netns needed:
    point NETNS_NS at nothing and NETNS_BROWSER_BIN at /bin/echo via a stub `ip`)."""
    shim = ni.write_browser_shim(str(tmp_path))
    # stub `ip` so the shim's `ip netns exec <ns> <browser> "$@"` resolves here
    stub = tmp_path / "ip"
    stub.write_text('#!/bin/sh\n# ip netns exec <ns> <cmd> args...\nshift 3\nexec "$@"\n')
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["NETNS_NS"] = "bd_cap_test"
    env["NETNS_BROWSER_BIN"] = "/bin/echo"
    r = subprocess.run([shim, "--headless", "--flag=value"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "--headless --flag=value", r.stdout


# ── browser_launch_env: what the launcher must pass ───────────────────
def test_browser_launch_env_carries_ns_and_real_binary():
    env = ni.browser_launch_env("bd_cap_x", "/opt/chrome/chrome")
    assert env["NETNS_NS"] == "bd_cap_x"
    assert env["NETNS_BROWSER_BIN"] == "/opt/chrome/chrome"


def test_browser_launch_env_requires_both():
    assert ni.browser_launch_env("", "/opt/chrome") == {}
    assert ni.browser_launch_env("bd_cap_x", "") == {}
