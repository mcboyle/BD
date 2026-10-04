"""Report helpers must select UTF-8 independently of the process locale."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("helper", ["write_md", "write_report", "write_json"])
@pytest.mark.parametrize("unicode_text", [False, True])
def test_report_writes_under_ascii_locale(tmp_path, helper, unicode_text):
    # ASCII-only child source avoids making command decoding the experiment.
    code = r'''
import builtins
import codecs
import json
import locale
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
import report_core
assert sys.flags.utf8_mode == 0
assert locale.getpreferredencoding(False).lower() in ("ansi_x3.4-1968", "ascii", "us-ascii")
text = "caf\u00e9 \u2713 \u65e5\u672c\u8a9e" if sys.argv[4] == "True" else "ASCII control"
opened_encodings = []
def observed_open(*args, **kwargs):
    handle = builtins.open(*args, **kwargs)
    opened_encodings.append(codecs.lookup(handle.encoding).name)
    return handle
report_core.open = observed_open
outdir = Path(sys.argv[2]) / "new" / "nested"
helper = sys.argv[3]
path = outdir / ("report.json" if helper == "write_json" else "report.md")
if helper == "write_report":
    returned = report_core.write_report(str(outdir), path.name, text)
elif helper == "write_json":
    returned = report_core.write_json(str(path), {"value": text})
else:
    returned = report_core.write_md(str(path), text)
assert returned == str(path)
raw = path.read_bytes()
if helper == "write_json":
    expected = json.dumps({"value": text}, indent=2, default=str).encode("utf-8")
    assert json.loads(raw.decode("utf-8")) == {"value": text}
else:
    expected = text.encode("utf-8")
assert raw == expected, "REPORT-UTF8-BYTES: report contents changed"
# Observe the real file stream; JSON stays escaped, but its stream also owns UTF-8.
if sys.argv[4] == "True":
    assert opened_encodings == ["utf-8"], "REPORT-UTF8-STREAM: " + repr(opened_encodings)
print("REPORT-UTF8-PASS")
'''
    assert code.isascii()
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0",
           "PYTHONCOERCECLOCALE": "0"}
    result = subprocess.run(
        [sys.executable, "-c", code, str(REPO / "tools"), str(tmp_path),
         helper, str(unicode_text)], env=env, cwd=REPO, text=True,
        capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, (
        f"REPORT-UTF8-LOCALE helper={helper} unicode={unicode_text}: {result.stderr}")
    assert result.stdout.strip() == "REPORT-UTF8-PASS"
