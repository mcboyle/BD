# toolchain/tests-hub-only

Tests for `toolchain/bin/bd-axtree-distill` and `toolchain/bin/bd-dag-schedule` (added in 33d2401ce).

Both tools import packages that are not in this repository:
`axtree_distill` and `dag_task_compiler`, found only on the hub at
`/home/mboyle/teamwork_projects/frontier_blueprint_integration/src` (`axtree_distill` also needs `bs4`).
The tests therefore run only on the hub:

    PYTHONPATH=/home/mboyle/teamwork_projects/frontier_blueprint_integration/src \
      ./venv/bin/python -m pytest -q toolchain/tests-hub-only/

CI, the VM gate and the H622 / v3_66_939 census do not collect this directory (they read `tests/`).
Moved here from `tests/` by T101 (PM ruling A2, 2026-09-25) because they failed wherever that folder is absent.
To bring them back into CI, vendor both packages into the repo and move the tests back to `tests/`.
