"""THE CONSUMER half of the shuffle lane's positive control. NOT A REAL TEST.

Read ``order_probe_alpha.py`` first; it carries this control's full rationale
and the reason neither file is named ``test_*.py``.

This module asserts the token alpha publishes. It FAILS when it runs first,
which is the whole point: it is the planted order dependency the shuffle lane
must be able to find. Its diagnostic carries the distinctive marker
``BD-SHUFFLE-CONTROL`` so a harness can prove the run failed FOR THE INTENDED
REASON and not because of a collection error, a missing plugin or an import
failure -- CLAUDE.md A5: when several refusals share an exit code, assert the
distinctive diagnostic.
"""
import os

EXPECT_VAR = "BD_SHUFFLE_CONTROL_EXPECT"
TOKEN_VAR = "BD_SHUFFLE_CONTROL_TOKEN"


def test_beta_requires_the_token():
    expected = os.environ.get(EXPECT_VAR)
    assert expected, (
        "BD-SHUFFLE-CONTROL: the harness did not set %s, so this probe could "
        "not tell a published token from an inherited one" % EXPECT_VAR)
    published = os.environ.get(TOKEN_VAR)
    assert published == expected, (
        "BD-SHUFFLE-CONTROL: order dependency observed -- %s is %r but this "
        "run needs %r, which order_probe_alpha.py publishes. beta ran before "
        "alpha." % (TOKEN_VAR, published, expected))
