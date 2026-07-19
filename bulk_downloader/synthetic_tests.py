"""Synthetic test harness (Phase 136, Block Q).

Operators want to validate that BD's extractors still work against
a site's current HTML — without hitting the site (rate limit / cost
/ legal). This module ships:

  • Fixture format: HAR (HTTP Archive 1.2) JSON of a recorded visit
    to a target URL. Includes the response headers + body + (where
    applicable) replayed JS state.

  • Fixture replay: take a HAR file + a site template, walk the
    template's selector chain over the HAR body, and assert that
    each step finds something. Reports which selectors broke.

  • Assertions: each fixture pairs with a small assertions.json:
      {
        "expects": {
          "video_url": "https://cdn.example.com/v/123.mp4",
          "title": "/.*scene.*/i",
          "duration_seconds_min": 60,
          "performers_min_count": 1
        }
      }

  • Run-all: scan a directory of fixtures, run each, report pass/fail.

Use:
  • Add fixtures under INSTALL_DIR/fixtures/<site_id>/<name>.har +
    <name>.assertions.json
  • POST /api/fixtures/run_all → run every fixture, return per-site
    pass/fail breakdown
  • CI: a release isn't shippable if fixture pass rate drops

This module does NOT make HTTP requests. Pure local fixture replay.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Optional


def _fixtures_root() -> Path:
    try:
        from .constants import INSTALL_DIR
        return Path(INSTALL_DIR) / "fixtures"
    except Exception:
        return Path.cwd() / "fixtures"


def _load_har(path: Path) -> Optional[dict]:
    """Parse a .har file, return the dict or None on error."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        sys.stderr.write(f"[fixtures] HAR parse failed for {path}: {e}\n")
        return None


def _extract_html_responses(har: dict) -> list:
    """Pull out (url, status, body) tuples for text/html responses."""
    out = []
    for entry in (har.get("log", {}) or {}).get("entries", []):
        req = entry.get("request", {}) or {}
        resp = entry.get("response", {}) or {}
        content = resp.get("content", {}) or {}
        mime = content.get("mimeType", "")
        if "html" not in mime and "json" not in mime:
            continue
        body = content.get("text", "")
        if not body and content.get("encoding") == "base64":
            try:
                import base64
                body = base64.b64decode(content.get("text") or "").decode(
                    "utf-8", errors="ignore")
            except Exception:
                continue
        out.append({
            "url": req.get("url", ""),
            "status": resp.get("status", 0),
            "body": body,
            "mime": mime,
        })
    return out


