set +e
cd /home/claude/work
Z=/mnt/user-data/uploads/BulkDownloader_v3_66_137.zip
STAGE=/home/claude/release_150
OUT=/mnt/user-data/outputs/BulkDownloader_v3_66_150.zip
rm -rf "$STAGE"; mkdir -p "$STAGE"
unzip -Z1 "$Z" | grep -vE '/$' | grep -vE '__pycache__|\.pyc$' | sort -u > /tmp/l137.txt
( find bulk_downloader tests tools docs kb live_tests extension frontend/src frontend/dist scripts templates -type f 2>/dev/null
  find . -maxdepth 1 -type f \( -name '*.md' -o -name '*.txt' \) 2>/dev/null | sed 's|^\./||'
) | grep -vE '__pycache__|\.pyc$|/node_modules/|/venv/' | sort -u > /tmp/wf.txt
comm -23 /tmp/wf.txt /tmp/l137.txt > /tmp/add.txt
cat /tmp/l137.txt /tmp/add.txt | sort -u > /tmp/ship.txt
miss=0; cp=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if [ -f "$f" ]; then mkdir -p "$STAGE/$(dirname "$f")"; cp "$f" "$STAGE/$f"; cp=$((cp+1)); else echo "  MISSING: $f"; miss=$((miss+1)); fi
done < /tmp/ship.txt
echo "copied=$cp missing=$miss"
echo "--- new-in-150 additions ---"
grep -E "pattern_hygiene|test_v3_66_150" /tmp/add.txt
echo "--- new dist bundle shipped ---"
ls "$STAGE"/frontend/dist/assets/ 2>/dev/null
rm -f "$OUT"; ( cd "$STAGE" && zip -r -q -X "$OUT" . )
echo "zip: $(du -h "$OUT"|cut -f1), $(unzip -Z1 "$OUT"|wc -l) entries; version $(unzip -p "$OUT" bulk_downloader/__init__.py | grep __version__ | tr -d ' ')"
