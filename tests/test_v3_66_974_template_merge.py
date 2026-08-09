"""bd-template-merge: N captures of one site -> one master template.

@974, item 43. Operator chose FREQUENCY-RANKED, KEEP ALL, RECORD SUPPORT over
the two alternatives, and the rejected ones say why this shape:

  * union      cannot tell a selector seen in 4/4 captures from one seen in 1/4,
                so a one-off from a broken capture ranks equal to a universal;
  * intersection silently drops anything a single capture saw, which is exactly
                how a site's rarer page shape stops being covered -- and the
                template gives no signal that it happened.

So: every candidate survives, the highest-support one takes the CANONICAL
position, and the full ranked evidence lives in a sibling `merge` block with an
explicit denominator ("3 of 4 captures"), never a bare count.

THE CANONICAL SHAPE IS A CONTRACT, NOT A PREFERENCE. `template_normalize
.normalize_draft` reads `selectors.<group>.<leaf>` as a VALUE. Turning a leaf
into a ranked list to carry support would break the pipeline this cut exists to
feed, so support goes BESIDE the draft rather than inside its selectors. That is
asserted here by actually running the normalizer over the merged output, not by
reasoning that it should work.
"""

import copy
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-template-merge"


def _draft(host="ex.com", selectors=None, patterns=None, capture="c.wacz"):
    return {
        "source": {"capture_file": capture, "capture_sha256": "0" * 64,
                   "host": host, "url_no_query": f"https://{host}/v",
                   "origin": f"https://{host}"},
        "match": {"hosts": [host], "url_patterns": patterns or [f"{host}/v/*"]},
        "selectors": selectors or {},
        "network_discovery": {"resolutions_seen": [720]},
        "resolution_priority": [720],
        "workflow": {"auth": "manual_or_existing_profile"},
    }


def _write(tmp, name, obj):
    p = tmp / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def _run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, timeout=300)


def _merge(tmp, drafts, *extra):
    out = tmp / "merged.json"
    r = _run("--drafts", *drafts, "--out", str(out), *extra)
    return r, out


def test_the_tool_exists():
    assert TOOL.is_file(), "%s is absent" % TOOL


def test_a_SINGLE_draft_is_UNKNOWN_not_a_merge(tmp_path):
    """THE LOAD-BEARING REFUSAL. Merging one thing is not merging.

    If this returned a 'merged' template from one capture, every support count
    in it would read 1/1 -- indistinguishable from a universal selector, which
    is the exact confusion the frequency ranking exists to remove.
    """
    d = _write(tmp_path, "a.json", _draft())
    r, out = _merge(tmp_path, [d])
    assert r.returncode == 2, (
        "merging a single draft exited %d; it must be UNKNOWN, because a "
        "support count of 1/1 cannot be told from a universal: %s"
        % (r.returncode, r.stdout[-300:]))
    # Exit 2 ALONE is not evidence: python itself exits 2 for "can't open file",
    # so a missing tool would satisfy the assertion above. Caught on the RED run,
    # where this test passed while the tool did not exist. Require the tool's own
    # words.
    said = (r.stdout + r.stderr).lower()
    assert "unknown" in said and ("one" in said or "1" in said), (
        "exit 2 with no explanation -- indistinguishable from the interpreter "
        "failing to find the script: %r" % (r.stdout + r.stderr)[-300:])
    assert not out.exists(), "a template was written from one capture"


def test_MIXED_hosts_are_refused(tmp_path):
    """Merging two sites into one template is a category error, not a merge."""
    a = _write(tmp_path, "a.json", _draft(host="one.com"))
    b = _write(tmp_path, "b.json", _draft(host="two.com"))
    r, out = _merge(tmp_path, [a, b])
    assert r.returncode == 2, "mixed hosts were merged: %s" % r.stdout[-300:]
    assert "one.com" in (r.stdout + r.stderr) and "two.com" in (r.stdout + r.stderr), (
        "the refusal did not name the conflicting hosts, so an operator cannot "
        "tell which input was wrong: %r" % r.stdout[-300:])
    assert not out.exists()


def test_the_HIGHEST_support_selector_takes_the_canonical_slot(tmp_path):
    """The MINORITY value is fed FIRST on purpose.

    A mutation battery escaped this test in its original form: it put the
    majority value in the first draft, so "highest support" and "first seen"
    picked the same winner and the assertion could not tell them apart. A test
    whose two candidate rules agree on the fixture proves neither. Feeding the
    1-of-3 value first makes the two rules disagree, and only the correct one
    passes.
    """
    a = _write(tmp_path, "a.json", _draft(selectors={"download": {"btn": ".minority"}}))
    b = _write(tmp_path, "b.json", _draft(selectors={"download": {"btn": ".dl"}}))
    c = _write(tmp_path, "c.json", _draft(selectors={"download": {"btn": ".dl"}}))
    r, out = _merge(tmp_path, [a, b, c])
    assert r.returncode == 0, r.stdout + r.stderr
    m = json.loads(out.read_text())
    assert m["selectors"]["download"]["btn"] == ".dl", (
        "the canonical slot holds %r. The 2-of-3 value is '.dl'; '.minority' is "
        "merely the one seen FIRST, so this is first-appearance ordering rather "
        "than frequency ranking." % m["selectors"]["download"]["btn"])
    ranked = m["merge"]["selector_support"]["download.btn"]
    assert [x["value"] for x in ranked] == [".dl", ".minority"], (
        "the evidence block is not ordered by support either: %r" % ranked)


