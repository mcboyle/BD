"""row127 residual (P4-DECIDED chunk04): SQLite's like(pattern, value) FUNCTION bypassed the
SELECT function allowlist (O1440/O1441) because "like" sits in _PAREN_WORDS for the infix
``x LIKE (...)`` operator. PG has no like() function, so a mirrored SELECT using it fails with
UndefinedFunction. The infix operator must keep translating.
"""

from __future__ import annotations

import pytest

from bulk_downloader.pg_backend import _shadow_dialect, translate

BD_GATE_SCOPE = "module"

FUNCTION_CALLS = [
    "SELECT like('%x%', url) FROM history",
    "SELECT like(?, url) FROM history",
    "SELECT id FROM history WHERE like(?, url)",
    "SELECT id FROM history WHERE NOT like(?, url)",
    "SELECT id FROM history WHERE a = 1 AND like(?, url)",
    "SELECT count(*), like(?, url) FROM history",
]

INFIX_OPERATORS = [
    "SELECT url FROM history WHERE url LIKE '%x%'",
    "SELECT url FROM history WHERE url LIKE (?)",
    "SELECT url FROM history WHERE url NOT LIKE (?)",
    "SELECT url FROM history WHERE h.url LIKE (?)",
    "SELECT url FROM history WHERE lower(url) LIKE (?)",
    "SELECT url FROM history WHERE 'a' LIKE (?)",
    "SELECT url FROM history WHERE ? LIKE (url)",
    "SELECT url FROM history WHERE url LIKE ? ESCAPE ?",
]


@pytest.mark.parametrize("sql", FUNCTION_CALLS)
def test_like_function_call_is_untranslatable(sql: str) -> None:
    assert translate(sql) is None, sql


@pytest.mark.parametrize("sql", FUNCTION_CALLS)
def test_like_function_call_is_skipped_by_shadow_dialect(sql: str) -> None:
    out, reason = _shadow_dialect(sql, ("%x%",))
    assert out is None and reason == "dialect-unknown:like", (sql, out, reason)


@pytest.mark.parametrize("sql", INFIX_OPERATORS)
def test_infix_like_operator_still_translates(sql: str) -> None:
    out = translate(sql)
    assert out is not None and "LIKE" in out, sql
    shadow, reason = _shadow_dialect(sql, ("%x%", "\\"))
    assert shadow is not None and reason is None, (sql, reason)


def test_control_unlisted_function_is_refused_and_literal_like_is_not_a_call() -> None:
    assert translate("SELECT round(1.5) FROM history") is None
    assert translate("SELECT url FROM history WHERE title = 'like(x)'") is not None
