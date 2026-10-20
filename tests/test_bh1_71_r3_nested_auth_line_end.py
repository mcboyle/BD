"""BH1-71 r3: a nested Authorization trigger at line end inside an Authorization value.

r2 (tree 09a533b9) re-scanned only token triggers inside an Authorization
value, so ``Authorization： authorization:`` + newline + X masked the first
line and served X on the next, which BASE masked (BASE saw only the nested
``authorization:``). The nested scan now takes an Authorization trigger there
too when its credential starts past the end of the line, in withhold_text and
in _withhold_except's kept-segment path alike, so every JSON response's
diagnostic_view masks it. The differential rows compare whole outputs against
the REAL BASE module (the r2 fixture, == 7a493091 egress_withhold.py).
"""
from __future__ import annotations

import random
import re
import time
import types
from pathlib import Path

import pytest

from bulk_downloader import egress_withhold as W

BD_GATE_SCOPE = "module"

C = "SECRETCANARY"
_BASE_PATH = Path(__file__).parent / "fixtures" / "bh1_71_r2" / "egress_withhold_base_10bd9c10.py.txt"


def _load_base():
    mod = types.ModuleType("bh1_71_r3_base_egress_withhold")
    exec(compile(_BASE_PATH.read_bytes(), str(_BASE_PATH), "exec"), mod.__dict__)
    return mod


B = _load_base()

# Served with SECRETCANARY on the r2 tree 09a533b9, masked at BASE.
LINE_END = {
    "fullwidth-colon": (f"Authorization： authorization:\n{C}", (), "Authorization： ***"),
    "dash": (f"Authorization - x Authorization:\n{C}", (), "Authorization - ***"),
    "newline-colon": (f"Authorization\n: x authorization:\n{C}", (), "Authorization\n: ***"),
    "em-dash-basic": (f"Authorization — Basic Authorization:\n {C}", (), "Authorization — ***"),
    "kept-bad-authorization": (f"bad Authorization: x authorization:\n{C}", ("bad Authorization:",),
                               "bad Authorization: ***"),
    "kept-other-fullwidth": (f"Authorization： x authorization:\n{C}", ("zzz",), "Authorization： ***"),
    "crlf": (f"Authorization: Basic x\r\nAuthorization:\r\n{C}", (), "Authorization: ***\r\nAuthorization:\r\n***"),
    "two-lines-down": (f"Authorization： a authorization:\nb authorization:\n{C}", (), "Authorization： ***"),
    # r2 correctness lens: the dash separator's value swallows the inner trigger.
    "corr-dash": (f"Authorization - Authorization:\n{C}", (), "Authorization - ***"),
    "corr-dash-lower": (f"authorization - authorization:\n{C}", (), "authorization - ***"),
    "corr-dash-kept-trigger": (f"Authorization - Authorization:\n{C}", ("Authorization -",), "Authorization - ***"),
    "corr-dash-kept-other": (f"authorization - authorization:\n{C}", ("zzz",), "authorization - ***"),
    "corr-dash-kept-word": (f"bad Authorization - Authorization:\n{C}", ("bad",), "bad Authorization - ***"),
}


def _view(text, keep):
    return W._withhold_except(text, keep) if keep else W.withhold_text(text)


@pytest.mark.parametrize("name", sorted(LINE_END))
def test_nested_authorization_at_line_end_is_masked(name):
    text, keep, want = LINE_END[name]
    got = _view(text, keep)
    assert C not in got, f"BH1-71r3 LINE-END AUTH LEAK {name}: {text!r} -> {got!r}"
    assert got == want


@pytest.mark.parametrize("name", sorted(LINE_END))
def test_nested_authorization_at_line_end_is_masked_in_diagnostic_view(name):
    text, keep, _ = LINE_END[name]
    got = W.diagnostic_view({"message": text, "data": {"error": [text]}}, keep)
    assert C not in repr(got), f"BH1-71r3 LINE-END AUTH LEAK diagnostic_view {name}: {text!r} -> {got!r}"


def test_base_masks_the_route_inputs():
    for name, (text, keep, _) in LINE_END.items():
        if name not in ("crlf", "two-lines-down"):
            base = B._withhold_except(text, keep) if keep else B.withhold_text(text)
            assert C not in base, name


