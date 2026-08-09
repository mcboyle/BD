#!/usr/bin/env python3
"""
capture_scrub.py — standalone, offline pre-share redactor for BulkDownloader
capture artifacts (.wacz / capture.json / any JSON).

PURPOSE
    Make a real capture SAFE TO SHARE (e.g. hand to an assistant for review) by
    aggressively stripping F2-sensitive material BEFORE it leaves your box. This
    is NOT BulkDownloader's functional redaction (which preserves URLs so replay
    works) — it OVER-redacts on purpose: you keep structure/shape for review,
    secrets and signing are destroyed. Closes the gaps BD defers (path-signed/
    base64 URLs, emails in text bodies, high-entropy tokens).

GUARANTEES
    • Pure Python 3 stdlib. No third-party deps. No network. No BD imports.
    • Non-destructive: writes <input>.redacted.<ext>; never touches the input.
    • PREVIEW FIRST: --preview shows exactly what it WOULD redact (path, reason,
      before -> after) and writes nothing, so you can verify and spot design
      flaws before applying or before porting any of this to BD's online F2.
    • Self-verifying: on apply, an INDEPENDENT second pass re-scans the output
      for residual secret/signing patterns and REFUSES to write (exit 2) if any
      remain. A file you share via this tool has been proven clean.

USAGE
    Preview (recommended first):
        python3 capture_scrub.py INPUT --preview              # samples per kind
        python3 capture_scrub.py INPUT --preview --full       # every change
    Apply:
        python3 capture_scrub.py INPUT [-o OUT] [--mode safe|strict|paranoid]
                                       [--token-min N]

    --mode safe      (default) floor + signing(query+PATH+base64) + emails +
                     userinfo + high-entropy tokens. Keeps hosts, path shape,
                     benign query keys, DOM structure — still useful to review.
    --mode strict    also strips ALL query strings; masks every non-trivial path
                     segment.
    --mode paranoid  masks every URL to scheme://host/<REDACTED>; reduces text
                     blobs to markers. Structure/counts only.
    --token-min N    opaque-token length threshold (default 24; lower = stricter)

WARNING
    --preview output contains the REAL pre-redaction values (it's for YOUR eyes
    on YOUR box). Do not paste preview output to anyone — share only the written
    *.redacted.* file.

EXIT CODES
    0 = preview shown, or redacted output written and VERIFIED clean
    2 = residual secret after redaction (output NOT written) — report it
    1 = usage / IO error
"""
from __future__ import annotations
import argparse, base64, json, os, re, sys, zipfile, io

# @971. bdtools_sec lives in toolchain/bin; this is the same shim tools/
# bd-audit-gate.py and bd-triage.py already use. Imported so the FOUR scrubbers
# share ONE answer to "can this member be scanned" -- v3.66.859 removed three
# divergent allowlists from the other tools and this one kept its own rule,
# which is how the .warc gap survived on the path that builds every shared twin.
_d_cs = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, _d_cs)
sys.path.insert(0, os.path.join(os.path.dirname(_d_cs), 'toolchain', 'bin'))
import bdtools_sec as _sec  # noqa: E402

PH = "<REDACTED>"

# ─────────────────────────── detector patterns ───────────────────────────
RE_JWT      = re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b")
RE_EMAIL    = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RE_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/@\s:]+:[^/@\s]+@")
RE_URL      = re.compile(r"(?:https?:)?//[^\s\"'<>)\]}]+", re.I)
RE_SIGN_KEY = re.compile(
    r"(?i)^(?:.*[-_])?(signature|x-amz-[a-z0-9-]+|sig|key[-_]?pair[-_]?id|"
    r"expires?|expiretime|policy|hmac|signed|credential|api[-_]?key|apikey|"
    r"access[-_]?token|auth|authorization|sid|session|secret|password|pwd|"
    r"jwt|bearer|salt|nonce|token|st|hash|md5|sha)(?:[-_].*)?$")
