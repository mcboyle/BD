"""THE PUBLISHER half of the shuffle lane's positive control. NOT A REAL TEST.

Deliberately order-dependent, and deliberately NOT named ``test_*.py`` so the
real suite never collects it: pytest applies its ``python_files`` pattern to
everything it walks, and only exempts a path handed to it explicitly on the
command line (``Session.isinitpath``). Both halves of this control are therefore
inert in ``pytest tests/`` and live only when a harness names them -- which
``toolchain/bin/bd-shuffle-lane --control`` does by COPYING them into a
temporary directory under ``test_*.py`` names, so that a directory-level
shuffled run collects both and is free to order them either way.

THE PLANTED DEFECT. This module publishes a token into ``os.environ`` that
``order_probe_beta.py`` requires. Run alpha-then-beta the pair PASSES; run
beta-then-alpha it FAILS. That is precisely the shape of backlog rows 491, 516
and 610 -- an earlier file in the same worker leaves state a later file depends
on -- reduced to two files and no I/O.

THE TOKEN IS SUPPLIED BY THE HARNESS, NOT HARDCODED, and the harness pops
``BD_SHUFFLE_CONTROL_TOKEN`` before launching. CLAUDE.md A7: an
environment-changing probe removes inherited values rather than merely declining
to set them. Without that, a ``BD_SHUFFLE_CONTROL_TOKEN`` left in the ambient
environment would let beta pass in EITHER order and the control would report a
lane that cannot fail as a lane that works.
"""
import os

EXPECT_VAR = "BD_SHUFFLE_CONTROL_EXPECT"
TOKEN_VAR = "BD_SHUFFLE_CONTROL_TOKEN"


def test_alpha_publishes_the_token():
    expected = os.environ.get(EXPECT_VAR)
    # A precondition, asserted rather than assumed (CLAUDE.md A7). If the
    # harness did not supply a token this probe must fail LOUDLY here, not
    # publish an empty string that beta then happily accepts.
    assert expected, (
        "BD-SHUFFLE-CONTROL: the harness did not set %s, so this probe would "
        "publish nothing and the control would be vacuous" % EXPECT_VAR)
    os.environ[TOKEN_VAR] = expected
    assert os.environ[TOKEN_VAR] == expected
