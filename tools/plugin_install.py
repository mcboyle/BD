#!/usr/bin/env python3
"""Managed plugin install CLI (O5). Stdlib-only so plain `python3` runs it on
the headless stash venv-free.

  plugin_install.py install <path> [--ack] [--force]
  plugin_install.py list
  plugin_install.py ack [--on|--off]

Install gates on the API version-range and an at-your-own-risk acknowledgment;
it ast-reads the plugin manifest and NEVER executes the candidate module. There
is no signing -- BD is single-operator and plugins run in-process with full
privilege, so authenticity without containment would buy nothing. Loading stays
the operator's plugins.json + load_all() concern; install only stages + records.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import plugins  # noqa: E402


def _cmd_install(args) -> int:
    res = plugins.install_plugin(args.path, ack=args.ack, force=args.force)
    if not res.get("installed"):
        sys.stderr.write("install refused: %s\n" % res.get("reason", "?"))
        if res.get("disclaimer"):
            sys.stderr.write("\n" + res["disclaimer"] + "\n\n")
            sys.stderr.write("Re-run with --ack (one-shot) or persist with "
                             "`plugin_install.py ack --on`.\n")
        return 1
    print("installed %s (%s) -> %s" % (res.get("name"), res.get("version") or "?",
                                       res.get("file")))
    return 0


def _cmd_list(_args) -> int:
    print(json.dumps(plugins.installed_registry(), indent=2))
    return 0


def _cmd_ack(args) -> int:
    on = not args.off  # --on is the default; --off clears
    plugins.write_config({"risk_acknowledged": on})
    print("risk_acknowledged = %s" % on)
    if on:
        print("\n" + plugins.disclaimer())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="plugin_install.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("install", help="install a plugin from a local path")
    pi.add_argument("path")
    pi.add_argument("--ack", action="store_true",
                    help="acknowledge the no-sandbox risk for THIS install")
    pi.add_argument("--force", action="store_true",
                    help="overwrite an existing un-registered file")
    pi.set_defaults(fn=_cmd_install)

    pl = sub.add_parser("list", help="list registry-managed installs")
    pl.set_defaults(fn=_cmd_list)

    pa = sub.add_parser("ack", help="persist/clear the at-your-own-risk ack")
    pa.add_argument("--on", action="store_true", help="acknowledge (default)")
    pa.add_argument("--off", action="store_true", help="clear the acknowledgment")
    pa.set_defaults(fn=_cmd_ack)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
