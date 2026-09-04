# MUTANT_INDEX -- the durable mutation batteries and the seams they pin

## What this file is

Every mutation battery in this repository is a tracked `bd-mutate` spec under
`tests/mutants/`. Most of them are PER-CUT: a cut proved its own gate by killing
its own mutants and left the spec behind as re-runnable evidence. Its anchors
describe one diff, and when that diff's neighbourhood moves the anchor is
re-aimed or the spec is retired.

This index covers the second kind. A DURABLE MODULE BATTERY outlives the cut
that created it because its subject is a whole CALL-SITE POPULATION rather than
one diff: it answers "is every production caller of this safety primitive
actually reached by a test that fails when the primitive fails open?" That
question stays interesting after the cut lands, and it gains a new answer every
time a new caller is written.

The complete spec population is whatever `git ls-files 'tests/mutants/*.json'`
returns. That command is the denominator; a count written here would be wrong
by the next cut. Three repo-wide gates judge each member of it -- and NONE of
them judges COMPLETENESS. They check that a spec that EXISTS is well formed;
nothing checks that a spec exists for every subject that needs one, or that a
battery still names every call site of the thing it claims to cover. That gap
is real and is written up as a proposed row rather than papered over:
`tests/test_v3_66_1184_mutation_specs_are_tracked.py` validates the schema, the
band, and that each anchor occurs exactly once;
`tests/test_row532_a_mutant_anchor_must_resolve_into_code.py` proves each anchor
resolves into executable source rather than a comment; and
`tests/test_row357_mutant_anchors_are_not_fragile.py` refuses an anchor that
freezes a value some tool re-derives.

## How to run a battery

`bd-mutate` refuses to mutate the tree that supplies it, so the work tree is a
detached scratch clone OUTSIDE this repository:

```bash
S=$(mktemp -d /tmp/bd-mutants.XXXXXX)
git clone -q --shared --no-hardlinks "$PWD" "$S/repo"
git -C "$S/repo" checkout -q --detach <the exact candidate sha>
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python toolchain/bin/bd-mutate \
    --spec tests/mutants/<name>.json --work "$S/repo" --timeout 300 --json
```

Exit 0 means every selected mutant was CAUGHT. Exit 1 means one ESCAPED. Exit 2
means the battery has no verdict at all, which never reads as success.

## CONTAINMENT -- read this before running an SSRF battery

A mutation battery RUNS THE PRODUCTION CODE WITH ITS GUARD REMOVED. For a
battery whose subject is an SSRF guard, that is the whole point and it is also
the hazard: with the guard bypassed the code under test does what it would have
done without one, which means it DIALS. Measured on the SSRF delegation battery below, by two independent reviewers on
their own socket tripwires: run unconstrained it makes THREE off-box CONNECTS --
two to `169.254.169.254:80` and one to `10.0.0.1:80` -- inside a wider count of
six attempts once refused DNS lookups are included. Read the connect count as
the one that matters. TWO OF THE THREE COME FROM TESTS THAT PREDATE THIS
BATTERY, so no guard added to one catcher can close it; containment has to be a
property of the RUN, which is why it is stated here rather than in a test.

`169.254.169.254` IS ROUTABLE FROM THIS FLEET, and not by accident of
configuration: there is no `169.254.0.0/16` route at all, so the address falls
through to the default gateway. Measured on test5:
`ip route get 169.254.169.254` -> `via 10.0.70.1 dev ens33`. Running such a
battery unconstrained on a fleet host is a live attempt against a cloud metadata
service from a machine that may have a role attached, and on a GitHub-hosted
runner (`BD_CI_HOSTED=1`, the sanctioned fallback) that address is the real
instance metadata service.

So: DO NOT run an SSRF battery on a fleet host without containment in force.
Containment means at least one of --

* an offline sandbox: no default route, or an egress firewall that DROPs
  169.254.0.0/16, 10/8, 172.16/12, 192.168/16 and ::1/128 for the run;
* a socket tripwire in the test process that records and refuses outbound
  connects, so an attempt is EVIDENCE rather than a packet.

