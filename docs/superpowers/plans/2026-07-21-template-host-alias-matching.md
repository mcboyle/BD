# Template Host Alias Matching Implementation Plan

> **Current status (2026-07-21):** The implementation and focused validation
> work in Tasks 1-4 is complete on the pre-documentation implementation baseline
> `51c63341de697bb3f585055ba73f84e03fe8658b`. The last fully validated live
> deployment is the distinct commit
> `b60f58f0d25cbfb5d3bda07b81ee113e10650218`. Task 5 remains open for the
> parent's final merged-head deployment and acceptance proof; earlier candidate
> or focused live evidence does not mark that final deployment complete. Later
> docs-only closeout commits do not change the runtime implementation baseline.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make reviewed templates safely reusable across explicitly declared sibling hosts, with an opt-in parent-domain scope, while preserving the already-general shared HTTP response fix.

**Architecture:** Centralize template host decisions in one pure specificity-scoring helper used by both primary lookup and variant discovery. Canonical hosts retain existing suffix behavior, aliases are exact by default, and broad sibling matching requires a validated `match.sibling_domain`. The FilthyKings template exercises the explicit-alias path; the shared transport adapter remains site-independent.

**Tech Stack:** Python 3.12, JSON reviewed templates, standard-library `ipaddress`, `re`, and `urllib.parse`, BulkDownloader template registry, direct assertion scripts, repository validation tools, systemd, PuTTY/Plink deployment to `stash`.

## Global Constraints

- `host` remains the canonical host and preserves existing behavior.
- `match.hosts` entries are exact aliases and do not implicitly enable children or siblings.
- `match.sibling_domain` is optional and must be explicitly declared to permit a whole domain family.
- Empty, malformed, IP-address, localhost, single-label, and canonical-host-unrelated sibling domains fail closed.
- Matching priority is exact canonical host, exact alias, canonical child, then opt-in sibling domain.
- Existing templates without the new metadata retain current behavior.
- Invalid metadata never raises during template lookup.
- `match.url_patterns` behavior is unchanged.
- Do not add redirect-based learning, public-suffix network lookups, selector-scoring changes, authentication changes, DRM handling, or automatic `www` widening.
- Preserve unrelated user changes in the dirty worktree and stage only files named by each task.

---

### Task 1: Centralized host-match specificity

**Files:**
- Create: `tests/test_template_registry_host_aliases.py`
- Modify: `bulk_downloader/template_registry.py:1-110`

**Interfaces:**
- Consumes: enabled template dictionaries loaded by `load_templates()` and a URL host string from `urlparse(url).netloc`.
- Produces: `_template_host_match_key(template: dict, url_host: str) -> tuple[int, int] | None`, used by `find_template_for_url()` and `find_template_variants_for_url()`.
- Preserves: `_host_matches(template_host: str, url_host: str) -> bool` for canonical exact/child behavior.

- [ ] **Step 1: Write the failing alias and sibling-domain tests**

Create `tests/test_template_registry_host_aliases.py` with this complete content:

