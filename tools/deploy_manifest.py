#!/usr/bin/env python3
"""bd-deploy-manifest -- the files an OVERLAY deploy must DELETE, not just add.

Deploy is `unzip -o` over the live tree. That overwrites and adds -- it NEVER removes.
So a file DELETED in a cut keeps living on stash, and because the graph gates
(dependency_graph, import_graph_gate) GLOB THE DISK, the orphan is scanned as live
source: its import edge reappears, and the frozen baseline -- which no longer lists it
-- trips. 718 went RED on stash exactly this way: app_sched_exports.py was deleted at
716, the release zip is correct and does not contain it, but the overlay left the old
copy on disk, and test_import_graph_no_new_edges saw the ghost edge.

The release zip is a complete tree, so the removal set is derivable: any tracked file
present in the DEPLOYED tree but absent from the new zip is an orphan the deploy must
`rm`. bd-cut already computes this internally; this surfaces it as an operator artifact.

WHEN: after building a release that DELETES any file, before/with deploy. The operator
runs the emitted `rm` lines (or `--script` writes them) as part of the overlay.

Read-only (never deletes anything itself). ASCII. --json. --selftest.
"""
import argparse
import json
import os
import sys
import zipfile

G, R, Y, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# tracked source/artifact extensions -- a leftover of these can trip a gate. We do NOT
# propose deleting runtime data (the DB, downloads, logs), only versioned tree files.
_TRACKED = (".py", ".ts", ".tsx", ".js", ".json", ".md", ".txt", ".sh", ".html",
            ".css", ".jsx")
# never propose removing these even if absent from the zip -- they are runtime/local.
_NEVER_RM = ("bulk_downloader.db", "app_config.json", ".env", "sites_config.json",
             "secrets.json", "vault_tokens.json")


def _zip_members(zpath):
    with zipfile.ZipFile(zpath) as zf:
        return {n for n in zf.namelist() if not n.endswith("/")}


def _tree_members(root):
    out = set()
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__", "node_modules",
                                            "venv", ".venv", "dist", "build")]
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), root)
            out.add(rel)
    return out


def orphans(zpath, deployed_root):
    """Tracked files in the deployed tree but NOT in the new zip -- the overlay's
    removal set."""
    zmem = _zip_members(zpath)
    tmem = _tree_members(deployed_root)
    gone = []
    for rel in sorted(tmem - zmem):
        if not rel.endswith(_TRACKED):
            continue
        if any(rel.endswith(n) for n in _NEVER_RM):
            continue
        gone.append(rel)
    return gone


def selftest():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "tree")
        os.makedirs(os.path.join(root, "bulk_downloader"))
        # deployed tree has an extra .py (the orphan) + a runtime db
        open(os.path.join(root, "bulk_downloader", "keep.py"), "w").write("x")
        open(os.path.join(root, "bulk_downloader", "orphan.py"), "w").write("x")
        open(os.path.join(root, "bulk_downloader.db"), "w").write("db")
        # the new zip has keep.py only (orphan.py + db absent)
        zpath = os.path.join(td, "rel.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("bulk_downloader/keep.py", "x")
        g = orphans(zpath, root)
        p1 = g == ["bulk_downloader/orphan.py"]
        print(("%sPASS%s" if p1 else "%sFAIL%s") % ((G, RST) if p1 else (R, RST)) +
              "  POS: the deleted .py is flagged for removal (%s)" % g)
        ok &= p1
        # NEG: the runtime DB, though absent from the zip, is NOT proposed for deletion
        n1 = "bulk_downloader.db" not in g
        print(("%sPASS%s" if n1 else "%sFAIL%s") % ((G, RST) if n1 else (R, RST)) +
              "  NEG: a runtime DB absent from the zip is NOT proposed for deletion")
        ok &= n1
        # NEG: a file present in BOTH is not flagged
        n2 = "bulk_downloader/keep.py" not in g
        print(("%sPASS%s" if n2 else "%sFAIL%s") % ((G, RST) if n2 else (R, RST)) +
              "  NEG: a file present in the zip is not flagged")
        ok &= n2
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="bd-deploy-manifest")
    ap.add_argument("--zip", help="the release zip being deployed")
    ap.add_argument("--deployed", default=os.path.expanduser("~/BulkDownloader"),
                    help="the live deployed tree (default ~/BulkDownloader)")
    ap.add_argument("--script", action="store_true", help="emit rm lines only")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.zip:
        ap.error("--zip is required")
    if not os.path.isfile(a.zip):
        print("%sno such zip: %s%s" % (R, a.zip, RST))
        return 2
    if not os.path.isdir(a.deployed):
        print("%sdeployed tree not found: %s (run this ON stash, or pass --deployed)%s"
              % (Y, a.deployed, RST))
        return 2

    gone = orphans(a.zip, a.deployed)
    if a.json:
        print(json.dumps({"zip": a.zip, "deployed": a.deployed,
                          "orphans": gone, "count": len(gone)}, indent=2))
        return 0
    if a.script:
        for rel in gone:
            print("rm -f %s" % os.path.join(a.deployed, rel))
        return 0
    if not gone:
        print("%sno orphans%s -- the overlay deploy leaves nothing stale." % (G, RST))
        return 0
    print("%s%d orphan(s)%s -- present on the deployed tree, ABSENT from the release "
          "zip. An `unzip -o` overlay will NOT remove them, and a disk-globbing gate "
          "(import_graph, dependency_graph) will trip on the ghost. Delete them as part "
          "of the deploy:" % (R, len(gone), RST))
    for rel in gone:
        print("  rm -f %s" % os.path.join(a.deployed, rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
