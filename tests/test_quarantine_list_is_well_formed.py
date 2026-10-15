"""O3194 quarantine list contract: tests/quarantine.tsv rows are well formed and point at tests that exist."""
import os
import re

BD_GATE_SCOPE = "module"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _rows():
    with open(os.path.join(HERE, "quarantine.tsv"), encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line and not line.startswith("#"):
                yield line


def test_every_row_has_three_tab_fields_and_a_timestamp():
    rows = list(_rows())
    for line in rows:
        parts = line.split("\t")
        assert len(parts) == 3, line
        test_id, row, added = parts
        assert "::" in test_id and test_id.startswith("tests/"), test_id
        assert row.startswith("FF-") or row.startswith("TEST-ONLY "), row
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", added), added


def test_every_quarantined_test_file_exists_and_names_its_function():
    for line in _rows():
        test_id = line.split("\t")[0]
        path, _, tail = test_id.partition("::")
        assert os.path.exists(os.path.join(ROOT, path)), path
        func = tail.split("::")[-1].split("[")[0]
        with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
            assert re.search(rf"def {re.escape(func)}\(", fh.read()), test_id


def test_no_duplicate_ids():
    ids = [line.split("\t")[0] for line in _rows()]
    assert len(ids) == len(set(ids))