```python
import json
import tempfile
from pathlib import Path

from bulk_downloader.template_registry import (
    find_template_for_url,
    find_template_variants_for_url,
)


def _write_template(directory, filename, *, host, match=None):
    path = Path(directory) / filename
    path.write_text(
        json.dumps(
            {
                "host": host,
                "status": "enabled",
                "match": match or {},
                "selectors": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_explicit_alias_matches_but_unlisted_sibling_does_not():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "site.template.json",
        host="www.example.com",
        match={"hosts": ["www.example.com", "members.example.com"]},
    )

    matched = find_template_for_url(
        "https://members.example.com/video/1", template_dirs=[directory]
    )
    rejected = find_template_for_url(
        "https://cdn.example.com/video/1", template_dirs=[directory]
    )

    assert matched is not None
    assert matched["host"] == "www.example.com"
    assert rejected is None


def test_valid_sibling_domain_matches_domain_family():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "family.template.json",
        host="www.example.com",
        match={"sibling_domain": "example.com"},
    )

    matched = find_template_for_url(
        "https://members.example.com/video/1", template_dirs=[directory]
    )

    assert matched is not None
    assert matched["host"] == "www.example.com"


def test_invalid_sibling_domains_fail_closed():
    invalid_values = (
        "other.example",
        "localhost",
        "127.0.0.1",
        "https://example.com",
        "-bad.example.com",
        123,
    )
    for index, sibling_domain in enumerate(invalid_values):
        directory = tempfile.mkdtemp()
        _write_template(
            directory,
            f"invalid-{index}.template.json",
            host="www.example.com",
            match={"sibling_domain": sibling_domain},
        )
        assert find_template_for_url(
            "https://members.example.com/video/1",
            template_dirs=[directory],
        ) is None


def test_non_list_and_non_string_alias_metadata_fails_closed():
    bad_values = (
        "members.example.com",
        {"members.example.com": True},
        [123, None],
    )
    for index, hosts in enumerate(bad_values):
        directory = tempfile.mkdtemp()
        _write_template(
            directory,
            f"bad-alias-{index}.template.json",
            host="www.example.com",
            match={"hosts": hosts},
        )
        assert find_template_for_url(
            "https://members.example.com/video/1",
            template_dirs=[directory],
        ) is None


def test_match_priority_is_canonical_then_alias_then_child_then_sibling():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "00-sibling.template.json",
        host="www.example.com",
        match={"sibling_domain": "example.com"},
    )
    _write_template(
        directory,
        "10-child.template.json",
        host="members.example.com",
    )
    _write_template(
        directory,
        "20-alias.template.json",
        host="app.other.test",
        match={"hosts": ["deep.members.example.com", "alias-only.members.example.com"]},
    )
    _write_template(
        directory,
        "30-exact.template.json",
        host="deep.members.example.com",
    )

    exact = find_template_for_url(
        "https://deep.members.example.com/video/1",
        template_dirs=[directory],
    )
    alias = find_template_for_url(
        "https://alias-only.members.example.com/video/1",
        template_dirs=[directory],
    )
    child = find_template_for_url(
        "https://child.members.example.com/video/1",
        template_dirs=[directory],
    )

    assert exact is not None
    assert exact["host"] == "deep.members.example.com"
    assert alias is not None
    assert alias["host"] == "app.other.test"
    assert child is not None
    assert child["host"] == "members.example.com"


def test_variant_discovery_uses_same_alias_rules_as_primary_lookup():
    directory = tempfile.mkdtemp()
    _write_template(
        directory,
        "alias.template.json",
        host="www.example.com",
        match={"hosts": ["members.example.com"]},
    )

    primary = find_template_for_url(
        "https://members.example.com/video/1", template_dirs=[directory]
    )
    variants = find_template_variants_for_url(
        "https://members.example.com/video/1", template_dirs=[directory]
    )

    assert primary is not None
    assert [template["host"] for template in variants] == [primary["host"]]


if __name__ == "__main__":
    test_explicit_alias_matches_but_unlisted_sibling_does_not()
    test_valid_sibling_domain_matches_domain_family()
    test_invalid_sibling_domains_fail_closed()
    test_non_list_and_non_string_alias_metadata_fails_closed()
    test_match_priority_is_canonical_then_alias_then_child_then_sibling()
    test_variant_discovery_uses_same_alias_rules_as_primary_lookup()
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```powershell
python tests/test_template_registry_host_aliases.py
```

Expected: nonzero exit on `assert matched is not None` in `test_explicit_alias_matches_but_unlisted_sibling_does_not`, because the current registry ignores `match.hosts`.

- [ ] **Step 3: Implement the minimal shared matcher**

In `bulk_downloader/template_registry.py`, add `import ipaddress` and `import re`, then add these helpers immediately after `_host_matches`:

```python
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _valid_sibling_domain(canonical_host: str, value) -> str:
    if not isinstance(value, str):
        return ""
    domain = value.strip().lower().rstrip(".")
    canonical = (canonical_host or "").strip().lower().rstrip(".")
    if not domain or domain == "localhost" or "." not in domain:
        return ""
    try:
        ipaddress.ip_address(domain)
        return ""
    except ValueError:
        pass
    if any(not _HOST_LABEL_RE.fullmatch(label) for label in domain.split(".")):
        return ""
    if canonical != domain and not canonical.endswith("." + domain):
        return ""
    return domain