def _check_selector(body: str, selector: str) -> dict:
    """Try a CSS selector against the HTML body using lxml.
    Falls back to XPath if cssselect isn't installed.
    Returns {ok, match_count, sample_text}."""
    try:
        from lxml import html as _lxml
    except ImportError:
        return {"ok": False, "error": "lxml not installed"}
    try:
        tree = _lxml.fromstring(body)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    # Prefer cssselect when available
    matches = None
    try:
        matches = tree.cssselect(selector)
    except ImportError:
        # cssselect missing — try interpreting the selector as XPath
        # (operator may have written XPath directly), or do a best-effort
        # transform for simple cases.
        try:
            xpath = _css_to_xpath(selector)
            matches = tree.xpath(xpath)
        except Exception as e:
            return {"ok": False,
                    "error": "cssselect not installed and selector "
                             "could not be auto-converted to XPath: " + str(e)[:120]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    if matches is None:
        return {"ok": False, "error": "no result"}
    sample = ""
    if matches:
        m0 = matches[0]
        if hasattr(m0, "text_content"):
            sample = (m0.text_content() or "").strip()[:120]
        elif isinstance(m0, str):
            sample = m0.strip()[:120]
        else:
            sample = str(m0)[:120]
    return {
        "ok": len(matches) > 0,
        "match_count": len(matches),
        "sample_text": sample,
    }


def _css_to_xpath(css: str) -> str:
    """Lightweight best-effort CSS→XPath converter for the common
    cases (tag, .class, #id). Anything fancier needs cssselect."""
    s = css.strip()
    if not s:
        raise ValueError("empty selector")
    # Simple tag
    import re
    if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", s):
        return f"//{s}"
    # .class
    if re.fullmatch(r"\.[a-zA-Z][\w-]*", s):
        cls = s[1:]
        return (f"//*[contains(concat(' ', normalize-space(@class), ' '), "
                f"' {cls} ')]")
    # #id
    if re.fullmatch(r"#[a-zA-Z][\w-]*", s):
        return f"//*[@id='{s[1:]}']"
    # tag.class
    m = re.fullmatch(r"([a-zA-Z][\w-]*)\.([a-zA-Z][\w-]*)", s)
    if m:
        tag, cls = m.group(1), m.group(2)
        return (f"//{tag}[contains(concat(' ', normalize-space(@class), ' '), "
                f"' {cls} ')]")
    # Unsupported — bail
    raise ValueError(f"unsupported selector for fallback: {s!r}")


def _check_assertions(extracted: dict, expects: dict) -> list:
    """Compare extracted values to assertions dict. Returns list of
    {key, op, expected, actual, passed}."""
    results = []
    for key, expectation in (expects or {}).items():
        if key.endswith("_min") or key.endswith("_max"):
            # numeric comparison
            real_key = key.rsplit("_", 1)[0]
            actual = extracted.get(real_key)
            try:
                actual_n = int(actual or 0) if isinstance(actual, (int, str)) \
                    else len(actual or [])
            except (TypeError, ValueError):
                actual_n = 0
            try:
                expected_n = int(expectation)
            except (TypeError, ValueError):
                results.append({"key": key, "op": "?",
                                "expected": expectation, "actual": actual,
                                "passed": False,
                                "error": "non-numeric expectation"})
                continue
            if key.endswith("_min"):
                passed = actual_n >= expected_n
            else:
                passed = actual_n <= expected_n
            results.append({"key": real_key, "op": "≥" if "_min" in key else "≤",
                            "expected": expected_n, "actual": actual_n,
                            "passed": passed})
        elif key.endswith("_count"):
            real_key = key[:-len("_count")]
            actual = extracted.get(real_key)
            actual_n = len(actual) if isinstance(actual, (list, tuple)) else 0
            try:
                expected_n = int(expectation)
            except (TypeError, ValueError):
                expected_n = 0
            results.append({"key": real_key, "op": "count=",
                            "expected": expected_n, "actual": actual_n,
                            "passed": actual_n == expected_n})
        elif isinstance(expectation, str) and \
             expectation.startswith("/") and expectation.endswith("/i"):
            # case-insensitive regex
            pattern = expectation[1:-2]
            actual = str(extracted.get(key, "") or "")
            try:
                passed = bool(re.search(pattern, actual, re.IGNORECASE))
            except re.error:
                passed = False
            results.append({"key": key, "op": "regex",
                            "expected": pattern, "actual": actual[:120],
                            "passed": passed})
        else:
            # exact equality
            actual = extracted.get(key)
            passed = actual == expectation
            results.append({"key": key, "op": "==",
                            "expected": expectation, "actual": actual,
                            "passed": passed})
    return results


class _JsonPathError(Exception):
    """A json_path step could not be resolved against the parsed body.

    Raised (not swallowed) so the caller can treat an unresolved path as
    template/extraction DRIFT and mark the fixture ok=False, the same way a
    CSS selector that no longer matches is treated.
    """


# token = a dotted key  OR  a [N] index  OR  a [*] wildcard
_JSON_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\*|-?\d+)\]")


def _json_path(obj, path: str):
    """Walk a parsed-JSON value by a compact path expression.

    Grammar (enough for fixture assertions, not a full JSONPath):
      * ``a.b.c``     — dotted dict keys
      * ``a[0].b``    — list index (negative allowed)
      * ``a[*].b``    — wildcard: map the remaining path over every list
                        element, returning a list

    Returns the resolved value (a scalar, or a list for a ``[*]`` branch).
    Raises ``_JsonPathError`` if any step does not resolve — callers turn
    that into a drift signal rather than a silent ``None``.
    """
    tokens = []
    for m in _JSON_PATH_TOKEN.finditer(path):
        key, idx = m.group(1), m.group(2)
        tokens.append(("key", key) if key is not None else ("idx", idx))
    if not tokens:
        raise _JsonPathError(f"empty json_path: {path!r}")
    return _json_path_walk(obj, tokens, path)


def _json_path_walk(obj, tokens, full):
    if not tokens:
        return obj
    (kind, val), rest = tokens[0], tokens[1:]
    if kind == "key":
        if not isinstance(obj, dict) or val not in obj:
            raise _JsonPathError(f"{full!r}: key {val!r} not in dict")
        return _json_path_walk(obj[val], rest, full)
    # index / wildcard
    if val == "*":
        if not isinstance(obj, list):
            raise _JsonPathError(f"{full!r}: [*] on non-list")
        return [_json_path_walk(el, rest, full) for el in obj]
    i = int(val)
    if not isinstance(obj, list) or i >= len(obj) or i < -len(obj):
        raise _JsonPathError(f"{full!r}: index {i} out of range")
    return _json_path_walk(obj[i], rest, full)


def _largest_json_body(responses: list) -> str:
    """Return the largest JSON response body among the HAR responses ('' if
    none). JSON fixtures (e.g. an /api/items or /spa/api/state capture) carry
    the real content here, distinct from any HTML shell."""
    js = [r for r in responses if "json" in (r.get("mime", "") or "")]
    if not js:
        return ""
    return max(js, key=lambda r: len(r.get("body", "") or "")).get("body", "") or ""


def _parse_json_body(body: str):
    """(parsed, err) — parse a JSON body string, returning the error string
    instead of raising so a malformed body becomes a reported failure."""
    if not body:
        return None, "empty json body"
    try:
        return json.loads(body), None
    except Exception as e:  # noqa: BLE001 — report, don't crash the run
        return None, f"json parse failed: {str(e)[:120]}"


