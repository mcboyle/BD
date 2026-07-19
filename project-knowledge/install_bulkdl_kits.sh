#!/bin/bash
# install_bulkdl_kits.sh -- install BulkDL kits inside the Claude sandbox.
#
# Auto-detects which bulkdl_*_kit.zip files were uploaded to
# /mnt/user-data/uploads/ and installs each.
#
# Paste at the start of a BulkDL session after uploading whichever
# kits you need.

set -e

UPLOADS="${UPLOADS:-/mnt/user-data/uploads}"
WORK_TREE="${WORK_TREE:-/home/claude/work}"

# Per SANDBOX_ENV_VARS.md §1.2, BD_DISABLE_KEEPALIVE=1 is required to
# stop bulk_downloader.app from spinning 14 background threads on
# every import (drops to 5 with this set). Critical for any python
# subprocess this installer launches.
export BD_HOME="${BD_HOME:-/home/claude/bd_home}"
export BD_DISABLE_KEEPALIVE="${BD_DISABLE_KEEPALIVE:-1}"

# Sandbox UPLOADS is read-only AND disk space is tight (~9 GB). Strategy:
#   - Don't pre-extract whole packs (each ~400 MB, ~3 packs = 1.2 GB extra)
#   - Extract one kit at a time, install, delete temp copy
#   - For ollama, stream parts directly into unzip; never materialize the
#     reassembled 3.3 GB zip as a separate file
STAGING=/tmp/bulkdl_staging
OKDIR="$STAGING/.ok"
mkdir -p "$STAGING" "$OKDIR"

# Sentinel-validated staging (v3.66.688 optimization, shared with bd-prestage):
# a full `unzip -t` costs ~92s per sweep of the staged set (~15 MB/s measured).
# $OKDIR/<kit> holds the byte size recorded AFTER a successful testzip; sentinel
# present + size match => trust (a stat, not a decompress). A kill mid-extract
# never receives a sentinel, so the v3.66.538 truncation guarantee holds.
_kit_valid() {
    local f="$STAGING/$1" s="$OKDIR/$1" sz
    [ -f "$f" ] || { rm -f "$s"; return 1; }
    sz=$(stat -c%s "$f" 2>/dev/null) || return 1
    if [ -f "$s" ] && [ "$(cat "$s" 2>/dev/null)" = "$sz" ]; then
        return 0
    fi
    if unzip -t "$f" >/dev/null 2>&1; then
        printf '%s' "$sz" > "$s"
        return 0
    fi
    rm -f "$s"
    return 1
}

# Discover which pack (if any) contains a given kit. Echo path or empty.
# Sets PACK_OF env each call so caller knows it came from a pack.
declare -A _PACK_INDEX
_index_packs() {
    [ -n "${_PACK_INDEX_BUILT:-}" ] && return
    for pack in "$UPLOADS"/pack_*.zip; do
        [ -f "$pack" ] || continue
        while IFS= read -r entry; do
            _PACK_INDEX["$entry"]="$pack"
        done < <(unzip -Z1 "$pack" 2>/dev/null | grep '\.zip$')
    done
    _PACK_INDEX_BUILT=1
}

# find_kit "bulkdl_X_kit.zip" -> echo a usable path to that zip.
# If the zip lives in a pack, extract it on-demand into STAGING.
# Caller may delete the file after installing (frees disk).
find_kit() {
    local name="$1"
    _index_packs
    # Direct upload wins
    if [ -f "$UPLOADS/$name" ]; then
        echo "$UPLOADS/$name"
        return 0
    fi
    # Already extracted in staging -- but ONLY trust it if it is a VALID zip.
    # A bd-prestage/bd-install run killed mid-write (exec-time limit) leaves a
    # TRUNCATED file that exists but fails `unzip -t`; trusting mere existence
    # here is the v3.66.538 truncation bug. Validate, and drop a bad file so we
    # re-extract it below.
    if [ -f "$STAGING/$name" ]; then
        if _kit_valid "$name"; then
            echo "$STAGING/$name"
            return 0
        fi
        echo "find_kit: staged $name is corrupt/truncated; re-extracting" >&2
        rm -f "$STAGING/$name" "$OKDIR/$name"
    fi
    # Available inside a pack -- extract, then VALIDATE before returning it.
    local pack="${_PACK_INDEX[$name]:-}"
    if [ -n "$pack" ]; then
        unzip -p "$pack" "$name" > "$STAGING/$name" 2>/dev/null \
            || { rm -f "$STAGING/$name" "$OKDIR/$name"; return 1; }
        if ! unzip -t "$STAGING/$name" >/dev/null 2>&1; then
            rm -f "$STAGING/$name" "$OKDIR/$name"
            echo "find_kit: extracted $name failed validation (pack corrupt?)" >&2
            return 1
        fi
        stat -c%s "$STAGING/$name" > "$OKDIR/$name"
        echo "$STAGING/$name"
        return 0
    fi
    return 1
}

# Clean up a kit extracted to STAGING after we installed it.
release_kit() {
    local name="$1"
    rm -f "$STAGING/$name" "$OKDIR/$name" 2>/dev/null
    return 0
}

echo "================================================================"
echo "  BulkDL sandbox kit installer"
echo "================================================================"

