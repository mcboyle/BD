<!-- verified-against: v3.66.185 -->
# #4 — Runtime artifacts vs shipped: the namelist rule

**The trap:** extract a release zip, run any app-booting test or the app in that dir, then diff it against a
clean tree — and you'll see "extra" files that look like the zip shipped scratch/secrets. It didn't. Those files
are **created by execution**, in cwd, at runtime.

## Files runtime creates (NOT in the zip)

`downloader_history.db` (+`-wal`/`-shm`), `sites_config.json`, `logs/`, `live_recordings/`, `screenshots/`,
`.integrity_check_last`, `.integrity_last_run`, `.fts_optimize_last`, `state/heartbeat.json`.

(These are exactly the names in `dev_suite._MANIFEST_EXCLUDE_*` — see #6 — because the builder deliberately keeps
them out.)

## The rule

> **Audit the zip's namelist, never a run directory.** A diff of `extracted-and-run` vs `clean` is meaningless;
> the namelist is ground truth for what ships.

```
# correct cleanliness check (operates on the archive, not a dir):
unzip -l <zip> | awk '{print $4}' \
  | grep -iE '__pycache__|\.pyc$|\.db($|-wal|-shm)|sites_config\.json|^logs/|/logs/|screenshots/|/state/|\.integrity|\.fts_optimize|\.wacz' \
  | grep -v 'frontend/node_modules'    # expect: no output
```

If you must work in an extracted dir, extract a **second, untouched** copy for diffing — or just trust the
namelist and move on.
