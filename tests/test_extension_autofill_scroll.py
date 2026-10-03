import json
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_RUNNER = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const [source, scrollX, scrollY] = process.argv.slice(1);
const timers = [];
const calls = [];
const mounted = [];
let rectCalls = 0;
function element(tagName) {
  return {
    tagName, style: {}, children: [], attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    appendChild(child) { this.children.push(child); },
    remove() {},
  };
}
const username = {
  value: 'unchanged-user', offsetParent: {},
  getBoundingClientRect() { rectCalls++; return {left: 25.2, bottom: 100.2}; },
};
const password = {
  value: 'unchanged-password', offsetParent: {},
  compareDocumentPosition(other) { return other === username ? 2 : 0; },
};
const context = {
  window: {scrollX: Number(scrollX), scrollY: Number(scrollY)},
  Node: {DOCUMENT_POSITION_PRECEDING: 2},
  location: {href: 'https://example.invalid/login'},
  setTimeout(callback) { timers.push(callback); },
  document: {
    querySelector() { return password; },
    querySelectorAll() { return [username]; },
    getElementById() { return null; },
    createElement: element,
    documentElement: {appendChild(menu) { mounted.push(menu); }},
    addEventListener() {}, removeEventListener() {},
  },
  chrome: {runtime: {sendMessage(request, reply) {
    calls.push(request);
    reply({ok: true, entries: [{id: 'test-entry', name: 'Example', username: 'alice'}]});
  }}},
};
(async () => {
  vm.runInNewContext(fs.readFileSync(source, 'utf8'), context, {filename: source});
  await timers.shift()();
  console.log(JSON.stringify({
    mounted, calls, rectCalls,
    username: username.value, password: password.value,
  }));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize("scroll_x,scroll_y", [(0, 0), (0, 500), (240, 0), (240, 500)])
def test_fixed_suggestion_uses_viewport_coordinates(scroll_x, scroll_y):
    source = Path(__file__).resolve().parents[1] / "extension" / "autofill.js"
    result = subprocess.run(
        ["node", "-e", _RUNNER, str(source), str(scroll_x), str(scroll_y)],
        capture_output=True, text=True, check=True, timeout=10,
    )
    rendered = json.loads(result.stdout)
    assert rendered["rectCalls"] == 1
    assert rendered["calls"] == [{
        "action": "vault_list_for_origin",
        "payload": {"origin": "https://example.invalid/login"},
    }]
    assert len(rendered["mounted"]) == 1
    menu = rendered["mounted"][0]
    assert menu["id"] == "__bd_autofill_menu"
    assert len(menu["children"]) == 1
    assert [child["textContent"] for child in menu["children"][0]["children"]] == [
        "Example", "alice",
    ]
    assert rendered["username"] == "unchanged-user"
    assert rendered["password"] == "unchanged-password"
    style = dict(item.split(":", 1) for item in menu["attributes"]["style"].split(";"))
    assert style["position"] == "fixed"
    assert style["left"] == "25px", f"fixed menu left includes scrollX={scroll_x}: {style['left']}"
    assert style["top"] == "104px", f"fixed menu top includes scrollY={scroll_y}: {style['top']}"
