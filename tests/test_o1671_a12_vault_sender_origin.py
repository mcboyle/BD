"""O1671 a12 (AUDIT-12 HIGH security): the extension's message router let the
CALLER name the origin a vault entry is fetched for.

extension/background.js routed ``vault_fetch_one`` / ``vault_list_for_origin``
with ``payload.origin || sender.url``, so a message from a tab on
evil.example that claimed ``origin: "https://bank.example/"`` got the
bank.example password back. ``vault_set_silent_for_origin`` took
``payload.origin`` from any sender.

The origin must come from the sender: a tab (content script) speaks only for
the page it runs in (``sender.url``); only the extension's own pages
(popup/options: ``sender.id == chrome.runtime.id`` and a
``chrome-extension://<id>/`` URL) may name another origin.

The behavioural tests load the REAL background.js under node with a stubbed
``chrome.*`` and ``fetch`` (the stub vault returns a password only to a
request whose origin is bank.example) and drive the real onMessage listener.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"  # subject: extension/background.js message router only

ROOT =Path(__file__).resolve().parent.parent
BACKGROUND = ROOT / "extension" / "background.js"
NODE = shutil.which("node")

EXT_ID = "abcdefghijklmnopabcdefghijklmnop"
BANK = "https://bank.example/login"
EVIL = "https://evil.example/phish"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [src, scenarioJson, extId] = process.argv.slice(-3);
const scenario = JSON.parse(scenarioJson);

const local = { bd_vault_token: "tok", bd_vault_idle_lock_minutes: 0,
                bd_vault_silent_origins: scenario.silent || {} };
let listener = null;
const noop = () => {};
const evt = () => ({ addListener: noop, removeListener: noop });
const chrome = {
  runtime: {
    id: extId,
    getURL: (p) => `chrome-extension://${extId}/` + (p || ""),
    onInstalled: evt(), onStartup: evt(), lastError: undefined,
    onMessage: { addListener: (fn) => { listener = fn; } },
    sendMessage: noop,
  },
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const k of [].concat(keys || Object.keys(local))) {
          if (k in local) out[k] = local[k];
        }
        return out;
      },
      set: async (obj) => { Object.assign(local, obj); },
    },
    sync: { get: async () => ({ bd_url: "http://bd.test:5555" }), set: async () => {} },
    onChanged: evt(),
  },
  tabs: { onUpdated: evt(), onActivated: evt(), get: async () => ({}), query: async () => [] },
  contextMenus: { create: noop, remove: noop, removeAll: noop, onClicked: evt() },
  action: { onClicked: evt(), setBadgeText: noop, setBadgeBackgroundColor: noop, setTitle: noop },
  commands: { onCommand: evt() },
  notifications: { create: noop },
  scripting: { executeScript: async () => [] },
};

const calls = [];
// Stub vault server: hands the bank.example password only to a request
// whose origin is bank.example -- exactly what the real server checks.
async function fetchStub(url, init) {
  url = String(url);
  let origin = "";
  if (url.includes("/fetch_one")) origin = JSON.parse((init && init.body) || "{}").origin || "";
  else origin = decodeURIComponent((url.split("origin=")[1] || "").split("&")[0]);
  calls.push({ url, origin });
  const forBank = /^https?:\/\/([^/]*\.)?bank\.example(\/|$)/.test(origin);
  const body = url.includes("/fetch_one")
    ? (forBank ? { ok: true, username: "u", password: "BANK-SECRET" }
               : { ok: false, error: "origin mismatch" })
    : { ok: true, entries: forBank ? [{ id: "e1", label: "bank" }] : [] };
  return { ok: true, status: 200, json: async () => body };
}

const ctx = { chrome, fetch: fetchStub, console, URL, URLSearchParams,
              setTimeout, clearTimeout, setInterval, clearInterval,
              Date, Promise, JSON, Math };
ctx.globalThis = ctx; ctx.self = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(src, "utf8"), ctx, { filename: "background.js" });
if (!listener) { console.log(JSON.stringify({ error: "no onMessage listener" })); process.exit(0); }

const resp = new Promise((resolve) => {
  const r = listener({ action: scenario.action, payload: scenario.payload },
                     scenario.sender, resolve);
  if (r !== true) setTimeout(() => resolve({ sync_return: r }), 50);
});
const timeout = new Promise((resolve) => setTimeout(() => resolve({ timeout: true }), 3000));
Promise.race([resp, timeout]).then((response) => {
  console.log(JSON.stringify({ response, calls,
                               silent: local.bd_vault_silent_origins }));
});
"""


def _tab(url: str) -> dict:
    return {"id": EXT_ID, "tab": {"id": 7, "url": url}, "frameId": 0,
            "url": url, "origin": re.match(r"^[a-z]+://[^/]+", url).group(0)}


POPUP = {"id": EXT_ID, "url": f"chrome-extension://{EXT_ID}/popup.html",
         "origin": f"chrome-extension://{EXT_ID}"}

# manifest "options_page" opens options.html IN A TAB, and Chrome sets
# MessageSender.tab for extension pages in a tab too.
OPTIONS_URL = f"chrome-extension://{EXT_ID}/options.html"
OPTIONS_TAB = {"id": EXT_ID, "tab": {"id": 9, "url": OPTIONS_URL}, "frameId": 0,
               "url": OPTIONS_URL, "origin": f"chrome-extension://{EXT_ID}"}