The unmutated tree dials none of this -- every guard refuses first, which is
what the battery exists to prove. The attempts belong to the mutants alone, and
they are the mutant doing exactly what the guard prevents. Report the attempt
count with the battery result; a battery that made attempts and did not say so
has under-reported what it did.

The catcher-local `socket.create_connection` containment covers S3 and S4 in
`test_c1_ssrf_and_fileread.py`, S7's guarded catcher in that same file, and
S10 in `test_v3_66_24_phase4_ssrf_hardening.py`. Each wrapper records attempted
connections and refuses a non-loopback address before calling the real socket;
each catcher asserts its exact `non_loopback_connects == 0` result. S3, S4 and
S10 are the three sites whose fail-open mutants otherwise dial off-box; S7 is
also guarded as required even though its former catcher used loopback.

A battery is paired with a `_transform_control.json` that applies the SAME
mutants against a band that does not judge the mutated behaviour. The control
must ESCAPE (exit 1). Without it, a CAUGHT result cannot be distinguished from a
mutant that simply failed to parse.

## Durable module batteries

### SSRF public-host delegation

* spec `tests/mutants/ssrf_public_host_delegation.json`
* control `tests/mutants/ssrf_public_host_delegation_transform_control.json`

Subject: every call site of `_is_safe_public_host` -- the canonical host
classifier defined in `bulk_downloader/provider_resolve_impl/_common.py` --
THAT THE COMMAND BELOW REPORTS is reached by a test that fails when that call
FAILS OPEN. The population is that command's output, scoped to
`bulk_downloader/`; it is not a claim about every caller in the tree, and no
gate enforces that the two stay equal.

The mutation is the security-relevant direction: each mutant replaces the call
expression with a tuple that reports every host as safe. A site whose mutant is
CAUGHT has a test that really executes it; a site whose mutant ESCAPES has no
such test, and an SSRF regression there would ship green.

Population and how to re-derive it: `git grep -n '_is_safe_public_host(' --
bulk_downloader/` minus the definition itself. When that command returns a site
this battery does not name, the battery is out of date -- which is the point of
writing the population as a command instead of a number.

Each site is anchored on the call expression alone and replaced by a tuple that
reports the host as safe. The sites, and the test that fails when each one
fails open:

| site | module | catcher lives in |
| --- | --- | --- |
| S1 | `bulk_downloader/app.py`, the admin URL check | `tests/test_v3_66_540_ssrf_classifier_consolidation.py` |
| S2 | `bulk_downloader/app_flaresolverr.py`, the operator-supplied endpoint | `tests/test_v3_66_781_flaresolverr_ssrf.py` |
| S3 | `bulk_downloader/app_template.py`, the sandbox pre-fetch guard | `tests/test_c1_ssrf_and_fileread.py` |
| S4 | `bulk_downloader/app_template.py`, `_GuardedRedirect.redirect_request` | `tests/test_c1_ssrf_and_fileread.py` |
| S5 | `bulk_downloader/deep_detect/orchestrate.py`, the probe guard | `tests/test_rec01_02_deep_detect_probe_ssrf.py` |
| S6 | `bulk_downloader/dev_suite/capture_diag.py`, the manifest probe | `tests/test_core_bd01_manifest_probe_ssrf.py` |
| S7 | `bulk_downloader/multi_conn.py`, `_guard_url` | `tests/test_c1_ssrf_and_fileread.py` |
| S8 | `bulk_downloader/multi_conn.py`, `_redirect_guard_hook` | `tests/test_multi_conn_ssrf.py` |
| S9 | `bulk_downloader/provider_resolve_impl/_common.py`, the pre-fetch guard | `tests/test_v3_66_24_phase4_ssrf_hardening.py` |
| S10 | `bulk_downloader/provider_resolve_impl/_common.py`, `_request_hook` | `tests/test_v3_66_24_phase4_ssrf_hardening.py` |
| S11 | `bulk_downloader/runner.py`, the listing scraper | `tests/test_v3_66_540_ssrf_classifier_consolidation.py` |
| S12 | `bulk_downloader/runner_extractors.py`, the injected http_get guard | `tests/test_v3_66_765_injected_http_get_ssrf_guard.py` |
| S13 | `bulk_downloader/runner_telemetry.py`, the mirror probe | `tests/test_mirror_probe_ssrf.py` |
| S14 | `bulk_downloader/selector_playground.py`, the page fetch | `tests/test_v3_66_552_playground_ssrf.py` |
| S15 | `bulk_downloader/site_weather.py`, the weather probe | `tests/test_v3_66_550_weather_ssrf.py` |
| S16 | `bulk_downloader/tier_probe.py`, the tier probe hook | `tests/test_v3_66_542_tier_probe_ssrf.py` |

