#!/bin/bash
# Start all three media-server mocks in background. Kill with stop_all_mocks.sh.
DIR="$(dirname "$(readlink -f "$0")")"
python3 "$DIR/mock_plex.py" >/tmp/mock_plex.log 2>&1 &
echo "$!" > /tmp/mock_plex.pid
python3 "$DIR/mock_jellyfin.py" >/tmp/mock_jellyfin.log 2>&1 &
echo "$!" > /tmp/mock_jellyfin.pid
python3 "$DIR/mock_stash.py" >/tmp/mock_stash.log 2>&1 &
echo "$!" > /tmp/mock_stash.pid
sleep 0.5
echo "Mocks running:"
echo "  Plex      http://localhost:32400/  (PID $(cat /tmp/mock_plex.pid))"
echo "  Jellyfin  http://localhost:8096/   (PID $(cat /tmp/mock_jellyfin.pid))"
echo "  Stash     http://localhost:9999/   (PID $(cat /tmp/mock_stash.pid))"
