"""ste-fix F4 (bug-hunt site-templates-editor, A3-A): cross_site_selectors.selector_shape pulled the
``:has-text(...)`` chunks out with re.sub(r":has-text\\([^)]*\\)"). On a selector with many ``:has-text(``
openers and no closing ``)`` every opener scanned to the end of the string before failing: quadratic,
20000 repeats (200 KB) took ~2.6 s. Learned/operator selectors feed it. The extraction is now one
left-to-right find() pass with the same matches.
"""

import random
import re
import subprocess
import sys
from pathlib import Path

from bulk_downloader import cross_site_selectors as css

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
_OLD = re.compile(r":has-text\([^)]*\)")


def _old_has_texts(s):
    return sorted(m.group(0).lower() for m in _OLD.finditer(s))


def test_unclosed_has_text_run_is_linear():
    # 160000 openers = 1.6 MB. Linear: a few ms. The old regex needs minutes; a
    # quadratic rescan built on str.find (memchr) still needs ~10 s. Run in a child
    # so a quadratic build is cut off at the wall cap instead of hanging the suite.
    code = (
        "import sys, time\n"
        "from bulk_downloader import cross_site_selectors as css\n"
        "sel = ':has-text(' * 160000\n"
        "t = time.monotonic(); css.selector_shape(sel)\n"
        "print(int((time.monotonic() - t) * 1000))\n"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "STE_F4_SELECTOR_SHAPE_SUPERLINEAR: >30 s for 160000 unclosed :has-text( openers"
        ) from None
    assert r.returncode == 0, r.stderr[-800:]
    ms = int(r.stdout.split()[-1])
    assert ms < 1000, (
        f"STE_F4_SELECTOR_SHAPE_SUPERLINEAR: {ms} ms for 160000 unclosed :has-text( openers"
    )


def test_has_text_extraction_matches_the_old_regex():
    rng = random.Random(1789)
    atoms = [
        ":has-text(",
        ")",
        "'Sign In'",
        "button",
        " ",
        "input[name=u]",
        ".joHetV",
        "#a1",
        ":HAS-TEXT(",
        "x",
    ]
    for _ in range(3000):
        s = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 12)))
        shape = css.selector_shape(s)
        want = _old_has_texts(s)
        got = [p for p in re.findall(r":has-text\([^)]*\)", shape)]
        assert sorted(got) == want, f"STE_F4_HAS_TEXT_CHANGED: {s!r} -> {shape!r}"


def test_known_shapes_unchanged():
    assert (
        css.selector_shape("button:has-text('Sign In')")
        == "button :has-text('sign in')"
    )
    assert css.selector_shape(
        'form.joHetV button:has-text("Log in") input[type=password]'
    ) == ('form button input[type=password] :has-text("log in")')
    assert (
        css.selector_shape("a:has-text(x):has-text(y) b")
        == "a b :has-text(x) :has-text(y)"
    )
    assert css.selector_shape("a:has-text(unclosed") == "a"
