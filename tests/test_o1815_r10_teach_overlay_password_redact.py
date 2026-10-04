"""O1815 R10 (P2-2): TEACH_OVERLAY_JS elementToRecord must redact password values.

The teach overlay carried a drifted copy of RECORDER_JS's element harvest
without the v3.43.49 password redaction: clicking a type=password input put
its typed value into `text`, which is rendered in the teach panel's click
log and mirrored into window.__pwrec_clicks (server-side harvest). The
overlay record must use the same "[pw]" sentinel and `secret` flag.

R1 (cx3 r1 REFUTE): both copies masked `text` on type=password only while
`secret` also covered password autocomplete, so a type=text input with
autocomplete=current-password/new-password -- including a password field
revealed by a show-password toggle -- leaked its value into the click log.

R2 (cx4 r2 REFUTE MED1): a password field WITHOUT password autocomplete lost
its secret identity once revealed (type password -> text). Both copies now
remember every element once seen as a password -- on a record, or from the
type attribute change itself (MutationObserver oldValue) -- and mask on it.

R4 (cx2 r3 REFUTE MED1): a site handler that reveals (type=text) and then
calls input.click() in the same task reaches the collectors before the
async observer callback ran. Both collectors now decide at READ time by
draining the observer's pending records (takeRecords) synchronously.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from bulk_downloader.learn_impl import _assets

BD_GATE_SCOPE = "module"

# Built at runtime so no literal fake secret sits in the source (gitleaks).
SECRET = "".join(("hunter2-", "O1815-R10", "-SECRET"))

_needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                 reason="node not installed")

_MK = """
function mk(type, ac) { return { tagName: 'INPUT', id: 'p', name: 'p', type,
  className: '', value: SECRET, innerText: '', textContent: '',
  getAttribute: (n) => (n === 'autocomplete' ? ac : null) }; }
"""

# Fake MutationObserver with browser timing: an attribute change queues a
# record at once (typeQueued); the callback only sees it when delivered
# (typeMutation = queue + deliver, i.e. the observer ran first);
# takeRecords() hands the pending records over synchronously.
_MO = """
let moCb = null; const mo = { target: null, opts: null, pending: [] };
const MutationObserver = class { constructor(cb) { moCb = cb; }
  observe(t, o) { mo.target = t; mo.opts = o; }
  takeRecords() { return mo.pending.splice(0); } };
const typeQueued = (el, oldValue) => { mo.pending.push({ type: 'attributes',
  attributeName: 'type', target: el, oldValue }); };
const typeMutation = (el, oldValue) => { typeQueued(el, oldValue);
  const recs = mo.pending.splice(0); if (moCb && recs.length) moCb(recs); };
"""


def _block(src: str, start: str) -> str:
    """Return src from `start` through the brace that closes its first '{'."""
    assert src.count(start) == 1, f"expected exactly one {start!r}"
    i = src.index(start)
    depth = 0
    for j in range(src.index("{", i), len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"unbalanced block {start!r}")


def _observer_src(src: str) -> str:
    """The type-attribute observer install block (R4 `_pwSync`; the R2
    bare observer when an older copy is under test, so a RED run shows
    the leak instead of an extraction error), or '' when there is none."""
    start = "const _pwSync = (() => {"
    assert src.count(start) <= 1
    if start in src:
        return _block(src, start) + ")();"
    start = "try {\n    const _pwSeen"
    assert src.count(start) <= 1
    if start not in src:
        return ""
    return _block(src, start) + " catch (e) { throw e; }"


def _element_to_record_src() -> str:
    return _block(_assets.TEACH_OVERLAY_JS, "function elementToRecord(el)")


def _node(js: str) -> dict:
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True,
                                     text=True, check=True).stdout)


def test_element_to_record_source_redacts_password():
    body = _element_to_record_src()
    assert "el.type === 'password'" in body, (
        "O1815-R10: TEACH_OVERLAY_JS elementToRecord has no type=password "
        "check; password values leak into click-record text")
    assert "'[pw]'" in body
    assert "secret:" in body


@_needs_node
def test_element_to_record_runtime_redacts_password():
    js = f"""
