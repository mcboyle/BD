"""The derived affected band must name real current provider-resolve suites."""
import json
from pathlib import Path
import subprocess
import sys


BD_GATE_SCOPE = "module"
ROOT = Path(__file__).resolve().parents[1]


def test_provider_resolve_band_contains_only_existing_current_suites():
    result = subprocess.run(
        [sys.executable, "toolchain/bin/bd-band-derive", "--files",
         "bulk_downloader/ytdlp_updater.py",
         "bulk_downloader/runner_extractors.py",
         "bulk_downloader/ytdlp_extractor.py",
         "bulk_downloader/provider_resolve_impl/youtube.py", "--json"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    band = json.loads(result.stdout)["band"]
    missing = [path for path in band if not (ROOT / path).is_file()]
    assert not missing, f"derived provider-resolve band names missing suites: {missing}"
    assert "tests/test_v3_66_16_phase4_p4_provider_resolve.py" in band
    assert "tests/test_v3_66_26_phase4_youtube_cipher.py" in band