RE_SENS_HDR = re.compile(r"(?i)^(cookie|set-cookie|authorization|proxy-authorization|"
                         r"x-api-key|x-auth-token|x-csrf-token|x-xsrf-token|"
                         r"x-amz-security-token|instance|"
                         r"x-forwarded-for|x-real-ip|x-client-ip|true-client-ip|"
                         r"cf-connecting-ip|forwarded|via|x-detected-ip)$")
RE_SENS_KEY_SUBSTR = re.compile(
    r"(?i)(cookie|token|secret|session|sid|auth|password|passwd|pwd|credential|"
    r"signature|bearer|api[-_]?key|apikey|jwt|access[-_]?key|private[-_]?key|"
    r"key[-_]?pair|turnstile|captcha|otp|csrf|xsrf)")
SIGN_WORDS  = re.compile(r"(?i)(expire|sig|signature|token|hmac|policy|secret|"
                         r"dirmatch|key[-_]?pair|credential|auth|md5|sha|st=)")
# TWO ARMS, deliberately. The whole-word arm keeps \b so `tokenizer=` is NOT
# masked by `token`. The substring arm has no \b, because \b never falls inside
# `bd_session` -- `_` is a word char, so the boundary sits before `bd`, and BD's
# own auth cookie sailed through the pre-share scrubber untouched. Only keys
# whose leak-shape is affix-prone go in the substring arm; widening every key
# that way would over-mask and a redactor that cries wolf gets switched off.
# csrf/xsrf were in RE_SENS_KEY_SUBSTR but absent here entirely.
# Both added groups are NON-capturing: consumers depend on group(1)=key and
# group(2)=value (scrub_string:199, scan_residual:260-261).
RE_KV_SECRET = re.compile(
    r"(?i)\b("
    r"(?:token|auth|sig|signature|secret|password|pwd|jwt|bearer|apikey|"
    r"api[-_]?key|access[-_]?token|key[-_]?pair[-_]?id)\b"
    r"|[\w-]*(?:session|sid|csrf|xsrf)[\w-]*)"
    r"\s*[=:]\s*([^&;,\s\"']{8,})")
HEXRUN = re.compile(r"^[0-9a-fA-F]{24,}$")
B64ISH = re.compile(r"^[A-Za-z0-9_\-+/]{24,}={0,2}$")
# patch B: bare-IPv4 detector for header values / page text (defense in depth). The
# operator egress/VPN IP survives in forwarding headers; patch A masks those by name,
# this also catches an IP in any other header value. UA version strings like
# "137.0.0.0" are skipped. URL hosts are NOT masked here (safe mode preserves hosts).
RE_IPV4 = re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w.])")
def _is_ua_version(ip: str) -> bool:
    o = ip.split(".")
    return o[1:] == ["0", "0", "0"] or o[2:] == ["0", "0"]
def _mask_ipv4(s: str) -> str:
    def _r(m):
        if _is_ua_version(m.group(0)): return m.group(0)
        bump("ipv4"); return PH
    return RE_IPV4.sub(_r, s)

stats: dict[str, int] = {}
def bump(kind, n=1): stats[kind] = stats.get(kind, 0) + n
def _snip(v, n=100):
    s = v if isinstance(v, str) else str(v)
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[:n] + f"…(+{len(s)-n})"

def _is_template_field(path: str) -> bool:
    """patch C: a template-draft selector slot / workflow descriptor is never a secret
    (``$.selectors.login.password`` is a CSS selector, not a credential; ``$.workflow.auth``
    is an auth-handling mode). Drafts are also name-skipped in main(); this guards an
    embedded or renamed draft block."""
    return (".selectors." in path
            or path.endswith(".workflow.auth")
            or path.endswith(".workflow.capture_mode"))

def _looks_base64(seg: str) -> bool:
    return bool(B64ISH.match(seg)) and any(c.isdigit() for c in seg) and any(c.isalpha() for c in seg)

