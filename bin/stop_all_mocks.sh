#!/bin/bash
for p in /tmp/mock_plex.pid /tmp/mock_jellyfin.pid /tmp/mock_stash.pid; do
    [ -f "$p" ] && kill -TERM "$(cat "$p")" 2>/dev/null && rm -f "$p"
done
echo "Mocks stopped"
