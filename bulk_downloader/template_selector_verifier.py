"""Resolve committed template selectors against a rendered page.

The verifier deliberately keeps three different facts separate:

* ``MALFORMED`` -- Playwright cannot parse the selector.
* ``MISS`` -- the selector parsed, but resolved to zero elements.
* ``UNKNOWN`` -- the template or subject could not be evaluated.

Saved HTML is rendered with all network requests blocked. URL subjects use
BD's canonical ephemeral browser backend and the same public-host guard used by
the selector playground. No result is called OK when its denominator is empty
or the subject/parser was unavailable.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_LOGIN_SELECTOR_KEYS = {"user_field", "pass_field", "submit_btn"}
_NODE_PROGRAM = r"""
const fs = require("fs");
const core = require(process.argv[1]);
const selectors = JSON.parse(fs.readFileSync(0, "utf8"));
const results = selectors.map((selector) => {
  try {
    core.iso.parseSelector(selector);
    return {ok: true, error: ""};
  } catch (error) {
    return {ok: false, error: String(error && error.message || error)};
  }
});
process.stdout.write(JSON.stringify(results));
"""


def _role(path: tuple[str, ...]) -> str:
    keys = set(path)
    if "trigger_selectors" in keys:
        return "trigger"
    if "row_selectors" in keys:
        return "row"
    if keys & _LOGIN_SELECTOR_KEYS:
        return "login"
    return "selector"


def enumerate_template_selectors(template: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every selector-bearing corpus value with its stable data path.

    The schema historically used both ``*_selectors`` fields and singular
    login fields. Walking both forms is intentional; silently auditing just
    the download block would create a false denominator.
    """
    template_id = str(template.get("id", "<missing-id>"))
    found: list[dict[str, Any]] = []

    def add(value: Any, path: tuple[str, ...]) -> None:
        found.append({
            "template_id": template_id,
            "path": ".".join(path),
            "selector": value,
            "role": _role(path),
        })

    def walk_grouped_selectors(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                walk_grouped_selectors(child, (*path, str(raw_key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk_grouped_selectors(child, (*path, f"[{index}]"))
        elif isinstance(value, str):
            add(value, path)

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key)
                child_path = (*path, key)
                if key == "selectors" and isinstance(child, dict):
                    walk_grouped_selectors(child, child_path)
                    continue
                if key in _LOGIN_SELECTOR_KEYS or "selector" in key.lower():
                    if isinstance(child, list):
                        for index, item in enumerate(child):
                            add(item, (*child_path, f"[{index}]"))
                    elif isinstance(child, str):
                        add(child, child_path)
                    elif child is not None:
                        add(child, child_path)
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, (*path, f"[{index}]"))

    walk(template, (template_id,))
    return found


def _playwright_parser_paths() -> tuple[Path, Path]:
    import playwright

    package = Path(playwright.__file__).resolve().parent
    node = package / "driver" / ("node.exe" if os.name == "nt" else "node")
    core = package / "driver" / "package" / "lib" / "coreBundle.js"
    if not node.is_file():
        raise FileNotFoundError(f"Playwright node runtime is absent: {node}")
    if not core.is_file():
        raise FileNotFoundError(f"Playwright selector parser is absent: {core}")
    return node, core


def parse_selectors(selectors: list[Any]) -> list[dict[str, Any]]:
    """Compile selectors with the exact parser used by Playwright locators."""
    results: list[dict[str, Any] | None] = [None] * len(selectors)
    string_indexes: list[int] = []
    strings: list[str] = []
    for index, selector in enumerate(selectors):
        if not isinstance(selector, str) or not selector.strip():
            results[index] = {
                "status": "MALFORMED",
                "error": "selector must be a non-empty string",
            }
        else:
            string_indexes.append(index)
            strings.append(selector)

    if strings:
        try:
            node, core = _playwright_parser_paths()
            run = subprocess.run(
                [str(node), "-e", _NODE_PROGRAM, str(core)],
                input=json.dumps(strings),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if run.returncode != 0:
                detail = (run.stderr or run.stdout or "parser exited nonzero").strip()
                raise RuntimeError(detail[:500])
            parsed = json.loads(run.stdout)
            if not isinstance(parsed, list) or len(parsed) != len(strings):
                raise RuntimeError("Playwright parser returned an unreconciled denominator")
            for index, result in zip(string_indexes, parsed):
                if result.get("ok") is True:
                    results[index] = {"status": "VALID", "error": ""}
                else:
                    results[index] = {
                        "status": "MALFORMED",
                        "error": str(result.get("error") or "selector parse failed"),
                    }
        except Exception as exc:
            error = f"selector parser unavailable: {type(exc).__name__}: {exc}"
            for index in string_indexes:
                results[index] = {"status": "UNKNOWN", "error": error[:700]}

    return [item for item in results if item is not None]


def _committed_template(template_id: str) -> dict[str, Any] | None:
    from .site_templates import TEMPLATES

    matches = [item for item in TEMPLATES if item.get("id") == template_id]
    return matches[0] if len(matches) == 1 else None


def audit_committed_selector_syntax() -> dict[str, Any]:
    """Pure static gate over every selector occurrence in committed templates."""
    from .site_templates import TEMPLATES

    entries = [
        entry
        for template in TEMPLATES
        for entry in enumerate_template_selectors(template)
    ]
    parsed = parse_selectors([entry["selector"] for entry in entries])
    rows = [{**entry, **result} for entry, result in zip(entries, parsed)]
    malformed = sum(row["status"] == "MALFORMED" for row in rows)
    unknown = sum(row["status"] == "UNKNOWN" for row in rows)
    checked = len(rows) - unknown
    if not rows or unknown:
        verdict = "UNKNOWN"
    elif malformed:
        verdict = "MALFORMED"
    else:
        verdict = "OK"
    return {
        "template_count": len(TEMPLATES),
        "selector_count": len(rows),
        "checked_count": checked,
        "malformed_count": malformed,
        "unknown_count": unknown,
        "verdict": verdict,
        "ok": verdict == "OK",
        "selectors": rows,
    }


def _unknown_report(
    template_id: str,
    entries: list[dict[str, Any]],
    subject: Any,
    error: str,
) -> dict[str, Any]:
    rows = [
        {
            **entry,
            "status": "UNKNOWN",
            "count": None,
            "initial_count": None,
            "error": error,
        }
        for entry in entries
    ]
    return {
        "template_id": template_id,
        "selector_count": len(rows),
        "verdict": "UNKNOWN",
        "ok": False,
        "subject": {
            "source": str(subject),
            "status": "UNKNOWN",
            "error": error,
        },
        "interaction": {
            "clicked": False,
            "click_count": 0,
            "selector": "",
            "error": "",
        },
        "selectors": rows,
    }


def _read_subject(subject: str | os.PathLike[str]) -> tuple[str, str, str]:
    """Return (kind, source, html); URL subjects have an empty html value."""
    source = os.fspath(subject)
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return "url", source, ""
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"saved HTML subject is not a file: {path}")
    raw = path.read_bytes()
    if not raw:
        raise ValueError(f"saved HTML subject is empty: {path}")
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"saved HTML subject is not UTF-8: {path}: {exc}") from exc
    if not html.strip():
        raise ValueError(f"saved HTML subject is blank: {path}")
    return "html", str(path.resolve()), html


def _route_public_request(route: Any) -> None:
    from .selector_playground import _host_public

    try:
        allowed, _reason = _host_public(route.request.url)
    except Exception:
        allowed = False
    if allowed:
        route.continue_()
    else:
        route.abort()


def _block_web_socket(web_socket: Any) -> None:
    """The HTTP route guard cannot validate WebSocket transport endpoints."""
    web_socket.close(code=1008, reason="template verifier blocks WebSockets")


def _serve_offline_document(page: Any, html: str, timeout_ms: int) -> None:
    """Fulfil one synthetic document and refuse every other request."""
    fixture_url = "https://bd-template-fixture.invalid/"
    served = False

    def route_offline(route: Any) -> None:
        nonlocal served
        request = route.request
        if (not served and request.resource_type == "document"
                and request.url == fixture_url):
            served = True
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=html,
            )
        else:
            route.abort()

    page.context.set_offline(True)
    page.route("**/*", route_offline)
    response = page.goto(
        fixture_url,
        wait_until="domcontentloaded",
        timeout=timeout_ms,
    )
    if response is None or response.status != 200 or not served:
        raise RuntimeError("saved HTML was not served from its supplied bytes")


