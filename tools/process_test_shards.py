#!/usr/bin/env python3
"""Deterministic split of tests/PROCESS_TESTS.txt across ci.yml's process-tests matrix.

    process_test_shards.py files <list> <k> <n>   print shard k of n (1-based), one path per line
    process_test_shards.py check <list> <n>       exit 0 iff the n shards partition the list

ci-shard r1 (O3203): the train-only process-tests lane ran every declared PROCESS file in one
job; it is now a matrix of n legs, each running `files <list> <k> <n>`. The split is the
declared entries (same parse as tools/ci_shards.process_tests: stripped, no blanks, no `#`
lines), deduplicated and sorted, dealt round-robin: entry i goes to shard i % n + 1. Sorting
makes the split independent of list order; round-robin keeps shard sizes within one file.

Every refusal exits 2 with a `PROCESS-SHARD:` line on stderr and prints nothing on stdout, so
the workflow's `test "${#files[@]}" -gt 0` and the check step both fail red, never silently
run zero files. Stdlib only: it runs before `pip install`.
"""
from __future__ import annotations

import sys
from pathlib import Path

MAX_LIST_BYTES = 1 << 20


class ShardError(Exception):
    pass


def entries(text: str) -> list[str]:
    return sorted({line.strip() for line in text.splitlines()
                   if line.strip() and not line.lstrip().startswith("#")})


def read_list(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ShardError(f"cannot read {path}: {exc.strerror}") from exc
    if len(raw) > MAX_LIST_BYTES:
        raise ShardError(f"{path} is {len(raw)} bytes, over the {MAX_LIST_BYTES}-byte cap")
    try:
        found = entries(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ShardError(f"{path} is not UTF-8") from exc
    if not found:
        raise ShardError(f"{path} declares no files")
    return found


def split(files: list[str], n: int) -> list[list[str]]:
    if n < 1:
        raise ShardError(f"shard count must be >= 1, got {n}")
    if n > len(files):
        raise ShardError(f"{n} shards for {len(files)} files leaves a shard empty")
    shards: list[list[str]] = [[] for _ in range(n)]
    for i, path in enumerate(files):
        shards[i % n].append(path)
    placed = [path for shard in shards for path in shard]
    if sorted(placed) != files or len(set(placed)) != len(placed):
        raise ShardError("split is not a partition of the declared list")
    return shards


def _int(value: str, what: str) -> int:
    try:
        return int(value, 10)
    except ValueError:
        raise ShardError(f"{what} must be an integer, got {value!r}") from None


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 4 and argv[0] == "files":
            k, n = _int(argv[2], "shard index"), _int(argv[3], "shard count")
            shards = split(read_list(Path(argv[1])), n)
            if not 1 <= k <= n:
                raise ShardError(f"shard index must be in 1..{n}, got {k}")
            sys.stdout.write("".join(f"{path}\n" for path in shards[k - 1]))
            return 0
        if len(argv) == 3 and argv[0] == "check":
            files = read_list(Path(argv[1]))
            shards = split(files, _int(argv[2], "shard count"))
            sizes = " ".join(str(len(shard)) for shard in shards)
            print(f"PROCESS-SHARD: OK {len(files)} files in {len(shards)} shards ({sizes})")
            return 0
    except ShardError as exc:
        print(f"PROCESS-SHARD: {exc}", file=sys.stderr)
        return 2
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