S15 also has a per-cut neighbour, `tests/mutants/v3_66_1229_optional_requests_ssrf.json`,
which pins what `site_weather` does AFTER the guard refuses. The two do not
overlap: that one mutates the refusal branch, this one mutates the delegation.
`tests/mutants/row250_ssrf_runner_guard_delegation.json` is the per-cut ancestor
of S11 and stays as the record of the cut that introduced it.

### What the first run of this battery found

S4 and S9 ESCAPED on their first measurement, and for the same reason: at both
sites a SECOND, still-intact guard refuses the same host, so every test that
predated this battery stayed green with the first guard bypassed. At S9 the
httpx request event-hook fires on the initial request as well as on redirects
and refuses with its own wording, so the pre-fetch guard could have failed open
unnoticed. At S4 no test reached the sandbox redirect handler at all.

Both were closed in the same cut, by tests that assert the DISTINCTIVE
diagnostic rather than the shared reason text -- `_default_http_get`'s pre-fetch
refusal is the bare `SSRF guard: ` prefix and the hook's carries `(redirect)`.
That is the general shape: where two guards refuse the same input, a test that
asserts only the reason cannot tell which one ran, and the redundant guard can
rot silently.

## Writing a new durable battery

1. Derive the call-site population with a command and record the command, not
   the count.
2. Anchor the CALL EXPRESSION, never the assignment around it. A NEW anchor
   whose matched text contains a digit, a quoted string, `True`/`False`/`None`,
   a spelled-out number, or an `=` character is value-bearing; the row357 gate
   has no producer record for it, returns UNKNOWN, and refuses the tree. Only
   the matched span is judged -- the replacement text is unconstrained.
3. Prove each anchor resolves exactly once in its own file before running
   anything. `old_regex` with a zero-width lookbehind is the way to isolate one
   of two identical calls without dragging the `=` into the span.
4. Name a catcher for every regression mutant, and keep every catcher's file
   inside the emitted band -- `bd-mutate` refuses an emitted spec otherwise.
5. Keep the band as small as the catchers allow. A durable battery is re-run by
   people who did not write it, and a battery nobody can afford to run is not
   evidence. This one runs its own catchers and nothing else.
6. Pair it with a transform control, and record caught/escaped/invalid
   separately.
7. CATCHER INPUTS ARE LITERAL ADDRESSES, NEVER NAMES. A hostname -- even a
   deliberately unresolvable one -- puts the ambient resolver inside the test.
   It exercises the guard's DNS-failure branch instead of its address
   classification, and on a resolver that hijacks NXDOMAIN the name comes back
   PUBLIC, no refusal is raised, and the test fails for a reason that has
   nothing to do with the code. It also emits a real DNS query: glibc has no
   RFC 6761 short-circuit for `.invalid`, so "unresolvable" is not "offline".
   This rule is written from a refutation, not from theory.
8. Say what the battery DIALS, under CONTAINMENT above.

## Open seams -- named, deliberately not pinned

A seam belongs here when a battery judged it and found no catcher, and the
missing test is too large to write in the same cut. It is a measurement, not a
forgotten to-do: closing one means writing the missing TEST, with its own RED
provenance.

The SSRF delegation battery currently has none -- both sites it found unguarded
were closed in the cut that first ran it.
