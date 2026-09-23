"""Host-wide admission for bd-band and bd-precut (H341).

BD_BATTERY_MAX is the host's positive slot count (default 2); all callers must
use the same setting. Fixed /tmp paths span worktrees and working directories.
Never unlink slot files: replacing a locked inode would admit a second holder.
A full/unavailable queue returns EX_TEMPFAIL (75), never a test verdict.
An admitted battery exports BD_BATTERY_HELD=<its pid>; a nested entry point
(a test inside the battery, or a remote lane slot that already holds its own
flock) whose marker names a live process is admitted without a second slot.
"""
import fcntl
import os
import sys
from functools import wraps

LOCK_BASE = "/tmp/bd-battery.lock"
HELD = "BD_BATTERY_HELD"
REFUSED = 75


def _held_by_live_battery():
    raw = os.environ.get(HELD, "")
    if not raw.isdigit() or int(raw) < 1:
        return False
    try:
        os.kill(int(raw), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def battery_limited(main):
    """Hold one non-inherited flock until the entry point exits, even on error."""
    @wraps(main)
    def run(*args, **kwargs):
        if _held_by_live_battery():
            return main(*args, **kwargs)
        raw = os.environ.get("BD_BATTERY_MAX", "2")
        try:
            maximum = int(raw)
            if maximum < 1:
                raise ValueError("must be positive")
        except ValueError:
            print(f"REFUSED-QUEUE: BD_BATTERY_MAX must be a positive integer: {raw!r}",
                  file=sys.stderr)
            return REFUSED
        for slot in range(maximum):
            try:
                fd = os.open(f"{LOCK_BASE}.{slot}", os.O_CREAT | os.O_RDWR, 0o600)
            except OSError as exc:
                print(f"REFUSED-QUEUE: battery slot unavailable: {exc}", file=sys.stderr)
                return REFUSED
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    print(f"REFUSED-QUEUE: battery lock unavailable: {exc}", file=sys.stderr)
                    return REFUSED
                # Python opens non-inheritable descriptors; subprocesses must
                # not retain a slot after the top-level battery exits.
                previous = os.environ.get(HELD)
                os.environ[HELD] = str(os.getpid())
                try:
                    return main(*args, **kwargs)
                finally:
                    if previous is None:
                        os.environ.pop(HELD, None)
                    else:
                        os.environ[HELD] = previous
            finally:
                os.close(fd)
        print(f"REFUSED-QUEUE: all {maximum} battery slots busy (BD_BATTERY_MAX)",
              file=sys.stderr)
        return REFUSED
    return run
