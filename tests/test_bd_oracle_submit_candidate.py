"""bd-oracle submit fix (harness-work/FIX/bd-oracle-submit): opt-in candidate test.

Run with BD_BD_ORACLE_SUBMIT_CANDIDATE=<path to a bd-oracle script>. The fake page models what was
measured on the BattleStation tabs: the oracle tab is hidden and unfocused, so an element.click()
from Runtime.evaluate is ignored and only a trusted Input.dispatchMouseEvent submits; the ChatGPT
composer's button is labelled "Send message".
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BD_ORACLE_SUBMIT_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

BUTTON = {"x": 10.0, "y": 20.0}


def _load(monkeypatch):
    stub = types.ModuleType("websocket")
    stub.create_connection = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "websocket", stub)
    path = str(Path(CANDIDATE))
    loader = importlib.machinery.SourceFileLoader("bd_oracle_candidate", path)
    spec = importlib.util.spec_from_loader("bd_oracle_candidate", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakePage:
    """Hidden, unfocused chat tab whose only send button is labelled 'Send message'."""

    def __init__(self):
        self.submitted = False
        self.sent = []

    def eval_js(self, ws, expr, req_id=1):
        has_button = 'aria-label="Send message"' in expr.replace('\\"', '"')
        if "getBoundingClientRect" in expr:
            return dict(BUTTON) if has_button else None
        if ".click()" in expr:
            return has_button  # untrusted click on an unfocused tab: found, but does not submit
        if "isTyping" in expr or "isGenerating" in expr:
            done = {"count": 1 if self.submitted else 0, "text": "READY" if self.submitted else ""}
            return dict(done, isTyping=False, isGenerating=False)
        if ".length" in expr and ("message-content" in expr or "data-message-author-role" in expr):
            return 1 if self.submitted else 0
        return False  # login probes, focus/input helpers

    def send(self, ws, method, params=None, req_id=1):
        params = params or {}
        self.sent.append((method, params))
        if (
            method == "Input.dispatchMouseEvent"
            and params.get("type") == "mouseReleased"
            and (params.get("x"), params.get("y")) == (BUTTON["x"], BUTTON["y"])
        ):
            self.submitted = True
        return {}


class _WS:
    def close(self):
        pass


def _wire(monkeypatch, mod):
    page = FakePage()
    monkeypatch.setattr(mod, "create_connection", lambda *a, **k: _WS())
    monkeypatch.setattr(mod, "cdp_eval_js", page.eval_js)
    monkeypatch.setattr(mod, "cdp_send", page.send)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return page


def test_gemini_prompt_submits_on_hidden_unfocused_tab(monkeypatch):
    mod = _load(monkeypatch)
    page = _wire(monkeypatch, mod)
    resp = mod.query_gemini({"webSocketDebuggerUrl": "ws://fake"}, "ping", timeout=1)
    assert resp == "READY", f"gemini prompt never submitted: {resp!r}"
    kinds = [p.get("type") for m, p in page.sent if m == "Input.dispatchMouseEvent"]
    assert kinds[-2:] == ["mousePressed", "mouseReleased"]
    assert ("Emulation.setFocusEmulationEnabled", {"enabled": True}) in page.sent


class LoggedInChatGPTPage(FakePage):
    """ChatGPT DOM measured after the operator logged in (FIX/bd-oracle-submit/DOM-DRIFT-r2.md): the send button is
    labelled just "Send" and the reply is only tagged on its markdown root, with no data-message-author-role."""

    def eval_js(self, ws, expr, req_id=1):
        expr = expr.replace('\\"', '"')
        has_button = 'aria-label="Send"' in expr
        if "getBoundingClientRect" in expr:
            return dict(BUTTON) if has_button else None
        if ".click()" in expr:
            return has_button
        reply_sel = 'data-markdown-text-style="assistant-message"' in expr
        if "isGenerating" in expr:
            done = self.submitted and reply_sel
            return {"isGenerating": False, "count": 1 if done else 0, "text": "READY" if done else ""}
        if ".length" in expr and reply_sel:
            return 1 if self.submitted else 0
        if ".length" in expr:
            return 0
        return False


def test_chatgpt_prompt_submits_via_send_message_button(monkeypatch):
    mod = _load(monkeypatch)
    page = _wire(monkeypatch, mod)
    resp = mod.query_chatgpt({"webSocketDebuggerUrl": "ws://fake"}, "ping", timeout=1)
    assert resp == "READY", f"chatgpt prompt never submitted: {resp!r}"
    assert page.submitted


def test_chatgpt_logged_in_dom_submits_and_reads_reply(monkeypatch):
    mod = _load(monkeypatch)
    page = LoggedInChatGPTPage()
    monkeypatch.setattr(mod, "create_connection", lambda *a, **k: _WS())
    monkeypatch.setattr(mod, "cdp_eval_js", page.eval_js)
    monkeypatch.setattr(mod, "cdp_send", page.send)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    resp = mod.query_chatgpt({"webSocketDebuggerUrl": "ws://fake"}, "ping", timeout=1)
    assert page.submitted, f"logged-in ChatGPT send button never clicked: {resp!r}"
    assert resp == "READY", f"logged-in ChatGPT reply not extracted: {resp!r}"