def _template_host_match_key(template: dict, url_host: str):
    host = (url_host or "").strip().lower().rstrip(".")
    canonical = str((template or {}).get("host") or "").strip().lower().rstrip(".")
    if not host or not canonical:
        return None
    if host == canonical:
        return (4, len(canonical))

    match = template.get("match")
    if not isinstance(match, dict):
        match = {}
    aliases = match.get("hosts")
    if isinstance(aliases, list):
        exact_aliases = {
            value.strip().lower().rstrip(".")
            for value in aliases
            if isinstance(value, str) and value.strip()
        }
        if host in exact_aliases:
            return (3, len(host))

    if _host_matches(canonical, host):
        return (2, len(canonical))

    sibling = _valid_sibling_domain(canonical, match.get("sibling_domain"))
    if sibling and (host == sibling or host.endswith("." + sibling)):
        return (1, len(sibling))
    return None
```

Replace the matching loop in `find_template_for_url()` with:

```python
    best = None
    best_key = None
    for template in load_templates(template_dirs):
        key = _template_host_match_key(template, host)
        if key is not None and (best_key is None or key > best_key):
            best, best_key = template, key
```

Replace the matching loop in `find_template_variants_for_url()` with:

```python
    matches = []
    for template in load_templates(template_dirs):
        key = _template_host_match_key(template, host)
        if key is not None:
            matches.append((key, template))
    matches.sort(key=lambda match: match[0], reverse=True)
    return [template for _, template in matches]
```

- [ ] **Step 4: Run focused and existing registry checks to verify GREEN**

Run:

```powershell
python tests/test_template_registry_host_aliases.py
python -c "import runpy; n=runpy.run_path('tests/test_template_registry_reptyle.py'); [n[k]() for k in ('test_reptyle_template_loads','test_reptyle_template_has_required_selectors','test_reptyle_template_has_media_patterns_only','test_t1_specific_host_beats_generic_parent_domain','test_t1_specificity_independent_of_file_order','test_t1_parent_domain_still_matches_when_no_specific','test_t1_no_match_returns_none')]"
python -m py_compile bulk_downloader/template_registry.py
```

Expected: all commands exit `0` with no traceback.

- [ ] **Step 5: Commit the centralized matcher**

```powershell
git add bulk_downloader/template_registry.py tests/test_template_registry_host_aliases.py
git diff --cached --check
git commit -m "feat: support safe template host aliases"
```

Expected: one commit containing only the registry and its focused tests.

---

### Task 2: FilthyKings secure-default template

**Files:**
- Modify: `templates/reviewed/filthykings.com.template.json:1-16`
- Modify: `tests/test_template_registry_filthykings.py:1-20`

**Interfaces:**
- Consumes: `match.hosts` exact-alias behavior from Task 1.
- Produces: an enabled reviewed template whose canonical host is `www.filthykings.com` and whose authenticated alias is `members.filthykings.com`.

- [ ] **Step 1: Tighten the test before changing template data**

Replace `tests/test_template_registry_filthykings.py` with:

```python
from bulk_downloader.template_registry import find_template_for_url


def test_filthykings_template_matches_authenticated_member_scene():
    template = find_template_for_url(
        "https://members.filthykings.com/en/video/filthykings/example/123"
    )
    assert template is not None
    assert template["status"] == "enabled"
    assert template["host"] == "www.filthykings.com"
    assert template["selectors"]["download"]["trigger"] == (
        '[title*="Download" i]'
    )


def test_filthykings_template_rejects_unlisted_sibling():
    assert find_template_for_url(
        "https://billing.filthykings.com/account"
    ) is None


if __name__ == "__main__":
    test_filthykings_template_matches_authenticated_member_scene()
    test_filthykings_template_rejects_unlisted_sibling()
