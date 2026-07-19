#!/usr/bin/env python3
"""Deep audit of login and download templates.

Goes beyond dev_suite.template_audit (which only checks for empty
lists and overlap) and looks for *real* bugs: invalid CSS, selectors
that fundamentally can't match, name/host mismatches, host typos,
duplicate IDs, and download-template-specific issues.
"""
from __future__ import annotations

import re
import sys
import json
from collections import defaultdict, Counter

sys.path.insert(0, "/root/BulkDownloader")

from bulk_downloader.login_templates_data import LOGIN_TEMPLATES
from bulk_downloader import templates as T


# CSS selector validity: try playwright's parser if available, else
# fall back to a manual check.
try:
    # cssselect parses CSS selectors the same way Playwright does.
    import cssselect  # type: ignore
    _have_cssselect = True
except ImportError:
    _have_cssselect = False


def check_selector(sel: str) -> str | None:
    """Return None if valid, else a short error description."""
    if not isinstance(sel, str):
        return f"non-string ({type(sel).__name__})"
    if not sel.strip():
        return "empty/whitespace"
    # Balanced brackets
    if sel.count("[") != sel.count("]"):
        return f"unbalanced []: {sel.count('[')} open vs {sel.count(']')} close"
    if sel.count("(") != sel.count(")"):
        return f"unbalanced (): {sel.count('(')} open vs {sel.count(')')} close"
    # Mismatched single quotes inside attr selectors
    for attr in re.findall(r"\[([^\]]*)\]", sel):
        if attr.count("'") % 2:
            return f"unbalanced quotes in [{attr}]"
        if attr.count('"') % 2:
            return f"unbalanced double-quotes in [{attr}]"
    if _have_cssselect:
        # cssselect is CSS3-only; strip the CSS Selectors-4
        # case-(in)sensitivity flag (`[attr*='x' i]` / ` s]`) before
        # parsing, since browsers and Playwright both accept it.
        probe = re.sub(r"\s+[is]\s*\]", "]", sel)
        try:
            cssselect.parse(probe)
        except Exception as e:
            return f"cssselect: {str(e)[:120]}"
    return None


VALID_INPUT_TYPES = {
    "text", "password", "email", "tel", "url", "search", "number",
    "hidden", "submit", "button", "reset", "checkbox", "radio",
    "file", "date", "datetime-local", "time", "month", "week",
    "color", "range", "image",
}


def check_input_type(sel: str) -> str | None:
    """Catch input[type='X'] where X is not a real HTML input type."""
    m = re.search(r"input\[type=['\"]([^'\"]+)['\"]\]", sel)
    if m:
        t = m.group(1).lower()
        if t not in VALID_INPUT_TYPES:
            return f"non-existent input type 'type={t!r}' — browsers fall back to 'text', so this selector behaves like input[type='text'] and may match the wrong field"
    return None


