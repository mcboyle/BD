// RED->GREEN probe for extension/background.js registrableDomain() (O1671 AUDIT-12 line 3).
//
// RED COMMAND:  node extension/test_registrable_domain.mjs
// On the defective base, evil.co.uk and bank.co.uk derive the SAME key ("co.uk")
// and this script exits 1. Runs the real background.js inside a node vm with a
// chrome stub and an importScripts shim that loads public_suffix_data.js; no
// browser, no network.
//
// Round 2 (lens F1): a suffix the bundle does not know must not degrade to
// last-two-labels. The flow checks drive the real message listener: allow
// silent fill on bank.co.ke, then ask as evil.co.ke.
//
// Also asserts PARITY of the curated inline set with _MULTI_PART_SUFFIXES in
// bulk_downloader/registrable_domain.py, parsed from the Python source.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..");
const bgPath = path.join(here, "background.js");
const pyPath = path.join(repo, "bulk_downloader", "registrable_domain.py");

const EXT_ID = "abcdefghijklmnopabcdefghijklmnop";

// Load background.js. withList=false leaves importScripts undefined, which is
// how the standalone vm harnesses (and a failed load) see the worker.
function loadWorker({ withList, silent }) {
  const noop = new Proxy(function () {}, { get: () => noop, apply: () => undefined });
  const local = { bd_vault_token: "tok", bd_vault_idle_lock_minutes: 0, bd_vault_silent_origins: silent || {} };
  let listener = null;
  const pick = (store) => async (keys) => Object.fromEntries((keys || []).filter((k) => k in store).map((k) => [k, store[k]]));
  const lax = (obj) => new Proxy(obj, { get: (t, k) => (k in t ? t[k] : noop) });
  const chrome = lax({
    runtime: lax({
      id: EXT_ID,
      getURL: (p) => `chrome-extension://${EXT_ID}/` + (p || ""),
      onMessage: { addListener: (fn) => { listener = fn; } },
    }),
    storage: lax({
      local: { get: pick(local), set: async (kv) => { Object.assign(local, kv); } },
      sync: { get: async () => ({}), set: async () => {} },
    }),
  });
  const fetch = async () => ({ ok: true, status: 200, json: async () => ({ entries: [] }) });
  const ctx = { chrome, fetch, console: { ...console, warn: () => {} }, URL, setTimeout, clearTimeout, encodeURIComponent };
  ctx.self = ctx;
  vm.createContext(ctx);
  if (withList) ctx.importScripts = (f) => vm.runInContext(fs.readFileSync(path.join(here, f), "utf8"), ctx, { filename: f });
  vm.runInContext(fs.readFileSync(bgPath, "utf8") +
    "\n;globalThis.__rd = registrableDomain;" +
    " globalThis.__suffixes = typeof BD_MULTI_PART_SUFFIXES === \"undefined\" ? undefined : BD_MULTI_PART_SUFFIXES;" +
    " globalThis.__listRules = typeof BD_PSL === \"undefined\" ? 0 : BD_PSL.rule_count;",
    ctx, { filename: "background.js" });
  if (typeof ctx.__rd !== "function") throw new Error("registrableDomain not found in background.js");
  const popup = { id: EXT_ID, url: `chrome-extension://${EXT_ID}/popup.html` };
  const send = (action, payload, sender) => new Promise((resolve) => {
    if (!listener) return resolve({ ok: false, error: "no onMessage listener registered" });
    listener({ action, payload }, sender || popup, resolve);
  });
  return { rd: ctx.__rd, suffixes: ctx.__suffixes, listRules: ctx.__listRules, send, local };
}