# --- KIT INVENTORY --------------------------------------------------------
echo
echo "-> Indexing kits available from uploads + packs..."
_index_packs
declare -A _SEEN
for f in "$UPLOADS"/bulkdl_*_kit.zip; do
    [ -f "$f" ] || continue
    _SEEN[$(basename "$f")]=upload
done
for name in "${!_PACK_INDEX[@]}"; do
    [ -n "${_SEEN[$name]:-}" ] || _SEEN[$name]="pack:$(basename "${_PACK_INDEX[$name]}")"
done
echo "   Kits found: ${#_SEEN[@]}"
for n in $(echo "${!_SEEN[@]}" | tr ' ' '\n' | sort); do
    echo "     $n  (${_SEEN[$n]})"
done

# --- OLLAMA REASSEMBLY (lightweight) -------------------------------------
# Don't write the 3.3 GB zip as a temp file -- pipe cat directly into
# unzip when the OLLAMA kit block runs. We just verify parts here.
if [ -f "$UPLOADS/ollama_part_0" ] \
   && [ ! -f "$UPLOADS/bulkdl_ollama_kit.zip" ]; then
    part_count=$(ls "$UPLOADS"/ollama_part_? 2>/dev/null | wc -l)
    echo
    echo "-> Detected $part_count ollama parts (will stream into unzip later)"

    if [ -f "$UPLOADS/ollama_part_checksum.txt" ]; then
        # Verify each PART (not the reassembled zip; we never produce that as a file)
        cd "$UPLOADS"
        part_check=$(grep -E '^[0-9a-f]+  ollama_part_[0-9]$' ollama_part_checksum.txt \
            | sha256sum -c - 2>&1)
        if echo "$part_check" | grep -qE "FAILED|WARNING"; then
            echo "   WARN: one or more parts has bad checksum:"
            echo "$part_check" | grep -E "FAILED|WARNING" | sed 's|^|     |'
        else
            echo "   OK all $part_count part checksums match"
        fi
        cd - >/dev/null
    fi