const SECRET = {json.dumps(SECRET)};
const window = {{}};
const document = {{}};
{_MK}
{_MO}
{_element_to_record_src()}
{_observer_src(_assets.TEACH_OVERLAY_JS)}
console.log(JSON.stringify({{
  pw: elementToRecord(mk('password', null)),
  ac: elementToRecord(mk('text', 'current-password')),
  nw: elementToRecord(mk('text', 'new-password')),
  tx: elementToRecord(mk('text', null)),
}}));
"""
    out = _node(js)
    assert SECRET not in json.dumps(out["pw"]), (
        "O1815-R10: password value leaked into teach overlay record")
    assert out["pw"]["text"] == "[pw]"
    assert out["pw"]["secret"] is True
    for k in ("ac", "nw"):
        assert SECRET not in json.dumps(out[k]), (
            f"O1815-R10-R1: password-autocomplete value leaked into teach "
            f"overlay record text ({out[k]['autocomplete']})")
        assert out[k]["text"] == "[pw]"
        assert out[k]["secret"] is True
    # Non-password inputs keep their visible text (selector synthesis).
    assert out["tx"]["text"] == SECRET
    assert out["tx"]["secret"] is False


def _overlay_click_js(ac, types, *, reveal_unseen=False, reveal_sync=False) -> str:
    """Drive the real overlay click handler + renderEvents over one field
    whose type is switched through `types` (a show-password toggle), then
    click a never-password text field. reveal_unseen: the field is revealed
    (password -> text) before any click, via the overlay's type observer.
    reveal_sync: revealed in the same task as the click -- the record is
    queued but the observer callback has NOT run (site handler sets
    type=text, then input.click())."""
    src = _assets.TEACH_OVERLAY_JS
    handler = _block(src, "document.addEventListener('click', (e) => {")
    handler = handler[handler.index("(e) => {"):]
    reveal = """
field.type = 'text';
typeMutation(field, 'password');
typeMutation(plain, 'text');
""" if reveal_unseen else ""
    if reveal_sync:
        reveal = """
field.type = 'text';
typeQueued(field, 'password');
"""
    return f"""
const SECRET = {json.dumps(SECRET)};
const log = {{ _html: '', kids: [],
  set innerHTML(v) {{ this._html = v; this.kids = []; }},
  get innerHTML() {{ return this._html + this.kids.map((k) => k.innerHTML).join(''); }},
  appendChild(k) {{ this.kids.push(k); }}, querySelectorAll() {{ return []; }} }};
const count = {{ textContent: '' }};
const panel = {{ contains: () => false,
  querySelector: (s) => (s === '#pw_teach_log' ? log : count) }};
const document = {{ createElement: () => ({{ style: {{}}, innerHTML: '',
  addEventListener() {{}} }}) }};
const window = {{ location: {{ href: 'https://example.invalid/' }}, __pwrec_clicks: [] }};
const state = {{ events: [], teach_only: true, chosen_selectors: {{}}, pinned_set: new Set() }};
const synth = () => ['input#p'];
const flash = () => {{}};
{_MK}
{_MO}
{_element_to_record_src()}
{_observer_src(src)}
{_block(src, "function renderEvents()")}
const onClick = {handler};
const click = (el) => onClick({{ target: el, shiftKey: false, preventDefault() {{}},
  stopPropagation() {{}}, stopImmediatePropagation() {{}} }});
const field = mk('password', {json.dumps(ac)});
const plain = mk('text', null); plain.id = 'q'; plain.name = 'q';
{reveal}
const seen = [];
for (const t of {json.dumps(types)}) {{
  field.type = t;
  click(field);
  const rec = state.events[state.events.length - 1];
  seen.push({{ type: t, text: rec.text, secret: rec.secret,
    recLeak: JSON.stringify(rec).includes(SECRET),
    bufLeak: JSON.stringify(window.__pwrec_clicks).includes(SECRET),
    logLeak: log.innerHTML.includes(SECRET) }});
}}
click(plain);
const prec = state.events[state.events.length - 1];
console.log(JSON.stringify({{ seen, mo: {{ document: mo.target === document, opts: mo.opts,
  pending: mo.pending.length }},
  plain: {{ text: prec.text, secret: prec.secret, logShows: log.innerHTML.includes(SECRET) }} }}));