def test_a_MINORITY_selector_is_KEPT_not_dropped(tmp_path):
    """The operator's choice, and the whole difference from intersection.

    A selector seen in ONE capture of three is a site page shape the other two
    did not visit. Dropping it is how coverage silently narrows.
    """
    a = _write(tmp_path, "a.json", _draft(selectors={"download": {"btn": ".dl"}}))
    b = _write(tmp_path, "b.json", _draft(selectors={"download": {"btn": ".dl"}}))
    c = _write(tmp_path, "c.json", _draft(selectors={"download": {"btn": ".rare"}}))
    r, out = _merge(tmp_path, [a, b, c])
    m = json.loads(out.read_text())
    blob = json.dumps(m["merge"])
    assert ".rare" in blob, (
        "the 1-of-3 selector was DROPPED. Intersection semantics were "
        "explicitly rejected: %s" % blob[:400])


def test_every_support_count_STATES_ITS_DENOMINATOR(tmp_path):
    """A bare 2 is not a measurement. 2 of 3 is."""
    a = _write(tmp_path, "a.json", _draft(selectors={"download": {"btn": ".dl"}}))
    b = _write(tmp_path, "b.json", _draft(selectors={"download": {"btn": ".dl"}}))
    c = _write(tmp_path, "c.json", _draft(selectors={"download": {"btn": ".rare"}}))
    r, out = _merge(tmp_path, [a, b, c])
    m = json.loads(out.read_text())
    assert m["merge"]["capture_count"] == 3, m["merge"]
    cands = m["merge"]["selector_support"]["download.btn"]
    for c_ in cands:
        assert "support" in c_ and "of" in c_, (
            "a candidate carries a bare count with no denominator: %r" % c_)
        assert c_["of"] == 3, c_
    top = cands[0]
    assert top["value"] == ".dl" and top["support"] == 2, cands
    assert cands[1]["value"] == ".rare" and cands[1]["support"] == 1, cands


def test_the_MERGED_output_still_feeds_the_normalizer(tmp_path):
    """The contract, asserted by RUNNING it rather than by reasoning.

    normalize_draft reads selectors.<group>.<leaf> as a VALUE. If support were
    carried inside the selector tree instead of beside it, this is the test that
    would go red -- which is why it exists.
    """
    sys.path.insert(0, str(REPO))
    from bulk_downloader.template_normalize import normalize_draft
    a = _write(tmp_path, "a.json", _draft(selectors={"download": {"btn": ".dl"}}))
    b = _write(tmp_path, "b.json", _draft(selectors={"download": {"btn": ".dl"}}))
    r, out = _merge(tmp_path, [a, b])
    assert r.returncode == 0, r.stdout + r.stderr
    merged = json.loads(out.read_text())
    norm = normalize_draft(copy.deepcopy(merged))
    assert isinstance(norm, dict) and norm, "normalize_draft returned nothing"
    assert "selectors" in norm, (
        "the normalizer produced no selectors from the merged draft, so the "
        "merge broke the pipeline contract: %r" % list(norm)[:10])


def test_it_is_DETERMINISTIC(tmp_path):
    """Same inputs, same bytes. A merge whose tie-breaks wander produces a diff
    on every run and makes review meaningless."""
    a = _write(tmp_path, "a.json", _draft(selectors={"download": {"btn": ".x"}}))
    b = _write(tmp_path, "b.json", _draft(selectors={"download": {"btn": ".y"}}))
    r1, o1 = _merge(tmp_path, [a, b])
    first = o1.read_text()
    o1.unlink()
    r2, o2 = _merge(tmp_path, [a, b])
    assert o2.read_text() == first, "two runs over identical inputs disagreed"


def test_a_TIE_is_recorded_not_hidden(tmp_path):
    """1-1 is a real state and the operator should see it, not a coin flip
    presented as a winner."""
    a = _write(tmp_path, "a.json", _draft(selectors={"download": {"btn": ".x"}}))
    b = _write(tmp_path, "b.json", _draft(selectors={"download": {"btn": ".y"}}))
    r, out = _merge(tmp_path, [a, b])
    m = json.loads(out.read_text())
    assert m["merge"].get("ties"), (
        "an even split was resolved silently -- no tie was recorded: %r"
        % m["merge"])


def test_gold_merge_guard_BLOCKS_a_thinner_merge(tmp_path):
    """A rich reviewed gold must not be overwritten by a thin auto-merge.

    Reuses tools/build_template_from_wacz.gold_merge_guard rather than
    reimplementing the rule -- two copies of a guard is how they drift.
    """
    out = tmp_path / "merged.json"
    out.write_text(json.dumps({
        "template_status": "reviewed",
        "selectors": {"download": {"a": "1", "b": "2", "c": "3", "d": "4"}},
    }), encoding="utf-8")
    a = _write(tmp_path, "a.json", _draft(selectors={"download": {"btn": ".dl"}}))
    b = _write(tmp_path, "b.json", _draft(selectors={"download": {"btn": ".dl"}}))
    r = _run("--drafts", a, b, "--out", str(out))
    assert r.returncode == 1, (
        "a 1-leaf merge overwrote a 4-leaf reviewed template (exit %d)" % r.returncode)
    assert json.loads(out.read_text())["template_status"] == "reviewed", (
        "the existing reviewed template was overwritten")
    incoming = tmp_path / "merged.incoming.json"
    assert incoming.exists(), (
        "blocked, but the incoming draft was not written beside it for diff "
        "review, so the work is simply lost")