fi
# --- CORE -----------------------------------------------------------------
_kp=$(find_kit "bulkdl_core_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing CORE kit..."
    unzip -q -o "$_kp" -d /tmp/

    # FAST PATH: prestaged site-packages -> just extend PYTHONPATH
    if [ -d /tmp/prestaged_site_packages ]; then
        export PYTHONPATH="/tmp/prestaged_site_packages:${PYTHONPATH:-}"
        echo "  -> using prestaged site-packages (skipping pip install)"
    else
        # FALLBACK: old-style pip install from local wheels
        pip install --break-system-packages --no-index \
            --find-links /tmp/wheels \
            pytest pytest-xdist pytest-timeout \
            -r "$WORK_TREE/requirements-dev.txt" 2>&1 | tail -5
    fi

    chmod +x /tmp/tools_bin/*
    export PATH=/tmp/tools_bin:$PATH
    echo "  OK CORE installed"
else
    echo
    echo "  (no bulkdl_core_kit.zip found -- skipping)"
fi

# --- VENV -----------------------------------------------------------------
# Installs a venv at $WORK_TREE/venv/ so capture.sh and friends find it
# at the path they hard-code.
_kp=$(find_kit "bulkdl_venv_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing VENV kit..."
    # Extract to a scratch dir then move into place
    rm -rf /tmp/venv_kit
    mkdir -p /tmp/venv_kit
    unzip -q -o "$_kp" -d /tmp/venv_kit/

    if [ -d /tmp/venv_kit/venv ]; then
        # Rewrite pyvenv.cfg paths to point at the sandbox python.
        # The venv was created on stash; the home path embedded in
        # pyvenv.cfg won't exist in the sandbox.
        SANDBOX_PY=$(command -v python3)
        if [ -n "$SANDBOX_PY" ] && [ -f /tmp/venv_kit/venv/pyvenv.cfg ]; then
            sed -i \
                -e "s|^home = .*|home = $(dirname "$SANDBOX_PY")|" \
                -e "s|^executable = .*|executable = $SANDBOX_PY|" \
                -e "s|^command = .*|command = $SANDBOX_PY -m venv|" \
                /tmp/venv_kit/venv/pyvenv.cfg
            echo "  Rewrote pyvenv.cfg to use $SANDBOX_PY"
        fi

        # The bin/python symlink may point at the stash python path,
        # which doesn't exist in the sandbox. Recreate it pointing at
        # the actual sandbox python.
        if [ -e /tmp/venv_kit/venv/bin/python ]; then
            rm -f /tmp/venv_kit/venv/bin/python \
                  /tmp/venv_kit/venv/bin/python3 \
                  /tmp/venv_kit/venv/bin/python3.12 2>/dev/null
            ln -sf "$SANDBOX_PY" /tmp/venv_kit/venv/bin/python
            ln -sf "$SANDBOX_PY" /tmp/venv_kit/venv/bin/python3
            ln -sf "$SANDBOX_PY" /tmp/venv_kit/venv/bin/python3.12
        fi

        # Place at the path capture.sh expects
        mkdir -p "$WORK_TREE"
        rm -rf "$WORK_TREE/venv"
        mv /tmp/venv_kit/venv "$WORK_TREE/venv"

        # Smoke test
        if "$WORK_TREE/venv/bin/python" -c "import flask; print('flask', flask.__version__)" 2>/dev/null; then
            echo "  OK VENV installed at $WORK_TREE/venv/"
        else
            echo "  WARN: venv installed but flask import failed"
            "$WORK_TREE/venv/bin/python" -c "import flask" 2>&1 | tail -3
        fi
    else
        echo "  ERROR: venv/ dir not found in kit"
    fi
    rm -rf /tmp/venv_kit
fi

# --- DEV (pytest + pyinstaller) -------------------------------------------
# The lean rebuilt venv kit carries core+cloak+websockets but NOT the dev layer
# (requirements-dev.txt: pytest + pyinstaller). This kit restores it so the
# sandbox venv can run pytest-based / reload / monkeypatch tests (stronger than
# the custom run_tests.py). Offline from the kit wheelhouse; live pip fallback.
# Non-fatal: a missing dev kit just leaves the sandbox on run_tests.py only.
_kp=$(find_kit "bulkdl_dev_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing DEV kit (pytest + pyinstaller)..."
    VENV_PY="$WORK_TREE/venv/bin/python"
    if [ -x "$VENV_PY" ]; then
        rm -rf /tmp/dev_kit && mkdir -p /tmp/dev_kit
        unzip -q -o "$_kp" -d /tmp/dev_kit/
        WH=$(find /tmp/dev_kit -maxdepth 2 -type d -name 'pip-wheels-dev' | head -1)
        if [ -n "$WH" ]; then
            env -u PYTHONPATH "$VENV_PY" -m pip install --no-index --find-links "$WH" pytest pyinstaller 2>&1 | tail -3
            if env -u PYTHONPATH "$VENV_PY" -c "import pytest" 2>/dev/null; then
                echo "  OK DEV kit installed (pytest $(env -u PYTHONPATH "$VENV_PY" -c 'import pytest;print(pytest.__version__)' 2>/dev/null))"
            else
                echo "  WARN: dev wheels present but pytest import failed"
            fi
        else
            echo "  WARN: pip-wheels-dev/ not found in dev kit"
        fi
        rm -rf /tmp/dev_kit
    else
        echo "  (venv not present yet; bd-venv --dev will install the dev layer)"
    fi
fi

# --- OPTIONAL -------------------------------------------------------------
_kp=$(find_kit "bulkdl_optional_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing OPTIONAL kit..."
    unzip -q -o "$_kp" -d /tmp/opt/
    # Prefer the requirements file bundled in the kit (post-fix); fall
    # back to $WORK_TREE for older kits.
    if [ -f /tmp/opt/requirements-optional.txt ]; then
        _req=/tmp/opt/requirements-optional.txt
    else
        _req="$WORK_TREE/requirements-optional.txt"
    fi
    pip install --break-system-packages --no-index \
        --find-links /tmp/opt/wheels \
        -r "$_req" 2>&1 | tail -5 \
        || echo "  (some optional packages may not have installed; OK to ignore)"
    echo "  OK OPTIONAL installed"
fi

# --- MEDIA ----------------------------------------------------------------
_kp=$(find_kit "bulkdl_media_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing MEDIA kit..."
    unzip -q -o "$_kp" -d /tmp/media/
    chmod +x /tmp/media/tools_bin/*
    export PATH=/tmp/media/tools_bin:$PATH
    echo "  OK MEDIA installed"
fi

# --- GTK ------------------------------------------------------------------
_kp=$(find_kit "bulkdl_gtk_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing GTK kit..."
    mkdir -p /home/claude/.local
    unzip -q -o "$_kp" -d /home/claude/.local/
    if [ -d /home/claude/.local/extracted ] && [ ! -d /home/claude/.local/gtk ]; then
        mv /home/claude/.local/extracted /home/claude/.local/gtk
    fi

    GTK_ROOT=/home/claude/.local/gtk

    export LD_LIBRARY_PATH="$GTK_ROOT/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
    export GI_TYPELIB_PATH="$GTK_ROOT/usr/lib/x86_64-linux-gnu/girepository-1.0:${GI_TYPELIB_PATH:-}"
    export XDG_DATA_DIRS="$GTK_ROOT/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
    if [ -d "$GTK_ROOT/usr/lib/python3/dist-packages" ]; then
        export PYTHONPATH="$GTK_ROOT/usr/lib/python3/dist-packages:${PYTHONPATH:-}"
    fi

    # Add Xvfb + X11 utils to PATH so we can spawn the display
    if [ -d "$GTK_ROOT/usr/bin" ]; then
        export PATH="$GTK_ROOT/usr/bin:$PATH"
    fi

    # Spawn Xvfb on :99 so GTK widgets can actually render (not just import)
    XVFB_BIN="$GTK_ROOT/usr/bin/Xvfb"
    if [ -x "$XVFB_BIN" ]; then
        # Kill any leftover Xvfb from a previous session
        pkill -f "Xvfb :99" 2>/dev/null || true
        sleep 0.3
        "$XVFB_BIN" :99 -screen 0 1024x768x24 -nolisten tcp \
            >/tmp/xvfb.log 2>&1 &
        XVFB_PID=$!
        sleep 0.5
        if kill -0 "$XVFB_PID" 2>/dev/null; then
            export DISPLAY=:99
            echo "  Xvfb running on :99 (PID $XVFB_PID)"
        else
            echo "  WARN: Xvfb failed to start; see /tmp/xvfb.log"
            tail -5 /tmp/xvfb.log 2>/dev/null
        fi
    else
        echo "  WARN: Xvfb binary not found at $XVFB_BIN"
    fi

    # Smoke test: can we actually import gi AND create a window?
    if python3 -c "
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk
w = Gtk.Window()
w.set_title('smoke')
print('GTK import + window create OK')
" 2>/dev/null; then
        echo "  OK GTK installed (import + widget creation verified)"
    elif python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('import only OK')" 2>/dev/null; then
        echo "  PARTIAL: GTK imports but widget creation failed"
        echo "           (Xvfb may not be running; tray_app load should still work)"
    else
        echo "  WARN: GTK kit extracted but 'import gi' still fails"
    fi
fi

# --- CHROMIUM -------------------------------------------------------------
_kp=$(find_kit "bulkdl_chromium_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing CHROMIUM kit..."
    mkdir -p /home/claude/.cache
    unzip -q -o "$_kp" -d /home/claude/.cache/
    export PLAYWRIGHT_BROWSERS_PATH=/home/claude/.cache/ms-playwright

    # Verify revision match against installed Playwright (if available)
    if [ -f /home/claude/.cache/ms-playwright/VERSION.txt ]; then
        kit_rev=$(grep "^playwright_revision:" \
            /home/claude/.cache/ms-playwright/VERSION.txt | awk '{print $2}')
        kit_pw_ver=$(grep "^playwright_version:" \
            /home/claude/.cache/ms-playwright/VERSION.txt | awk '{print $2}')
        echo "  Kit revision: $kit_rev (Playwright $kit_pw_ver)"
        # Try the venv first, fall back to system python. Use
        # importlib.metadata since playwright doesn't expose __version__.
        installed_ver=""
        for py in /home/claude/work/venv/bin/python ~/BulkDownloader/venv/bin/python python3; do
            if [ -x "$(command -v "$py" 2>/dev/null || true)" ] || [ -x "$py" ]; then
                installed_ver=$("$py" -c \
                    "import importlib.metadata as m; print(m.version('playwright'))" \
                    2>/dev/null) && break
            fi
        done
        if [ -z "$installed_ver" ]; then
            echo "  WARN: playwright python package not yet importable;"
            echo "        revision check deferred until core kit installs."
        elif [ "$installed_ver" != "$kit_pw_ver" ]; then
            echo "  WARN: kit built for Playwright $kit_pw_ver,"
            echo "        installed Playwright is $installed_ver."
            echo "        Browser binaries may be re-downloaded at runtime."
        else
            echo "  Match: installed Playwright $installed_ver"
        fi
    fi

    echo "  OK CHROMIUM installed (PLAYWRIGHT_BROWSERS_PATH set)"
fi

# --- SPA ------------------------------------------------------------------
_kp=$(find_kit "bulkdl_spa_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing SPA kit..."
    unzip -q -o "$_kp" -d /home/claude/

    # SPA kit needs Node runtime. Check for it (either from the node kit
    # or pre-existing in the sandbox).
    if command -v node >/dev/null 2>&1; then
        node_ver=$(node --version 2>&1)
        echo "  Node runtime found: $node_ver"

        # Smoke test: can vitest actually run?
        if [ -d /home/claude/spa/node_modules/vitest ]; then
            cd /home/claude/spa
            if node ./node_modules/.bin/vitest --version 2>/dev/null | grep -qE '^[0-9]'; then
                echo "  OK vitest works (run with: cd /home/claude/spa && npx vitest)"
            else
                echo "  WARN: vitest in node_modules but version probe failed"
            fi
            cd - >/dev/null
        else
            echo "  WARN: no vitest in /home/claude/spa/node_modules/"
        fi
    else
        echo "  WARN: SPA kit installed but no 'node' on PATH."
        echo "        Also install the 'node' kit to run npm/vitest/vite."
    fi

    echo "  OK SPA installed at /home/claude/spa/"
fi

# --- FRONTEND -------------------------------------------------------------
# The actual React SPA tree with the 32 deps the resolution-card UI etc.
# need. Extracts to $WORK_TREE/frontend/ so it sits next to BulkDL source
# when both kits are installed.
_kp=$(find_kit "bulkdl_frontend_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing FRONTEND kit..."
    mkdir -p "$WORK_TREE"
    rm -rf "$WORK_TREE/frontend"
    unzip -q -o "$_kp" -d "$WORK_TREE/"

    # Quick smoke: does the dep count look right?
    if [ -d "$WORK_TREE/frontend/node_modules" ]; then
        pkg_count=$(ls "$WORK_TREE/frontend/node_modules" 2>/dev/null \
            | grep -v '^\.' | wc -l)
        echo "  node_modules package count: $pkg_count"

        # Verify the dep names from frontend/package.json are present
        for must_have in "@radix-ui" "@dnd-kit" "@tanstack" \
                         "react-grid-layout" "recharts" "cmdk" "sonner"; do
            if [ -d "$WORK_TREE/frontend/node_modules/$must_have" ]; then
                echo "    OK $must_have"
            else
                echo "    MISSING $must_have"
            fi
        done

        # If node is on PATH, try a build dry-run
        if command -v node >/dev/null 2>&1; then
            cd "$WORK_TREE/frontend"
            if [ -x ./node_modules/.bin/vite ]; then
                echo "  vite: $(./node_modules/.bin/vite --version 2>&1 | head -1)"
            fi
            if [ -x ./node_modules/.bin/vitest ]; then
                echo "  vitest: $(./node_modules/.bin/vitest --version 2>&1 | head -1)"
            fi
            cd - >/dev/null
        else
            echo "  WARN: no 'node' on PATH; install the 'node' kit to use vite/vitest"
        fi
    else
        echo "  ERROR: frontend/node_modules not found in kit"
    fi

    echo "  OK FRONTEND installed at $WORK_TREE/frontend/"
    echo "    Build:  cd $WORK_TREE/frontend && npm run build"
    echo "    Test:   cd $WORK_TREE/frontend && npm test"
    echo "    Dev:    cd $WORK_TREE/frontend && npm run dev"

    # If the BulkDL source tree has been extracted alongside (typical
    # bootstrap), create the two symlinks documented in
    # SANDBOX_ENV_VARS.md §5. The in-tree frontend/ and spa/ both ship
    # package.json but no node_modules; the symlink bridges to the
    # kit's installed tree.
    for tree_root in "$WORK_TREE/BulkDownloader" "$WORK_TREE"; do
        for sub in frontend spa; do
            if [ -d "$tree_root/$sub" ] && [ -f "$tree_root/$sub/package.json" ] \
               && [ ! -d "$tree_root/$sub/node_modules" ]; then
                ln -sfn "$WORK_TREE/frontend/node_modules" "$tree_root/$sub/node_modules"
                echo "    symlink: $tree_root/$sub/node_modules -> $WORK_TREE/frontend/node_modules"
            fi
        done
    done
fi

# --- RSUITE ---------------------------------------------------------------
# Standalone rsuite library. Extracts node_modules to /home/claude/rsuite_kit/
# and exports NODE_PATH so 'require("rsuite")' resolves globally.
_kp=$(find_kit "bulkdl_rsuite_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing RSUITE kit..."
    mkdir -p /home/claude/rsuite_kit
    unzip -q -o "$_kp" -d /home/claude/rsuite_kit/
    if [ -d /home/claude/rsuite_kit/node_modules/rsuite ]; then
        export NODE_PATH=/home/claude/rsuite_kit/node_modules:${NODE_PATH:-}
        pkg_count=$(ls /home/claude/rsuite_kit/node_modules/ 2>/dev/null \
            | grep -v '^\.' | wc -l)
        echo "  RSUITE installed: $pkg_count packages in /home/claude/rsuite_kit/node_modules/"
        if command -v node >/dev/null 2>&1; then
            ver=$(NODE_PATH=/home/claude/rsuite_kit/node_modules node -e \
                "console.log(require('rsuite/package.json').version)" 2>/dev/null)
            [ -n "$ver" ] && echo "  rsuite version: $ver"
        fi
        echo "  Usage: NODE_PATH=/home/claude/rsuite_kit/node_modules node yourfile.js"
    else
        echo "  WARN: rsuite/ not found in extracted node_modules"
    fi
fi

# --- BDUTILS --------------------------------------------------------------
# Sandbox CLI utilities (bd-* + the bd dispatcher). Extracts to
# /home/claude/.local/bin which the canonical env block puts on PATH.
#
# Layout: the canonical kit is flat: `bin/bd`, `bin/bd-*`, and `VERSION.txt`
# at the root of the zip. Some legacy packs accidentally shipped this kit
# double-wrapped (an outer zip whose `bin/` contains a build/install
# toolchain plus the real kit as `bin/bulkdl_bdutils_kit.zip`). We auto-
# detect and unwrap that case so the installer is robust either way.
#
# The dispatcher binary is renamed `bd` -> `bdu` on install, to avoid a
# PATH collision with bdkit's own `bd` wrapper in /usr/local/bin.
_kp=$(find_kit "bulkdl_bdutils_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing BDUTILS kit..."
    mkdir -p /home/claude/.local/bin
    tmpdir=$(mktemp -d)
    unzip -q -o "$_kp" -d "$tmpdir"

    # Detect & auto-unwrap legacy double-wrapped kits. The outer wrapper
    # ships a nested bulkdl_bdutils_kit.zip alongside build/install scripts
    # and lacks the bd-* CLI files we actually want.
    if [ -f "$tmpdir/bin/bulkdl_bdutils_kit.zip" ] && [ ! -f "$tmpdir/bin/bd" ]; then
        echo "  Detected double-wrapped kit; unwrapping inner zip"
        inner="$tmpdir/bin/bulkdl_bdutils_kit.zip"
        innerdir=$(mktemp -d)
        unzip -q -o "$inner" -d "$innerdir"
        rm -rf "$tmpdir"
        tmpdir="$innerdir"
    fi

    # Sanity check: the kit must have the dispatcher.
    if [ ! -f "$tmpdir/bin/bd" ]; then
        echo "  WARN: bdutils kit at $_kp has no bin/bd; skipping" >&2
        rm -rf "$tmpdir"
    else
        # Copy bin/* contents flat into /home/claude/.local/bin/, renaming
        # the dispatcher 'bd' -> 'bdu' so it doesn't collide with bdkit's bd.
        for f in "$tmpdir/bin/"*; do
            name=$(basename "$f")
            if [ "$name" = "bd" ]; then
                cp "$f" /home/claude/.local/bin/bdu
            else
                cp "$f" /home/claude/.local/bin/"$name"
            fi
        done
        chmod +x /home/claude/.local/bin/bdu /home/claude/.local/bin/bd-* 2>/dev/null || true
        if [ -f "$tmpdir/VERSION.txt" ]; then
            cp "$tmpdir/VERSION.txt" /home/claude/.local/bdutils-VERSION.txt
            # The dispatcher looks for $KIT_DIR/../VERSION.txt, i.e.
            # /home/claude/.local/VERSION.txt. Symlink so `bdu --version` works.
            ln -sf bdutils-VERSION.txt /home/claude/.local/VERSION.txt
        fi
        count=$(ls /home/claude/.local/bin/bdu /home/claude/.local/bin/bd-* 2>/dev/null | wc -l)
        rm -rf "$tmpdir"
        echo "  BDUTILS installed: $count commands in /home/claude/.local/bin/"
        echo "  Run 'bdu help' for the catalog (PATH includes /home/claude/.local/bin via env block)"
        echo "  Note: dispatcher renamed from 'bd' to 'bdu' to avoid collision with bdkit's 'bd'"
    fi
fi

# --- PYPY -----------------------------------------------------------------
_kp=$(find_kit "bulkdl_pypy_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing PYPY kit..."
    mkdir -p /home/claude/pypy_kit
    unzip -q -o "$_kp" -d /home/claude/pypy_kit/
    export PYPY_BIN=/home/claude/pypy_kit/pypy/bin/pypy3
    export PYPY_WHEELS=/home/claude/pypy_kit/wheels
    if [ -x "$PYPY_BIN" ]; then
        echo "  PyPy: $($PYPY_BIN --version 2>&1 | head -1)"
        echo "  To use: $PYPY_BIN -m pip install --no-index --find-links $PYPY_WHEELS <pkg>"
        echo "  OK PYPY installed"
    else
        echo "  WARN: PyPy binary not found at $PYPY_BIN"
    fi
fi

# --- OLLAMA ---------------------------------------------------------------
# Two paths: (1) intact kit zip found via find_kit, or (2) parts uploaded.
# Path 2 streams cat-parts directly into unzip without writing a 3.3 GB
# intermediate file -- critical for the sandbox's tight disk budget.
_ollama_src=""
_kp=$(find_kit "bulkdl_ollama_kit.zip") && _ollama_src="zip"
if [ -z "$_ollama_src" ] && [ -f "$UPLOADS/ollama_part_0" ]; then
    _ollama_src="parts"
fi

if [ -n "$_ollama_src" ]; then
    echo
    echo "-> Installing OLLAMA kit (source: $_ollama_src)..."
    mkdir -p /home/claude/ollama_kit
    if [ "$_ollama_src" = "zip" ]; then
        unzip -q -o "$_kp" -d /home/claude/ollama_kit/
    else
        # Stream parts directly through unzip. -q quiet, -o overwrite.
        # 'bsdtar' would also work, but unzip handles zip natively.
        # unzip needs a real file to seek, so we cat to a fifo... no,
        # unzip can't read from a pipe (it seeks). Workaround: cat to
        # /tmp briefly, unzip, delete in same step to minimize peak disk.
        echo "   Reassembling parts to /tmp (transient)..."
        cat "$UPLOADS"/ollama_part_? > /tmp/_ollama_tmp.zip
        echo "   Extracting and removing temp ($(du -h /tmp/_ollama_tmp.zip | cut -f1))..."
        unzip -q -o /tmp/_ollama_tmp.zip -d /home/claude/ollama_kit/
        rm -f /tmp/_ollama_tmp.zip
    fi
    chmod +x /home/claude/ollama_kit/bin/ollama 2>/dev/null
    export PATH=/home/claude/ollama_kit/bin:$PATH
    export OLLAMA_MODELS=/home/claude/ollama_kit/models

    # Start ollama serve in background
    pkill -f "ollama serve" 2>/dev/null || true
    sleep 0.3
    OLLAMA_MODELS=/home/claude/ollama_kit/models \
        /home/claude/ollama_kit/bin/ollama serve \
        >/tmp/ollama.log 2>&1 &
    OL_PID=$!
    sleep 2
    if kill -0 "$OL_PID" 2>/dev/null; then
        echo "  Ollama serving on :11434 (PID $OL_PID)"
        models=$(curl -s http://localhost:11434/api/tags 2>/dev/null | \
                 python3 -c "import sys,json;d=json.load(sys.stdin);print(','.join(m['name'] for m in d.get('models',[])))" 2>/dev/null)
        echo "  Models: ${models:-(none returned)}"
        echo "  OK OLLAMA installed"
    else
        echo "  WARN: ollama serve failed; see /tmp/ollama.log"
        tail -5 /tmp/ollama.log 2>/dev/null
    fi
    # Free staging zip too (if find_kit pulled it from a pack)
    release_kit "bulkdl_ollama_kit.zip" 2>/dev/null || true
fi

# --- BDHOME ---------------------------------------------------------------
_kp=$(find_kit "bulkdl_bdhome_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing BDHOME kit..."
    mkdir -p /home/claude
    unzip -q -o "$_kp" -d /home/claude/
    if [ -d /home/claude/bd_home ]; then
        export BD_HOME=/home/claude/bd_home
        echo "  BD_HOME: $BD_HOME"
        echo "  Size: $(du -sh "$BD_HOME" 2>/dev/null | cut -f1)"
        echo "  OK BDHOME installed"
    else
        echo "  WARN: bd_home dir not extracted as expected"
    fi
fi

# --- APPRISE --------------------------------------------------------------
_kp=$(find_kit "bulkdl_apprise_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing APPRISE kit..."
    mkdir -p /home/claude/apprise_kit
    unzip -q -o "$_kp" -d /home/claude/apprise_kit/
    chmod +x /home/claude/apprise_kit/bin/*.py
    export PATH=/home/claude/apprise_kit/bin:$PATH

    # Start the fake webhook receiver in background
    pkill -f "fake_webhook_server.py" 2>/dev/null || true
    sleep 0.2
    python3 /home/claude/apprise_kit/bin/fake_webhook_server.py \
        >/tmp/apprise_webhook.log 2>&1 &
    WH_PID=$!
    sleep 0.5
    if kill -0 "$WH_PID" 2>/dev/null; then
        echo "  Fake webhook receiver on http://localhost:8765/ (PID $WH_PID)"
        echo "  Captures: /tmp/apprise_capture/"
        echo "  OK APPRISE installed"
    else
        echo "  WARN: webhook receiver failed; see /tmp/apprise_webhook.log"
    fi
fi

# --- MOCKS ----------------------------------------------------------------
_kp=$(find_kit "bulkdl_mocks_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing MOCKS kit..."
    mkdir -p /home/claude/mocks_kit
    unzip -q -o "$_kp" -d /home/claude/mocks_kit/
    chmod +x /home/claude/mocks_kit/bin/*.py /home/claude/mocks_kit/bin/*.sh
    export PATH=/home/claude/mocks_kit/bin:$PATH

    # Start all three mock servers in background
    pkill -f "mock_plex.py\|mock_jellyfin.py\|mock_stash.py" 2>/dev/null || true
    sleep 0.2
    bash /home/claude/mocks_kit/bin/start_all_mocks.sh
    echo "  OK MOCKS installed"
fi

# --- TOOLS ----------------------------------------------------------------
_kp=$(find_kit "bulkdl_tools_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing TOOLS kit..."
    unzip -q -o "$_kp" -d /tmp/tools_kit/
    chmod +x /tmp/tools_kit/tools_bin/* 2>/dev/null
    export PATH=/tmp/tools_kit/tools_bin:$PATH
    # Install litecli wheel if present
    if [ -d /tmp/tools_kit/wheels ] && ls /tmp/tools_kit/wheels/*.whl >/dev/null 2>&1; then
        pip install --quiet --break-system-packages /tmp/tools_kit/wheels/*.whl 2>/dev/null || true
    fi
    echo "  OK TOOLS installed"
fi

# --- NODE -----------------------------------------------------------------
_kp=$(find_kit "bulkdl_node_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing NODE kit..."
    mkdir -p /home/claude/.local
    rm -rf /home/claude/.local/node
    mkdir -p /home/claude/.local/node
    unzip -q -o "$_kp" -d /home/claude/.local/node/
    chmod +x /home/claude/.local/node/bin/* 2>/dev/null
    export PATH=/home/claude/.local/node/bin:$PATH
    if command -v node >/dev/null 2>&1; then
        echo "  OK NODE installed: $(node --version) / npm $(npm --version)"
    else
        echo "  WARN: node binary not on PATH after install"
    fi
fi

# --- PROFILING ------------------------------------------------------------
_kp=$(find_kit "bulkdl_profiling_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing PROFILING kit..."
    unzip -q -o "$_kp" -d /tmp/profiling_kit/
    chmod +x /tmp/profiling_kit/tools_bin/* 2>/dev/null
    export PATH=/tmp/profiling_kit/tools_bin:$PATH
    if [ -d /tmp/profiling_kit/wheels ]; then
        pip install --quiet --break-system-packages /tmp/profiling_kit/wheels/*.whl 2>/dev/null || true
    fi
    echo "  OK PROFILING installed"
fi

# --- SUPERVISORD ----------------------------------------------------------
_kp=$(find_kit "bulkdl_supervisord_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing SUPERVISORD kit..."
    unzip -q -o "$_kp" -d /home/claude/supervisord_kit/
    pip install --quiet --break-system-packages /home/claude/supervisord_kit/wheels/*.whl 2>/dev/null || true
    echo "  OK SUPERVISORD installed (conf at /home/claude/supervisord_kit/conf/)"
fi

# --- RECORDINGS -----------------------------------------------------------
_kp=$(find_kit "bulkdl_recordings_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing RECORDINGS kit..."
    unzip -q -o "$_kp" -d /home/claude/recordings_kit/
    pip install --quiet --break-system-packages /home/claude/recordings_kit/wheels/*.whl 2>/dev/null || true
    echo "  OK RECORDINGS installed (cassettes at /home/claude/recordings_kit/cassettes/)"
fi

# --- WEBPROXY -------------------------------------------------------------
_kp=$(find_kit "bulkdl_webproxy_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing WEBPROXY kit..."
    unzip -q -o "$_kp" -d /home/claude/webproxy_kit/
    chmod +x /home/claude/webproxy_kit/tools_bin/* 2>/dev/null
    export PATH=/home/claude/webproxy_kit/tools_bin:$PATH
    echo "  OK WEBPROXY installed (Caddyfile at /home/claude/webproxy_kit/conf/)"
fi

# --- DATASTORES -----------------------------------------------------------
_kp=$(find_kit "bulkdl_datastores_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing DATASTORES kit..."
    unzip -q -o "$_kp" -d /home/claude/datastores_kit/
    chmod +x /home/claude/datastores_kit/tools_bin/* 2>/dev/null
    export PATH=/home/claude/datastores_kit/tools_bin:$PATH
    echo "  OK DATASTORES installed (redis/postgres on PATH)"
fi

# --- LSP ------------------------------------------------------------------
_kp=$(find_kit "bulkdl_lsp_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing LSP kit..."
    unzip -q -o "$_kp" -d /home/claude/lsp_kit/
    pip install --quiet --break-system-packages /home/claude/lsp_kit/wheels/*.whl 2>/dev/null || true
    echo "  OK LSP installed (pylsp + pyright available)"
fi

# --- PRECOMMIT ------------------------------------------------------------
_kp=$(find_kit "bulkdl_precommit_kit.zip") && if [ -n "$_kp" ]; then
    echo
    echo "-> Installing PRECOMMIT kit..."
    unzip -q -o "$_kp" -d /home/claude/precommit_kit/
    pip install --quiet --break-system-packages /home/claude/precommit_kit/wheels/*.whl 2>/dev/null || true
    echo "  OK PRECOMMIT installed (config at /home/claude/precommit_kit/conf/.pre-commit-config.yaml)"
fi

# --- SUMMARY --------------------------------------------------------------
echo
echo "================================================================"
echo "  Available tools:"
echo "================================================================"
for tool in pytest rg jq fd sqlite3 ffmpeg ffprobe node npm; do
    if command -v "$tool" >/dev/null 2>&1; then
        ver=$("$tool" --version 2>&1 | head -1 | cut -d$'\n' -f1)
        printf "  %-10s %s\n" "$tool" "$ver"
    fi
done

if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
    echo
    printf "  %-10s %s\n" "chromium" "$PLAYWRIGHT_BROWSERS_PATH"
fi

# Paste-ready env block. The installer's own exports DON'T persist
# across bash_tool invocations -- they live in this subshell only.
# Print the canonical block from SANDBOX_ENV_VARS.md §1 so the user
# can copy it into every subsequent shell.
echo
echo "================================================================"
echo "  Per-shell env block -- paste this at the top of every"
echo "  bash_tool call (bash_tool state doesn't persist):"
echo "================================================================"
cat << 'EOF'
# PATH + Python deps
export PATH=/tmp/tools_bin:/tmp/media/tools_bin:/home/claude/.local/node/bin:/home/claude/.local/bin:$PATH
export PYTHONPATH="/tmp/prestaged_site_packages:${PYTHONPATH:-}"

# BulkDL app config
export BD_HOME=/home/claude/bd_home
export BD_DISABLE_KEEPALIVE=1

# Browser automation
export DISPLAY=:99
export PLAYWRIGHT_BROWSERS_PATH=/home/claude/.cache/ms-playwright

# GTK stack (tray_app, captcha relay, anything pygobject)
export GTK_ROOT=/home/claude/.local/gtk
export LD_LIBRARY_PATH="$GTK_ROOT/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="$GTK_ROOT/usr/lib/x86_64-linux-gnu/girepository-1.0:${GI_TYPELIB_PATH:-}"
export XDG_DATA_DIRS="$GTK_ROOT/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

# Xvfb (the installer's Xvfb dies between bash_tool calls)
Xvfb :99 -screen 0 1024x768x24 >/tmp/xvfb.log 2>&1 &
sleep 1
EOF
echo "================================================================"

# --- CLEANUP --------------------------------------------------------------
# Free any kit zips we extracted from packs into STAGING (now installed).
if [ -d "$STAGING" ]; then
    leftover=$(ls "$STAGING"/bulkdl_*_kit.zip 2>/dev/null | wc -l)
    if [ "$leftover" -gt 0 ]; then
        size=$(du -sh "$STAGING" 2>/dev/null | cut -f1)
        echo
        echo "-> Cleaning up staging area ($size, $leftover kit zips)..."
        rm -f "$STAGING"/bulkdl_*_kit.zip; rm -rf "$OKDIR" "$STAGING/.index" "$STAGING/.index.key"
    fi
fi

# --- OPTIONAL EXPANSION PACKS (pack_E-H) -----------------------------------
# Flat single-capability packs with NO kit handler here (they are indexed by
# the pack glob above but intentionally NOT auto-installed). Surface them so the
# operator knows they're available and how to install on demand.
_optpacks=""
for _L in E F G H; do
    for _d in "$UPLOADS" /home/claude /home/claude/packs_out; do
        [ -f "$_d/pack_${_L}.zip" ] && { _optpacks="$_optpacks $_L"; break; }
    done
done
if [ -n "$_optpacks" ]; then
    echo
    echo "==> Optional expansion packs present:$_optpacks  (install-on-demand)"
    echo "    Not auto-installed. Manage with:  bd-optpack list | install <E|F|G|H|all>"
fi