"""


def _assert_overlay_never_shows(seen, tag, ac):
    for s in seen:
        leaks = {k: s[k] for k in ("recLeak", "bufLeak", "logLeak") if s[k]}
        assert not leaks, (
            f"{tag}: revealed password (type={s['type']}, "
            f"autocomplete={ac}) leaked into overlay {sorted(leaks)}")
        assert s["text"] == "[pw]"
        assert s["secret"] is True


def _assert_plain_visible(plain, where):
    # NEGATIVE CONTROL: a field that was never a password keeps its value.
    assert plain["text"] == SECRET and plain["secret"] is False, (
        f"O1815-R10-R2: never-password text field over-redacted in {where}")


@_needs_node
@pytest.mark.parametrize("ac", ["current-password", "new-password"])
def test_overlay_reveal_toggle_never_shows_password(ac):
    out = _node(_overlay_click_js(ac, ["password", "text", "password"]))
    assert [s["type"] for s in out["seen"]] == ["password", "text", "password"]
    _assert_overlay_never_shows(out["seen"], "O1815-R10-R1", ac)


@_needs_node
def test_overlay_reveal_without_autocomplete_keeps_secret_identity():
    types = ["password", "text", "password", "text"]
    out = _node(_overlay_click_js(None, types))
    assert [s["type"] for s in out["seen"]] == types
    _assert_overlay_never_shows(out["seen"], "O1815-R10-R2", None)
    _assert_plain_visible(out["plain"], "overlay record")
    assert out["plain"]["logShows"], "probe blind: plain value not in log"


@_needs_node
def test_overlay_reveal_before_first_touch_keeps_secret_identity():
    types = ["text", "password", "text"]
    out = _node(_overlay_click_js(None, types, reveal_unseen=True))
    assert [s["type"] for s in out["seen"]] == types
    _assert_overlay_never_shows(out["seen"], "O1815-R10-R2 unseen", None)
    _assert_plain_visible(out["plain"], "overlay record")
    assert out["mo"]["document"] is True
    assert out["mo"]["opts"] == {"subtree": True, "attributes": True,
                                 "attributeFilter": ["type"],
                                 "attributeOldValue": True}


@_needs_node
def test_overlay_reveal_then_immediate_click_is_redacted():
    # R4: no autocomplete, no earlier touch, observer callback not yet run.
    out = _node(_overlay_click_js(None, ["text"], reveal_sync=True))
    assert [s["type"] for s in out["seen"]] == ["text"]
    _assert_overlay_never_shows(out["seen"], "O1815-R10-R4 reveal-then-click",
                                None)
    _assert_plain_visible(out["plain"], "overlay record")
    assert out["plain"]["logShows"], "probe blind: plain value not in log"


@_needs_node
def test_overlay_click_probe_positive_control():
    # The same probe sees an ordinary text field's value in record, buffer
    # and visible log -- a zero above is not a blind probe.
    seen = _node(_overlay_click_js(None, ["text"]))["seen"]
    assert seen[0]["recLeak"] and seen[0]["bufLeak"] and seen[0]["logLeak"]
    assert seen[0]["secret"] is False


def _recorder_js(body: str) -> str:
    src = _assets.RECORDER_JS
    info = _block(src, "const _info = (el) =>")
    handler = _block(src, "document.addEventListener('click', (e) => {")
    handler = handler[handler.index("(e) => {"):]
    return f"""
const SECRET = {json.dumps(SECRET)};
const document = {{}};
const window = {{ location: {{ href: 'https://example.invalid/' }}, __pwrec_clicks: [] }};
const _attr = (el, n) => {{ try {{ return el.getAttribute(n) || ''; }} catch (e) {{ return ''; }} }};
const _findUrlAncestor = () => null;
const _pwrecPersist = () => {{}};
{_MK}
{_MO}
{info};
{_observer_src(src)}
const onClick = {handler};
const clickRec = (el) => {{ onClick({{ target: el }});
  return window.__pwrec_clicks[window.__pwrec_clicks.length - 1]; }};
{body}
"""


@_needs_node
def test_recorder_info_redacts_password_autocomplete():
    out = _node(_recorder_js("""
