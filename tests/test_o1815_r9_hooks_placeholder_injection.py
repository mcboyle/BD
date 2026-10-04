"""O1815 R9: placeholder substitution must not rescan substituted values.

_render_template used to substitute placeholders one after another on the
already-quoted string. A site-controlled {url} carrying the literal text
"{filename}" then had the shlex-quoted filename spliced INSIDE the url's
own quotes; the filename's opening quote closed the url's quote and its
body ran as shell via post_download_cmd (shell=True).
"""
import os
import shlex

import pytest

from bulk_downloader import hooks

BD_GATE_SCOPE = "module"

posix_only = pytest.mark.skipif(
    os.name == "nt", reason="run_command_hook refuses on Windows (ROW 444)")


def _attack_vars(marker):
    return {"url": "http://x/{filename}", "filename": f"a; touch {marker}; b"}


def test_url_containing_placeholder_text_is_not_rescanned(tmp_path):
    marker = tmp_path / "PWNED"
    rendered = hooks._render_template(
        "notify {url}", _attack_vars(marker), shell_quote=True)
    assert rendered == "notify " + shlex.quote("http://x/{filename}"), (
        f"O1815-R9: substituted {{url}} was rescanned for placeholders: {rendered!r}")


@posix_only
def test_command_hook_does_not_execute_spliced_filename(tmp_path):
    # Positive control: the probe can see execution -- an operator-trusted
    # template that touches a file does create it.
    control = tmp_path / "CONTROL"
    ok, out = hooks.run_command_hook(f"touch {shlex.quote(str(control))}", {}, timeout=30)
    assert ok and control.exists(), f"positive control did not execute: {out!r}"

    marker = tmp_path / "PWNED"
    ok, out = hooks.run_command_hook("echo {url}", _attack_vars(marker), timeout=30)
    assert not marker.exists(), (
        f"O1815-R9: site-controlled {{url}}+{{filename}} escaped shlex.quote and ran: {out!r}")
    assert ok and "http://x/{filename}" in out


def test_template_semantics_preserved():
    vars = {"site": "s 1", "filename": "f.mp4", "key": ""}
    assert hooks._render_template(
        "{site}/{filename} --o={key} {missing}", vars, shell_quote=True
    ) == "'s 1'/f.mp4 --o= {missing}"
    assert hooks._render_template("[{site}] {filename}{url}", {"site": "s", "filename": "f", "url": ""}) == "[s] f"
    assert hooks._render_template("{a}", {}) == "{a}"
    assert hooks._render_template("", {"a": "b"}) == ""
