# Version- and row-pinned one-shots

These 20 scripts are hardcoded to a specific cut, row or investigation that is
over. They were moved out of ~ on 2026-08-31, not deleted: each one is still
the record of how that piece of work was actually driven.

They were moved because the flat ~ namespace IS the inventory a session reads
before deciding whether a tool already exists, and 14% of it named work that
finished days ago. Twice in this session that listing failed to surface the
tool that already did the job.

Verified before the move, on test5:
  - no other harness script references any of them, except bd-1241-chain.sh ->
    bd-1241-finish.py, and that pair moved together;
  - none was running. The first probe said every one of the twenty was running
    exactly once, which is the tell: a uniform count means the pattern matched
    the shell doing the matching. Re-probed anchored on the invocation
    (^bash /home/mboyle/<name>), the real count is zero.

To restore one: cp it back to /home/mboyle/ and to ../ so the archive drift
check in bd-persist/verify.sh stays green.