const field = mk('password', 'current-password');
const toggle = ['password', 'text', 'password'].map((t) => { field.type = t; return _info(field); });
console.log(JSON.stringify({
  pw: _info(mk('password', null)),
  ac: _info(mk('text', 'current-password')),
  nw: _info(mk('text', 'new-password')),
  tx: _info(mk('text', null)),
  toggle,
}));
"""))
    recs = [out["pw"], out["ac"], out["nw"], *out["toggle"]]
    for r in recs:
        assert r["text"] == "[pw]", (
            f"O1815-R10-R1: RECORDER_JS _info leaked a secret field value "
            f"into text (type={r['type']}, autocomplete={r['autocomplete']})")
        assert r["secret"] is True
        # The raw classifier channel is intentional and kept (classify_login).
        assert r["_input_value"] == SECRET
    assert out["tx"]["text"] == SECRET
    assert out["tx"]["secret"] is False


@_needs_node
@pytest.mark.parametrize("reveal_unseen", [False, True])
def test_recorder_reveal_without_autocomplete_keeps_secret_identity(reveal_unseen):
    types = ["text", "password", "text"] if reveal_unseen else [
        "password", "text", "password", "text"]
    reveal = ("field.type = 'text'; typeMutation(field, 'password'); "
              "typeMutation(plain, 'text');") if reveal_unseen else ""
    out = _node(_recorder_js(f"""
const field = mk('password', null);
const plain = mk('text', null); plain.id = 'q';
{reveal}
const toggle = {json.dumps(types)}.map((t) => {{ field.type = t; return clickRec(field); }});
// text only: _input_value is the intentional raw classifier channel.
const bufLeak = window.__pwrec_clicks.some((r) => (r.text || '').includes(SECRET));
const plainRec = clickRec(plain);
const bufShows = window.__pwrec_clicks.some((r) => (r.text || '').includes(SECRET));
console.log(JSON.stringify({{ toggle, bufLeak, bufShows, plain: plainRec }}));
"""))
    assert [r["type"] for r in out["toggle"]] == types
    tag = "O1815-R10-R2" + (" unseen" if reveal_unseen else "")
    for r in out["toggle"]:
        assert r["text"] == "[pw]" and r["secret"] is True, (
            f"{tag}: RECORDER_JS click record leaked a revealed password "
            f"(type={r['type']}, no autocomplete) into text")
        assert r["_input_value"] == SECRET
    assert not out["bufLeak"], f"{tag}: __pwrec_clicks text holds the password"
    _assert_plain_visible(out["plain"], "RECORDER_JS click record")
    assert out["bufShows"], "probe blind: plain value not in __pwrec_clicks"


@_needs_node
def test_recorder_reveal_then_immediate_click_is_redacted():
    # R4: site handler sets type=text then input.click() -- the type record
    # is queued, the observer callback has not run, nothing touched it before.
    out = _node(_recorder_js("""
const field = mk('password', null);
const plain = mk('text', null); plain.id = 'q';
field.type = 'text'; typeQueued(field, 'password');
const rec = clickRec(field);
const bufLeak = window.__pwrec_clicks.some((r) => (r.text || '').includes(SECRET));
const plainRec = clickRec(plain);
const bufShows = window.__pwrec_clicks.some((r) => (r.text || '').includes(SECRET));
console.log(JSON.stringify({ rec, bufLeak, bufShows, plain: plainRec }));
"""))
    rec = out["rec"]
    assert rec["type"] == "text"
    assert rec["text"] == "[pw]" and rec["secret"] is True, (
        "O1815-R10-R4 reveal-then-click: RECORDER_JS click record leaked a "
        "password revealed in the same task as the click (observer not run)")
    assert rec["_input_value"] == SECRET
    assert not out["bufLeak"], (
        "O1815-R10-R4 reveal-then-click: __pwrec_clicks text holds the password")
    _assert_plain_visible(out["plain"], "RECORDER_JS click record")
    assert out["bufShows"], "probe blind: plain value not in __pwrec_clicks"
