# BUNDLE / GIT-IMPORT VALIDATION -- v3.66.805

<!-- verified-against: v3.66.805 ; git import commit 2c1e4dd -->

**What this establishes.** That the git repository is a faithful, complete,
byte-exact representation of the original v3.66.805 release artifacts (the
pre-git zips), and that the release gates pass on the imported tree.

**Point-in-time, not standing authority.** Every number here was MEASURED on
2026-07-20 by running the instrument named next to it, against the git initial
import commit `2c1e4dd` and the five source zips. Per the repo's doc-staleness
rule (CLAUDE.md section 1), re-derive at decision time rather than quoting this
file. Counts that move are marked with their instrument so they can be
re-measured, not trusted.

---

## 0 | Verdict

The conversion from zips to git dropped nothing and corrupted nothing. Every
file in the 2620-file release zip is either present byte-identical in git, or
excluded on purpose by `.gitignore` (build/generated artifacts), or -- in one
case -- intentionally replaced (a stray README stub swapped for a real project
README). Every file in the 3189-file git initial import traces to a source zip
or is one of eight hand-authored git-infrastructure files.

---

## 1 | Instruments and predicates

- **Content equality is by sha256 of file bytes**, not by git blob id and not by
  path. This is deliberate: hashing bytes is robust to any path-remapping the
  import performed and it catches line-ending rewrites or truncation that a
  name-only comparison would miss. The prior sandbox note established the file
  *set* matched "names only"; this closes that gap with content.
- **The git tree** was materialized with `git archive 2c1e4dd | tar -x` (3189
  files, equal to `git ls-tree -r 2c1e4dd | wc -l`).
- **Exclusions** were confirmed with `git check-ignore`, not assumed from a
  filename pattern.
- **Guard files** were checked against an anchor OUTSIDE the tree
  (`STATE.json` from the next-session zip), so the attestation is not
  self-referential to `CLAUDE.md`.

---

## 2 | Release zip -> git, fully accounted

Source: `BulkDownloader_v3_66_805.zip` (2620 files, 0 symlinks).

| Outcome | Count | Instrument | Read |
| --- | --- | --- | --- |
| same path, byte-identical | 2563 | sha256 join on path | exact copy |
| same path, content replaced | 1 | sha256 differ | `README.md`: datastores-kit stub -> project README (intentional) |
| absent from git, matched by `.gitignore` | 56 | `git check-ignore` (56/56) | build/generated artifacts, correctly not committed |
| **total** | **2620/2620** | | nothing dropped, nothing corrupted |

The 56 excluded, by category (instrument: path prefix): `frontend/dist/` build
output 35, generated `reports/` 11, fixture captures (`canary.har`, `.wacz`) 6,
TS build cache (`.tsbuildinfo`) 2, `frontend/app_config.json` 1, one other. All
56 return true from `git check-ignore`; zero unexplained.

---

## 3 | Git initial import (3189 files) -> provenance of every file

Content-hash membership of each git file into a source zip (instrument: sha256
set membership; a git file is attributed to the first source whose hash set
contains its content):

| Provenance | Count |
| --- | --- |
| release zip, same path | 2564 (2563 identical + 1 README replaced) |
| bdsuite zip (`toolchain/bin/` + the documented `bd-pk-mirror` copies in `project-knowledge/`) | 501 |
| project-files zip (`project-knowledge/`) | 90 |
| release-zip content duplicated at another path | 26 |
| git-native (no source zip) | 8 |
| **total** | **3189/3189** |

The 501/90 figures are inflated by mirroring: the `bd-*` tools live in both
`toolchain/bin/` and mirrored under `project-knowledge/`, so one bdsuite file's
content matches two git paths. This is by design (`bd-pk-mirror` keeps them
honest), not duplication to purge.

**The eight git-native files** -- present in git, in no source zip -- are
exactly the files a git conversion adds:

```
.gitattributes
.github/workflows/ci.yml
.gitignore
.gitleaks-baseline.json
.gitleaks.toml
CLAUDE.md
SECURITY.md
docs/repo/SANDBOX_SPEC_AND_LAYOUT_v3_66_805.md
```

(The remaining `docs/repo/` files and `scripts/cloud-setup.sh` were added in the
commits after `2c1e4dd`, so they are not part of this initial-import count.)

**Correctly not committed:** the next-session zip (31 files) and audit-state zip
(149 files) contributed ZERO content to the git tree -- they are session and
audit scaffolding, not source.

---

## 4 | External-anchor guard check

Instrument: full sha256 of each guard file's bytes vs the `guards_full_sha256`
map in `STATE.json` (next-session zip), which is independent of `CLAUDE.md`.

Result: **7/7 match.** `STATE.json`'s own `guards_note` corroborates: "All 7
release guards byte-identical to 805 ... re-verified from the source zip."

---

## 5 | Release gates on the imported tree

Executed against the tree, real output captured:

| Gate | Result |
| --- | --- |
| `git bundle verify` / `git fsck --full` | clean; complete history |
| 7 SHA-pinned guard files | 7/7 match, 0 drift |
| version-pin coherence | `3.66.805` across `__init__.py`, `tests/test_settings_center_slice4.py:200`, CHANGELOG header |
| current CHANGELOG entry ASCII | clean |
| `compileall bulk_downloader tools tests` | exit 0 under Python 3.12 (CI's pin) |
| `shellcheck -S error scripts/cloud-setup.sh` | 0 findings |

**compileall interpreter trap.** The gate false-fails under Python <= 3.11 on
`tools/diag_csrf_bootstrap.py` -- a backslash inside an f-string expression,
which PEP 701 made legal only in 3.12+. It parses clean on 3.12 and 3.13. CI
pins 3.12, so the gate is green there; a bare `python3` that resolves to 3.11
will show a spurious red. This is an interpreter artifact, not a tree defect.

---

## 6 | Not established here

- **gitleaks baseline gate.** Requires the runner; not executed in this
  validation. Both inputs (`.gitleaks.toml`, `.gitleaks-baseline.json`) are
  present in the tree.
- **Anything about the host.** This validates the git import against the release
  zips only. It says nothing about stash.
- **pyflakes backlog.** Advisory gate, not run.
