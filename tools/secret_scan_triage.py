#!/usr/bin/env python3
"""secret_scan_triage -- classify every gitleaks finding, with evidence.

The scheduled CI secret scan walks FULL history (gitleaks-action on `schedule`),
the push scan only the pushed range, so history the baseline never covered turns
the nightly run red. This tool is the triage step between that report and the
baseline: it never decides "baseline it" on a rule's say-so, it records WHY.

Input: an UNREDACTED gitleaks JSON report (the evidence is computed from the
value; a redacted report is COULD-NOT-LOOK and is refused). Reproduce it as CI
does, pinned gitleaks 8.24.3:

    gitleaks git --config .gitleaks.toml --baseline-path <empty.json> \
        --report-format json --report-path full.json .

Output (neither ever contains a secret value):
  * --tsv PATH       fingerprint, commit, path, rule, class, evidence
  * --baseline PATH  .gitleaks-baseline.json entries for FIXTURE / NONSECRET / ROTATED,
                     redacted exactly as `gitleaks --redact` reports them (CI
                     scans with --redact, so Secret/Match must be the redacted
                     form to match), each carrying "class" and "reason".

Classes:
  FIXTURE  provably fake: the value itself shows it (markers/filler make up the
           value, structurally invalid token) or a reviewed KNOWN row (keyed by
           sha256 so no value lives here) says why it was never issued.
  NONSECRET  real or not, not a credential: route/doc text, a hostname, or a
           public-by-design identifier (KNOWN, e.g. a reCAPTCHA site key).
  ROTATED  real, already rotated: only from --rotations (sha256<TAB>record).
  LIVE     anything else. Never baselined; the scheduled scan stays red until
           the secret is rotated and recorded.

Exit: 0 no LIVE rows, 1 LIVE rows present, 2 unreadable/redacted input.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from pathlib import Path

FIXTURE, NONSECRET, ROTATED, LIVE = "FIXTURE", "NONSECRET", "ROTATED", "LIVE"

# Words that a fixture author puts in a value and an issuer never does.
_MARKER = re.compile(
    r"fake|example|dummy|sample|placeholder|synthetic|sentinel|canary|redacted|"
    r"not.?real|evalsecret|row\d{3,}",
    re.IGNORECASE,
)
# A marker only counts when it DOMINATES the value: strip the markers, filler
# (ascending/descending runs and repeats of >= 3 chars, e.g. abcd, 1234, AAAA)
# plain credential words (the "api"/"key"/"token" a fixture spells out) and
# separators; a real key keeps its random body, a fixture keeps almost nothing.
# A marker inside a long random value (a real key that happens to contain AAAA
# or abcd) is therefore NOT evidence.
_MAX_RESIDUE = 4
_PLAIN_WORDS = re.compile(r"secret|test|token|stash|value|live|key|api|sk|vt|pw", re.IGNORECASE)
_SEPARATORS = re.compile(r"[-_./:=+ ]")
# MAC length (base64url chars) for the HMAC JWT algorithms.
_JWT_SIG_LEN = {"HS256": 43, "HS384": 64, "HS512": 86}
_PATH_LIKE = re.compile(r"^[a-z0-9_.-]+(?:/[a-z0-9_.-]+)+$")
_HOSTNAME = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")

# Values no rule can prove fake by shape, reviewed by hand. sha256(value) ->
# evidence. Adding a row here is a security decision: the evidence must name a
# public source or a structural reason, never "looks fake".
KNOWN: dict[str, tuple[str, str]] = {
    # bd-opv push self-test, webpush "auth": the subscription it belongs to has
    # endpoint https://push.example.test/... (.test is reserved, RFC 6761) and the
    # p256dh beside it is the public web-push codelab example key, so no push
    # service ever issued it.
    "7bc81f72dd06267712bc46a98cd5850c2b553e40f986f0dda6ab70c302c55ce6": (
        FIXTURE, "webpush auth of a subscription on reserved push.example.test (RFC 6761); never issued"),
    # bd-opv QR self-test: a literal round-tripped through qrcode->pyzbar in
    # memory, never sent. Product pairing tokens are secrets.token_urlsafe(12)
    # (16 chars, app_pair.py) held in memory with PAIRING_TTL; this is 17 chars.
    "1580fffe5c574203f94c9fca7c612d9f868ea1ebc582be514cd4ef072adf47ee": (
        FIXTURE, "QR round-trip literal, 17 chars; issued pairing tokens are token_urlsafe(12) = 16 chars, in-memory, TTL-expired"),
    # test_v3_66_19_phase4_brightcove: POLICY_KEY = <this 12-char prefix> + "A" * 80;
    # the key the test uses is 80 repeated 'A' after the prefix.
    "531e7bdded27d04a22d22a163c00d749c84469da3c8edd8ac04ff535d8666b92": (
        FIXTURE, "policy-key prefix concatenated with 'A' * 80 in the test; the used value is a repeated-char pad"),
    # bd-opv captcha-detector fixture: <div class="g-recaptcha" data-sitekey=...>.
    # NOT Google's published test key (checked); a real-format v2 SITE key. Site
    # keys are public by design -- every protected page serves it in its HTML;
    # the credential is the paired secret key, which is not in this repository.
    "f5e95c9954635b65acab57e6d5ea663eb900f06dc8794a90358f5abb1c77d4e0": (
        NONSECRET, ("reCAPTCHA site key (data-sitekey): public by design, served in page HTML; "
                    "the secret key is not in the repo; not a credential")),
}


def _b64(s: str) -> bytes | None:
    padded = s + "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        pass
    try:
        return base64.b64decode(padded)
    except (binascii.Error, ValueError):
        return None


def _markers(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _MARKER.finditer(text)})


def _strip_filler(text: str) -> str:
    """Drop runs of >= 3 chars that step by +1, -1 or 0 (case-insensitive)."""
    low = text.lower()
    keep = [True] * len(text)
    i = 0
    while i < len(low) - 2:
        step = ord(low[i + 1]) - ord(low[i])
        if step in (-1, 0, 1) and low[i + 1].isalnum() and low[i].isalnum():
            j = i + 1
            while j + 1 < len(low) and low[j + 1].isalnum() and ord(low[j + 1]) - ord(low[j]) == step:
                j += 1
            if j - i + 1 >= 3:
                for k in range(i, j + 1):
                    keep[k] = False
                i = j + 1
                continue
        i += 1
    return "".join(c for c, k in zip(text, keep) if k)


def _residue(text: str) -> str:
    """What is left of a value once markers, filler and separators are removed."""
    return _SEPARATORS.sub("", _PLAIN_WORDS.sub("", _strip_filler(_MARKER.sub("", text))))


def _dominant_markers(text: str) -> str | None:
    """Evidence string when markers/filler make up all but <= _MAX_RESIDUE chars."""
    rest = _residue(text)
    if len(rest) > _MAX_RESIDUE:
        return None
    found = _markers(text)
    what = ("marker(s) " + ",".join(found) + " + ") if found else ""
    return f"{what}sequence/repeat filler make up the value; {len(rest)} other chars of {len(text)}"


def _jwt_evidence(value: str) -> str | None:
    parts = value.split(".")
    if len(parts) < 2 or not value.startswith("eyJ"):
        return None
    try:
        header = json.loads(_b64(parts[0]) or b"")
    except (ValueError, UnicodeDecodeError):
        return None
    alg = header.get("alg") if isinstance(header, dict) else None
    sig = parts[2] if len(parts) > 2 else ""
    want = _JWT_SIG_LEN.get(alg or "")
    if want is not None and len(sig) != want:
        return (f"jwt {alg} signature segment is {len(sig)} chars; a real {alg} MAC is "
                f"{want} -> cannot verify, never issued")
    if len(parts) < 3 or not sig:
        return "jwt has no signature segment -> cannot verify, never issued"
    return None


def classify(value: str, rotations: dict[str, str]) -> tuple[str, str]:
    """(class, evidence) for one secret value. Evidence never quotes the value."""
    digest = hashlib.sha256(value.encode()).hexdigest()
    if digest in rotations:
        return ROTATED, f"rotation record: {rotations[digest]}"
    if digest in KNOWN:
        cls, why = KNOWN[digest]
        return cls, f"reviewed (KNOWN): {why}"
    jwt = _jwt_evidence(value)
    if jwt:
        return FIXTURE, jwt
    dominant = _dominant_markers(value)
    if dominant:
        return FIXTURE, "fixture value: " + dominant
    raw = _b64(value)
    if raw and len(raw) >= 8 and all(32 <= c < 127 for c in raw):
        text = raw.decode("ascii")
        for size in range(3, len(text) // 2 + 1):
            unit = text[:size]
            if text.startswith(unit * (len(text) // size)) and len(text) // size >= 2:
                return FIXTURE, f"base64 decodes to a repeated {size}-char ASCII word"
        dominant = _dominant_markers(text)
        if dominant:
            return FIXTURE, "base64 decodes to ASCII fixture text: " + dominant
    if _PATH_LIKE.match(value):
        return NONSECRET, "not a credential: lowercase path segments (route/doc text)"
    if _HOSTNAME.match(value):
        return NONSECRET, "not a credential: a DNS hostname"
    return LIVE, "no evidence it is fake; treat as a real secret until rotated"


def _redacted(finding: dict) -> dict:
    entry = dict(finding)
    entry["Match"] = finding["Match"].replace(finding["Secret"], "REDACTED")
    entry["Secret"] = "REDACTED"
    return entry


def triage(findings: list[dict], rotations: dict[str, str]) -> list[dict]:
    rows = []
    for f in findings:
        if f.get("Secret") in ("", "REDACTED") or "REDACTED" in str(f.get("Secret")):
            raise ValueError(f"redacted finding {f.get('Fingerprint')}: COULD-NOT-LOOK")
        cls, evidence = classify(f["Secret"], rotations)
        rows.append({"finding": f, "class": cls, "evidence": evidence})
    return rows


def _load_rotations(path: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if path:
        for line in Path(path).read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                digest, _, record = line.partition("\t")
                if not record.strip():
                    raise ValueError(f"rotation row without a record: {digest}")
                out[digest.strip()] = record.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", help="unredacted gitleaks JSON report")
    ap.add_argument("--tsv", help="write SECRET-SCAN-TRIAGE.tsv here")
    ap.add_argument("--baseline", help="write FIXTURE/NONSECRET/ROTATED baseline entries here")
    ap.add_argument("--rotations", help="sha256<TAB>rotation-record file")
    args = ap.parse_args(argv)
    try:
        findings = json.loads(Path(args.report).read_text())
        rows = triage(findings, _load_rotations(args.rotations))
    except (OSError, ValueError, KeyError) as exc:
        print(f"COULD-NOT-LOOK: {exc}", file=sys.stderr)
        return 2
    if args.tsv:
        lines = ["fingerprint\tcommit\tpath\trule\tclass\tevidence"]
        for r in rows:
            f = r["finding"]
            lines.append("\t".join((f["Fingerprint"], f["Commit"], f["File"], f["RuleID"],
                                    r["class"], r["evidence"])))
        Path(args.tsv).write_text("\n".join(lines) + "\n")
    if args.baseline:
        entries = []
        for r in rows:
            if r["class"] == LIVE:
                continue
            entry = _redacted(r["finding"])
            entry["class"] = r["class"]
            entry["reason"] = r["evidence"]
            entries.append(entry)
        Path(args.baseline).write_text(json.dumps(entries, indent=2) + "\n")
    counts = {c: sum(r["class"] == c for r in rows) for c in (FIXTURE, NONSECRET, ROTATED, LIVE)}
    print(" ".join(f"{k}={v}" for k, v in counts.items()) + f" total={len(rows)}")
    for r in rows:
        if r["class"] == LIVE:
            f = r["finding"]
            print(f"LIVE\t{f['Fingerprint']}\t{f['Commit'][:12]}")
    return 1 if counts[LIVE] else 0


if __name__ == "__main__":
    sys.exit(main())
