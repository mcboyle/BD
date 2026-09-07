"""Row 672: bd-template-verify can name the REVIEWED template corpus.

The tool's positional resolved against ``site_templates.TEMPLATES`` and nothing
else. That corpus is keyed by generic ENGINE names -- ``video_js``,
``jw_player``, ``html5_native`` -- and carries no reviewed entry at all, so an
operator holding ``templates/reviewed/app.reptyle.com.template.json`` had no
way to point the verifier at it. This gate drives the CLI's REAL entry point
(``main(argv)``), never an internal helper.

The fail-open this cut could easily have introduced is the one it refuses: a
reviewed path that does not resolve must be REFUSED with its own diagnostic and
its own exit code, never allowed to fall through to the corpus, because a silent
fallback verifies a DIFFERENT template and reports it green.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-template-verify"
_REVIEWED = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"
_SUBJECT = _REPO / "tests" / "fixtures" / "row126" / "reptyle_download_modal.html"
_CORPUS_ID = "video_js"


def _tool():
    loader = importlib.machinery.SourceFileLoader("row672_template_verify", str(_TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _committed_ids() -> list[str]:
    from bulk_downloader.site_templates import TEMPLATES

    return [str(item.get("id")) for item in TEMPLATES]


def test_the_committed_corpus_cannot_name_the_reviewed_template():
    # THE PRECONDITION IS THE ROW'S OWN PROPERTY, not today's corpus size: the
    # reviewed template's host and its path are both absent from the committed
    # ids, so neither can be reached through the corpus resolver.
    assert _TOOL.is_file(), f"precondition: tool absent: {_TOOL}"
    assert _REVIEWED.is_file(), f"precondition: reviewed template absent: {_REVIEWED}"
    host = json.loads(_REVIEWED.read_text(encoding="utf-8"))["host"]
    ids = _committed_ids()
    assert len(ids) > 0, "precondition: the committed corpus is empty"
    assert host not in ids, host
    assert str(_REVIEWED) not in ids
    assert str(_REVIEWED.relative_to(_REPO)) not in ids
    # And the corpus IS reachable, so the denominator above is not vacuous.
    assert ids.count(_CORPUS_ID) == 1, ids.count(_CORPUS_ID)


def test_a_reviewed_template_path_verifies_end_to_end_through_the_cli(capsys):
    module = _tool()
    assert _SUBJECT.is_file(), f"precondition: recorded subject absent: {_SUBJECT}"
    rc = module.main([str(_REVIEWED), str(_SUBJECT), "--json", "--timeout", "30"])
    report = json.loads(capsys.readouterr().out)
    assert report["template_id"] == "app.reptyle.com", report["template_id"]
    assert report["verdict"] == "HIT", report["verdict"]
    assert report["selector_count"] == 19, report["selector_count"]
    assert report["match_count"] == 18, report["match_count"]
    assert report["ok"] is True
    assert rc == 0, rc


def test_a_reviewed_template_id_resolves_the_same_composed_template():
    module = _tool()
    by_id, id_refusal = module._resolve_template_argument("app.reptyle.com")
    by_path, path_refusal = module._resolve_template_argument(str(_REVIEWED))
    assert id_refusal == "" and path_refusal == ""
    assert by_id == by_path, (by_id, by_path)
    assert by_id["id"] == "app.reptyle.com"
    assert sorted(by_id["learned"]) == ["download", "login", "player"]


# --- negative controls ----------------------------------------------------


def test_a_plain_corpus_id_still_resolves_through_the_unchanged_path(capsys):
    module = _tool()
    # The resolver hands the corpus id STRAIGHT THROUGH as the same str the
    # tool passed before this row, so the downstream call is literally the
    # pre-cut call -- not a re-implementation of it.
    resolved, refusal = module._resolve_template_argument(_CORPUS_ID)
    assert (resolved, refusal) == (_CORPUS_ID, "")
    from bulk_downloader.site_templates import TEMPLATES
    from bulk_downloader.template_selector_verifier import (
        enumerate_template_selectors,
    )

    entry = next(item for item in TEMPLATES if item.get("id") == _CORPUS_ID)
    expected = len(enumerate_template_selectors(entry))
    assert expected > 0, "precondition: the corpus entry has no selectors"

    rc = module.main([_CORPUS_ID, str(_SUBJECT), "--json", "--timeout", "30"])
    report = json.loads(capsys.readouterr().out)
    assert report["template_id"] == _CORPUS_ID, report["template_id"]
    assert report["selector_count"] == expected, (report["selector_count"], expected)
    # The exit-code grammar this row extends is unchanged for the corpus path:
    # 0 HIT / 2 UNKNOWN / 1 otherwise, and never the new refusal code.
    assert rc == {"HIT": 0, "UNKNOWN": 2}.get(report["verdict"], 1), (
        rc, report["verdict"]
    )
    assert rc != module._REFUSED_EXIT, rc


def test_audit_corpus_is_unchanged_and_still_reports_every_committed_row(capsys):
    module = _tool()
    rc = module.main(["--audit-corpus", "--json"])
    report = json.loads(capsys.readouterr().out)
    from bulk_downloader.site_templates import TEMPLATES

    assert report["template_count"] == len(TEMPLATES), report["template_count"]
    assert report["selector_count"] > 0, report
    assert report["checked_count"] + report["unknown_count"] == report["selector_count"]
    assert report["verdict"] == "OK", report["verdict"]
    assert rc == 0, rc


def test_an_absent_reviewed_path_is_refused_and_never_falls_back_to_the_corpus(
    capsys, tmp_path
):
    module = _tool()
    missing = tmp_path / "absent.template.json"
    assert not missing.exists()
    rc = module.main([str(missing), str(_SUBJECT), "--json"])
    captured = capsys.readouterr()
    assert rc == module._REFUSED_EXIT, rc
    assert rc not in (0, 1, 2), rc
    assert f"reviewed template path is not a file: {missing}" in captured.err
    # NO report at all: a fallback to the corpus would have printed one, and a
    # green verdict about a DIFFERENT template is the fail-open this refuses.
    assert captured.out == "", captured.out


def test_a_malformed_reviewed_path_is_refused_with_its_own_diagnostic(
    capsys, tmp_path
):
    module = _tool()
    empty = tmp_path / "nogroups.template.json"
    empty.write_text(json.dumps({"host": "x.example"}), encoding="utf-8")
    rc = module.main([str(empty), str(_SUBJECT), "--json"])
    captured = capsys.readouterr()
    assert rc == module._REFUSED_EXIT, rc
    assert f"reviewed template carries no selectors block: {empty}" in captured.err
    assert "path is not a file" not in captured.err
    assert captured.out == "", captured.out

    broken = tmp_path / "unparseable.template.json"
    broken.write_text("{not json", encoding="utf-8")
    rc = module.main([str(broken), str(_SUBJECT), "--json"])
    captured = capsys.readouterr()
    assert rc == module._REFUSED_EXIT, rc
    assert f"reviewed template is unreadable: {broken}" in captured.err
    assert captured.out == "", captured.out


def test_a_reviewed_template_missing_a_selector_group_is_refused_not_composed(
    capsys, tmp_path
):
    module = _tool()
    partial = tmp_path / "partial.template.json"
    partial.write_text(
        json.dumps({"host": "y.example", "selectors": {"login": {"email": "#e"}}}),
        encoding="utf-8",
    )
    rc = module.main([str(partial), str(_SUBJECT), "--json"])
    captured = capsys.readouterr()
    assert rc == module._REFUSED_EXIT, rc
    assert f"reviewed template is missing a selector group: {partial}" in captured.err
    assert captured.out == "", captured.out


def test_row672_transform_control_import_only():
    """Imports the tool without resolving any argument through it.

    This is the band for ``row672_reviewed_template_is_reachable_transform_
    control.json``: a transform of the resolver that this test cannot observe
    MUST ESCAPE it.
    """
    module = _tool()
    assert callable(module.main)
    assert callable(module._resolve_template_argument)
    assert module._REFUSED_EXIT == 3


def test_a_name_that_is_both_a_corpus_id_and_a_reviewed_id_resolves_to_the_corpus(
    tmp_path, monkeypatch
):
    """The collision the row leaves open, decided and MEASURED.

    No name in the tree is both today, so the collision is built here rather
    than asserted from prose. THE COMMITTED CORPUS WINS a bare name: that is
    what the positional has always meant, and silently re-pointing a working
    invocation at a different template is the fail-open this cut exists to
    refuse. The reviewed template stays reachable by its PATH, which cannot
    collide, because a corpus id carries neither a separator nor a suffix.
    """
    module = _tool()
    collision = tmp_path / f"{_CORPUS_ID}{module._REVIEWED_SUFFIX}"
    collision.write_text(
        json.dumps({"host": _CORPUS_ID, "selectors": {
            "login": {"email": "#e", "password": "#p", "submit": "#s"},
            "player": {"container": "#c", "play_button": "#b"},
        }}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_REVIEWED_DIR", tmp_path)
    # PRECONDITION: the collision is real -- the bare name now resolves in BOTH
    # namespaces, so the order below is doing the work.
    assert module._reviewed_by_id(_CORPUS_ID) == collision
    assert module._corpus_carries(_CORPUS_ID) is True

    resolved, refusal = module._resolve_template_argument(_CORPUS_ID)
    assert (resolved, refusal) == (_CORPUS_ID, "")

    by_path, path_refusal = module._resolve_template_argument(str(collision))
    assert path_refusal == ""
    assert by_path["id"] == _CORPUS_ID
    assert by_path["learned"]["login"]["user_field"] == "#e"
