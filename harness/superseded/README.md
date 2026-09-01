# Superseded harness tools

## bd-retrio.py -- retired 2026-08-31, folded into bd-rebase-cut.py

I wrote bd-retrio.py on 2026-08-31 to resolve the release-trio rebase
collision. bd-rebase-cut.py had done exactly that job since 2026-08-25, and had
three callers (bd-endgame.sh, bd-parallel-lane.sh, bd-pipeline.sh) to
bd-retrio's zero. This is the mistake CLAUDE.md A8 exists to prevent and the
second time this session that the flat ~ listing failed to surface a tool that
already existed.

ONE THING IT DID THAT THE OTHER COULD NOT, and it is kept: renumbering a PARKED
cut. bd-rebase-cut.py's resolve_trio assumed the cut's version is always the
higher one -- "the cut was numbered after the base it was frozen against, and
main has only moved forward since." That holds for a cut frozen from the tip
and fails for one that has been sitting: BD's v3.66.1374 candidate was parked
while main reached v3.66.1377, so taking its side would have walked the version
backwards with every internal assertion still passing.

bd-rebase-cut.py now takes --renumber, refuses an UNDECLARED backwards
renumber by name, and rewrites only the version token in the CHANGELOG header
while recovering the rest of the entry from the commit. Covered by
tests/test_rebase_cut_renumbers_a_parked_cut.py, whose third test replays the
same input against bd-rebase-cut.py.ORIGINAL-pre-renumber and requires it to
FAIL -- so the green above is attributable to the renumber support and not to
something else in the fixture.
