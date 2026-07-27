"""Live checks must only read response keys the endpoint actually produces.

A live check that reads a key the API never emits does not fail -- it silently
evaluates to a default, forever. `len(body.get("done") or [])` is `len([])` is
`0`, so the term contributes nothing and no test, no lint and no runtime error
says so. The check keeps reporting a number it never measured.

That is the CLAUDE.md 0 shape: the check asserts over a denominator that
structurally excludes its subject, so it reports cleanly and uselessly. The fix
pattern is to make the denominator contain the subject -- derive BOTH sides
from source and compare them -- rather than trusting either one in isolation.

Instrument note (CLAUDE.md 1): this uses AST, not grep, in both directions.
Grep would answer the wrong question here; `"done"` appears in checks.py in
three unrelated places (a status-name tuple and a counts lookup) that have
nothing to do with the /api/queue/v2 payload, so a string search reports
matches that are not the subject. The predicate is scoped to `.get(<literal>)`
calls on the name bound to the response body of a specific endpoint.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKS = REPO_ROOT / "live_tests" / "checks.py"
APP_QUEUE = REPO_ROOT / "bulk_downloader" / "app_queue.py"

ENDPOINT = "/api/queue/v2"
HANDLER = "api_queue_v2"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _handler_response_keys(tree: ast.Module, handler: str) -> set[str]:
    """Every literal key the handler can put in a jsonify({...}) payload."""
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == handler):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "jsonify":
                continue
            for arg in call.args:
                if isinstance(arg, ast.Dict):
                    keys.update(
                        k.value for k in arg.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    )
    return keys


def _body_names_for_endpoint(fn: ast.FunctionDef, endpoint: str) -> set[str]:
    """Names bound to a response body from ``ctx.get(endpoint, ...)``.

    The harness returns a 4-tuple ``(ok, status, body, elapsed)``; the body is
    element 2. Only that element is treated as a response body, so a status int
    or an ok flag is never mistaken for one.
    """
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if getattr(call.func, "attr", None) != "get":
            continue
        if not call.args:
            continue
        first = call.args[0]
        if not (isinstance(first, ast.Constant) and first.value == endpoint):
            continue
        for target in node.targets:
            if isinstance(target, ast.Tuple) and len(target.elts) >= 3:
                body = target.elts[2]
                if isinstance(body, ast.Name):
                    names.add(body.id)
    return names


def _keys_read_from(fn: ast.FunctionDef, body_names: set[str]) -> set[str]:
    """Literal keys read via ``<body>.get("key")`` for the given names."""
    keys: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "attr", None) != "get":
            continue
        owner = getattr(func, "value", None)
        if not (isinstance(owner, ast.Name) and owner.id in body_names):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                keys.add(value)
    return keys


def _checks_reading_endpoint(tree: ast.Module, endpoint: str):
    """Yield (function, body_names) for every check that calls the endpoint."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        names = _body_names_for_endpoint(node, endpoint)
        if names:
            yield node, names


def test_the_instrument_can_see_its_subject():
    """Guard the guard: prove both derivations are non-empty before comparing.

    An empty set on either side would make the subset assertion below pass
    vacuously -- the precise failure this file exists to prevent. If the
    harness's return shape or the handler's name ever changes, this fails
    loudly instead of certifying an empty comparison.
    """
    produced = _handler_response_keys(_parse(APP_QUEUE), HANDLER)
    assert produced, f"derived no response keys for {HANDLER} -- predicate is broken"

    consumers = list(_checks_reading_endpoint(_parse(CHECKS), ENDPOINT))
    assert consumers, f"derived no live check reading {ENDPOINT} -- predicate is broken"

    read = set()
    for fn, names in consumers:
        read |= _keys_read_from(fn, names)
    assert read, f"derived no keys read from {ENDPOINT} -- predicate is broken"


def test_live_checks_only_read_keys_the_queue_endpoint_emits():
    """Every key a check reads from /api/queue/v2 must exist in the payload.

    Regression: checks.py read ``body.get("done")`` while api_queue_v2 emits
    ``done_today_count``. ``len(body.get("done") or [])`` evaluated to 0 on
    every run, so L28's "total known URLs" silently omitted its third term --
    and worse, the omission turned a conserved quantity into a shrinking one.
    L28 asserts running+waiting+done is preserved across a service restart; a
    job completing mid-restart moves a URL from running to done, so with the
    done term stuck at 0 the totals legitimately fail to match and the check
    reports "queue lost 1 URL". A latent false FAIL, masked only because an
    empty queue makes the check WARN out before it gets there.
    """
    produced = _handler_response_keys(_parse(APP_QUEUE), HANDLER)

    offenders: dict[str, set[str]] = {}
    for fn, names in _checks_reading_endpoint(_parse(CHECKS), ENDPOINT):
        unknown = _keys_read_from(fn, names) - produced
        if unknown:
            offenders[fn.name] = unknown

    assert not offenders, (
        f"live checks read keys {ENDPOINT} never emits: "
        + "; ".join(f"{fn}() reads {sorted(keys)}" for fn, keys in offenders.items())
        + f"\nendpoint emits: {sorted(produced)}"
    )


@pytest.mark.parametrize("key", ["running", "waiting", "done_today_count"])
def test_the_three_terms_of_the_queue_total_are_all_real_keys(key):
    """Pin the specific keys L28's conservation invariant depends on.

    The subset test above would still pass if a future edit dropped the done
    term entirely rather than fixing it. This pins that all three terms exist,
    so silently deleting one is a failure rather than a simplification.
    """
    assert key in _handler_response_keys(_parse(APP_QUEUE), HANDLER)
