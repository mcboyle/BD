"""Stage 1 of the socket guard: the recorder records, and blocks nothing.

The operator's decision (v3.66.980) is explicitly STAGED -- record first,
measure, enforce in a second cut. So the property under test is not "outbound
calls are refused"; it is "an outbound call is SEEN, a loopback call is not
mistaken for one, and neither is impeded".

The over-sensitivity direction is tested in the same file on purpose. CLAUDE.md
section 6: a fix for "reports clean when blind" that simply calls everything
suspicious passes the escape's test and destroys the tool. A recorder that
flagged loopback would drown the stage-2 list in the suite's own fixtures and
get switched off.

NOTE ON HERMETICITY, which is the trap this file is most exposed to: the guard
for "no live network in unit tests" must not itself make a live network call.
`test_urllib_reaches_the_hook` therefore proves REACH against a loopback HTTP
server -- the hook sees the connect, and classifies it local -- rather than by
fetching a real URL. The one measurement against the real PyPI path was taken
out-of-suite while building this and is recorded in `_socket_record`'s
docstring, where it cannot run.
"""
import http.server
import socket
import threading
import urllib.request

import pytest

import _socket_record as sr


@pytest.fixture
def recorder(tmp_path):
    """Arm the recorder against an isolated sink, then hand the session back.

    The session-level hook is dropped and re-armed against the REAL directory
    (captured before any patching), so a test that isolates its sink cannot
    leave the rest of the run writing into a deleted tmp_path.
    """
    real_dir = sr.sink_dir()
    sr.disarm()
    sr.reset_counters()
    sr.arm(tmp_path / "rec")
    try:
        yield sr
    finally:
        sr.disarm()
        sr.reset_counters()
        sr.arm(real_dir)