def _decode_b64(seg: str):
    s = seg.replace("-", "+").replace("_", "/"); s += "=" * (-len(s) % 4)
    try: return base64.b64decode(s, validate=False).decode("utf-8", "replace")
    except Exception: return ""

def scrub_url(u: str, mode: str, token_min: int) -> str:
    if not isinstance(u, str) or "//" not in u:
        return u
    u2 = RE_USERINFO.sub(lambda m: m.group("scheme"), u)
    if u2 != u: bump("userinfo")
    u = u2
    m = re.match(r"(?P<pre>(?:https?:)?//[^/?#]+)(?P<path>[^?#]*)(?P<q>\?[^#]*)?(?P<frag>#.*)?$", u, re.I)
    if not m: return u
    pre, path, q, frag = m.group("pre"), m.group("path") or "", m.group("q") or "", m.group("frag") or ""
    if mode == "paranoid":
        bump("url_paranoid"); return f"{pre}/{PH}"
    new_segs = []
    for seg in path.split("/"):
        if not seg: new_segs.append(seg); continue
        masked = False
        if _looks_base64(seg) and SIGN_WORDS.search(_decode_b64(seg)): masked = True; bump("path_base64_signed")
        elif HEXRUN.match(seg): masked = True; bump("path_hexrun")
        elif mode == "strict" and len(seg) >= token_min and _looks_base64(seg): masked = True; bump("path_opaque_strict")
        new_segs.append(PH if masked else seg)
    path = "/".join(new_segs)
    if q:
        if mode == "strict":
            q = "?" + PH; bump("query_stripped")
        else:
            pairs = []
            for pair in q[1:].split("&"):
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    if RE_SIGN_KEY.match(k) or (len(v) >= token_min and (B64ISH.match(v) or HEXRUN.match(v))):
                        pairs.append(f"{k}={PH}"); bump("query_signed")
                    else:
                        pairs.append(f"{k}={v}")
                else:
                    pairs.append(pair)
            q = "?" + "&".join(pairs)
    if frag and ("=" in frag) and SIGN_WORDS.search(frag):
        frag = "#" + PH; bump("fragment_signed")
    return pre + path + q + frag

def _shannon_bits(s: str) -> float:
    """Shannon entropy of ``s`` in bits per character (0.0 for empty)."""
    if not s:
        return 0.0
    from math import log2
    freq: dict = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((k / n) * log2(k / n) for k in freq.values())


# F-TOOLSO04-01: an opaque credential/blob run of length >= token_min. Mixed
# digit+alpha keeps the original masker behaviour; an all-ALPHABETIC run must
# clear a high Shannon-entropy bar (so structured text -- CamelCase, long class
# names, words -- is NOT masked, only near-random alpha blobs are); an all-
# NUMERIC run of that length is an id / nonce / signed value (its entropy can't
# exceed ~3.3 bits so it is length-gated, not entropy-gated).
_OPAQUE_ALPHA_BITS = 4.5


def _is_opaque_run(tok: str, token_min: int) -> bool:
    if len(tok) < token_min:
        return False
    has_digit = any(c.isdigit() for c in tok)
    has_alpha = any(c.isalpha() for c in tok)
    if has_alpha and has_digit:
        return True
    if has_alpha:
        return _shannon_bits(tok) >= _OPAQUE_ALPHA_BITS
    return has_digit