def run_fixture(har_path: Path,
               assertions_path: Optional[Path] = None,
               *, site_cfg: Optional[dict] = None) -> dict:
    """Run one fixture. Returns {ok, site, fixture, selector_results,
    assertion_results, summary}."""
    out = {"fixture": har_path.name, "ok": False}
    har = _load_har(har_path)
    if not har:
        out["error"] = "HAR parse failed"
        return out
    responses = _extract_html_responses(har)
    out["responses"] = len(responses)
    if not responses:
        out["error"] = "no html/json responses in HAR"
        return out
    # Pick the response that looks like the main scene page
    # (largest HTML body). Fallback: first.
    main = max(responses, key=lambda r: len(r.get("body", ""))
               if "html" in r.get("mime", "") else 0)
    body = main.get("body", "")
    # Optional: run the site's selectors against it
    selector_results = []
    if site_cfg:
        for sel_key in ("video_selector", "title_selector",
                        "performers_selector", "tags_selector"):
            sel = site_cfg.get(sel_key)
            if not sel:
                continue
            r = _check_selector(body, sel)
            r["selector_key"] = sel_key
            r["selector"] = sel
            selector_results.append(r)
    # Assertions
    assertion_results = []
    extracted: dict = {}
    if assertions_path and assertions_path.exists():
        try:
            with assertions_path.open("r", encoding="utf-8") as f:
                ass = json.load(f)
            # (a) HTML: title pulled from the title_selector match
            for r in selector_results:
                if r["selector_key"] == "title_selector":
                    extracted["title"] = r.get("sample_text", "")
            # (b) JSON: walk json_path entries over the parsed JSON body.
            #     An unresolved path is left out of `extracted` (drift) so
            #     the paired `expects` then fails -> ok=False.
            json_path = ass.get("json_path")
            if json_path:
                jbody = _largest_json_body(responses)
                parsed, jerr = _parse_json_body(jbody)
                if jerr:
                    out["json_error"] = jerr
                else:
                    for ekey, jpath in json_path.items():
                        try:
                            extracted[ekey] = _json_path(parsed, jpath)
                        except _JsonPathError as je:
                            out.setdefault("json_path_unresolved", []).append(
                                {"key": ekey, "path": jpath, "why": str(je)[:120]})
            assertion_results = _check_assertions(extracted,
                                                  ass.get("expects", {}))
        except Exception as e:
            out["assertion_error"] = str(e)[:200]
    failed = ([r for r in selector_results if not r.get("ok")] +
              [r for r in assertion_results if not r.get("passed")])
    out.update({
        "ok": not failed,
        "selector_results": selector_results,
        "assertion_results": assertion_results,
        "extracted": extracted,
        "failed_count": len(failed),
    })
    return out


def run_all(*, s_cfg: Optional[dict] = None, root: Optional[Path] = None) -> dict:
    """Walk a fixtures root and run every .har it finds.
    Pairs each .har with its sibling .assertions.json (if present)
    and the matching site config from s_cfg.

    ``root`` defaults to INSTALL_DIR/fixtures; pass an explicit dir to replay
    a fixtures tree without an install (the sandbox canary smoke does this).
    """
    root = Path(root) if root is not None else _fixtures_root()
    out: dict = {"total": 0, "passed": 0, "failed": 0,
                 "per_site": {}, "started_at": time.time()}
    if not root.is_dir():
        return out
    for site_dir in sorted(root.iterdir()):
        if not site_dir.is_dir():
            continue
        sid = site_dir.name
        site_cfg = (s_cfg or {}).get(sid, {}) if s_cfg else {}
        per_site_pass = 0
        per_site_fail = 0
        per_site_results = []
        for har in sorted(site_dir.glob("*.har")):
            ass = har.with_suffix(".assertions.json")
            r = run_fixture(har, ass if ass.exists() else None,
                          site_cfg=site_cfg)
            r["site"] = sid
            per_site_results.append(r)
            out["total"] += 1
            if r.get("ok"):
                per_site_pass += 1
                out["passed"] += 1
            else:
                per_site_fail += 1
                out["failed"] += 1
        out["per_site"][sid] = {
            "passed": per_site_pass,
            "failed": per_site_fail,
            "results": per_site_results,
        }
    out["pass_rate"] = (out["passed"] / max(1, out["total"])) * 100
    out["completed_at"] = time.time()
    return out


def list_fixtures() -> list:
    """List all fixtures on disk. Useful for the operator UI."""
    root = _fixtures_root()
    if not root.is_dir():
        return []
    out = []
    for site_dir in sorted(root.iterdir()):
        if not site_dir.is_dir():
            continue
        for har in sorted(site_dir.glob("*.har")):
            ass = har.with_suffix(".assertions.json")
            out.append({
                "site": site_dir.name,
                "fixture": har.name,
                "path": str(har),
                "has_assertions": ass.exists(),
                "size_bytes": har.stat().st_size,
            })
    return out
