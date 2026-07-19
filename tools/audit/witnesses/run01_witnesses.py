#!/usr/bin/env python3
"""RUN-01 witness suite -- self-checking knowledge for the runner-kernel audit."""
import io
import os
import re
import sys
import json
import shutil
import subprocess

RESULTS = []
WORK = os.environ.get("BD_WORK", "/home/claude/work")
RUNNER = os.path.join(WORK, "bulk_downloader/runner.py")
EXTRACT = os.path.join(WORK, "bulk_downloader/runner_extractors.py")


def w(claim_id, kind, flips_to=""):
    def deco(fn):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"witness raised: {type(e).__name__}: {e}"
        RESULTS.append({"id": claim_id, "kind": kind, "ok": bool(ok),
                        "flips_to": flips_to, "detail": detail})
        return fn
    return deco


def _src(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _lines(path):
    return _src(path).splitlines()


@w("F-RUN01-01", "finding", flips_to="ok=False once the guard uses `not is_global`")
def _w1():
    import ipaddress
    src = _src(RUNNER)
    m = re.search(r"ip_obj\.is_private\s+or\s+ip_obj\.is_loopback"
                  r"[\s\S]{0,180}?is_unspecified", src)
    if not m:
        return False, "denylist predicate not found where expected (source changed)"
    guard_text = m.group(0)
    uses_is_global = "is_global" in guard_text
    ip = ipaddress.ip_address("100.64.0.1")
    denylisted = (ip.is_private or ip.is_loopback or ip.is_link_local
                  or ip.is_multicast or ip.is_reserved or ip.is_unspecified)
    accepted_by_guard = not denylisted
    should_reject = not ip.is_global
    gap = accepted_by_guard and should_reject and (not uses_is_global)
    return gap, (f"guard uses is_global={uses_is_global}; 100.64.0.1 "
                 f"denylisted={denylisted} is_global={ip.is_global} -> "
                 f"accepted_but_not_global={gap}")


@w("F-RUN01-02", "finding",
   flips_to="ok=False once builder inserts '--' before the url argv element")
def _w2():
    sys.path.insert(0, WORK)
    from bulk_downloader.runner_extractors import _build_ytdlp_cmd
    cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/tmp/x", url="--version")
    body = cmd[:-1]
    has_sep = "--" in body
    url_is_last = cmd[-1] == "--version"
    structural_vuln = url_is_last and not has_sep
    behavioural = None
    ytdlp = shutil.which("yt-dlp") or shutil.which("youtube-dl")
    if ytdlp:
        real = _build_ytdlp_cmd(ytdlp=ytdlp, dl_dir="/tmp/x", url="--version")
        r = subprocess.run(real, capture_output=True, text=True, timeout=60)
        out = (r.stdout or "").strip().splitlines()
        behavioural = (r.returncode == 0 and bool(out)
                       and bool(re.match(r"^\d{4}\.\d", out[0])))
    vuln = structural_vuln and (behavioural is not False)
    return vuln, (f"has_bare_'--'_separator={has_sep}; url_is_argv[-1]="
                  f"{url_is_last}; ytdlp_parsed_slot_as_option={behavioural}; "
                  f"cmd_tail={cmd[-3:]}")


@w("FP-RUN01-01", "fp_confirmation", flips_to="n/a (permanent FP class)")
def _w3():
    ls = _lines(RUNNER)
    region = "\n".join(ls[1046:1058])
    is_log = "log_event" in region and "Disk pressure" in region
    no_sql = ("execute(" not in region and "SELECT" not in region.upper())
    return (is_log and no_sql), ("runner.py:~1055 is self.log_event('disk_throttle', "
                                 "f'Disk pressure...') -- log call, no SQL")


@w("FP-RUN01-02", "fp_confirmation", flips_to="n/a (permanent FP class)")
def _w4():
    ls = _lines(EXTRACT)
    region = "\n".join(ls[340:349])
    is_log = "stderr.write" in region and "deep-detect" in region
    no_sql = "execute(" not in region
    return (is_log and no_sql), ("runner_extractors.py:~343 is sys.stderr.write("
                                 "f'deep-detect: WARNING...') -- log, no SQL")


@w("FP-RUN01-03", "fp_confirmation", flips_to="n/a (permanent FP class)")
def _w5():
    ls = _lines(RUNNER)
    assign = [l for l in ls[1140:1145]
              if re.search(r"login_session\s*=\s*getattr\(self,\s*['\"]"
                           r"_manual_login_handle['\"],\s*None\)", l)]
    return bool(assign), ("login_session = getattr(self, '_manual_login_handle', "
                          "None) -> always bound; no NameError path")


@w("A-RUN01-subproc-noshell", "assurance", flips_to="RED if shell=True appears")
def _w6():
    src = _src(EXTRACT)
    call = re.search(r"subprocess\.run\(cmd[^\n]*\)", src)
    no_shell_true = "shell=True" not in src
    list_argv = bool(re.search(r"cmd\s*=\s*\[", src))
    return (bool(call) and no_shell_true and list_argv), (
        f"subprocess.run(cmd,...) list-form; shell=True absent={no_shell_true}")


@w("A-RUN01-cookie-count-only", "assurance",
   flips_to="RED if a cookie VALUE is interpolated into a log")
def _w7():
    src = _src(RUNNER)
    good = "injected {len(" in src or re.search(r"len\([a-z_.]*cookies\)", src)
    leaks = re.findall(r"log_event\([^)]*cookies\[[^)]*\.value", src)
    return (bool(good) and not leaks), (
        "cookie injection logs use len(cookies) counts; no cookie .value in logs")


@w("A-RUN01-updatejob-pathgate", "assurance", flips_to="RED if isfile gate drops")
def _w8():
    src = _src(RUNNER)
    joins = len(re.findall(r"os\.path\.join\(dl_dir,\s*filename", src)) \
        + len(re.findall(r"os\.path\.join\(dl_dir,\s*filename_", src))
    gated = len(re.findall(r"if\s+os\.path\.isfile\(file_path\)", src))
    return (joins >= 3 and gated >= 3), (
        f"os.path.join(dl_dir, filename) sites={joins}, "
        f"each behind os.path.isfile gate; count={gated}")


@w("A-RUN01-no-dyn-exec", "assurance", flips_to="RED if any dynamic exec appears")
def _w9():
    bad = []
    for p in (RUNNER, EXTRACT):
        s = _src(p)
        for pat in (r"\beval\(", r"\bexec\(", r"os\.system\(", r"shell\s*=\s*True",
                    r"pickle\.load", r"yaml\.load\((?!.*Loader)"):
            if re.search(pat, s):
                bad.append(f"{os.path.basename(p)}:{pat}")
    return (not bad), (f"no eval/exec/os.system/shell=True/pickle in either file"
                       if not bad else f"FOUND: {bad}")


@w("F-RUN01-03", "finding",
   flips_to="ok=False once a math.isfinite (or upstream validator) rejects NaN/inf")
def _w10():
    src = _src(RUNNER)
    site_a = bool(re.search(r"threshold\s*=\s*float\(self\.config\.get\("
                            r"['\"]disk_threshold_gb['\"]", src))
    site_b = bool(re.search(r"target_mbps\s*=\s*float\(self\.config\.get\("
                            r"['\"]bandwidth_target_mbps['\"]", src))
    no_isfinite_near = "math.isfinite" not in src and "isfinite" not in src
    nan = float("nan")
    free = 1.0
    gate_would_fire_with_nan = (free < nan)
    vuln = site_a and site_b and no_isfinite_near and (not gate_would_fire_with_nan)
    return vuln, (f"float(disk_threshold_gb)={site_a} float(bandwidth_target_mbps)="
                  f"{site_b}; no isfinite guard={no_isfinite_near}; "
                  f"free<NaN fires_gate={gate_would_fire_with_nan} (NaN evades)")


def main():
    findings = [r for r in RESULTS if r["kind"] == "finding"]
    fps = [r for r in RESULTS if r["kind"] == "fp_confirmation"]
    assur = [r for r in RESULTS if r["kind"] == "assurance"]
    print(f"RUN-01 witnesses: {len(RESULTS)} total | "
          f"findings={len(findings)} fp={len(fps)} assurances={len(assur)}")
    print("-" * 72)
    allok = True
    for r in RESULTS:
        tag = "DEMONSTRATED" if r["ok"] else "NOT-SHOWN"
        if not r["ok"]:
            allok = False
        print(f"[{tag:>12}] {r['id']:<26} {r['kind']}")
        print(f"               {r['detail']}")
    print("-" * 72)
    print("ALL WITNESSES EXECUTED + DEMONSTRATED THEIR CLAIM"
          if allok else "SOME WITNESSES DID NOT DEMONSTRATE (see NOT-SHOWN above)")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
