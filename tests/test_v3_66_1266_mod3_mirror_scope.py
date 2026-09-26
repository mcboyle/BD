"""v3.66.1266 -- MOD3 history dual-write mirror scope vs PG schema (Row 127).

DEFECT (FINDING-ROW127-MIRROR-SCOPE-bd-pm-A-20260926T0335Z.md):
  pg_backend:53 _MIRRORED_VERBS = {INSERT, UPDATE, DELETE} and is_mirrored()
  tested the verb only, causing every DML statement across all ~70 SQLite
  tables to be mirrored to PostgreSQL. _PG_SCHEMA defines only 6 tables:
  captures, history, host_throughput, push_subscriptions, queue, and
  session_history. On the live daemon at 2026-09-26T03:27Z, periodic lock sweeps
  issued `DELETE FROM fed_url_locks WHERE expires_at < ?`, triggering
  UndefinedTable errors in Postgres, incrementing _stats.failed once per minute,
  and degrading the mirror.

  Second defect: bulk_downloader/_envfile.py filtered undeclared keys and logged
  `ignoring 1 undeclared key(s) in .env: MOD3_PG_DSN`.

CORRECTION:
  1. `_MIRRORED_TABLES` derived from `_PG_SCHEMA` at import.
  2. `is_mirrored(sql)` requires verb in `_MIRRORED_VERBS` AND target table
     in `_MIRRORED_TABLES`. Out-of-scope DML -> `_stats["skipped"] += 1`, no
     Postgres round-trip attempted.
  3. `_envfile.py` declares `_MOD3 = ("MOD3_PG_DSN", "MOD3_SHADOW_READ", "MOD3_CUTOVER")`
     in `SEEDABLE_KEYS` so dual-write / shadow-read / cutover switches can seed
     from `.env` without triggering undeclared key warnings.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))

import mod3_pg_isolation
from bulk_downloader import _envfile as EF
from bulk_downloader import pg_backend

_MODULE = "test_v3_66_1266_mod3_mirror_scope.py"

_EXPECTED_SCHEMA_TABLES = frozenset({
    "captures",
    "history",
    "host_throughput",
    "push_subscriptions",
    "queue",
    "session_history",
})


def _pg_available() -> tuple[bool, str]:
    """(available, dsn_or_reason)."""
    if not mod3_pg_isolation.real_dsn():
        return False, "no MOD3_PG_TEST_DSN in the environment"
    try:
        import psycopg
    except ImportError:
        return False, "psycopg not installed (optional dep)"
    dsn = mod3_pg_isolation.dsn_for(_MODULE)
    if not dsn:
        return False, "could not create isolated schema"
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            return True, dsn
    except (psycopg.Error, OSError) as e:
        return False, f"postgres unreachable: {type(e).__name__}"


# ── 1. Unit assertions: scope filtering & schema table derivation ─────────────

class TestMirrorScopeBoundary:
    def test_out_of_scope_tables_are_not_mirrored(self):
        """RED-first on 9224efccc552: is_mirrored previously returned True for
        DELETE FROM fed_url_locks."""
        # The live failure statement:
        assert pg_backend.is_mirrored(
            "DELETE FROM fed_url_locks WHERE expires_at < ?"
        ) is False
        # Additional SQLite-only tables outside _PG_SCHEMA:
        assert pg_backend.is_mirrored(
            "INSERT INTO alert_rules (rule_id, rule_json) VALUES (?, ?)"
        ) is False
        assert pg_backend.is_mirrored(
            "UPDATE api_auth_tokens SET last_used = ? WHERE token = ?"
        ) is False
        assert pg_backend.is_mirrored(
            "DELETE FROM app_config WHERE key = ?"
        ) is False

    def test_in_scope_schema_tables_are_mirrored(self):
        """Positive control: every table defined in _PG_SCHEMA stays mirrored."""
        assert pg_backend.is_mirrored(
            "DELETE FROM history WHERE id = ?"
        ) is True
        assert pg_backend.is_mirrored(
            "INSERT INTO queue (site_id, url) VALUES (?, ?)"
        ) is True
        assert pg_backend.is_mirrored(
            "INSERT INTO push_subscriptions (endpoint, subscription, ts) VALUES (?, ?, ?)"
        ) is True
        assert pg_backend.is_mirrored(
            "UPDATE session_history SET detail = ? WHERE id = ?"
        ) is True
        assert pg_backend.is_mirrored(
            "INSERT INTO captures (site_id, url, path, ts) VALUES (?, ?, ?, ?)"
        ) is True
        assert pg_backend.is_mirrored(
            "UPDATE host_throughput SET bytes = ? WHERE host = ?"
        ) is True

    def test_mirrored_tables_derived_from_pg_schema(self):
        """_MIRRORED_TABLES is dynamically derived from _PG_SCHEMA without a
        second hand-kept list."""
        assert pg_backend._MIRRORED_TABLES == _EXPECTED_SCHEMA_TABLES
        assert len(pg_backend._MIRRORED_TABLES) == 6

    def test_non_dml_is_not_mirrored(self):
        """SELECT, PRAGMA, CREATE, etc. remain SQLite-only."""
        assert pg_backend.is_mirrored("SELECT * FROM history") is False
        assert pg_backend.is_mirrored("PRAGMA table_info(history)") is False
        assert pg_backend.is_mirrored("CREATE TABLE IF NOT EXISTS temp(x)") is False


# ── 2. Real PostgreSQL integration: mirror exact-counts & degradation ─────────

class TestRealPGMirrorScope:
    def _skip_unless_pg(self) -> str:
        ok, why = _pg_available()
        if not ok:
            pytest.skip(f"REAL-PG mirror scope not verifiable here: {why}")
        return why

    def test_out_of_scope_skipped_and_in_scope_mirrored_exact_counts(
        self, monkeypatch
    ):
        """Exact-count assertion: after 1 out-of-scope + 1 in-scope statement:
        stats == {mirrored: 1, skipped: 1, failed: 0, degraded_reason: None}.
        Prove the fixture built the shape before any verdict.
        """
        dsn = self._skip_unless_pg()
        import psycopg

        monkeypatch.setenv("MOD3_PG_DSN", dsn)
        importlib.reload(pg_backend)

        # 1. Prove the fixture built the PG schema (table count nonzero)
        built = pg_backend.ensure_schema()
        assert built is True, "ensure_schema() failed to build Postgres schema"

        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
            row = cur.fetchone()
            assert row is not None
            table_count = row[0]
            assert table_count >= len(_EXPECTED_SCHEMA_TABLES), (
                f"expected at least {len(_EXPECTED_SCHEMA_TABLES)} tables in PG, "
                f"got {table_count}"
            )

        # 2. Reset counters
        with pg_backend._lock:
            pg_backend._stats["mirrored"] = 0
            pg_backend._stats["skipped"] = 0
            pg_backend._stats["failed"] = 0
            pg_backend._stats["degraded_reason"] = None

        # 3. Issue out-of-scope DML -> must be skipped, no PG round-trip, no failure
        out_res = pg_backend.mirror(
            "DELETE FROM fed_url_locks WHERE expires_at < ?", (1000000,)
        )
        assert out_res is False

        # 4. Issue in-scope DML -> must be mirrored, reach Postgres cleanly
        in_res = pg_backend.mirror(
            "DELETE FROM history WHERE id = ?", (999999999,)
        )
        assert in_res is True

        # 5. Exact count check
        st = pg_backend.stats()
        assert st == {
            "mirrored": 1,
            "skipped": 1,
            "failed": 0,
            "degraded_reason": None,
        }, f"unexpected stats state: {st}"


# ── 3. _envfile key declaration & loader warning behavior ─────────────────────

class TestEnvfileMod3Keys:
    def test_mod3_keys_declared_in_seedable_keys(self):
        """MOD3 keys must be part of SEEDABLE_KEYS so .env can configure them."""
        for key in ("MOD3_PG_DSN", "MOD3_SHADOW_READ", "MOD3_CUTOVER"):
            assert key in EF.SEEDABLE_KEYS, f"{key} missing from EF.SEEDABLE_KEYS"

    def test_envfile_seeds_mod3_pg_dsn_without_warning(
        self, monkeypatch, tmp_path, capsys
    ):
        """RED on 9224efccc552: load_envfile warned 'ignoring 1 undeclared key(s)
        in .env: MOD3_PG_DSN'. GREEN: it applies cleanly with zero stderr warnings."""
        for k in ("MOD3_PG_DSN", "MOD3_SHADOW_READ", "MOD3_CUTOVER"):
            monkeypatch.delenv(k, raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text(
            "MOD3_PG_DSN=postgresql://test_user:test_pass@localhost:5432/test_db\n"
            "MOD3_SHADOW_READ=1\n"
            "MOD3_CUTOVER=1\n",
            encoding="utf-8",
        )

        applied = EF.load_envfile(env_file)
        assert applied == 3

        err = capsys.readouterr().err
        assert err == "", f"expected clean stderr, got: {err}"
        assert os.environ.get("MOD3_PG_DSN") == "postgresql://test_user:test_pass@localhost:5432/test_db"
        assert os.environ.get("MOD3_SHADOW_READ") == "1"
        assert os.environ.get("MOD3_CUTOVER") == "1"

    def test_envfile_still_warns_on_actually_unknown_key_negative_control(
        self, monkeypatch, tmp_path, capsys
    ):
        """Negative control: genuinely undeclared keys are still reported."""
        monkeypatch.delenv("MOD3_PG_DSN", raising=False)
        monkeypatch.delenv("TOTALLY_UNKNOWN_ROGUE_KEY", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text(
            "MOD3_PG_DSN=postgresql://test_user:test_pass@localhost:5432/test_db\n"
            "TOTALLY_UNKNOWN_ROGUE_KEY=malicious_payload\n",
            encoding="utf-8",
        )

        applied = EF.load_envfile(env_file)
        assert applied == 1

        err = capsys.readouterr().err
        assert "ignoring 1 undeclared key(s)" in err
        assert "TOTALLY_UNKNOWN_ROGUE_KEY" in err
        assert "MOD3_PG_DSN" not in err
        assert "TOTALLY_UNKNOWN_ROGUE_KEY" not in os.environ
