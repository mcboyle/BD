"""`scripts/deploy_fleet.sh` must refuse rather than deploy an empty fleet, and
must not carry the fleet's addresses in a public repo.

The addresses live in an UNTRACKED file by operator decision at v3.66.1039. That
only holds if something checks it, because the natural next edit is to "just put
the hosts in the script" -- so the check is here rather than in a comment.

The refusals matter more than they look. A host file that is missing, empty or
all-comments would otherwise deploy NOTHING and exit 0, which reads in a log
exactly like a successful fleet deploy. That is section 0's empty denominator,
in the one script whose whole job is to act on every host.
"""
import pathlib
import re
import subprocess

_REPO = pathlib.Path(__file__).resolve().parent.parent
_FLEET = _REPO / "scripts" / "deploy_fleet.sh"
_EXAMPLE = _REPO / "docs" / "repo" / "hosts.example"

# RFC 1918 plus RFC 5737's documentation range, which is what the example uses.
_PRIVATE = re.compile(r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b")


def _run(*args, **kw):
    return subprocess.run(["bash", str(_FLEET), *args], capture_output=True,
                          text=True, **kw)


def test_the_script_carries_no_fleet_addresses():
    """The operator's decision, enforced. A tracked address is public forever."""
    hits = _PRIVATE.findall(_FLEET.read_text(encoding="utf-8"))
    assert not hits, (
        "deploy_fleet.sh contains what look like real fleet addresses %s -- the "
        "host list is deliberately untracked because this repo is public" % hits)


def test_the_tracked_example_uses_documentation_addresses():
    """RFC 5737 (192.0.2.0/24) exists for exactly this. A tracked example with
    real addresses defeats the whole arrangement while looking like a template."""
    text = _EXAMPLE.read_text(encoding="utf-8")
    assert not _PRIVATE.findall(text), (
        "hosts.example carries private-range addresses; use 192.0.2.x")
    assert "192.0.2." in text, "the example has no example addresses at all"


def test_a_missing_host_file_is_refused_and_names_the_path():
    r = _run("--hosts", "/nonexistent/bd/hosts")
    assert r.returncode == 2, r
    assert "/nonexistent/bd/hosts" in r.stderr, (
        "the refusal does not say WHICH file is missing: %s" % r.stderr)


def test_an_empty_fleet_is_refused_not_reported_as_success(tmp_path):
    """The dangerous case: nothing deployed, exit 0, and a log that reads green."""
    f = tmp_path / "hosts"
    f.write_text("# every line a comment\n\n   \n", encoding="utf-8")
    r = _run("--hosts", str(f))
    assert r.returncode == 2, (
        "an all-comment host file exited %s -- deploying nothing must never "
        "report success: %s" % (r.returncode, r.stdout))


def test_a_line_without_an_address_is_refused_before_anything_runs(tmp_path):
    """Parse first, act second: a malformed list must not deploy half a fleet."""
    f = tmp_path / "hosts"
    f.write_text("good 192.0.2.10\nbroken\n", encoding="utf-8")
    r = _run("--hosts", str(f))
    assert r.returncode == 2, r
    assert "broken" in r.stderr, r.stderr


def test_a_dry_run_does_not_claim_a_deploy(tmp_path):
    """OVER-SENSITIVITY CONTROL on the summary line, and it caught a real bug.

    The first version fell through to "all N host(s) deployed and verified"
    after touching nothing. The summary is the line an operator reads.
    """
    f = tmp_path / "hosts"
    f.write_text("a 192.0.2.10\nb 192.0.2.11\n", encoding="utf-8")
    r = _run("--hosts", str(f), "--dry-run")
    assert r.returncode == 0, r
    assert "DRY RUN" in r.stdout and "NOTHING deployed" in r.stdout, r.stdout
    assert "deployed and verified" not in r.stdout, (
        "a dry run claimed a deploy: %s" % r.stdout)


def test_it_delegates_to_deploy_sh_rather_than_reimplementing_it():
    """Every safety property -- the pytest preflight, the stopped-window
    recovery, the health gate -- lives in deploy.sh. A second implementation
    here would drift from it silently."""
    src = _FLEET.read_text(encoding="utf-8")
    assert "scripts/deploy.sh" in src or "deploy.sh" in src
    for forbidden in ("systemctl stop", "git reset --hard", "__pycache__"):
        assert forbidden not in src, (
            "deploy_fleet.sh reimplements %r instead of delegating to deploy.sh"
            % forbidden)
