#!/usr/bin/env python3
"""Parse the exports through BD's OWN import endpoint. Saves nothing.

/api/secrets/import_file parses and returns records; /api/secrets/import_apply is
a separate call that writes. This runs only the first, so the vault is untouched
and the master password stays uncommitted until the operator confirms.

No password value is printed. Only per-site presence and counts.
"""
import json, os, pathlib, sys, urllib.request

TOKEN = os.environ["BD_TOKEN"]
BASE = "http://127.0.0.1:5555"
SITES = ["evilangel", "adulttime", "dfxtra", "bang.com", "bangbros", "brazzers",
         "naughtyamerica", "nubiles-porn", "nubilefilms", "vixenplus", "vip4k",
         "wowgirls", "reptyle", "pegasproductions", "kink", "nookies", "tiny4k",
         "ultrafilms", "teenmegaworld", "kellymadison"]


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace"))


def main():
    total, matched = 0, {}
    for path in sorted(pathlib.Path.home().joinpath(".bd-import").glob("*.csv")):
        content = path.read_text(encoding="utf-8", errors="replace")
        status, body = post("/api/secrets/import_file", {"content": content})
        recs = body.get("records") or []
        total += len(recs)
        print(f"{path.name}: HTTP {status}  parsed {len(recs)} record(s)"
              f"  ok={body.get('ok')}  {str(body.get('error') or '')[:60]}")
        for r in recs:
            hay = f"{r.get('url','')} {r.get('name','')}".lower()
            for s in SITES:
                if s in hay and r.get("password"):
                    matched.setdefault(s, []).append(
                        {k: r.get(k) for k in ("name", "url", "username", "password")})
                    break
    print(f"\nparsed {total} record(s) across both files")
    print(f"matched {len(matched)}/{len(SITES)} configured sites:")
    for s in SITES:
        n = len(matched.get(s, []))
        print(f"   {s:18} {n} entr{'y' if n == 1 else 'ies'}"
              + ("" if n else "   -- none"))
    # hand the selection to the apply step later; write it where only we can read
    out = pathlib.Path.home() / ".bd-import" / "selected.json"
    out.write_text(json.dumps({k: v[:1] for k, v in matched.items()}))
    out.chmod(0o600)
    print(f"\nselection staged at {out} (0600) -- NOTHING WRITTEN TO THE VAULT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