def _run(action: str, payload: dict, sender: dict, silent: dict | None = None) -> dict:
    if not NODE:
        pytest.skip("node not on PATH: behavioural router test needs node "
                    "(the static router check below still runs)")
    out = subprocess.run(
        [NODE, "-e", HARNESS, str(BACKGROUND),
         json.dumps({"action": action, "payload": payload, "sender": sender,
                     "silent": silent or {}}),
         EXT_ID],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    line = out.stdout.strip().splitlines()[-1]
    return json.loads(line)


def test_tab_cannot_fetch_another_sites_password_by_claiming_its_origin():
    r = _run("vault_fetch_one", {"id": "e1", "origin": BANK}, _tab(EVIL))
    origins = [c["origin"] for c in r["calls"]]
    assert "BANK-SECRET" not in json.dumps(r["response"]), (
        "A12-VAULT-PAYLOAD-ORIGIN: a tab on evil.example received the "
        f"bank.example password by claiming payload.origin; fetch origins={origins}")
    assert all("bank.example" not in o for o in origins), origins


def test_tab_cannot_list_another_sites_entries_by_claiming_its_origin():
    r = _run("vault_list_for_origin", {"origin": BANK}, _tab(EVIL))
    origins = [c["origin"] for c in r["calls"]]
    assert all("bank.example" not in o for o in origins), (
        "A12-VAULT-PAYLOAD-ORIGIN: list_for_origin asked the vault for "
        f"bank.example on behalf of a tab on evil.example; fetch origins={origins}")
    assert not (r["response"] or {}).get("entries"), r["response"]


def test_tab_cannot_mark_another_site_silent():
    r = _run("vault_set_silent_for_origin", {"origin": BANK, "allow": True}, _tab(EVIL))
    assert "bank.example" not in r["silent"], (
        "A12-VAULT-PAYLOAD-ORIGIN: a tab on evil.example allow-listed "
        f"bank.example for silent fill: {r['silent']}")


def test_tab_on_the_vaulted_site_still_gets_its_own_entry():
    # Positive control: the honest content-script call (origin == its page).
    r = _run("vault_fetch_one", {"id": "e1", "origin": BANK}, _tab(BANK))
    assert r["response"].get("password") == "BANK-SECRET", r


def test_extension_page_may_name_the_origin():
    # The popup/options pages act for the active tab, so they must keep
    # naming the origin in the payload.
    r = _run("vault_fetch_one", {"id": "e1", "origin": BANK}, POPUP)
    assert r["response"].get("password") == "BANK-SECRET", r
    r = _run("vault_set_silent_for_origin", {"origin": BANK, "allow": True}, POPUP)
    assert r["silent"].get("bank.example") is True, r


def test_options_page_in_a_tab_can_revoke_silent_fill():
    # options.js "remove" sends payload.origin for the site being revoked.
    r = _run("vault_set_silent_for_origin", {"origin": "https://bank.example", "allow": False},
             OPTIONS_TAB, silent={"bank.example": True})
    assert "bank.example" not in r["silent"], (
        "A12-OPTIONS-TAB-REVOKE: the options page (opened in a tab) revoked silent fill "
        f"for bank.example but it is still allowed: {r['silent']} response={r['response']}")
    r = _run("vault_set_silent_for_origin", {"origin": "https://bank.example", "allow": True},
             OPTIONS_TAB)
    assert r["silent"] == {"bank.example": True}, (
        f"A12-OPTIONS-TAB-REVOKE: options allow wrote the wrong key: {r['silent']}")


@pytest.mark.parametrize("sender", [
    # neither id nor URL is ours
    {"id": "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
     "url": "chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/x.html"},
    # our URL prefix but another id: the id check alone must refuse
    {"id": "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "url": f"chrome-extension://{EXT_ID}/x.html"},
    # our id but a web URL and no tab: the URL check alone must refuse
    {"id": EXT_ID, "url": EVIL},
], ids=["foreign", "foreign-id-own-url", "own-id-web-url"])
def test_foreign_sender_is_not_trusted_for_an_origin(sender):
    r = _run("vault_fetch_one", {"id": "e1", "origin": BANK}, sender)
    assert "BANK-SECRET" not in json.dumps(r["response"]), (
        f"A12-VAULT-PAYLOAD-ORIGIN: untrusted sender {sender} got the bank.example password")
    assert not r["calls"], r["calls"]


def test_router_never_passes_payload_origin_straight_to_the_vault():
    """Static guard that runs without node: no vault origin call in the
    router takes ``payload.origin`` as its argument."""
    src = BACKGROUND.read_text(encoding="utf-8")
    router = src[src.index("chrome.runtime.onMessage.addListener"):]
    bad = re.findall(
        r"vault(?:ListForOrigin|FetchOne|SetSilentForOrigin)\([^)]*payload\.origin[^)]*\)",
        router)
    assert not bad, f"A12-VAULT-PAYLOAD-ORIGIN: router trusts payload.origin: {bad}"