HOST_RE = re.compile(r"^([a-z0-9]([a-z0-9-]*[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def check_host(host: str) -> str | None:
    if not isinstance(host, str) or not host.strip():
        return "empty/non-string host"
    h = host.strip().lower()
    if h != host:
        return f"host has whitespace or uppercase: {host!r}"
    if h.startswith(".") or h.endswith("."):
        return f"host has leading/trailing dot: {host!r}"
    if "//" in h or " " in h or "/" in h:
        return f"host looks like URL not bare hostname: {host!r}"
    if not HOST_RE.match(h):
        return f"host doesn't match dotted-hostname pattern: {host!r}"
    return None


def audit_login():
    print("=" * 72)
    print("LOGIN TEMPLATES AUDIT")
    print("=" * 72)
    print(f"  cssselect available: {_have_cssselect}")
    print(f"  total: {len(LOGIN_TEMPLATES)}")
    print()
    issues = defaultdict(list)  # tid -> list of bug strings
    seen_ids = Counter(t.get("id", "") for t in LOGIN_TEMPLATES)
    seen_hosts = Counter(t.get("host", "") for t in LOGIN_TEMPLATES)

    for t in LOGIN_TEMPLATES:
        tid = t.get("id", "<no id>")
        name = t.get("name", "")
        host = t.get("host", "")
        login = t.get("login") or {}

        # ID & host uniqueness
        if seen_ids[tid] > 1:
            issues[tid].append(f"duplicate id (appears {seen_ids[tid]} times)")

        # Host format
        herr = check_host(host)
        if herr:
            issues[tid].append(f"host: {herr}")

        # Name/host sanity: does the host contain at least one word from name?
        # (catches copy-paste errors where someone forgot to update the host)
        name_tokens = re.findall(r"[a-z0-9]+", name.lower()) if name else []
        name_tokens = [tok for tok in name_tokens if tok not in (
            "login", "the", "and", "or", "of", "com", "net", "xxx", "tv",
        )]
        if name_tokens and host:
            host_norm = host.lower()
            if not any(tok in host_norm for tok in name_tokens):
                issues[tid].append(
                    f"name/host mismatch: name {name!r} contains "
                    f"{name_tokens} but host {host!r} contains none"
                )

        for field_name in ("user_field", "pass_field", "submit_btn"):
            sels = login.get(field_name) or []
            for i, sel in enumerate(sels):
                err = check_selector(sel)
                if err:
                    issues[tid].append(
                        f"{field_name}[{i}] invalid CSS: {err}: {sel!r}"
                    )
                # Field-specific semantics
                if field_name == "user_field":
                    typ_err = check_input_type(sel)
                    if typ_err:
                        issues[tid].append(
                            f"{field_name}[{i}]: {typ_err}: {sel!r}"
                        )
                    # If a user_field selector explicitly targets password
                    # inputs, that's a bug
                    if "type='password'" in sel or 'type="password"' in sel:
                        issues[tid].append(
                            f"{field_name}[{i}] targets password input: {sel!r}"
                        )
                elif field_name == "pass_field":
                    if ("type='text'" in sel or 'type="text"' in sel
                            or "type='email'" in sel
                            or 'type="email"' in sel):
                        issues[tid].append(
                            f"{field_name}[{i}] doesn't target password input: {sel!r}"
                        )

        # Submit button — must not be obviously wrong
        for i, sel in enumerate(login.get("submit_btn") or []):
            # Submit selectors should NOT contain input[type='text'] etc
            if re.search(r"input\[type=['\"](text|email|password|tel)['\"]\]", sel):
                issues[tid].append(
                    f"submit_btn[{i}] targets a non-button input type: {sel!r}"
                )

    return issues, seen_ids, seen_hosts


def audit_download():
    print()
    print("=" * 72)
    print("DOWNLOAD TEMPLATES AUDIT")
    print("=" * 72)
    print(f"  total: {len(T.TEMPLATES)}")
    print()
    issues = defaultdict(list)
    seen_ids = Counter(t.get("id", "") for t in T.TEMPLATES)

    for t in T.TEMPLATES:
        tid = t.get("id", "<no id>")
        name = t.get("name", "")
        patterns = t.get("patterns") or []
        learned = t.get("learned") or {}

        if seen_ids[tid] > 1:
            issues[tid].append(f"duplicate id (appears {seen_ids[tid]} times)")

        if not tid:
            issues["<unnamed>"].append("missing id")
        if not name:
            issues[tid].append("missing name")

        # patterns should be valid regexes if non-empty
        for i, pat in enumerate(patterns):
            if not isinstance(pat, str):
                issues[tid].append(f"patterns[{i}] not a string")
                continue
            try:
                re.compile(pat)
            except re.error as e:
                issues[tid].append(
                    f"patterns[{i}] invalid regex: {e}: {pat!r}"
                )

        # learned.download.* selectors
        dl = (learned.get("download") or {}) if isinstance(learned, dict) else {}
        for key in ("row_selectors", "trigger_selectors",
                    "next_page_selectors", "title_selectors",
                    "image_selectors"):
            sels = dl.get(key) or []
            if not isinstance(sels, list):
                continue
            for i, sel in enumerate(sels):
                err = check_selector(sel)
                if err:
                    issues[tid].append(
                        f"learned.download.{key}[{i}] invalid: {err}: {sel!r}"
                    )

        # url_attribute: scalar string OR parallel list aligned with
        # row_selectors (v3.43.16 invariant; auto-heal in app.py:1963).
        ua = dl.get("url_attribute")
        rs = dl.get("row_selectors") or []
        attr_name_re = re.compile(r"^[a-zA-Z][a-zA-Z0-9_:-]*$")

        def _valid_attr(a):
            return isinstance(a, str) and (a == "" or attr_name_re.match(a))

        if ua is None:
            pass
        elif isinstance(ua, str):
            if ua and not attr_name_re.match(ua):
                issues[tid].append(
                    f"url_attribute invalid attribute name: {ua!r}"
                )
        elif isinstance(ua, list):
            if rs and len(ua) != len(rs):
                issues[tid].append(
                    f"url_attribute is a list of {len(ua)} entries but "
                    f"row_selectors has {len(rs)} — parallel arrays must "
                    f"align (v3.43.16 healer pads at front; this template "
                    f"will be healed at load but should be fixed at source)"
                )
            for i, a in enumerate(ua):
                if not _valid_attr(a):
                    issues[tid].append(
                        f"url_attribute[{i}] invalid attribute name: {a!r}"
                    )
        else:
            issues[tid].append(
                f"url_attribute wrong type ({type(ua).__name__}): {ua!r}"
            )

        # learned.login.* — same checks as login templates
        login_block = (learned.get("login") or {}) if isinstance(learned, dict) else {}
        for key in ("user_field", "pass_field", "submit_btn"):
            sels = login_block.get(key) or []
            if not isinstance(sels, list):
                continue
            for i, sel in enumerate(sels):
                err = check_selector(sel)
                if err:
                    issues[tid].append(
                        f"learned.login.{key}[{i}] invalid: {err}: {sel!r}"
                    )

    return issues, seen_ids


def fragility_scan_login():
    """Selectors that PASS structural validation but are at high
    risk of silently breaking. Reported separately from bugs."""
    print()
    print("=" * 72)
    print("LOGIN TEMPLATES — FRAGILITY SCAN (warnings, not bugs)")
    print("=" * 72)
    findings = defaultdict(list)
    # Heuristic for true hashed/build-generated class names:
    # • 5-8 chars
    # • At least 2 case flips inside (e.g. .joHetV: j-o-H-e-t-V → flips
    #   at positions 2 and 5 = 2 flips). Semantic English words rarely
    #   have more than one case flip (PascalCase has 0 internal flips,
    #   camelCase has 1).
    # • No common English bigram. Cheap proxy: no consecutive vowels
    #   AND no double consonants like 'tt', 'll', 'ss'.
    common_bigrams = re.compile(r"[aeiou]{2}|tt|ll|ss|nn|mm|rr|pp")
    class_pat = re.compile(r"\.[a-zA-Z][a-zA-Z]{4,7}\b")

    def case_flips(s):
        flips = 0
        for i in range(1, len(s)):
            if s[i - 1].isalpha() and s[i].isalpha() and (
                    s[i - 1].islower() != s[i].islower()):
                flips += 1
        return flips

    def looks_hashed(cls):
        if len(cls) < 5 or len(cls) > 8:
            return False
        if common_bigrams.search(cls.lower()):
            return False
        return case_flips(cls) >= 2

    too_generic = {".button", ".btn", "button.button", "input.button",
                   "button.btn"}
    for t in LOGIN_TEMPLATES:
        tid = t["id"]
        login = t.get("login") or {}
        for field, sels in login.items():
            for i, s in enumerate(sels or []):
                if not isinstance(s, str):
                    continue
                # Track unique hashed classes per selector (avoid
                # reporting the same class twice when a selector has
                # `.foo.bar` where both look hashed).
                reported = set()
                for h in class_pat.findall(s):
                    cls = h[1:]
                    if cls in reported:
                        continue
                    if looks_hashed(cls):
                        findings[tid].append(
                            f"{field}[{i}] uses likely-hashed class "
                            f"{h} — these change per CSS build: {s!r}"
                        )
                        reported.add(cls)
                if s in too_generic:
                    findings[tid].append(
                        f"{field}[{i}] is too generic ({s!r}) — "
                        f"may match unrelated buttons on the page"
                    )
    if not findings:
        print("  (nothing)")
    else:
        for tid, msgs in sorted(findings.items()):
            print(f"  {tid}:")
            for m in msgs:
                print(f"    • {m}")
    return findings


def main():
    li, lids, lhosts = audit_login()
    di, dids = audit_download()
    frag = fragility_scan_login()

    print()
    print("┌" + "─" * 70 + "┐")
    print("│ LOGIN TEMPLATES — FINDINGS" + " " * 43 + "│")
    print("└" + "─" * 70 + "┘")
    if not li:
        print("  (nothing)")
    else:
        for tid, bugs in sorted(li.items()):
            print(f"  {tid}:")
            for b in bugs:
                print(f"    • {b}")

    print()
    print("┌" + "─" * 70 + "┐")
    print("│ DOWNLOAD TEMPLATES — FINDINGS" + " " * 40 + "│")
    print("└" + "─" * 70 + "┘")
    if not di:
        print("  (nothing)")
    else:
        for tid, bugs in sorted(di.items()):
            print(f"  {tid}:")
            for b in bugs:
                print(f"    • {b}")

    print()
    print("=" * 72)
    print(f"SUMMARY")
    print(f"  login templates with issues:        {len(li)} / {len(LOGIN_TEMPLATES)}")
    print(f"  download templates with issues:     {len(di)} / {len(T.TEMPLATES)}")
    print(f"  login templates with fragility:     {len(frag)} / {len(LOGIN_TEMPLATES)}")


if __name__ == "__main__":
    main()