```

- [ ] **Step 2: Run the FilthyKings test to verify RED**

Run:

```powershell
python tests/test_template_registry_filthykings.py
```

Expected: nonzero exit because the current hot-patch template uses broad canonical host `filthykings.com`, causing the canonical-host assertion and unlisted-sibling rejection to fail.

- [ ] **Step 3: Restrict the reviewed template to explicit aliases**

In `templates/reviewed/filthykings.com.template.json`, change only the top-level canonical host:

```json
"host": "www.filthykings.com"
```

Keep this alias list and do not add `sibling_domain`:

```json
"match": {
  "hosts": [
    "www.filthykings.com",
    "members.filthykings.com"
  ],
  "url_patterns": [
    "^https://(?:www|members)\\.filthykings\\.com/"
  ]
}
```

- [ ] **Step 4: Run template tests and validation to verify GREEN**

Run:

```powershell
python tests/test_template_registry_filthykings.py
python tools/validate_templates.py --root templates
```

Expected: both commands exit `0`; validator prints `VERDICT: ok`.

- [ ] **Step 5: Commit the secure-default template**

```powershell
git add templates/reviewed/filthykings.com.template.json tests/test_template_registry_filthykings.py
git diff --cached --check
git commit -m "fix: scope FilthyKings template to declared hosts"
```

Expected: one commit containing only the reviewed template and its test.

---

### Task 3: Preserve the site-independent HTTP response fix

**Files:**
- Modify: `bulk_downloader/runner_transport.py:15-105,1235-1265` (already present as an uncommitted live hot patch)
- Create: `tests/test_curl_cffi_response_context.py` (already present as an uncommitted regression test)

**Interfaces:**
- Consumes: a closeable response from `curl_cffi.requests.request(..., stream=True)`.
- Produces: `_closeable_response_context(response)` returning a context manager that yields the same response and calls `response.close()` on exit.
- Preserves: the existing `httpx.stream(...)` context-manager branch unchanged.

- [ ] **Step 1: Confirm the hot-patch diff is narrowly scoped**

Run:

```powershell
git diff -- bulk_downloader/runner_transport.py tests/test_curl_cffi_response_context.py
```

Expected: the production diff imports `contextlib`, adds `_closeable_response_context`, and wraps only the `curl_cffi` response; the test uses a closeable object without `__enter__` or `__exit__`.

- [ ] **Step 2: Run the behavior test with the deployed dependency environment**

Because the local Python installation may not contain Playwright, run the test through the saved `stash` environment after copying it to `/tmp`:

```powershell
& 'C:\Program Files\PuTTY\pscp.exe' -batch -load stash `
  '.\tests\test_curl_cffi_response_context.py' `
  'mboyle@10.0.70.20:/tmp/test_curl_cffi_response_context.py'
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "cd /home/mboyle/BulkDownloader && PYTHONPATH=/home/mboyle/BulkDownloader ./venv/bin/python /tmp/test_curl_cffi_response_context.py"
```

Expected: exit `0` with no traceback. The RED state was already captured before the hot patch as an assertion failure for the missing helper and a live error: `'Response' object does not support the context manager protocol`.

- [ ] **Step 3: Run source compilation**

```powershell
python -m py_compile bulk_downloader/runner_transport.py
```

If local Playwright imports block test import, compilation must still exit `0` because it does not import dependencies.

- [ ] **Step 4: Commit the shared transport fix**

```powershell
git add bulk_downloader/runner_transport.py tests/test_curl_cffi_response_context.py
git diff --cached --check
git commit -m "fix: close curl cffi streaming responses safely"
```

Expected: one commit containing only the shared transport and its regression test.

---

### Task 4: Focused regression and repository validation

**Files:**
- Test: `tests/test_template_registry_host_aliases.py`
- Test: `tests/test_template_registry_filthykings.py`
- Test: `tests/test_curl_cffi_response_context.py`
- Validate: `templates/reviewed/filthykings.com.template.json`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: fresh test evidence suitable for deployment approval.

- [ ] **Step 1: Run every focused pure-Python check**

```powershell
python tests/test_template_registry_host_aliases.py
python tests/test_template_registry_filthykings.py
python -m py_compile bulk_downloader/template_registry.py bulk_downloader/runner_transport.py
python tools/validate_templates.py --root templates
```

Expected: every command exits `0`; validator prints `VERDICT: ok`.

- [ ] **Step 2: Run the shared transport test on `stash`**

```powershell
& 'C:\Program Files\PuTTY\pscp.exe' -batch -load stash `
  '.\tests\test_curl_cffi_response_context.py' `
  'mboyle@10.0.70.20:/tmp/test_curl_cffi_response_context.py'
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "cd /home/mboyle/BulkDownloader && PYTHONPATH=/home/mboyle/BulkDownloader ./venv/bin/python /tmp/test_curl_cffi_response_context.py"
```

Expected: exit `0`, no traceback.

- [ ] **Step 3: Confirm the final source diff contains no unrelated files**

```powershell
git status --short -- `
  bulk_downloader/template_registry.py `
  bulk_downloader/runner_transport.py `
  templates/reviewed/filthykings.com.template.json `
  tests/test_template_registry_host_aliases.py `
  tests/test_template_registry_filthykings.py `
  tests/test_curl_cffi_response_context.py