@pytest.fixture
def loopback_server():
    """A real HTTP server on 127.0.0.1, for the reach and no-block proofs."""
    class Quiet(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d/" % srv.server_port
    finally:
        srv.shutdown()
        srv.server_close()


LOCAL = [
    ("127.0.0.1", 8080),
    ("127.53.0.1", 53),
    ("::1", 443),
    ("localhost", 11434),
    ("anything.localhost", 80),
    ("", 5555),
    "/run/postgresql/.s.PGSQL.5432",        # AF_UNIX
    b"/tmp/some.sock",
]

NOT_LOCAL = [
    ("151.101.192.223", 443),               # the measured PyPI address
    ("pypi.org", 443),                      # a name that reached connect()
    ("192.168.1.10", 80),                   # LAN is not loopback
    ("10.0.0.5", 5432),
    ("169.254.169.254", 80),                # link-local metadata endpoint
    ("2606:4700::1111", 443),
    ("8.8.8.8", 53),
]


@pytest.mark.parametrize("address", LOCAL)
def test_local_addresses_are_not_recorded(address):
    assert sr.is_local(address) is True


@pytest.mark.parametrize("address", NOT_LOCAL)
def test_addresses_that_leave_the_host_are_recorded(address):
    assert sr.is_local(address) is False


def test_a_non_loopback_connect_is_recorded(recorder):
    """TEST-NET-1 (RFC 5737) with a tiny timeout: the record precedes the call."""
    s = socket.socket()
    s.settimeout(0.01)
    try:
        s.connect(("192.0.2.1", 9))
    except OSError:
        pass                                 # unroutable by design; the SYN is dropped
    finally:
        s.close()

    rows = [r for rs in recorder.summarize().values() for r in rs]
    assert [r["host"] for r in rows] == ["192.0.2.1"], rows
    assert rows[0]["port"] == 9
    assert rows[0]["nodeid"] is not None, "a record with no test attached is not a list"
    assert any("test_v3_66_1031" in f for f in rows[0]["frames"]), rows[0]["frames"]


def test_a_udp_route_lookup_is_recorded_but_marked_as_sending_nothing(recorder):
    """The single most decision-relevant field in a record.

    `_lan_ip_guess` connects a SOCK_DGRAM socket to 8.8.8.8:53 to ask the
    routing table which source address it would use; no packet is sent, and
    app.py says so. That mechanism was 107 of the 124 rows in this recorder's
    first harvest. If stage 2 cannot tell it from a real HTTPS call it will
    either block LAN-IP discovery for no benefit, or drown the four genuine
    callers in a list of 124.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
    finally:
        s.close()

    rows = [r for rs in recorder.summarize().values() for r in rs]
    assert len(rows) == 1, rows
    assert rows[0]["type"] == "SOCK_DGRAM"
    assert rows[0]["sends_packets"] is False


def test_a_tcp_attempt_is_marked_as_sending_packets(recorder):
    s = socket.socket()
    s.settimeout(0.01)
    try:
        s.connect(("192.0.2.1", 9))
    except OSError:
        pass
    finally:
        s.close()

    rows = [r for rs in recorder.summarize().values() for r in rs]
    assert len(rows) == 1, rows
    assert rows[0]["type"] == "SOCK_STREAM"
    assert rows[0]["sends_packets"] is True


def test_a_background_thread_connect_is_attributed_but_marked_ambient(recorder):
    """9 of the first harvest's 16 packet-senders had no test attached.

    They came from background threads, where the per-test fixture's thread-local
    nodeid is not set. An unattributable row cannot be actioned, so the record
    falls back to the last test the main thread announced -- and says "ambient"
    so a reader knows it is a lead rather than a finding.
    """
    def call_out():
        s = socket.socket()
        s.settimeout(0.01)
        try:
            s.connect(("192.0.2.9", 9))
        except OSError:
            pass
        finally:
            s.close()

    t = threading.Thread(target=call_out, name="bd-probe-thread")
    t.start()
    t.join()

    rows = [r for rs in recorder.summarize().values() for r in rs]
    assert len(rows) == 1, rows
    assert rows[0]["attribution"] == "ambient"
    assert rows[0]["thread"] == "bd-probe-thread"
    assert "test_a_background_thread_connect" in (rows[0]["nodeid"] or ""), rows[0]


def test_a_main_thread_connect_is_attributed_to_the_test(recorder):
    """The over-sensitivity control for the fallback: don't label everything ambient."""
    s = socket.socket()
    s.settimeout(0.01)
    try:
        s.connect(("192.0.2.10", 9))
    except OSError:
        pass
    finally:
        s.close()

    rows = [r for rs in recorder.summarize().values() for r in rs]
    assert rows[0]["attribution"] == "test", rows[0]


def test_a_loopback_connect_is_not_recorded(recorder, loopback_server):
    """The over-sensitivity control. Loopback is the suite's normal traffic."""
    urllib.request.urlopen(loopback_server, timeout=5).read()

    assert recorder.summarize() == {}
    assert recorder.observed > 0, (
        "nothing reached the hook, so the empty sink above proves nothing -- "
        "this is the blind-gate-reports-clean shape, not a pass")


def test_urllib_reaches_the_hook(recorder, loopback_server):
    """urllib -> http.client -> create_connection -> socket.connect.

    This is the @977 code path's shape, exercised against loopback so the proof
    costs no live traffic. It is what makes the recorder's denominator contain
    the defect it was built for.
    """
    before = recorder.observed
    urllib.request.urlopen(loopback_server, timeout=5).read()
    assert recorder.observed > before


def test_the_recorder_does_not_block(recorder, loopback_server):
    """Stage 1 records and gets out of the way -- return values pass through."""
    assert urllib.request.urlopen(loopback_server, timeout=5).read() == b'{"ok": true}'

    s = socket.socket()
    try:
        host, port = loopback_server.rstrip("/").rsplit("/", 1)[1].split(":")
        assert s.connect_ex((host, int(port))) == 0
    finally:
        s.close()


def test_arming_is_idempotent_and_restores_the_real_connect(tmp_path):
    real_dir = sr.sink_dir()
    was_armed = sr.is_armed()
    sr.disarm()
    pristine = socket.socket.connect

    assert sr.arm(tmp_path / "a") is True
    assert sr.arm(tmp_path / "b") is False, "a second arm must not stack a wrapper"
    assert socket.socket.connect is not pristine
    assert sr.disarm() is True
    assert socket.socket.connect is pristine
    assert sr.disarm() is False

    if was_armed:
        sr.arm(real_dir)


def test_the_blind_spots_are_declared():
    """A zero in the report must not be readable as "nothing called out".

    Section 0's third state. The recorder cannot see child processes or C-level
    sockets, and the numbers in those strings are the measured reason stage 2
    cannot simply trust an empty list.
    """
    assert sr.BLIND_SPOTS, "an undeclared blind spot is a gate reporting OK while blind"
    joined = " ".join(sr.BLIND_SPOTS)
    assert "child process" in joined
    assert "psycopg" in joined


def test_the_sink_needs_no_env_var():
    """No config surface at all -- not an unprefixed one, not a BD_ one.

    Either would join the inventory `test_gui_parity` grades (its scan has not
    been BD_-only since v3.66.713), and any env var is inherited by every
    subprocess the suite spawns. The run token travels via xdist `workerinput`
    instead, so there is nothing here to ledger.
    """
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    assert repo not in sr.sink_dir().resolve().parents
    assert _module_env_reads() == [], _module_env_reads()


def _module_env_reads():
    """Every environment name `_socket_record` reads, by AST -- not by grep.

    Section 1: grep is not a denominator. The claim is about os.environ reads in
    that module, so the predicate is the syntax node, and a mention inside this
    file's own prose cannot enter the count.
    """
    import ast
    import pathlib
    src = pathlib.Path(sr.__file__).read_text(encoding="utf-8")
    names = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            if node.value.attr == "environ" and isinstance(node.slice, ast.Constant):
                names.append(node.slice.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("get", "getenv") and node.args:
                target = node.func.value
                is_environ = (
                    (isinstance(target, ast.Attribute) and target.attr == "environ")
                    or (isinstance(target, ast.Name) and target.id == "os")
                )
                if is_environ and isinstance(node.args[0], ast.Constant):
                    names.append(node.args[0].value)
    return names


def test_the_dependency_pypi_thread_is_disabled_for_the_suite():
    """ITEM 46. cloakbrowser GETs its own PyPI JSON from a daemon thread on
    import, once per process -- so once per xdist worker, landing on whatever
    test is running. Measured at v3.66.1031 by this recorder: 5 attempts to
    151.101.*:443 in one full run, from thread `_check_wrapper_update`.

    Asserted here rather than trusted: a mutant flipping the value to "true"
    ESCAPED the battery, because nothing in the band imports cloakbrowser.
    Verified out-of-suite at v3.66.1034 with the recorder armed -- unset gave 1
    packet-sending connect to 151.101.0.223, "false" gave 0.
    """
    import os
    assert os.environ.get("CLOAKBROWSER_AUTO_UPDATE") == "false", (
        "CLOAKBROWSER_AUTO_UPDATE is %r -- the dependency's update thread is "
        "armed and the suite makes live PyPI calls, which is @977's class."
        % os.environ.get("CLOAKBROWSER_AUTO_UPDATE"))
