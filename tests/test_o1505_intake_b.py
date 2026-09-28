"""O1505 intake (b): bd-pm-batch-summary -- a <=5-line Hermes summary of every PM board batch, escalations carried verbatim.

BD_O1505_INTAKE_B_CANDIDATE = absolute path of the candidate bd-pm-batch-summary.py (opt-in; no live fallback). Hermetic:
LiteLLM is an in-test stub on 127.0.0.1; board/pm.md is a tmp_path fixture in bd-relay.sh's batch format.
Live measurement (copy of 2026-09-28 board/pm.md, 97 batches): 51624 -> 30847 chars, 31/31 escalations carried.
"""
BD_GATE_SCOPE = "module"
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

CANDIDATE = os.environ.get("BD_O1505_INTAKE_B_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
MODEL_OUT = "1. a\n2. b\n3. c\n4. d\n5. e\n6. f\n7. g"  # 7 lines: the tool must keep 5


class Stub:
    def __init__(self, status=200, content=MODEL_OUT):
        self.seen = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                stub.seen.append({"headers": dict(self.headers),
                                  "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                b = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1/chat/completions"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def batch(ts, msgs):
    out = [f"# BOARD: pm (coalesced at {ts}, count: {len(msgs)})", ""]
    for i, m in enumerate(msgs):
        out += [f"--- Message: 2026{i:04d}.md ---", m, ""]
    return "\n".join(out + ["--- End of Batch ---", "", ""])


BIG = [f"[from bd-worker-{i}] routine note {i} /home/x/N{i}.md" for i in range(8)] + [
    "[from bd-cx-worker-2] WAKE HIGH /home/x/VERDICT.md", "[from bd-worker-A4-A] ESC r22 row722 wt gone: /home/x/ESC.md"]


@pytest.fixture
def board(tmp_path):
    assert Path(CANDIDATE).is_file(), f"candidate supplied but absent: {CANDIDATE}"
    p = tmp_path / "pm.md"
    p.write_text(batch("2026-09-28T20:00:01Z", BIG) + batch("2026-09-28T20:06:01Z", ["[from bd-x] idle /home/x/I.md"]))
    return p


def run(board, *args, url="http://127.0.0.1:9/v1/chat/completions", **env):
    e = {"PATH": os.environ["PATH"], "HOME": str(board.parent), "BD_PM_BOARD": str(board), "LITELLM_URL": url,
         "LITELLM_MASTER_KEY": "k-test", "BD_PM_SUMMARY_TIMEOUT": "10", **env}
    r = subprocess.run([sys.executable, CANDIDATE, *args], env=e, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, f"rc={r.returncode} {r.stderr[-400:]}"
    return r


def summary(board):
    return (board.parent / "pm-summary.md").read_text()


def section(text, ts):
    return text.split(f"## {ts}")[1].split("\n## ")[0]


def test_big_batch_is_summarised_to_five_lines_on_the_intake_alias(board):
    st = Stub()
    try:
        r = run(board, "run", url=st.url)
    finally:
        st.close()
    assert st.seen, "stub saw no request"
    assert len(st.seen) == 1, f"O1505: {len(st.seen)} model calls; the 1-message batch must be copied, not summarised"
    body = st.seen[0]["body"]
    assert body["model"] == "intake" and "num_ctx" not in json.dumps(body), f"O1505: wrong alias or num_ctx sent: {body}"
    assert st.seen[0]["headers"].get("Authorization") == "Bearer k-test"
    sec = section(summary(board), "2026-09-28T20:00:01Z")
    lines = sec.split("  ESCALATIONS (verbatim):")[0].strip().splitlines()[1:]
    assert len(lines) == 5, f"O1505: summary has {len(lines)} lines, max 5: {lines}"
    assert json.loads(r.stdout)["missed_escalations"] == 0


def test_escalations_are_carried_verbatim_even_when_the_model_drops_them(board):
    st = Stub(content="all fine")  # the model mentions no escalation
    try:
        run(board, "run", url=st.url)
    finally:
        st.close()
    sec = section(summary(board), "2026-09-28T20:00:01Z")
    for esc in BIG[-2:]:
        assert esc in sec, f"O1505: escalation lost from the PM summary: {esc!r}"
    assert "routine note 3" not in sec, "routine lines must not be carried verbatim"


def test_short_batch_is_copied_without_a_model_call(board):
    st = Stub()
    try:
        run(board, "run", url=st.url)
    finally:
        st.close()
    sec = section(summary(board), "2026-09-28T20:06:01Z")
    assert "verbatim" in sec.splitlines()[0] and "[from bd-x] idle /home/x/I.md" in sec


def test_model_down_still_records_the_batch_and_its_escalations(board):
    st = Stub(status=500)
    try:
        r = run(board, "run", url=st.url)
    finally:
        st.close()
    sec = section(summary(board), "2026-09-28T20:00:01Z")
    assert "MODEL-UNAVAILABLE" in sec and all(e in sec for e in BIG[-2:]), f"O1505: outage lost the batch: {sec}"
    assert json.loads(r.stdout)["missed_escalations"] == 0


def test_each_batch_is_summarised_once_and_new_batches_are_picked_up(board):
    st = Stub()
    try:
        run(board, "run", url=st.url)
        again = json.loads(run(board, "run", url=st.url).stdout)
        with open(board, "a") as f:
            f.write(batch("2026-09-28T20:12:01Z", BIG))
        later = json.loads(run(board, "run", url=st.url).stdout)
    finally:
        st.close()
    assert again["summarised"] == 0, f"O1505: batches re-summarised on a second run: {again}"
    assert later["summarised"] == 1 and summary(board).count("## 2026-09-28T20:12:01Z") == 1
    assert len(st.seen) == 2


def test_input_is_capped(board):
    board.write_text(batch("2026-09-28T21:00:01Z", [f"line {i:05d} " + "y" * 90 for i in range(3000)]))
    st = Stub()
    try:
        run(board, "run", url=st.url)
    finally:
        st.close()
    sent = st.seen[0]["body"]["messages"][0]["content"]
    assert len(sent) <= 8000 * 3 + 400, f"O1505: {len(sent)} chars sent, cap 24000 + prompt"
    assert "line 02999" in sent, "the cap must keep the newest messages"


def test_dry_run_and_measure_write_nothing(board):
    st = Stub()
    try:
        r = run(board, "run", url=st.url, DRY_RUN="1")
        m = json.loads(run(board, "measure", url=st.url).stdout)
    finally:
        st.close()
    assert "## 2026-09-28T20:00:01Z" in r.stdout
    assert not (board.parent / "pm-summary.md").exists() and not (board.parent / ".pm-summary.md.state").exists()
    assert m["esc_in"] == 2 and m["missed_escalations"] == 0 and m["summarised"] == 1


# ---- G2 (REFUTE bd-cx-worker-1, .review/VERDICT-correctness-bd-cx-worker-1.md) ----

def test_batch_still_being_appended_stays_pending_and_its_late_escalation_is_kept(board):
    # F1: bd-relay writes header, frames and terminator in separate shell writes. A run that lands mid-append must not
    # mark the batch done; the escalation that arrives afterwards must reach the summary.
    board.write_text("# BOARD: pm (coalesced at 2026-09-28T22:00:01Z, count: 2)\n\n--- Message: a.md ---\n[from bd-x] routine\n\n")
    first = json.loads(run(board, "run").stdout)
    with open(board, "a") as f:
        f.write("--- Message: b.md ---\n[from bd-y] HIGH database outage /evidence.md\n\n--- End of Batch ---\n\n")
    second = json.loads(run(board, "run").stdout)
    assert first["summarised"] == 0, f"O1505: an unterminated batch was summarised and marked done (F1): {first}"
    assert second["summarised"] == 1, f"O1505: completed batch never summarised (F1): {second}"
    assert "[from bd-y] HIGH database outage /evidence.md" in summary(board), "O1505: LATE_ESCALATION_LOST (F1)"


def test_frame_count_short_of_header_count_is_not_complete(board):
    board.write_text(batch("2026-09-28T22:10:01Z", ["[from bd-x] one"]).replace("count: 1", "count: 2"))
    assert json.loads(run(board, "run").stdout)["summarised"] == 0, "O1505: batch with 1 of 2 frames treated as complete"


def test_no_replay_after_the_board_outgrows_any_fixed_window(board):
    # F2: the state kept only the newest 2000 ids, so a 2001-batch board replayed its oldest batch on every run.
    board.write_text("".join(batch(f"2026-09-{1 + i // 1440:02d}T{(i // 60) % 24:02d}:{i % 60:02d}:01Z",
                                   [f"[from bd-x] n{i}"]) for i in range(2001)))
    assert json.loads(run(board, "run", "--max", "2001").stdout)["summarised"] == 2001
    again = json.loads(run(board, "run").stdout)
    assert again["summarised"] == 0, f"O1505: OLD_BATCH_REPLAY after 2001 batches (F2): {again}"
    assert summary(board).count("## 2026-09-01T00:00:01Z") == 1


def test_all_frames_written_but_no_terminator_yet_stays_pending(board):
    # F1, terminator half: frames == count while the last payload is still being written -- only End of Batch closes it.
    board.write_text("# BOARD: pm (coalesced at 2026-09-28T22:20:01Z, count: 1)\n\n--- Message: a.md ---\nOUTPUT-TRUNCATED: x\n")
    first = json.loads(run(board, "run").stdout)
    with open(board, "a") as f:
        f.write("[from bd-z] BLOCKED no brief /b.md\n\n--- End of Batch ---\n\n")
    run(board, "run")
    assert first["summarised"] == 0, f"O1505: batch without its terminator was summarised (F1): {first}"
    assert "[from bd-z] BLOCKED no brief /b.md" in summary(board), "O1505: LATE_ESCALATION_LOST (terminator)"
