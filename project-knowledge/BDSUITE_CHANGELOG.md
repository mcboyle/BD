<!-- verified-against: v3.66.805 (bdsuite rev-810) -->
# bdsuite rev-810 -- THE STAGER THAT COULD NOT STAGE (+ two anchors and a provenance slot)

  * bd-kb-sync: `stage` CRASHED on any freshly-mounted /mnt/project. ZipInfo.from_file
    raises "ZIP does not support timestamps before 1980" and MEASURED @805, 345 of 365
    PK files carry 1979 mtimes -- so `stage` died mid-write and left a 22-BYTE
    TRUNCATED zip. Every session that edits the static PK hits this on the close path;
    it is the one command standing between a PK edit and a paste-ready bundle.
    FIX: write NORMALIZED 1980-01-01 timestamps via explicit ZipInfo (mode preserved,
    entries sorted). mtime is not part of the manifest -- it hashes CONTENT -- so
    normalizing costs nothing and makes the staged zip byte-reproducible for identical
    content. Verified: unzip -t clean, 365 entries, staged copy byte-identical to bin/.
    LESSON: a truncated artifact + a nonzero exit is the GOOD case. The same crash one
    line later would have produced a plausible, incomplete bundle.

  * bd-factcheck: two anchor fixes, both about SUBJECT rather than staleness.
    (1) toolchain_<N> keys are per-rev historical narrative ("ships in
    bdsuite_v3_66_702.zip, 228 tools") and are now excluded ENTIRELY -- not
    all-but-newest, because the newest such key tracks a PAST rev (728 while live is
    809+) so none is ever present-tense, AND because "tools" there means the bdsuite
    toolchain while the live denominator is work/tools/*.py (216): a subject mismatch.
    (2) changes_<N> present-tense is now anchored on ==built_version, not newest-key.
    The old premise was FALSE at 805 -- newest was changes_796 against built 3.66.805,
    nine releases stale and checked as present-tense -- and dormant only because
    changes_796 was an unauthored stub ("TODO(author): what this cut did") with NO
    entries at all for 797-805. Those nine were authored from the real CHANGELOG.
    Soundness-probed: an injected stale number FIRES in changes_805 and is SUPPRESSED
    in changes_799 and under toolchain_<N>.

  * bd-ratchet: skip "_"-prefixed keys in the check loop. A hand-declared floor needs
    its provenance stored WITH it (a sidecar is the written-to-one-channel/
    read-from-another shape), but a _meta block fell through to the None branch and
    printed "[skip] _meta (not measurable now)" -- provenance impersonating a metric
    that failed to collect.

  * bd-boot: the ratchet-baseline phase now PREFERS a carried declaration from the
    version pack over a live re-seed, and either path SAYS which it took. Previously
    the block ran only when the file was ABSENT -- i.e. exactly a fresh sandbox -- so
    an operator-declared ceiling was silently discarded and replaced by a cold measure
    every session. The carried branch warns that the file may hold HAND-DECLARED values
    and to read its _meta before citing anything as measured.

<!-- verified-against: v3.66.805 (bdsuite rev-809; byte-identical to the shipped bdsuite CHANGELOG.md) -->
# bdsuite rev-809 -- SHEBANG PARITY (the library that claimed to be a tool)

  * bdtools_cache.py shipped with an exec bit and NO shebang, while its three
    named siblings (bdtools_sec, bdtools_taint, bdtools_cli) all carry both.
    bd-selfcheck flagged it as "1 tool with a real defect" every run; the file
    itself self-describes as "A shared library ... NOT a tool", so the standing
    read was that the CHECK was mis-scoped. Measured @805: the file carries a
    __main__ block, so the exec bit is not vestigial -- direct invocation would
    misfire on the missing interpreter line. The check was RIGHT and the file
    was wrong; the docstring was the misleading party.

    FIX: prepend `#!/usr/bin/env python3`, matching the siblings. Applied to
    BOTH bin/ and the static PK in the same act, so bd-pk-mirror stays
    MIRROR-IDENTICAL (a one-sided edit is how the sidecar drifted a full
    version at 808). bd-selfcheck now exits 0: 249/249 structurally sound.

    LESSON (folded into KB_JUDGMENT as shape (g)'s neighbour): a file's own
    docstring is not authority about how the file is INSTALLED. The exec bit and
    the __main__ block are the facts; the prose was stale. Verify the mode, not
    the self-description.

# bdsuite rev-808 -- KBSYNC-CONTENT-PIN (the canon pin attested WHEN, not WHAT)

  * bd-kb-sync: write_manifest stamps a wall-clock `generated` INSIDE the sha'd
    body, so two seeds of an UNCHANGED tree hash differently -- MEASURED live at
    807: two stages 2s apart over the same /mnt/project produced 1bafb7f7... vs
    19d3094d..., sole differing byte the timestamp; the files dict byte-identical.
    The external canon pin (`static_kb_manifest_pin.sha256`) was therefore an
    attestation of WHEN the manifest was written, not WHAT it tracks -- the exact
    rev-806 bd-release-attestation defect, sitting in the canon gate. Failure
    direction is the INVERSE of the dominant shape: not blind but over-sensitive
    -- every re-derivation of identical content reports "the canon PK changed",
    training the operator to wave the gate through. Live cost at 807: two
    sessions independently reseeded the same 372-file set and got "different"
    pins (399e096c vs 08fd68df) that were the same content; a reconciliation was
    nearly run over a diff that did not exist.

    FIX: `_content_sha()` = sha256 over the sorted files dict, canonical json --
    WHAT is tracked. `generated`/`version_context` stay IN the file as metadata,
    OUTSIDE the attestation. cmd_pin records BOTH (`content_sha256` = the
    attestation; `sha256` = the exact bytes handed over, kept for provenance).
    _check_external_pin/_pin_verdict prefer the content pin; a legacy byte-pin
    still compares bytes and SAYS SO in the verdict line ("byte-pin, legacy --
    re-pin to upgrade") -- a silent fallback is how mirrors drift (R1). An
    unreadable manifest yields None -> fails closed, never "match".

    RED-first: CONTENT-PIN-DETERMINISM control (seed -> pin -> reseed unchanged
    1.1s later -> verdict must be match) proven FAILING on the pristine tool
    (verdict=mismatch), passing after. Control asserts on the VERDICT -- the
    consumer-visible subject -- not on _content_sha itself (the rev-807
    wrong-subject lesson). Paired NEG CONTENT-PIN-BITES: changed content must
    still mismatch (a pin that matches everything is a gate that cannot fire).
    Live-scale replay: reseed of a copy of the real pasted 372-file set -> byte
    sha moved (f3dd04bb != pinned 399e096c) while the verdict stayed `match`
    against the upgraded content pin (fbeaed19b9a9).

    Consumers audited before install: bd-boot kbsync phase (exit-code + text
    only; its freshness gate reads `generated`, which remains in the file),
    bd-consumer-graph (path listing), bd-handoff repin (copies the pin dict
    whole -- new key survives). Selftest 6/6 from the installed path.

# bdsuite rev-808 (cont) -- GATE-SOUND-REM: bd-mutation-test --only selector

  * VERIFY-THEN-ACT found the two owed mutation rows (bd-evidence/cross-view-
    consistency@753, bd-agent-watchdog/guard-set-folded@753) ALREADY PRESENT and
    each CAUGHT 1/1 in isolation -- the rows were sound. The residual was the
    RUNNER's --only selector, and it was the empty-denominator shape in the tool
    built to hunt it:
      - `--only` was a single-value arg: `--only A --only B` silently kept only B.
        MEASURED: `--only bd-evidence --only bd-agent-watchdog` ran ONLY watchdog
        and reported success, dropping the bd-evidence audit with no error.
      - `--only foo` matching NO row ran 0 rows and returned exit 0 ("0/0 caught").
        A mutation sweep that verifies nothing, reported as a pass.
    FIX: `--only` is action="append" (OR across substrings); any selector matching
    zero rows FAILS LOUD (rc=2, prints the unmatched selector + the known id list).
    RED-first via two subprocess controls driving the real CLI (SELECTOR-MULTI,
    SELECTOR-NOMATCH), proven failing on pristine (selected 1 / rc 0), passing
    after (selected 2 / rc 2). No --only callers exist in bin or tools (operator
    flag), so nothing downstream breaks; --list --json catalog surface (bd-golden
    gate) unchanged at 16. Deliverable: both rows run together, 2/2 CAUGHT.

# bdsuite rev-808 (cont) -- MIRROR RESIDUALS R2 + R3

  * R2 -- bd-repin-dist re-typed the runtime-artifact cleanup set (5 names +
    *.db globs) while the AUTHORITY is the tree's manifest-exclusion canon
    (dev_suite/release_lint.py: 23 names / 2 paths / 6 suffixes). Every canon
    addition since was invisible here: video_hashes.db (781), .premigration.bak
    (783), plugins/.plugin_state.json (798). WRONG IN THE SAFE DIRECTION --
    build_release's manifest gate turns leftovers into noise, never a dirty zip
    -- which is exactly why it drifted 20+ versions unnoticed.
    FIX: cleanup_canon() reads the canon BY AST (not import: release_lint uses
    package-relative imports, so standalone loading fails -- MEASURED, the first
    cut of this fix fell back silently and the control caught it; and executing
    app code to learn a constant is a side effect this tool has no business
    causing). Fallback to the old literals is retained but ANNOUNCED (R1
    precedent: a silent fallback is how the mirror drifted). Deliberately
    NARROWER than canon on .zip/.pyc/.pyo/.log -- .zip is build OUTPUT and
    deleting it would destroy the artifact being built -- stated, not silent.
    ALSO FOUND (undeclared): bd-repin-dist's selftest printed "SKIP: needs pip
    network access" AND "SELFTEST PASS", returning 0. The cleanup denominator
    needs no network; the blanket SKIP was the @756 kb-sync shape (a test that
    could exist but claims it cannot). Now 3 real controls + an honest SKIP
    scoped to the build/verify path only.

  * R3 -- bd-sym has TWO engines that answered DIFFERENT questions: rg searches
    every file minus node_modules/*.min.*, while the python fallback filtered to
    a re-typed extension tuple (.py/.ts/.tsx/.jsx/.js/.md/.json/.sh/.txt). On a
    host without rg, a symbol living in .html/.css/.yml/.toml/.service reported
    ZERO HITS -- and "no hits" is precisely what bd-sym is asked before retiring
    a symbol. RED replay on the pristine filter: a fixture with the token in
    a.py/page.html/conf.yml/unit.service returned ONLY a.py.
    FIX: exclusions declared ONCE (_EXCLUDE_DIRS/_BINARY_EXT), consumed by both
    engines; the fallback skips binaries by NUL-byte sniff rather than an
    extension allowlist (the guess is what went stale); the summary line now
    ANNOUNCES the engine, so "0 hits" is attributable. Control asserts
    cross-engine AGREEMENT (rg=4 py=4 on the fixture), skipping honestly if rg
    is absent rather than passing.
    SEVERITY CONFIRMED LOW, and a correction worth recording: rg IS present at
    /tmp/tools_bin/rg (media kit) and IS the live path -- `which rg` returns 127
    because the lookup is PATH-based while bd-sym resolves a hardcoded path list.
    An earlier claim in-session that "the fallback is the only path here" was
    wrong and was retracted on measurement.

  GATES: bd-tool-lint --gate 0 errors / 248 tools (critical-core 0/7 untested,
  corpus-guard 0/106 unguarded, when-coverage OK); bd-tool-smoke --gate CLEAN
  (240 scanned); all four edited tools selftest PASS from their INSTALLED paths.
  Tool count unchanged at 251 -- no net-new tool, so the BDSUITE_TOOL_BUDGET
  sidecar is untouched.

# bdsuite rev-808 (cont) -- FOOTGUNS.json: the SIDECAR blind spot

  * VERIFY-THEN-ACT, third strike: the @806 register records FOOTGUNS.json as
    live drift (PK 9d17bc74 / 65,323 b vs bin aa18c168 / 61,266 b). MEASURED
    @808: PK and bin are IDENTICAL (aa18c168, 61,266 b) -- the @807 PK cut
    already closed it. No content work was owed.
    What IS owed is the gate hole underneath. _is_source deliberately excludes
    dotted non-.py files so widening cannot sweep in data/docs (correct, and
    pinned by a NEG), but FOOTGUNS.json ships in bin/ AND is mirrored in PK --
    a mirror by any honest reading. It drifted for a full version and this gate
    reported clean every run, truthfully and uselessly.
    FIX: a SECOND, narrower class -- _is_sidecar_mirror = dotted, non-.py, and
    present in bin/. New verdicts SIDECAR-DRIFTED / SIDECAR-IDENTICAL, gated in
    --check alongside MIRROR-DRIFTED.
    The COLLISION the @804 and @806 registers said blocked this ("README.md is
    24,834 b in PK vs 533 b in the tree -- a wider glob trades a blind spot for
    a false positive") is DISSOLVED, not adjudicated: sidecars match against
    bin/ ONLY, never the work tree, and README.md is not in bin/ at all
    (measured). A PK doc with no bin twin is never a sidecar, so there is
    nothing to get wrong. The NEG pins it: README.md planted in pk+work must
    stay unreported, and did -- on the RED run too, which is what proves the
    fix bought coverage without buying a false positive.
    SCOPE CORRECTION (in-session): an earlier claim that 7 PK files were
    invisible was WRONG -- the six bdtools_*.py / bd-triage.py / bd-audit-gate.py
    files already qualify via _is_source's .py fast path and were always
    checked. The blind spot was exactly ONE file: FOOTGUNS.json.
    Live after fix: SIDECAR-IDENTICAL=1, MIRROR-IDENTICAL=266, PK-ONLY=22,
    MIRROR-DRIFTED=4 (the four tools this rev edited, pending the PK paste).

# bdsuite rev-808 (cont) -- bd-golden I-7: the recorder could execute mutators

  * VERIFY-THEN-ACT on "cover bd-golden's 47 uncovered gate-shaped tools" found
    a LIVE HAZARD that blocks the extension and outranks it.
    bd-golden EXECUTES every population member (`--work <tree> --json`) at
    --record time and NEVER consulted the canonical NEVER_EXEC list. MEASURED:
    its 112-tool population contains THREE NEVER_EXEC mutators --
      bd-mutation-test  safe only BY ACCIDENT (a _SNAPSHOT_ARGS override)
      bd-deep-capture   UNPROTECTED -- --record drives real capture
      bd-fullsuite      UNPROTECTED -- --record runs the FULL test suite
    bd-tool-lint learned exactly this at I-7 and imports the list from bd-sweep
    fail-closed; bd-golden kept no copy at all. A recorder that mutates the tree
    it is snapshotting is the "linter that destroys your session by linting".
    FIX: never_exec() imports the canonical set from bd-sweep (I-9: one source,
    no second copy) and FAILS CLOSED via a _BlockAll sentinel whose __contains__
    is True for everything -- if the list cannot be loaded or is empty, NOTHING
    is executed. Proven live: running the tool from a directory without an
    adjacent bd-sweep refused every tool, which is the correct direction.
    exec_plan() refuses NEVER_EXEC members UNLESS they declare a deterministic
    catalog invocation in _SNAPSHOT_ARGS -- the override is the justification,
    not an exception. Live result: population 112 unchanged, now-blocked =
    [bd-deep-capture, bd-fullsuite], bd-mutation-test still covered.
    RED-first: 4 controls (LOADED / BLOCKS / OVERRIDE / NARROW) proven failing
    on pristine (NameError: never_exec undefined), passing after.

  * THE 47 REMAIN UNCOVERED, DELIBERATELY, and the scoping is now measured:
    - 8 of the 47 are chaos/proof tools (bd-capture-chaos, bd-db-chaos,
      bd-netns-proof, bd-network-chaos, bd-plugin-chaos, bd-proof-ledger,
      bd-queue-chaos, bd-scrub-proof) and only ONE of the 47 (bd-sweep) is in
      NEVER_EXEC today. Auto-invoking the rest would inject faults against the
      live tree and services. The NEVER_EXEC list needs extending FIRST, and
      that is an operator ruling about what is unsafe to run, not a code change
      I should make unilaterally.
    - The corpus contract is heterogeneous but DERIVABLE: of a 20-tool sample,
      15 declare no corpus flag at all, 3 take --home, 1 --corpus, 1 --root.
      A derived-flag invocation (extending the --work/--tree filter to the flag
      each tool actually declares, no-corpus tools invoked bare) covers them
      WITHOUT a hand-typed 47-entry registry.
    Sequence is therefore: (1) I-7 boundary [DONE, this rev], (2) operator
    ruling on extending NEVER_EXEC to the chaos/proof set, (3) derived-flag
    population widening. Doing (3) before (2) would have run chaos injectors.

# bdsuite rev-808 (cont) -- the 47: NEVER_EXEC ruling + derived replay contract

  * OPERATOR RULING (all 8): the fault injectors and proof harnesses join
    NEVER_EXEC in bd-sweep (canonical, 30 -> 38): bd-capture-chaos, bd-db-chaos,
    bd-network-chaos, bd-plugin-chaos, bd-queue-chaos, bd-netns-proof,
    bd-scrub-proof, bd-proof-ledger. Ruled as a set rather than adjudicating the
    three proof harnesses individually -- bd-netns-proof creates network
    namespaces, and being wrong about a "read-only verifier" costs a damaged
    session while being over-broad costs a visibly uncovered tool.
    Uncovered-and-visible beats covered-by-detonation. bd-golden inherits this
    automatically (single source, I-9). Downstream verified: bd-tool-lint --gate
    unchanged at 0 errors / 126 warnings, critical-core 0/7, corpus-guard 0/106
    -- extending the list did NOT shrink the linter's denominator.

  * THE 47, CLOSED. candidates() hard-filtered the population to --work/--tree,
    so a gate whose only difference was a corpus flag (--root/--home/--corpus)
    or no corpus at all fell into an opaque bucket. The flag a tool accepts is a
    property of its declared interface, so DERIVE it -- the way
    declares_json_arg() already derives the --json contract -- rather than
    hard-coding two names or hand-typing a 47-entry registry.
    corpus_invocation() returns the argv fragment for the first _CORPUS_FLAGS
    entry the tool declares, and [] for a flagless tool (invoked BARE, not
    handed a flag it would reject -- that would surface as a tool failure, i.e.
    false drift, rather than as "takes no corpus").
    Population 112 -> 159. The remaining "uncovered" bucket is now 83 tools with
    NO --json surface at all, and the label was corrected to say so: it used to
    read "no --work/--tree replay contract", which after the widening described
    a denominator that no longer existed.
    NOTE: coverage is now POSSIBLE, not PRESENT -- goldens do not survive a
    session boundary (ok 0 | unknown 159 | recorded 0 in a fresh sandbox), and
    --record is an operator action against a tree worth pinning.

  * SELF-INFLICTED, CAUGHT BY THE CONTROL: the first cut of the uncovered()
    rewrite left `if not declares_json_arg(src): continue` followed by
    `if not declares_json_arg(src): out.append(t)` -- a dead branch, so the
    function could only ever return []. An empty denominator reporting clean,
    written INTO the fix for empty denominators. The DENOM-UNCOVERED control
    failed and named it.
  * CONTRACT-CHANGE DISCLOSURE: DENOM-UNCOVERED's WITNESS was rewritten (a
    --root gate is no longer "uncovered"; a no---json tool is). The control's
    SUBJECT -- whatever falls outside the population must be named, never
    silently dropped -- is unchanged. Rewriting the witness rather than the
    assertion is the distinction that makes this legitimate rather than
    laundering a failing test.
