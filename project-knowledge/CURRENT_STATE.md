# Current-state overlay

`CURRENT_STATE.json` is a local, generated view of the currently checked-out
repository. Generate it on demand from the repository root:

```bash
python3 project-knowledge/build_current_overlay.py \
  --repo "$PWD" --out project-knowledge/CURRENT_STATE.json
```

Never commit `CURRENT_STATE.json`. The generator records the repository HEAD in
the overlay, while committing the overlay creates a new HEAD and necessarily
makes that recorded identity stale. The 2026-08-18 reproduction demonstrated
the complete two-commit cycle: the copied overlay reported `STALE` after the
first commit; regenerating it succeeded against that clean commit, and the
regenerated overlay again reported `STALE` immediately after the second commit.
The file is ignored and a repository-wide test forbids tracking it.
