# Test artifact retention policy

This policy covers disposable test and capture artifacts. It does not govern
operator media or the keep-forever deletion rules in
`RETENTION_AND_TAKEDOWN_POLICY.md`.

Every removable population must expose an on-disk ownership marker and a
kernel-owned advisory lock. A marker is published atomically. A consumer that
cannot read the marker, cannot evaluate the lock, or cannot prove the object's
identity classifies the object as `UNKNOWN` and must not remove it.

## Decidable states

| State | On-disk decision | Retention decision |
| --- | --- | --- |
| `LIVE` | The ownership lock is held. | Never a candidate, regardless of age. |
| `KEPT_FOR_FORENSICS` | A terminal marker records a nonzero test exit. | Keep the newest 20 and keep every root younger than 24 hours. |
| `RECLAIMABLE` | A terminal marker records exit zero but immediate removal was refused. | Reclaim after the 24-hour age floor. |
| `ABANDONED` | A running marker has no held lock because the process ended without publishing a terminal outcome. | Keep the newest 20 and keep every root younger than 24 hours. |
| `UNKNOWN` | The marker is absent, malformed, contradictory, or the lock or identity cannot be evaluated. | Never remove; count and report it. |

`ABANDONED` deliberately has the same bound as `KEPT_FOR_FORENSICS`: a killed
or wedged run may be the artifact under investigation. A shorter bound would
recreate the loss this policy prevents.

## Enforcement

Bounds are enforced on the way into a test or capture workflow, including by
the harness that can kill one. A session-finish hook may perform immediate
cleanup, but it is not the retention owner because `SIGKILL` prevents it from
running.

Dry-run is the default for an operator-invoked cleanup tool. Destruction is
object-bound: the implementation must acquire and verify the object before it
uses a private no-clobber name and may never issue a recursive removal against
the public pathname. BulkDownloader delegates that operation to
`tools.safe_temp_remove.rename_verify_destroy`; it does not copy the primitive.

`tests._tmproot.install` publishes the `.bd-testrun` marker and holds
`.bd-testrun.lock`. `tests._tmproot.finish` publishes the terminal outcome.
The test-root sweeper interprets those facts; it does not infer liveness from a
directory modification time.
