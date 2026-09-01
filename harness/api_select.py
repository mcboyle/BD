#!/usr/bin/env python3
"""Choose ONE credential per site, by evidence, and show the choice for review.

Most sites carry several entries -- dfxtra has seven -- and picking a stale one
means a failed login against a real account. Proton Pass records modifyTime, so
recency is real evidence where it exists; where it does not, the entry is still
shown so the operator can correct it. Passwords are never printed: only the
entry's name, its username, and how the choice was made.
"""
import csv, glob, json, os, pathlib, sys

SITES = ["evilangel", "adulttime", "dfxtra", "bang.com", "bangbros", "brazzers",
         "naughtyamerica", "nubiles-porn", "nubilefilms", "vixenplus", "vip4k",
         "wowgirls", "reptyle", "pegasproductions", "kink", "nookies", "tiny4k",
         "ultrafilms", "teenmegaworld", "kellymadison"]


def mask(u: str) -> str:
    """Enough to recognise the account, not enough to publish it."""
    u = (u or "").strip()
    if "@" in u:
        a, b = u.split("@", 1)
        return f"{a[:2]}{'*' * max(len(a) - 2, 1)}@{b}"
    return f"{u[:2]}{'*' * max(len(u) - 2, 1)}"


def main():
    cands = {}
    for path in sorted(glob.glob(os.path.expanduser("~/.bd-import/*.csv"))):
        src = pathlib.Path(path).name
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                hay = f"{row.get('url','')} {row.get('name','')}".lower()
                user = (row.get("username") or row.get("email") or "").strip()
                pw = (row.get("password") or "").strip()
                if not (user and pw):
                    continue
                for s in SITES:
                    if s in hay:
                        cands.setdefault(s, []).append({
                            "src": src, "name": (row.get("name") or "")[:28],
                            "user": user, "password": pw,
                            "totp": bool((row.get("totp") or "").strip()),
                            "mtime": (row.get("modifyTime") or "").strip(),
                            "url": (row.get("url") or "")[:44],
                        })
                        break
    chosen = {}
    print(f"{'site':18} {'chosen account':26} {'why':30} alts")
    for s in SITES:
        entries = cands.get(s) or []
        if not entries:
            print(f"{s:18} {'-- none --':26}")
            continue
        dated = [e for e in entries if e["mtime"].isdigit()]
        if dated:
            pick = max(dated, key=lambda e: int(e["mtime"]))
            why = "newest modifyTime"
        else:
            uniq = {e["password"] for e in entries}
            pick = entries[0]
            why = ("only one password" if len(uniq) == 1
                   else f"NO DATES -- {len(uniq)} differ, REVIEW")
        chosen[s] = pick
        print(f"{s:18} {mask(pick['user']):26} {why:30} {len(entries)}"
              + ("  [TOTP]" if pick["totp"] else ""))
    out = pathlib.Path.home() / ".bd-import" / "selected.json"
    out.write_text(json.dumps(chosen))
    out.chmod(0o600)
    ambiguous = [s for s, p in chosen.items()
                 if len({e["password"] for e in cands[s]}) > 1
                 and not any(e["mtime"].isdigit() for e in cands[s])]
    print(f"\nstaged {len(chosen)} selection(s) at {out} (0600); vault still untouched")
    if ambiguous:
        print(f"AMBIGUOUS, needs your eyes: {', '.join(ambiguous)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