def test_same_line_authorization_value_is_unchanged():
    """BASE serves the next line here; the tree masks it (stricter, not a
    regression); a nested Authorization on the same line changes nothing."""
    assert W.withhold_text(f"Authorization: x authorization:\n{C}") == "Authorization: ***"
    assert W.withhold_text(f"Authorization: x Authorization:\n{C}") == "Authorization: ***"
    assert W.withhold_text(f"Authorization: x authorization: y\n{C}") == f"Authorization: ***\n{C}"
    assert W.withhold_text(f"Authorization: Basic x\nnext line {C}") == f"Authorization: ***\nnext line {C}"
    assert W.withhold_text("the authorization was denied\nretry") == "the authorization was denied\nretry"


_OUTER = ["Authorization: ", "Authorization： ", "Authorization - ", "Authorization — ", "Authorization\n: ",
          "authorization= ", '"Authorization": "', "bad Authorization: ", "token: Authorization: ", "Bearer "]
_FILL = ["", "x ", "Basic ", "Bearer ", "a b ", "token ", "authorization ", "- ", "http://h/p?q=1 "]
_NESTED_AUTH = ["authorization:", "Authorization:", "authorization：", "Authorization -", "authorization =",
                "Authorization:\t", "Authorization:   ", "authorization​:", "authorization: x authorization:"]
_EOL = ["\n", "\r\n", "\n ", "\n\n", "\r", "\n\t"]
_KEEP = (("bad Authorization:",), ("Authorization:",), ("zzz",), ("x",), ("Basic",))


def _line_end_corpus(seed, n):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        s = rnd.choice(_OUTER) + rnd.choice(_FILL) + rnd.choice(_NESTED_AUTH) + rnd.choice(_EOL)
        for _ in range(rnd.randint(0, 2)):
            s += rnd.choice(_FILL) + rnd.choice(_NESTED_AUTH) + rnd.choice(_EOL)
        out.append((s + C + rnd.choice(["", " tail", "\nnext " + C, '"}']), rnd.choice(_KEEP)))
    return out


def _numbered(text):
    count = iter(range(10 ** 6))
    return re.sub(C, lambda _: f"{C}{next(count):04d}Q", text)


def _served(out):
    return set(re.findall(C + r"\d{4}Q", out))


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("view", ["withhold_text", "withhold_except"])
def test_tree_never_serves_what_base_withheld_at_line_end(seed, view):
    corpus = _line_end_corpus(seed, 4000)

    def run(mod, text, keep):
        return mod._withhold_except(text, keep) if view == "withhold_except" else mod.withhold_text(text)

    masked = [t for t, k in corpus if C not in run(B, t, k)]
    assert len(masked) >= 200, f"BASE-masked denominator too small: {len(masked)}"
    leaks = [(t, k) for t, k in corpus
             if not _served(run(W, _numbered(t), k)) <= _served(run(B, _numbered(t), k))]
    assert not leaks, (f"BH1-71r3 LINE-END AUTH LEAK vs BASE {view}/{seed} {len(leaks)}/{len(corpus)}: "
                       f"{leaks[0]!r} -> {run(W, *leaks[0])!r}")


def _best_ms(fn, text, runs=3):
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        fn(text)
        best = min(best, time.perf_counter() - t0)
    return best * 1000


@pytest.mark.parametrize("name,make", [
    ("auth-same-line", lambda n: "Authorization: " + "authorization: " * n),
    ("auth-glued-same-line", lambda n: "Authorization: " + "authorization:x" * n),
    ("auth-line-chain", lambda n: "Authorization: x" + " authorization:\n" * n),
    ("auth-line-chain-filled", lambda n: "Authorization: x authorization:\n" + "y " * 8 + "authorization:\n" * n),
    ("auth-trailing-ws", lambda n: "Authorization: authorization:" + " " * (16 * n)),
    ("auth-ws-no-sep", lambda n: "Authorization: " + "authorization    " * n),
    ("token-auth-chain", lambda n: "token: Authorization:\n" * n),
])
@pytest.mark.parametrize("view", ["withhold_text", "withhold_except"])
def test_nested_authorization_scan_is_linear_time(name, make, view):
    fn = W.withhold_text if view == "withhold_text" else (lambda t: W._withhold_except(t, ("x",)))
    n = 5000
    small, large = _best_ms(fn, make(n)), _best_ms(fn, make(4 * n))
    assert large < 8 * max(small, 0.5) and large < 2000, \
        f"BH1-71r3 SLOW {name}/{view}: N={small:.1f}ms 4N={large:.1f}ms"