def _measure(page: Any, rows: list[dict[str, Any]], field: str) -> None:
    for row in rows:
        if row["status"] == "MALFORMED":
            row[field] = None
            continue
        try:
            count = page.locator(row["selector"]).count()
        except Exception as exc:
            row["status"] = "MALFORMED"
            row["error"] = str(exc)[:700]
            row[field] = None
            continue
        row[field] = count
        row["count"] = count
        row["status"] = "HIT" if count else "MISS"


def _maybe_open_download(
    page: Any,
    rows: list[dict[str, Any]],
    timeout_ms: int,
) -> dict[str, Any]:
    interaction = {"clicked": False, "click_count": 0, "selector": "", "error": ""}
    row_controls = [
        row for row in rows
        if row["role"] == "row" and row["status"] != "MALFORMED"
    ]
    if not row_controls or any(
        (row.get("initial_count") or 0) > 0 for row in row_controls
    ):
        return interaction

    trigger_controls = [row for row in rows if row["role"] == "trigger"]
    if not trigger_controls:
        # Direct-link templates require no reveal interaction; zero is a real
        # observed count rather than an unavailable post-click state.
        return interaction
    triggers = [
        row for row in trigger_controls
        if row.get("initial_count") == 1
    ]
    if not triggers:
        interaction["error"] = "no uniquely resolved download trigger"
        return interaction

    chosen = triggers[0]
    try:
        page.locator(chosen["selector"]).click(timeout=timeout_ms)
        interaction.update({
            "clicked": True,
            "click_count": 1,
            "selector": chosen["selector"],
        })
        combined = None
        for row in row_controls:
            locator = page.locator(row["selector"])
            combined = locator if combined is None else combined.or_(locator)
        if combined is not None:
            try:
                combined.first.wait_for(state="attached", timeout=timeout_ms)
            except Exception:
                pass
    except Exception as exc:
        interaction["error"] = (
            f"trigger interaction failed: {type(exc).__name__}: {exc}"
        )[:700]
    return interaction


