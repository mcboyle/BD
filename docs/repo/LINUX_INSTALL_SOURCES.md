# Linux installation source coverage

Run `venv/bin/python tools/install_source_coverage.py` from a checkout. The
repository gate is `tests/test_install_source_coverage.py`, scheduled in CI.
The command reads sources and Git's tracked-file set; it runs no installer,
package manager, service, login or network request.

## Supported entries and evidence

The seven entry points are declared in `tools/linux_install_sources.json`.
Every reference below must still resolve to tracked source. Launch targets
come from the live command text, including required shell descendants.

| Entry | Existing reference | Required local inputs |
| --- | --- | --- |
| `SETUP.md`, Linux / macOS section | Its executable manual setup block | `requirements.txt`, `sites_config.example.json`, `downloader_ui.py` |
| `install_linux.sh` | Provisioner's `run_step ... core bash ./install_linux.sh` | Entry script; its local install aids listed below are optional on this path |
| `start_linux.sh` | Installer's “Start the app” command | `downloader_ui.py` |
| `install_service.sh` | `FRESH_HOST_BRINGUP.md` service instructions | `downloader_ui.py`, `bulk_downloader/ai_boot_readiness.py` |
| `scripts/provision_test_host.sh` | `FRESH_HOST_BRINGUP.md` provisioner instructions | `install_linux.sh`, `requirements-test.txt`, both system/dev fragments, requirement checker, inventory and graph tools |
| `scripts/deploy_fleet.sh` | Tracked fleet launcher's local and remote deploy commands | `scripts/deploy.sh`, then its required closure |
| `scripts/deploy.sh` | `FRESH_HOST_BRINGUP.md`, Routine deploy and rollback | Core/test/cloak manifests, frontend package/lock manifests, requirement checker, inventory/graph tools, `scripts/lib/download_dirs.sh` |

The current live population is **34 owner→source pairs: 26 required and 8
optional, covering 18 distinct paths**. Repeated calls within one owner count
once. This is an executable-input census; Python's internal import closure is
the existing dependency graph's responsibility.

## Requiredness belongs to the invocation

`install_linux.sh` permits absent `scripts/lib/system_deps.sh`: it reports that
the system-package step was skipped and continues. The provisioner requires
the same fragment and exits 2 when it is absent. Its `dev_capabilities.sh`
fragment is also required: an absent fragment records FAIL, even though some
capabilities defined inside it are optional. A filename-wide exemption would
hide both provisioner failures.

The installer's core requirements have a pinned-package fallback; its test
requirements and inventory generator are guarded; its cloak manifest has an
index fallback; the frontend directory may be absent, and a missing lockfile
uses `npm install`. Those seven installer inputs are optional on that path.
The service's version-writing helper is the eighth optional pair: absence
warns, and its generated unit uses `ExecStartPre=-`.

Optional labels pin the complete relevant live control-flow block (excluding
blank/comment lines). A changed block fails and requires a fresh review of
the branch; the tool never assumes that a file-existence guard means optional.
Each launch retains its source line. Only occurrences inside that guarded
region inherit its optionality; another invocation of the same file outside
the region is required. Root-variable bindings are scoped to the owning
script and checked against its live assignments. All other recognized local
inputs are required, including newly added calls.

## What is outside the tracked source set

Generated `venv/bin/activate`, venv executables, systemd units, frontend bundles,
reports and deployment pins need not be tracked. The fleet host inventory and
the operator's `$HOME/bd-vault-unlock.sh` are external. The two encountered
generated/external launch references are reported separately by the command.
They are not counted as successfully checked required sources.

`install_dev.bat` and `install_windows.bat` are outside the Linux entry section.
No Windows file is restored or deleted. AI/tool bootstrap scripts, including
the cloud session bootstrap, are outside this app/fleet entry set unless a
supported entry actually invokes one. An added `bash install_ai_ollama.sh`
launch is therefore discovered and checked normally; the service's existing
app-owned AI readiness module is already included.

## Failure contract and boundary

An empty entry set, an empty local-input census, a missing/untracked required
target, an unresolved local variable or an escaping/untracked symlink fails.
Diagnostics name the owning entry and expression. Complete tracked/resolving
required inputs pass even if optional files are absent.

The recognizer handles the current launch forms: `source`/`.`; Bash `.sh`
launches; Python `.py` and app-module launches; pip requirement arguments;
the deploy requirement-convergence calls; generated service commands; and
frontend package inputs. It is deliberately not an arbitrary Bash interpreter.
New dynamic launcher conventions require an explicit recognizer and tests;
unresolved variables in recognized local launches always fail closed.