def scrub_string(s: str, mode: str, token_min: int, header_ctx: bool = False) -> str:
    if not isinstance(s, str) or not s or s == PH:
        return s
    s, n = RE_JWT.subn(PH, s);    bump("jwt", n) if n else None
    s, n = RE_EMAIL.subn(PH, s);  bump("email", n) if n else None
    s = RE_URL.sub(lambda m: scrub_url(m.group(0), mode, token_min), s)
    s = RE_KV_SECRET.sub(lambda m: f"{m.group(1)}={PH}", s)
    def _opaque(m):
        tok = m.group(0)
        if tok == PH or "REDACTED" in tok: return tok
        bump("opaque_token"); return PH
    s = re.sub(rf"\b[A-Za-z0-9_\-]{{{token_min},}}\b",
               lambda m: _opaque(m) if _is_opaque_run(m.group(0), token_min) else m.group(0),
               s)
    if header_ctx:                       # patch B: mask a bare IP in a header value
        s = _mask_ipv4(s)
    return s

def walk(o, mode, token_min, header_ctx=False, key=None, path="$", changes=None):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            kp = f"{path}.{k}" if isinstance(k, str) else f"{path}[{k!r}]"
            kl = k.lower() if isinstance(k, str) else k
            if isinstance(k, str) and RE_SENS_HDR.match(k):
                if changes is not None and v not in (PH, None, ""): changes.append((kp, "sensitive_header", _snip(v), PH))
                out[k] = PH; bump("sensitive_header"); continue
            if isinstance(k, str) and RE_SENS_KEY_SUBSTR.search(k) and isinstance(v, str) and v and v != PH \
               and not _is_template_field(kp):
                if changes is not None: changes.append((kp, "sensitive_key", _snip(v), PH))
                out[k] = PH; bump("sensitive_key"); continue
            hctx = header_ctx or (isinstance(k, str) and kl in ("headers", "request_headers", "response_headers"))
            out[k] = walk(v, mode, token_min, hctx, k, kp, changes)
        return out
    if isinstance(o, list):
        new = []
        for i, item in enumerate(o):
            ip = f"{path}[{i}]"
            if isinstance(item, dict) and "name" in item and "value" in item \
               and isinstance(item.get("name"), str) and RE_SENS_HDR.match(item["name"]):
                if changes is not None and item.get("value") not in (PH, None, ""):
                    changes.append((f"{ip}.value", "sensitive_header", _snip(item.get("value")), PH))
                new.append({**item, "value": PH}); bump("sensitive_header")
            else:
                new.append(walk(item, mode, token_min, header_ctx, key, ip, changes))
        return new
    if isinstance(o, str):
        new = scrub_string(o, mode, token_min, header_ctx)
        if changes is not None and new != o:
            changes.append((path, "string", _snip(o), _snip(new)))
        return new
    return o

def scan_residual(o, path="$", hits=None, key=None, header_ctx=False, token_min=24):
    if hits is None: hits = []
    if isinstance(o, dict):
        is_hdr_pair = ("name" in o and "value" in o and isinstance(o.get("name"), str))
        for k, v in o.items():
            kl = k.lower() if isinstance(k, str) else k
            hctx = header_ctx or (isinstance(k, str) and kl in ("headers", "request_headers", "response_headers")) \
                   or (is_hdr_pair and k == "value")
            scan_residual(v, f"{path}.{k}", hits, k, hctx, token_min)
    elif isinstance(o, list):
        for i, v in enumerate(o): scan_residual(v, f"{path}[{i}]", hits, key, header_ctx, token_min)
    elif isinstance(o, str) and o and o != PH:
        if isinstance(key, str) and RE_SENS_KEY_SUBSTR.search(key) and len(o) >= 4:
            hits.append((path, f"sensitive-key-value:{key}"))
        for m in RE_KV_SECRET.finditer(o):
            if m.group(2) and m.group(2) != PH: hits.append((path, f"kv-secret:{m.group(1)}"))
        if RE_JWT.search(o):      hits.append((path, "jwt"))
        if RE_EMAIL.search(o):    hits.append((path, "email"))
        if RE_USERINFO.search(o): hits.append((path, "userinfo"))
        if header_ctx:            # patch B: a bare IP in a header value is residual
            for m in RE_IPV4.finditer(o):
                if not _is_ua_version(m.group(0)): hits.append((path, "ipv4-header")); break
        for mm in RE_URL.finditer(o):
            url = mm.group(0)
            qpart = url.split("?", 1)[1] if "?" in url else ""
            for pair in qpart.split("&"):
                k, _, v = pair.partition("=")
                if RE_SIGN_KEY.match(k) and v and v != PH: hits.append((path, f"signed-query:{k}"))
            for seg in url.split("?", 1)[0].split("/"):
                if seg and seg != PH and _looks_base64(seg) and SIGN_WORDS.search(_decode_b64(seg)):
                    hits.append((path, "path-base64-signed"))
        # F-TOOLSO04-01: a high-entropy opaque token the scrub masker may have
        # missed (all-alpha or all-numeric) must be caught here so it cannot pass
        # the CLEAN verify -- restoring the redact-then-verify-then-refuse backstop.
        for _m in re.finditer(rf"\b[A-Za-z0-9_\-]{{{token_min},}}\b", o):
            _t = _m.group(0)
            if _t != PH and "REDACTED" not in _t and _is_opaque_run(_t, token_min):
                hits.append((path, "opaque-token")); break
    return hits

