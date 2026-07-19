"""v3.66.766 -- runner_seams.UNITS is DERIVED from runner_struct, not hand-copied.

DERIVE-AUDIT (LOW, tool-to-tool hygiene): tools/runner_seams.py carried a full copy
of the curated method grouping with the comment "same curated grouping as
runner_struct.py (keep in sync)". runner_struct.py owns that grouping (its comment:
"EDIT if the method inventory changes"). Two hand-kept copies of the same OrderedDict
drift; both are advisory nav-aid tools, so the cost is only a wrong advisory grouping,
but the fix is free: import the one source. runner_struct is import-safe (its analysis
is under `if __name__ == "__main__"`), so loading it for UNITS has no side effects.

RED-first on pristine: runner_seams populates UNITS via `UNITS["..."] = {...}` subscript
assignments (test 1 fails).
"""
import ast
import importlib.util
import pathlib

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
SEAMS = TOOLS / "runner_seams.py"
STRUCT = TOOLS / "runner_struct.py"


def _load(p, name):
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_runner_seams_does_not_hand_populate_units():
    tree = ast.parse(SEAMS.read_text(encoding="utf-8"))
    subs = [n for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                    and t.value.id == "UNITS" for t in n.targets)]
    assert not subs, (
        "runner_seams hand-populates UNITS via %d subscript assignments -- derive it "
        "from runner_struct.UNITS (the source of truth) so it cannot drift." % len(subs))


def test_runner_seams_units_equals_runner_struct_units():
    seams = _load(SEAMS, "_seams_ut")
    struct = _load(STRUCT, "_struct_ut")
    assert dict(seams.UNITS) == dict(struct.UNITS), (
        "the derived grouping must equal runner_struct's -- behavior-neutral")
    assert list(seams.UNITS.keys()) == list(struct.UNITS.keys())
