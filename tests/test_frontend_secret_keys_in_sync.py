"""RED-first guard for REDACT-SOT Cut 3 (D3): frontend secret-key sync.

The frontend must not hand-maintain secret-key sets that drift from the server.
frontend/src/lib/secretKeys.generated.ts is emitted from the server SoT by
tools/gen_frontend_secret_keys.py; this test fails if that file is missing or
stale (same enforcement pattern as PIN_INDEX / route_map baselines).

Pre-fix: the generated file does not exist -> RED.
Post-fix: file present and byte-identical to a fresh generation -> GREEN.

Also asserts the file actually carries the Cut-1/Cut-2 floor additions
(cookies / passphrase / preshared) that the old Vpn.tsx mirror missed, proving
the real client/server desync is closed.

Sandbox-safe: zero-arg, pure Python, no fixtures.
"""
import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_GEN_TOOL = _REPO / "tools" / "gen_frontend_secret_keys.py"
_OUT = _REPO / "frontend" / "src" / "lib" / "secretKeys.generated.ts"


def _load_tool():
    spec = importlib.util.spec_from_file_location("gen_fe_secret_keys", _GEN_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_file_exists():
    assert _OUT.exists(), (
        "frontend/src/lib/secretKeys.generated.ts is missing; run "
        "python tools/gen_frontend_secret_keys.py"
    )


def test_generated_file_in_sync_with_server():
    mod = _load_tool()
    expected = mod.generate()
    actual = _OUT.read_text()
    assert actual == expected, (
        "secretKeys.generated.ts is stale vs the server SoT; regenerate with "
        "python tools/gen_frontend_secret_keys.py"
    )


def test_generated_carries_floor_additions():
    # The concrete desync Cut 3 closes: the config floor terms the old Vpn.tsx
    # mirror missed must be present in the generated constants.
    text = _OUT.read_text()
    for term in ("cookies", "passphrase", "preshared"):
        assert f'"{term}"' in text, (
            f"generated secret-key constants missing floor term {term!r}"
        )


def test_generated_has_both_domains():
    text = _OUT.read_text()
    for sym in ("isSecretConfigKey", "isVpnSecretKey", "isUrlSecretKey",
                "CONFIG_SECRET_FLOOR", "URL_SECRET_SUBSTRINGS"):
        assert sym in text, f"generated file missing export {sym}"
