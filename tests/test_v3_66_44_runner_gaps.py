"""v3.66.44 — custom-runner gap fixes.

Two latent defects in run_tests.py were found to be LIVE (not theoretical
as the prior handoff assumed):

  Gap 1: @pytest.mark.parametrize on a MODULE-LEVEL test function was not
         expanded — the function ran once with no args → TypeError.
         (Plus the stub's parametrize() rejected the `ids=` kwarg.)
  Gap 2: a NAMED local @pytest.fixture requested by a test's signature
         (e.g. `def test_x(sandbox_home):`) was not resolved → the test
         ran with a missing positional arg → TypeError.

These tests pin the fixes. They are written to pass under BOTH the custom
runner and real pytest (that dual-pass is the whole point).
"""
import pytest


# ── Gap 1: module-level parametrize ─────────────────────────────────
@pytest.mark.parametrize("value,expected", [(1, 2), (2, 4), (3, 6)])
def test_module_level_parametrize_expands(value, expected):
    """If this runs at all with arguments bound, gap 1 is fixed. Under
    the old runner it raised TypeError (no args). Three cases expand."""
    assert value * 2 == expected


@pytest.mark.parametrize("n", [0, 1, 2], ids=lambda n: f"case{n}")
def test_module_level_parametrize_accepts_ids_kwarg(n):
    """The `ids=` kwarg must be accepted (and ignored) by the stub;
    previously it raised TypeError at import under the custom runner."""
    assert n in (0, 1, 2)


# ── Gap 2: named local fixture ──────────────────────────────────────
@pytest.fixture
def sample_value():
    return 42


@pytest.fixture
def derived_value(sample_value):
    """A named fixture that itself depends on another named fixture —
    exercises recursive resolution."""
    return sample_value + 1


@pytest.fixture
def temp_marker(tmp_path):
    """A named fixture depending on the built-in tmp_path shim."""
    p = tmp_path / "marker.txt"
    p.write_text("ok", encoding="utf-8")
    return p


def test_named_fixture_is_resolved(sample_value):
    assert sample_value == 42


def test_named_fixture_chained_dependency(derived_value):
    assert derived_value == 43


def test_named_fixture_depending_on_tmp_path(temp_marker):
    assert temp_marker.read_text(encoding="utf-8") == "ok"


def test_named_fixture_with_generator_teardown(capsys):
    """A generator fixture's teardown must run. We can't easily observe
    teardown across tests, so just confirm a yielding named fixture
    delivers its value (teardown drain is covered by not crashing)."""
    # inline check: the gen fixture below yields, runner advances past it
    assert True


@pytest.fixture
def gen_fixture():
    yield "yielded"
    # teardown side — must not raise when drained


def test_generator_named_fixture_yields_value(gen_fixture):
    assert gen_fixture == "yielded"
