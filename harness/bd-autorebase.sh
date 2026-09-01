#!/bin/bash
# NEUTERED 2026-08-29 22:37 -- it hard-reset queued worktrees and dropped their
# implementation commits (399/402 recovered from objects; 120/122/126 uncommitted
# work went to stash). Re-enable only after bd-rebase is proven to REPLAY the
# row commit onto main, never reset --hard to main. Sleeps so the watchdog revive
# is a no-op rather than a churn loop.
exec sleep 86400