def _process(raw: bytes, mode, token_min, changes=None):
    obj = json.loads(raw.decode("utf-8", "replace"))
    red = walk(obj, mode, token_min, changes=changes)
    return red, scan_residual(red, token_min=token_min)

def _process_jsonl(data: bytes, mode, token_min, member, all_residual, changes=None):
    lines = []
    for ln in data.decode("utf-8", "replace").splitlines():
        if not ln.strip(): lines.append(ln); continue
        try: o = json.loads(ln)
        except Exception: lines.append(scrub_string(ln, mode, token_min)); continue
        r = walk(o, mode, token_min, changes=changes)
        all_residual += [(f"{member}:{p}", k) for p, k in scan_residual(r, token_min=token_min)]
        lines.append(json.dumps(r, ensure_ascii=False))
    return ("\n".join(lines)).encode("utf-8")

def _print_preview(changes, full):
    print(f"\nPREVIEW — {len(changes)} redaction(s) planned (NOTHING written):")
    by_reason: dict[str, list] = {}
    for p, reason, before, after in changes:
        by_reason.setdefault(reason, []).append((p, before, after))
    for reason in sorted(by_reason):
        items = by_reason[reason]
        print(f"\n  ── {reason} ({len(items)}) ──")
        show = items if full else items[:8]
        for p, before, after in show:
            print(f"    {p}")
            print(f"        - {before}")
            print(f"        + {after}")
        if not full and len(items) > len(show):
            print(f"    … +{len(items)-len(show)} more (use --full to see all)")

