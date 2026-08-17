#!/usr/bin/env python3
"""Export the canonical live OpenAPI 3.1 document.

The sole producer is :func:`bulk_downloader.openapi_spec.generate`, which is
also called by ``/api/openapi.json``.  This adapter only imports the complete
Flask route map and serializes that same result.  It never maintains a second
schema or a checked-in export.

Usage::

    python tools/build_openapi.py --stdout
    python tools/build_openapi.py --out /tmp/openapi.json

With neither flag the document is written to stdout.  Exit 0 means a complete
document was emitted; exit 2 means import, generation, or I/O failed.
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_endpoint_catalog as BEC  # type: ignore  # noqa: E402
from bulk_downloader.openapi_spec import generate  # noqa: E402


def _spec() -> dict:
    # Some optional blueprint registrations narrate to stdout at import time.
    # Keep stdout machine-readable by routing that narration to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        app = BEC._import_app()
    return generate(app)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--stdout", action="store_true",
                        help="write the canonical document to stdout")
    output.add_argument("--out", type=Path,
                        help="write one transient canonical document")
    args = parser.parse_args(argv)

    try:
        text = json.dumps(_spec(), indent=2, sort_keys=False) + "\n"
        if args.out is None:
            sys.stdout.write(text)
        else:
            args.out.write_text(text, encoding="utf-8")
            print(f"wrote {args.out}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail closed
        print(f"REFUSED: could not export OpenAPI: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
