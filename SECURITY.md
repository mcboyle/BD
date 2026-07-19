# Security policy

## Repository visibility

**Keep this repository private.**

`tests/fixtures/recon_corpus/` holds six real captures from authenticated member
sites, recorded 2026-05-14 (~19 MB). Five recognizer/extraction tests assert
against them, and synthetic substitutes would not reproduce the real page shapes.

Verified state of that material:

- All embedded JWTs **expired** 2026-05-14..16 — they cannot be replayed for
  authentication.
- Payloads still contain account identifiers (`sub`, `user_id`, `partner_id`),
  and the filenames name the six sites.

So the residual risk is **identity linkage, not credential compromise**. That is
a disclosure question rather than a security-breach question, and it is why
visibility matters more here than rotation would.

If this repository is ever made public, or transferred to an organisation, remove
that directory and rewrite history first — deleting it in a later commit is not
sufficient.

## Secret scanning

CI runs `gitleaks` against `.gitleaks-baseline.json`.

The baseline records **41 accepted findings**, nearly all inside secret-handling
tooling (`bd-opv`, `bd-redaction-compiler`, `bd-secret-fixture`, `bd-log-sanitize`)
and tests that deliberately seed secret-shaped strings to prove redaction fires.
`bulk_downloader/llm_eval.py` carries a synthetic canary (`sk-EVALSECRET…`) for
the same reason.

A baseline was chosen over path allowlists deliberately: an allowlist broad
enough to silence these would also blind the scanner to real secrets in the same
files. **Any finding not in the baseline fails CI.** This was verified by
injecting a fresh AWS key and GitHub token and confirming a non-zero exit.

If you accept a new finding, regenerate the baseline in its own commit so the
diff is reviewable:

```bash
gitleaks detect --no-git -s . -c .gitleaks.toml -r .gitleaks-baseline.json --redact
```

## Runtime material never belongs in git

`.gitignore` excludes `bd_home/`, `captures/`, `live_recordings/`, `*.db`,
`*.har`, `*.wacz`, and `app_config.json`. These hold live cookies, session
state, and download history.