const failures = [];
function check(name, got, want) {
  const ok = got === want;
  console.log(`${ok ? "PASS" : "FAIL"} ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  if (!ok) failures.push(name);
}

const tab = (url) => ({ id: EXT_ID, url, tab: { id: 7 } });

const { rd, suffixes, listRules } = loadWorker({ withList: true });
check("fixture: the bundled list loaded (rule_count > 9000)", listRules > 9000, true);

// -- the defect: multipart public suffixes must separate registrants --------
check("evil.co.uk != bank.co.uk", rd("https://evil.co.uk/x") === rd("https://bank.co.uk/login"), false);
check("evil.co.uk key", rd("https://evil.co.uk/x"), "evil.co.uk");
check("bank.co.uk key", rd("https://bank.co.uk/login"), "bank.co.uk");
check("www.bank.co.uk == bank.co.uk", rd("https://www.bank.co.uk/"), "bank.co.uk");
check("a.github.io != b.github.io", rd("https://a.github.io/") === rd("https://b.github.io/"), false);
check("site.com.au key", rd("https://www.site.com.au/"), "site.com.au");
check("longest suffix wins (s3.amazonaws.com)", rd("https://bucket.s3.amazonaws.com/"), "bucket.s3.amazonaws.com");

// -- F1: a suffix outside the curated set (co.ke) ---------------------------
check("F1 evil.co.ke != bank.co.ke", rd("https://evil.co.ke/") === rd("https://bank.co.ke/"), false);
check("F1 bank.co.ke key", rd("https://bank.co.ke/"), "bank.co.ke");
check("F1 www.bank.co.ke shares bank.co.ke", rd("https://www.bank.co.ke/"), "bank.co.ke");
check("private suffix under a flat TLD (onrender.com)", rd("https://evil.onrender.com/") === rd("https://bank.onrender.com/"), false);
check("three-label ICANN suffix (abashiri.hokkaido.jp)", rd("https://evil.abashiri.hokkaido.jp/") === rd("https://bank.abashiri.hokkaido.jp/"), false);
check("three-label ICANN suffix key", rd("https://www.bank.abashiri.hokkaido.jp/"), "bank.abashiri.hokkaido.jp");
check("exception inside a wildcard (!city.sapporo.jp)", rd("https://www.city.sapporo.jp/"), "city.sapporo.jp");
check("wildcard rule *.ck", rd("https://shop.foo.ck/"), "shop.foo.ck");
check("wildcard rule *.ck: a subdomain shares its registrant's key", rd("https://www.shop.foo.ck/"), "shop.foo.ck");
check("wildcard rule separates registrants", rd("https://a.foo.ck/") === rd("https://b.bar.ck/"), false);
check("exception rule !www.ck", rd("https://a.www.ck/"), "www.ck");
check("IDN suffix as A-label (xn--55qx5d.cn)", rd("https://www.bank.xn--55qx5d.cn/"), "bank.xn--55qx5d.cn");

// -- fail closed: no rule matched -> the whole host, never last-two ----------
check("unknown TLD keys the whole host", rd("https://a.b.invalidsuffixtld/"), "a.b.invalidsuffixtld");
check("unknown TLD: neighbours stay apart", rd("https://evil.co.invalidsuffixtld/") === rd("https://bank.co.invalidsuffixtld/"), false);

// -- bare suffix / single-label / IP literal key only themselves -------------
check("bare co.uk keys itself", rd("https://co.uk/"), "co.uk");
check("bare co.uk != bank.co.uk", rd("https://co.uk/") === rd("https://bank.co.uk/"), false);
check("bare github.io keys itself", rd("https://github.io/"), "github.io");
check("localhost keys itself", rd("http://localhost:5555/"), "localhost");
check("127.0.0.1 keys whole address", rd("http://127.0.0.1:8080/"), "127.0.0.1");
check("127.0.0.1 != 10.0.0.1", rd("http://127.0.0.1/") === rd("http://10.0.0.1/"), false);
check("IPv6 literal keys itself", rd("http://[::1]:5555/"), "[::1]");

// -- positive control: cases the old rule got right are unchanged -----------
check("www.example.com == login.example.com", rd("https://www.example.com/") === rd("https://login.example.com/"), true);
check("example.com key", rd("https://www.example.com/"), "example.com");
check("a.b.example.com key", rd("https://a.b.example.com/"), "example.com");
check("example.com != example.org", rd("https://www.example.com/") === rd("https://www.example.org/"), false);
check("trailing dot and case fold", rd("https://WWW.Example.COM./"), "example.com");
check("garbage -> empty", rd("not a url"), "");
check("empty -> empty", rd(""), "");

// -- list NOT loaded (standalone harness, failed importScripts): fail closed --
const bare = loadWorker({ withList: false });
check("no list: fixture really has no list", bare.listRules, 0);
check("no list: evil.co.ke != bank.co.ke", bare.rd("https://evil.co.ke/") === bare.rd("https://bank.co.ke/"), false);
check("no list: unknown suffix keys the whole host", bare.rd("https://www.example.com/"), "www.example.com");
check("no list: curated co.uk still gives suffix+1", bare.rd("https://www.bank.co.uk/"), "bank.co.uk");

// -- the consent flow through the real message listener ---------------------
{
  const w = loadWorker({ withList: true });
  const before = await w.send("vault_list_for_origin", {}, tab("https://www.bank.co.ke/login"));
  check("F1 flow fixture: nothing is silent before the grant", before.silent_allowed, false);
  const grant = await w.send("vault_set_silent_for_origin", { origin: "https://bank.co.ke/login", allow: true });
  check("F1 flow: the grant is stored under bank.co.ke", JSON.stringify(Object.keys(w.local.bd_vault_silent_origins)), JSON.stringify(["bank.co.ke"]));
  check("F1 flow: grant response names the key", grant.etld1, "bank.co.ke");
  const same = await w.send("vault_list_for_origin", {}, tab("https://www.bank.co.ke/login"));
  check("F1 flow control: www.bank.co.ke is silent after the grant", same.silent_allowed, true);
  const evil = await w.send("vault_list_for_origin", {}, tab("https://evil.co.ke/phish"));
  check("F1 flow: evil.co.ke is not silent after bank.co.ke was allowed", evil.silent_allowed, false);
  const key = await w.send("vault_silent_key", { origin: "https://www.bank.co.ke/x" });
  check("popup key action: key and state", JSON.stringify([key.ok, key.etld1, key.allowed]), JSON.stringify([true, "bank.co.ke", true]));
  const foreign = await w.send("vault_silent_key", { origin: "https://bank.co.ke/" }, tab("https://evil.co.ke/"));
  check("popup key action: a tab only learns its own page's key", JSON.stringify([foreign.etld1, foreign.allowed]), JSON.stringify(["evil.co.ke", false]));
  await w.send("vault_set_silent_for_origin", { origin: "https://bank.co.ke/", allow: false });
  const after = await w.send("vault_list_for_origin", {}, tab("https://www.bank.co.ke/login"));
  check("F1 flow: revoke clears it", after.silent_allowed, false);
}
{
  // A key the old rule stored ("co.ke") must not make any registrant silent now.
  const w = loadWorker({ withList: true, silent: { "co.ke": true, "co.uk": true } });
  const a = await w.send("vault_list_for_origin", {}, tab("https://evil.co.ke/"));
  const b = await w.send("vault_list_for_origin", {}, tab("https://bank.co.uk/"));
  check("legacy over-broad keys grant nothing", JSON.stringify([a.silent_allowed, b.silent_allowed]), JSON.stringify([false, false]));
}

// -- the popup has no second copy of the rule --------------------------------
const popupSrc = fs.readFileSync(path.join(here, "popup.js"), "utf8");
check("popup.js asks the background for the key", popupSrc.includes('bgCall("vault_silent_key"'), true);
check("popup.js derives no key of its own (slice(-2) count)", (popupSrc.match(/slice\(-2\)/g) || []).length, 0);

// -- parity of the curated set with the Python reference ---------------------
const py = fs.readFileSync(pyPath, "utf8");
const block = py.match(/_MULTI_PART_SUFFIXES = frozenset\(\{([\s\S]*?)\}\)/);
if (!block) { failures.push("python set not found"); console.log("FAIL could not parse _MULTI_PART_SUFFIXES from " + pyPath); }
else {
  const pySet = new Set(block[1].split("\n").filter((l) => !l.trim().startsWith("#")).join("\n").match(/"([^"]+)"/g).map((s) => s.slice(1, -1)));
  // Duck-typed: the vm context has its own Set constructor, so instanceof is cross-realm false.
  const loaded = !!suffixes && typeof suffixes.has === "function";
  const jsSet = loaded ? suffixes : new Set();
  check("parity: background.js defines BD_MULTI_PART_SUFFIXES", loaded, true);
  const onlyPy = [...pySet].filter((s) => !jsSet.has(s));
  const onlyJs = [...jsSet].filter((s) => !pySet.has(s));
  check(`parity: python set size ${pySet.size} == js set size ${jsSet.size}`, pySet.size, jsSet.size);
  check("parity: only-in-python (" + onlyPy.length + ")", onlyPy.length, 0);
  check("parity: only-in-js " + JSON.stringify(onlyJs.slice(0, 5)), onlyJs.length, 0);
}

console.log(failures.length ? `FOUND ${failures.length} failing: ${failures.join("; ")}` : "FOUND NONE failing");
process.exit(failures.length ? 1 : 0);
