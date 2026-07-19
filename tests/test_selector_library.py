"""RED-first tests for Cut 623 / C3: named-selector library.

A store of reusable, named selectors (``selector_library.json``) plus a resolver
that expands ``@lib:<name>`` references. Reference expansion is pass-through for
any value that isn't a live reference, so wiring it into the selector-materialize
path is byte-identical for every template that uses no references.

Sandbox-runner conventions: zero-arg, tempfile.mkdtemp, base_dir params, no
monkeypatch. Read/resolve paths never raise.
"""
from __future__ import annotations

import tempfile


def _base():
    return tempfile.mkdtemp(prefix="bdsellib_")


# ── CRUD ────────────────────────────────────────────────────────────────

def test_add_then_get_round_trips():
    from bulk_downloader import selector_library as L
    base = _base()
    ok, _ = L.add_named("dl_button", "button.download", description="std dl", base_dir=base)
    assert ok
    e = L.get_named("dl_button", base_dir=base)
    assert e["selector"] == "button.download"
    assert e["description"] == "std dl"


def test_add_rejects_bad_name_or_empty_selector():
    from bulk_downloader import selector_library as L
    base = _base()
    ok1, _ = L.add_named("", "button.x", base_dir=base)
    ok2, _ = L.add_named("has space", "button.x", base_dir=base)
    ok3, _ = L.add_named("ok_name", "", base_dir=base)
    assert not ok1 and not ok2 and not ok3


def test_list_and_remove():
    from bulk_downloader import selector_library as L
    base = _base()
    L.add_named("a", "div.a", base_dir=base)
    L.add_named("b", "div.b", base_dir=base)
    assert {x["name"] for x in L.list_named(base_dir=base)} == {"a", "b"}
    assert L.remove_named("a", base_dir=base) is True
    assert {x["name"] for x in L.list_named(base_dir=base)} == {"b"}
    assert L.remove_named("nope", base_dir=base) is False


# ── resolution / expansion ──────────────────────────────────────────────

def test_resolve_ref_expands_only_live_refs():
    from bulk_downloader import selector_library as L
    base = _base()
    L.add_named("dl", "button.download", base_dir=base)
    assert L.resolve_ref("@lib:dl", base_dir=base) == "button.download"
    # a plain selector passes through unchanged
    assert L.resolve_ref("div.plain", base_dir=base) == "div.plain"
    # an unknown ref passes through unchanged (never blanks a selector)
    assert L.resolve_ref("@lib:missing", base_dir=base) == "@lib:missing"


def test_expand_in_selectors_walks_and_expands_leaves():
    from bulk_downloader import selector_library as L
    base = _base()
    L.add_named("dl", "button.download", base_dir=base)
    L.add_named("row", "a.item", base_dir=base)
    src = {
        "trigger": "@lib:dl",
        "row_selectors": ["@lib:row", "div.literal"],
        "nested": {"x": "@lib:dl"},
        "kept": "unchanged",
    }
    out = L.expand_in_selectors(src, base_dir=base)
    assert out["trigger"] == "button.download"
    assert out["row_selectors"] == ["a.item", "div.literal"]
    assert out["nested"]["x"] == "button.download"
    assert out["kept"] == "unchanged"
    # input not mutated
    assert src["trigger"] == "@lib:dl"


# ── read paths never raise on empty store ───────────────────────────────

def test_reads_never_raise_on_empty_store():
    from bulk_downloader import selector_library as L
    base = _base()
    assert L.get_named("x", base_dir=base) is None
    assert L.list_named(base_dir=base) == []
    assert L.resolve_ref("@lib:x", base_dir=base) == "@lib:x"
    assert L.expand_in_selectors({"a": "@lib:x"}, base_dir=base) == {"a": "@lib:x"}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    p = f = 0
    for fn in fns:
        try:
            fn(); p += 1; print(f"  [PASS] {fn.__name__}")
        except Exception as e:
            f += 1; print(f"  [FAIL] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{p} passed / {f} failed")
    raise SystemExit(1 if f else 0)