def _verdict(rows: list[dict[str, Any]]) -> str:
    statuses = {row["status"] for row in rows}
    if not rows or "UNKNOWN" in statuses:
        return "UNKNOWN"
    if "MALFORMED" in statuses:
        return "MALFORMED"
    if "HIT" in statuses:
        return "HIT"
    return "MISS"


def verify_template_source(
    template: str | dict[str, Any],
    subject: str | os.PathLike[str],
    *,
    timeout: float = 10.0,
    headless: bool = True,
    wait_for: str | None = None,
) -> dict[str, Any]:
    """Render ``subject`` and report count + HIT/MISS/MALFORMED per selector."""
    if isinstance(template, str):
        template_id = template
        template_data = _committed_template(template)
        if template_data is None:
            return _unknown_report(
                template_id,
                [],
                subject,
                f"unknown committed template id: {template_id}",
            )
    elif isinstance(template, dict):
        template_data = template
        template_id = str(template.get("id", "<missing-id>"))
    else:
        return _unknown_report(
            "<invalid-template>", [], subject, "template must be an id or mapping"
        )

    entries = enumerate_template_selectors(template_data)
    if not entries:
        return _unknown_report(
            template_id, entries, subject, "template has no selector denominator"
        )
    try:
        kind, source, html = _read_subject(subject)
    except Exception as exc:
        return _unknown_report(
            template_id, entries, subject, f"{type(exc).__name__}: {exc}"
        )

    if timeout <= 0:
        return _unknown_report(
            template_id, entries, subject, "timeout must be greater than zero"
        )
    timeout_ms = max(1, int(timeout * 1000))
    subject_info: dict[str, Any] = {
        "source": source,
        "kind": kind,
        "status": "OK",
        "error": "",
        "final_url": "",
        "networkidle": None,
    }

    if kind == "url":
        from .selector_playground import _host_public

        allowed, reason = _host_public(source)
        if not allowed:
            return _unknown_report(
                template_id, entries, subject, f"blocked URL subject: {reason}"
            )

    parsed = parse_selectors([entry["selector"] for entry in entries])
    rows = [
        {
            **entry,
            "status": result["status"],
            "count": None,
            "initial_count": None,
            "error": result["error"],
        }
        for entry, result in zip(entries, parsed)
    ]
    if any(row["status"] == "UNKNOWN" for row in rows):
        error = next(row["error"] for row in rows if row["status"] == "UNKNOWN")
        return _unknown_report(template_id, entries, subject, error)

    try:
        from .cloak import cloaked_page

        browser_config = {"browser_backend": "playwright"} if kind == "html" else None
        with cloaked_page(
            headless=headless,
            config=browser_config,
            context_options={"service_workers": "block"},
        ) as page:
            page.set_default_timeout(timeout_ms)
            if not hasattr(page, "route_web_socket"):
                raise RuntimeError("browser cannot guard WebSocket requests")
            page.route_web_socket("**/*", _block_web_socket)
            if kind == "html":
                _serve_offline_document(page, html, timeout_ms)
            else:
                page.route("**/*", _route_public_request)
                response = page.goto(
                    source,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                if response is None:
                    raise RuntimeError("URL navigation produced no response")
                if response.status >= 400:
                    raise RuntimeError(f"URL subject returned HTTP {response.status}")
                subject_info["final_url"] = page.url
                try:
                    page.wait_for_load_state(
                        "networkidle", timeout=min(timeout_ms, 5000)
                    )
                    subject_info["networkidle"] = True
                except Exception:
                    subject_info["networkidle"] = False
            if wait_for:
                page.locator(wait_for).first.wait_for(
                    state="attached", timeout=timeout_ms
                )

            _measure(page, rows, "initial_count")
            interaction = _maybe_open_download(page, rows, timeout_ms)
            _measure(page, rows, "count")
            if interaction["error"]:
                for row in rows:
                    if row["role"] == "row" and row["status"] == "MISS":
                        row["status"] = "UNKNOWN"
                        row["error"] = interaction["error"]
            if kind == "url" and subject_info["networkidle"] is False and not wait_for:
                for row in rows:
                    if row["status"] == "MISS":
                        row["status"] = "UNKNOWN"
                        row["error"] = (
                            "page readiness is unknown: networkidle was not reached; "
                            "repeat with --wait-for SELECTOR"
                        )
    except Exception as exc:
        return _unknown_report(
            template_id,
            entries,
            subject,
            f"subject could not be rendered: {type(exc).__name__}: {exc}"[:700],
        )

    verdict = _verdict(rows)
    return {
        "template_id": template_id,
        "selector_count": len(rows),
        "verdict": verdict,
        "ok": verdict == "HIT",
        "subject": subject_info,
        "interaction": interaction,
        "selectors": rows,
    }
