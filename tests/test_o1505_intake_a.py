"""O1505 intake (a): operator->PM prompts are spooled by the UserPromptSubmit hook and classified in SHADOW.

BD_O1505_INTAKE_A_CANDIDATE = absolute path of the candidate DIR holding bd-touch-typed.sh and bd-operator-intake-shadow.py
(opt-in; no live fallback). Hermetic: the hook runs from a tmp copy with every /home/mboyle/bd-persist path rewritten into
tmp_path; LiteLLM is an in-test stub on 127.0.0.1. Live smoke (8 operator-style texts, real intake alias): SAMPLE-live-8.jsonl.
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

CANDIDATE = os.environ.get("BD_O1505_INTAKE_A_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
LONG = "ORDER: bd-persist/ORDER-OPTIMIZE-2080TI-CDP.md -- and please also hold the P3 lens queue until the relay is back up"


class Stub:
    def __init__(self, status=200, word="Order."):
        self.seen = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                stub.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                b = json.dumps({"choices": [{"message": {"content": word}}]}).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1/chat/completions"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def sb(tmp_path):
    d = Path(CANDIDATE)
    hook, tool = d / "bd-touch-typed.sh", d / "bd-operator-intake-shadow.py"
    assert hook.is_file() and tool.is_file(), f"candidate dir incomplete: {d}"
    p = tmp_path / "persist"; p.mkdir()
    src = hook.read_text().replace("/home/mboyle/bd-persist", str(p))
    assert "/home/mboyle" not in src, "sandbox rewrite left a host path"
    (tmp_path / "hook.sh").write_text(src)
    return tmp_path


def hook(sb, prompt, seat="bd-pm-Z9", **env):
    e = {"PATH": os.environ["PATH"], "HOME": str(sb), "BD_SEAT": seat, **env}
    r = subprocess.run(["bash", str(sb / "hook.sh")], input=json.dumps({"prompt": prompt}), env=e,
                       capture_output=True, text=True, timeout=30, check=False)
    return r


def spooled(sb):
    return sorted((sb / "persist" / "intake" / "operator-spool").glob("*.json"))


def test_operator_prompt_is_spooled_whole_and_presence_still_recorded(sb):
    r = hook(sb, LONG)
    assert r.returncode == 0
    assert (sb / "persist" / "OPERATOR-LAST-TYPED").exists(), "O1505: the hook's original job (presence) broke"
    files = spooled(sb)
    assert len(files) == 1, f"O1505: operator prompt not spooled: {files}"
    rec = json.loads(files[0].read_text())
    assert rec["prompt"] == LONG and rec["session"] == "bd-pm-Z9", f"O1505: spool lost the prompt (20-char cut?): {rec}"


@pytest.mark.parametrize("prompt,seat", [("[from bd-worker-B1-B] P2 PATCH /x/DONE.md", "bd-pm-Z9"),
                                         ("PM SELF-DRIVING tick", "bd-pm-Z9"), (LONG, "bd-worker-Z9")])
def test_relays_self_drive_and_non_pm_sessions_are_never_spooled(sb, prompt, seat):
    assert hook(sb, prompt, seat=seat).returncode == 0
    assert not spooled(sb), f"O1505: spooled a non-operator prompt: {prompt!r} seat={seat}"


def test_unwritable_spool_never_blocks_the_prompt(sb):
    blocker = sb / "persist" / "intake"; blocker.write_text("a file where the dir should be")
    r = hook(sb, LONG)
    assert r.returncode == 0 and r.stdout == "", f"O1505: spool failure leaked into the prompt path: rc={r.returncode} {r.stdout!r}"
    assert (sb / "persist" / "OPERATOR-LAST-TYPED").exists()


def tool(sb, *args, url="http://127.0.0.1:9/v1/chat/completions", **env):
    e = {"PATH": os.environ["PATH"], "HOME": str(sb), "BD_INTAKE_DIR": str(sb / "persist" / "intake"), "LITELLM_URL": url,
         "LITELLM_MASTER_KEY": "k", "BD_INTAKE_TIMEOUT": "10", **env}
    r = subprocess.run([sys.executable, str(Path(CANDIDATE) / "bd-operator-intake-shadow.py"), *args], env=e,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr[-400:]
    return r


def log(sb):
    p = sb / "persist" / "intake" / "operator-shadow.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_classifies_on_intake_alias_with_regex_path_and_consumes_the_spool(sb):
    hook(sb, LONG)
    st = Stub(word="Order.")
    try:
        tool(sb, "run", url=st.url)
    finally:
        st.close()
    assert st.seen, "stub saw no request"
    assert st.seen[0]["model"] == "intake" and "num_ctx" not in json.dumps(st.seen[0]), f"O1505: {st.seen[0]}"
    rows = log(sb)
    assert len(rows) == 1 and rows[0]["class"] == "order" and rows[0]["how"] == "intake"
    assert rows[0]["path"] == "bd-persist/ORDER-OPTIMIZE-2080TI-CDP.md", f"O1505: path not extracted: {rows[0]}"
    assert "prompt" not in rows[0], "the shadow log records the class, not the operator's text"
    assert not spooled(sb), "classified prompt left in the spool"


@pytest.mark.parametrize("word,want", [("banana", "unknown"), ("order question", "unknown"),
                                       ("status or question", "unknown"), ("Order.", "order"), (" QUESTION\n", "question")])
def test_only_exactly_one_enum_word_is_a_class(sb, word, want):
    # G2 F1 (REFUTE cx-worker-1): "order question" was recorded as order and counted as a judged classification.
    hook(sb, LONG)
    st = Stub(word=word)
    try:
        tool(sb, "run", url=st.url)
    finally:
        st.close()
    assert log(sb)[0]["class"] == want, f"O1505: model answer {word!r} -> {log(sb)[0]['class']!r}, want {want!r} (AMBIGUOUS_MODEL_ACCEPTED)"


def test_model_down_keeps_the_prompt_then_records_unclassified(sb):
    hook(sb, LONG)
    st = Stub(status=500)
    try:
        for _ in range(2):
            tool(sb, "run", url=st.url)
            assert spooled(sb) and not log(sb), "O1505: a failed classification dropped or logged the prompt early"
        tool(sb, "run", url=st.url)
    finally:
        st.close()
    rows = log(sb)
    assert len(rows) == 1 and rows[0]["class"] == "unclassified" and rows[0]["how"] == "fallback"
    assert not spooled(sb)


def test_dry_run_and_measure_write_nothing(sb):
    hook(sb, LONG)
    tree = lambda: sorted(str(p.relative_to(sb)) for p in (sb / "persist").rglob("*"))
    before = tree()
    st = Stub()
    try:
        r = tool(sb, "run", url=st.url, DRY_RUN="1")
        after = tree()
    finally:
        st.close()
    assert '"class": "order"' in r.stdout and not log(sb) and len(spooled(sb)) == 1
    assert after == before, f"O1505: DRY_RUN changed the tree (F2 DRY_RUN_WROTE_PATHS): {sorted(set(after) ^ set(before))}"
    m = json.loads(tool(sb, "measure").stdout)
    assert m["records"] == 0
