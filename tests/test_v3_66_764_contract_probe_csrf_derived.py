"""v3.66.764 -- api_contract_probe must DERIVE its CSRF-exempt set, not re-type it.

DERIVE-AUDIT (HIGH): tools/api_contract_probe.py hand-kept
WHITELIST_CSRF_BYPASS = {"/api/pair/redeem", "/api/secrets/extension/pair"}.
The second entry is WRONG: /api/secrets/extension/pair is AUTH-exempt (_check_token,
app.py ~426) but CSRF-ENFORCED (_check_csrf returns 403). The hand-kept set conflated
the two, so this coverage probe SILENTLY SKIPPED a CSRF-enforced endpoint from its
"POST rejects missing CSRF" section -- a coverage gap the mirror created. Deriving from
bd_app.CSRF_EXEMPT_PATHS (the app's ONE declared exempt set) removes the mirror AND
closes the gap: the probe now tests that endpoint and expects 403.

RED-first on pristine: WHITELIST_CSRF_BYPASS is a set literal (test 1 fails).
"""
import ast
import pathlib

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "api_contract_probe.py"


def test_whitelist_csrf_bypass_is_derived_not_a_literal():
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "WHITELIST_CSRF_BYPASS":
                    assert not isinstance(n.value, (ast.Set, ast.List, ast.Dict, ast.Tuple)), (
                        "WHITELIST_CSRF_BYPASS is a hand-kept literal -- derive it from "
                        "bd_app.CSRF_EXEMPT_PATHS so it cannot drift from the app's guard "
                        "(and does not wrongly bypass a CSRF-enforced endpoint).")
    assert "CSRF_EXEMPT_PATHS" in TOOL.read_text(encoding="utf-8"), (
        "the probe must reference bd_app.CSRF_EXEMPT_PATHS")


def test_secrets_extension_pair_enforces_csrf_and_is_not_in_exempt_set():
    import bulk_downloader.app as A
    assert "/api/pair/redeem" in A.CSRF_EXEMPT_PATHS
    assert "/api/secrets/extension/pair" not in A.CSRF_EXEMPT_PATHS
    c = A.app.test_client()
    c.get("/")
    assert c.post("/api/secrets/extension/pair", json={}).status_code == 403, (
        "endpoint must enforce CSRF -- the whole reason it must not be whitelisted")