git log -4 --oneline
```

Expected: named implementation files are clean after their commits; the log shows the design plus the three implementation commits.

---

### Task 5: Deploy and prove the generalized behavior on `stash`

**Files:**
- Deploy: `bulk_downloader/template_registry.py`
- Deploy: `bulk_downloader/runner_transport.py`
- Deploy: `templates/reviewed/filthykings.com.template.json`
- Preserve: remote timestamped backups under `/home/mboyle/BulkDownloader`

**Interfaces:**
- Consumes: committed, validated Tasks 1-4.
- Produces: a healthy BulkDownloader service using explicit aliases, optional sibling-domain support, and safe shared streaming-response cleanup.

- [ ] **Step 1: Capture pre-deployment status**

```powershell
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "systemctl is-active bulkdownloader.service; systemctl --user is-active bd-filthykings-quota.service; curl -fsS http://127.0.0.1:5555/api/health"
```

Expected: both services print `active`; health JSON contains `"ok":true` and `"version":"3.66.811"`.

- [ ] **Step 2: Back up the exact remote targets**

```powershell
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "cp -a /home/mboyle/BulkDownloader/bulk_downloader/template_registry.py /home/mboyle/BulkDownloader/bulk_downloader/template_registry.py.bak.host-alias-20260721; cp -a /home/mboyle/BulkDownloader/bulk_downloader/runner_transport.py /home/mboyle/BulkDownloader/bulk_downloader/runner_transport.py.bak.host-alias-20260721; cp -a /home/mboyle/BulkDownloader/templates/reviewed/filthykings.com.template.json /home/mboyle/BulkDownloader/templates/reviewed/filthykings.com.template.json.bak.host-alias-20260721"
```

Expected: exit `0`; no source files are removed.

- [ ] **Step 3: Upload only the three runtime targets**

```powershell
& 'C:\Program Files\PuTTY\pscp.exe' -batch -load stash `
  '.\bulk_downloader\template_registry.py' `
  'mboyle@10.0.70.20:/home/mboyle/BulkDownloader/bulk_downloader/template_registry.py'
& 'C:\Program Files\PuTTY\pscp.exe' -batch -load stash `
  '.\bulk_downloader\runner_transport.py' `
  'mboyle@10.0.70.20:/home/mboyle/BulkDownloader/bulk_downloader/runner_transport.py'
& 'C:\Program Files\PuTTY\pscp.exe' -batch -load stash `
  '.\templates\reviewed\filthykings.com.template.json' `
  'mboyle@10.0.70.20:/home/mboyle/BulkDownloader/templates/reviewed/filthykings.com.template.json'
```

Expected: all three transfers exit `0`.

- [ ] **Step 4: Compile and run remote focused checks before restart**

Copy the three focused tests to `/tmp`, then run them against the deployed tree:

```powershell
& 'C:\Program Files\PuTTY\pscp.exe' -batch -load stash `
  '.\tests\test_template_registry_host_aliases.py' `
  '.\tests\test_template_registry_filthykings.py' `
  '.\tests\test_curl_cffi_response_context.py' `
  'mboyle@10.0.70.20:/tmp/'
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "cd /home/mboyle/BulkDownloader && ./venv/bin/python -m py_compile bulk_downloader/template_registry.py bulk_downloader/runner_transport.py && PYTHONPATH=/home/mboyle/BulkDownloader ./venv/bin/python /tmp/test_template_registry_host_aliases.py && PYTHONPATH=/home/mboyle/BulkDownloader ./venv/bin/python /tmp/test_template_registry_filthykings.py && PYTHONPATH=/home/mboyle/BulkDownloader ./venv/bin/python /tmp/test_curl_cffi_response_context.py && ./venv/bin/python tools/validate_templates.py --root templates"
```

Expected: exit `0`; validator prints `VERDICT: ok`.

- [ ] **Step 5: Restart and verify service safeguards**

```powershell
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "sudo -n systemctl restart bulkdownloader.service; systemctl is-active bulkdownloader.service; systemctl --user is-active bd-filthykings-quota.service; systemctl --user is-enabled bd-filthykings-quota.service; curl -fsS http://127.0.0.1:5555/api/health"
```

Expected: BulkDownloader and watchdog print `active`, watchdog prints `enabled`, and health JSON contains `"ok":true`.

- [ ] **Step 6: Prove explicit alias matching and default sibling rejection remotely**

Run:

```powershell
$aliasCheck = @'
from bulk_downloader.template_registry import find_template_for_url