def main():
    ap = argparse.ArgumentParser(description="Offline pre-share redactor for capture artifacts.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--mode", choices=["safe", "strict", "paranoid"], default="safe")
    ap.add_argument("--token-min", type=int, default=24)
    ap.add_argument("--preview", action="store_true", help="show planned redactions; write nothing")
    ap.add_argument("--full", action="store_true", help="with --preview, show every change (not a sample)")
    args = ap.parse_args()

    inp = args.input
    if not os.path.isfile(inp):
        print(f"ERROR: no such file: {inp}", file=sys.stderr); return 1
    if re.search(r"_draft.*\.json$", os.path.basename(inp), re.I):   # patch C
        print(f"REFUSING: {inp} looks like a template draft, not a share artifact.\n"
              f"  Drafts hold functional selector slots (e.g. selectors.login.password) and\n"
              f"  workflow descriptors that must NOT be redacted. Scrub the capture WACZ instead.",
              file=sys.stderr)
        return 1

    # @971: initialised before the branch so the report below can reference it
    # unconditionally. The first attempt read it back through locals()/globals(),
    # which is a lookup that returns empty when it cannot see its subject and
    # then prints nothing -- the same shape as the defect being fixed.
    unscanned: list = []
    is_wacz = inp.lower().endswith(".wacz") or zipfile.is_zipfile(inp)
    all_residual, changes = [], ([] if args.preview else None)

    if is_wacz:
        out = args.output or (re.sub(r"\.wacz$", "", inp, flags=re.I) + ".redacted.wacz")
        with zipfile.ZipFile(inp) as zin:
            names = zin.namelist(); buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
                for n in names:
                    data = zin.read(n); low = n.lower()
                    if low.endswith(".jsonl"):
                        data = _process_jsonl(data, args.mode, args.token_min, n, all_residual, changes)
                    elif low.endswith(".json"):
                        red, residual = _process(data, args.mode, args.token_min, changes)
                        all_residual += [(f"{n}:{p}", k) for p, k in residual]
                        data = json.dumps(red, ensure_ascii=False).encode("utf-8")
                    elif _sec.should_scan(n, data):
                        # errors="ignore" deliberately: should_scan has already
                        # decided this is TEXT (no NUL). A strict decode here is
                        # what silently skipped every latin-1 WARC body -- the
                        # bytes it cannot map are not the bytes a secret is in.
                        data = scrub_string(data.decode("utf-8", "ignore"),
                                            args.mode, args.token_min).encode("utf-8")
                    else:
                        # Provably binary: pass through, but NEVER silently.
                        # A member that could not be read is UNKNOWN, and
                        # unknown reported as clean is the whole defect class.
                        unscanned.append(n)
                    if not args.preview: zout.writestr(n, data)
            blob = buf.getvalue()
    else:
        out = args.output or (re.sub(r"\.json$", "", inp, flags=re.I) + ".redacted.json")
        red, residual = _process(open(inp, "rb").read(), args.mode, args.token_min, changes)
        all_residual += residual
        blob = json.dumps(red, ensure_ascii=False, indent=0).encode("utf-8")

    print(f"capture_scrub — mode={args.mode} token-min={args.token_min}{'  [PREVIEW]' if args.preview else ''}")
    print(f"input : {inp}")
    print("redactions:")
    for k in sorted(stats): print(f"  {stats[k]:>6}  {k}")
    if not stats: print("  (nothing matched — is this a capture file?)")
    # @971. Say what was NOT examined, beside what was. Without this the run
    # prints "Safe to share" over members it never read, and a reader cannot
    # tell that from a run where everything was scanned -- measured before the
    # fix on an archive whose only binary member was passed through untouched.
    _un = unscanned
    if _un:
        print("unscanned (binary, passed through unchanged): %d member(s)" % len(_un))
        for _n in _un[:10]:
            print("  %s" % _n)
        if len(_un) > 10:
            print("  ... and %d more" % (len(_un) - 10))

    if args.preview:
        _print_preview(changes, args.full)
        # show what the verify pass WOULD flag on the would-be output
        if all_residual:
            print(f"\n  NOTE: post-redaction verify WOULD flag {len(all_residual)} residual(s) "
                  f"(e.g. {all_residual[0][1]}) — these need a pattern fix before apply.")
        else:
            print("\n  Post-redaction verify on the previewed result: CLEAN.")
        print("\nPREVIEW ONLY — nothing written. Re-run without --preview to apply.")
        print("WARNING: the '-' lines above are REAL pre-redaction values — do not share this preview.")
        return 0

    if all_residual:
        print(f"\n!!! VERIFY FAILED: {len(all_residual)} residual secret(s) after redaction:")
        for p, k in all_residual[:20]: print(f"    {k}  @ {p}")
        print("OUTPUT NOT WRITTEN. Do NOT share this file. Report to the tool author.")
        return 2

    print("\nVERIFY: re-scan of redacted output found NO residual secrets — CLEAN.")
    with open(out, "wb") as f: f.write(blob)
    print(f"written: {out}")
    print("Safe to share. (Structure/shape preserved; secrets + signing destroyed.)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
