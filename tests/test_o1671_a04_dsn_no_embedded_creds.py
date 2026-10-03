"""Configured credentials are required before either pgvector connection path runs."""
import sys
from types import SimpleNamespace

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from bulk_downloader import db_search, pg_vector

BD_GATE_SCOPE = "module"
MODULES = ((db_search, db_search.db_search_dsn), (pg_vector, pg_vector.pg_vector_dsn))


@pytest.fixture(autouse=True)
def clear_dsns(monkeypatch):
    monkeypatch.delenv("PGVECTOR_DSN", raising=False)
    monkeypatch.delenv("MOD3_PG_DSN", raising=False)


@pytest.fixture
def driver(monkeypatch):
    calls = []
    connection = object()

    def connect(dsn, **kwargs):
        calls.append((dsn, kwargs))
        return connection

    fake = SimpleNamespace(connect=connect, conninfo=SimpleNamespace(make_conninfo=make_conninfo))
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    return fake, calls, connection


@pytest.mark.parametrize("module,resolver", MODULES)
@pytest.mark.parametrize("value", [None, "   "])
def test_unconfigured_skips_connect(module, resolver, value, monkeypatch, driver):
    if value is not None:
        monkeypatch.setenv("PGVECTOR_DSN", value)
        monkeypatch.setenv("MOD3_PG_DSN", value)
    assert resolver() is None, "A04_UNCONFIGURED_DSN: unconfigured feature resolved a DSN"
    assert module.connect() is None
    assert driver[1] == [], "A04_IMPLICIT_CONNECT: unconfigured feature contacted driver"


@pytest.mark.parametrize("module,resolver", MODULES)
def test_configured_precedence_and_explicit_dsn(module, resolver, monkeypatch, driver):
    primary = "postgresql://configured:secret@127.0.0.1:5432/search?application_name=a04"
    secondary = "host=127.0.0.2 user=operator dbname=other"
    monkeypatch.setenv("MOD3_PG_DSN", secondary)
    assert resolver() == secondary
    monkeypatch.setenv("PGVECTOR_DSN", " " + primary + " ")
    assert resolver() == primary
    assert module.connect() is driver[2]
    assert driver[1] == [(primary, {"connect_timeout": 5})]
    assert module.connect(secondary) is driver[2]
    assert driver[1][-1] == (secondary, {"connect_timeout": 5})


@pytest.mark.parametrize("dsn", [
    "postgresql://configured:secret@127.0.0.2:6543/search?application_name=a04",
    "host=127.0.0.2 port=6543 user=configured password=secret dbname=search application_name=a04",
])
def test_fallback_preserves_configured_identity(dsn, monkeypatch, driver):
    fake, calls, connection = driver
    monkeypatch.setenv("PGVECTOR_DSN", dsn)

    def fail_first(target, **kwargs):
        calls.append((target, kwargs))
        if len(calls) == 1:
            raise OSError("configured database unavailable")
        return connection

    fake.connect = fail_first
    assert db_search.connect() is connection
    assert len(calls) == 2
    expected = conninfo_to_dict(dsn)
    expected["dbname"] = "postgres"
    assert conninfo_to_dict(calls[1][0]) == expected, "A04_FALLBACK_IDENTITY: fallback replaced configured identity"
    assert calls[1][1] == {"connect_timeout": 5}


@pytest.mark.parametrize("module,resolver", MODULES)
def test_explicit_failure_has_no_fallback(module, resolver, driver):
    fake, calls, _connection = driver
    dsn = "host=127.0.0.2 dbname=explicit user=operator"

    def fail(target, **kwargs):
        calls.append((target, kwargs))
        raise OSError("explicit connection unavailable")

    fake.connect = fail
    assert module.connect(dsn) is None
    assert calls == [(dsn, {"connect_timeout": 5})]