member = find_template_for_url(
    "https://members.filthykings.com/en/video/filthykings/example/123"
)
unlisted = find_template_for_url("https://billing.filthykings.com/account")
assert member is not None
assert member["host"] == "www.filthykings.com"
assert unlisted is None
print("alias=ok unlisted=blocked")
'@
$aliasCheck | & 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "cd /home/mboyle/BulkDownloader && ./venv/bin/python -"
```

Expected output: `alias=ok unlisted=blocked`.

- [ ] **Step 7: Verify live 2160p transfer continuity**

Run this condition-based live check. It starts the site only when necessary,
then requires actual `.part` growth across a 30-second interval:

```powershell
$liveCheck = @'
import json
import os
import subprocess
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:5555"
SITE_ID = "026255e0"
DOWNLOAD_DIR = Path("/StashDB3/OPV_FilthyKings")

session = requests.Session()


def status():
    return session.get(BASE + "/api/status", timeout=30).json()[SITE_ID]


def part_snapshot():
    files = []
    for root, _dirs, names in os.walk(DOWNLOAD_DIR):
        for name in names:
            if not name.endswith(".part"):
                continue
            path = Path(root) / name
            try:
                files.append((path.stat().st_size, str(path)))
            except OSError:
                pass
    return sorted(files, reverse=True)


initial_status = status()
if initial_status["state"] != "running":
    csrf = session.get(BASE + "/api/csrf", timeout=10).json()
    token = csrf.get("csrf_token") or csrf.get("token")
    response = session.post(
        BASE + f"/api/sites/{SITE_ID}/start",
        headers={"X-CSRF-Token": token},
        timeout=30,
    )
    response.raise_for_status()

deadline = time.time() + 180
first = []
first_status = None
while time.time() < deadline:
    current = status()
    files = part_snapshot()
    if current["counts"].get("running", 0) == 1 and files:
        first_status = current
        first = files
        break
    time.sleep(5)
assert first_status is not None, "no running job with a partial file within 180s"

time.sleep(30)
second_status = status()
second = part_snapshot()
assert second_status["state"] == "running"
assert second_status["counts"].get("running", 0) == 1
assert second_status["counts"].get("needs_review", 0) == 0
assert first and second
assert second[0][0] > first[0][0], (first[0], second[0])
assert "2160p" in second[0][1].lower() or "4k" in second[0][1].lower()

logs = subprocess.run(
    [
        "journalctl",
        "-u",
        "bulkdownloader.service",
        "--since",
        "5 minutes ago",
        "--no-pager",
    ],
    check=True,
    capture_output=True,
    text=True,
).stdout
assert "Response object does not support the context manager protocol" not in logs
print(
    json.dumps(
        {
            "state": second_status["state"],
            "counts": second_status["counts"],
            "part_before": first[0],
            "part_after": second[0],
        },
        indent=2,
    )
)
'@
$liveCheck | & 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "cd /home/mboyle/BulkDownloader && ./venv/bin/python -"
```

Expected: exit `0`; JSON shows `state: running`, `needs_review: 0`, and
`part_after` larger than `part_before`. Do not claim success from queue state
alone.

- [ ] **Step 8: Run the full validation suite**

```powershell
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "cd /home/mboyle/BulkDownloader && ./capture.sh --workers=600 --summary"
```

Expected: command exits `0` and reports zero failed suites/live tests. If any failure occurs, leave the existing live download and quota watchdog state unchanged, report the failing suite, and do not mark the generalized change complete.

- [ ] **Step 9: Remove only temporary remote test copies**

```powershell
& 'C:\Program Files\PuTTY\plink.exe' -batch -load stash `
  "rm -f /tmp/test_template_registry_host_aliases.py /tmp/test_template_registry_filthykings.py /tmp/test_curl_cffi_response_context.py"
```

Expected: exit `0`; timestamped backups and the persistent quota watchdog remain intact.
