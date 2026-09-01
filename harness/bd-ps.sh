#!/bin/bash
# Find live processes by argv WITHOUT the two failure modes I keep hitting:
#   - `pgrep -f X` matches the shell that is doing the searching (self-match)
#   - a bare /proc loop races processes that exit mid-scan and spews hundreds
#     of "No such file or directory" lines, drowning the answer it was asked for
# Reads argv from /proc, skips itself and its parent, and silences the race.
set -u
PAT="${1:?usage: bd-ps.sh <argv-substring>}"
SELF=$$; PARENT=$PPID
for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
  [ "$p" = "$SELF" ] || [ "$p" = "$PARENT" ] && continue
  # `tr ... < /proc/$p/cmdline 2>/dev/null` does NOT silence this: the failure
  # comes from the SHELL opening the redirect, not from tr, so its stderr
  # redirection never applies. cat inside the pipeline owns the open.
  a=$(cat "/proc/$p/cmdline" 2>/dev/null | tr '\0' ' ')
  [ -z "$a" ] && continue
  case "$a" in *"$PAT"*) printf '%s  %.100s\n' "$p" "$a";; esac
done
