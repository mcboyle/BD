#!/usr/bin/env bash
# install_ai_ollama.sh - set up local AI (Ollama) for BulkDownloader
# on Ubuntu. Separate from install_linux.sh on purpose: the core app
# runs fine without AI; this is opt-in.
#
# Installs the Ollama runtime, starts it as a systemd service, and
# pulls the two models BulkDownloader's Ollama provider expects:
#
#     vision : qwen2.5vl:7b     (image-based template extraction)
#     text   : qwen2.5:7b       (text reasoning / login assist)
#
# These match ai_provider.py::OllamaProvider.default_models. If you
# change the models in the app's AI settings, pull those instead.
#
# Assumes NVIDIA drivers are already installed (this script does NOT
# install them). Ollama auto-detects the GPU via the CUDA libraries it
# bundles; if no GPU is found it falls back to CPU (much slower).
#
# Run:  chmod +x install_ai_ollama.sh && ./install_ai_ollama.sh
set -u

VISION_MODEL="qwen2.5vl:7b"
TEXT_MODEL="qwen2.5:7b"
OLLAMA_HOST="http://localhost:11434"

set -o pipefail
echo " ================================================================"
echo "  BulkDownloader - local AI (Ollama) install  [Ubuntu]"
echo " ================================================================"

# Prime sudo early with a banner so the operator knows a prompt is
# coming. Cheap and avoids the silent-pause-in-the-middle UX problem.
if command -v sudo >/dev/null 2>&1; then
    if ! sudo -n true 2>/dev/null; then
        echo "  (this installer needs sudo for apt + systemctl;"
        echo "   you may be prompted now)"
        sudo -v || { echo "  ERROR: sudo authentication failed."; exit 1; }
    fi
fi

# --- 0. sanity: Ubuntu + curl -------------------------------------
# Test apt-get (the CLI we actually invoke) -- some minimal/CI images
# ship apt-get but not the user-facing `apt` wrapper.
if ! command -v apt-get >/dev/null 2>&1; then
    echo "  ERROR: this script targets Ubuntu/Debian (apt-get not found)."
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "  Installing curl (needed to fetch the Ollama installer)..."
    if ! sudo apt-get update -qq; then
        echo "  ERROR: apt-get update failed."; exit 1
    fi
    if ! sudo apt-get install -y curl; then
        echo "  ERROR: could not install curl."; exit 1
    fi
fi

# --- 1. GPU check (delegated to gpu_check.sh — read-only, no install) ----
# v3.63.5: dropped the inline NVIDIA-only check in favour of a
# standalone gpu_check.sh that covers NVIDIA, AMD ROCm, Intel,
# and Apple Silicon (with copy-pasteable driver hints when a GPU
# is present but no runtime is installed). If gpu_check.sh isn't
# present in the same directory (e.g. someone is running an
# old-layout install), fall back to a minimal nvidia-smi probe.
echo
echo "  [1] GPU check"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
if [ -x "$SCRIPT_DIR/gpu_check.sh" ]; then
    # Exit 0 = real accelerator found; 1 = CPU-only; 2 = detection error.
    # We don't bail on 1 here; the operator may still want Ollama on CPU.
    # Unset first so a stale value from the caller's environment can't
    # leak in and skew the branch below.
    unset GPU_RC
    "$SCRIPT_DIR/gpu_check.sh" || GPU_RC=$?
    GPU_RC="${GPU_RC:-0}"
    case "$GPU_RC" in
        0) ;;  # accelerator found, proceed quietly
        1)
            echo
            echo "  Detection reports no usable accelerator. Ollama will run"
            echo "  on CPU (slow but functional). Continuing in 5s — Ctrl+C"
            echo "  to abort..."
            sleep 5
            ;;
        2)
            echo
            echo "  WARNING: GPU detection itself failed (gpu_check.sh exit 2)."
            echo "  This usually means lspci, nvidia-smi, or rocminfo are"
            echo "  broken -- not that there's no GPU. Ollama will still"
            echo "  install and auto-detect at runtime, but performance is"
            echo "  unpredictable. Continuing in 5s — Ctrl+C to abort..."
            sleep 5
            ;;
        *)
            echo
            echo "  WARNING: gpu_check.sh returned unexpected exit code $GPU_RC."
            echo "  Continuing anyway in 5s — Ctrl+C to abort..."
            sleep 5
            ;;
    esac
else
    echo "      (fallback: gpu_check.sh not found in $SCRIPT_DIR)"
    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null \
            | sed 's/^/      /' || true
    else
        echo "      No NVIDIA driver detected. Ollama will still install"
        echo "      but run on CPU (slow). Continuing in 5s — Ctrl+C to abort..."
        sleep 5
    fi
fi

# --- 2. install Ollama --------------------------------------------
echo
echo "  [2] Ollama runtime"
if command -v ollama >/dev/null 2>&1; then
    echo "      Ollama already installed: $(ollama --version 2>/dev/null | head -1)"
else
    echo "      Installing Ollama via the official installer..."
    # The official script installs the binary and a systemd unit.
    # The official installer can exit non-zero on benign conditions
    # (e.g. `useradd: group 'ollama' already exists`) even after printing
    # "Install complete". Under `set -o pipefail` that aborts a perfectly
    # good install, so verify by the binary rather than the exit code.
    curl -fsSL https://ollama.com/install.sh | sh || true
    hash -r 2>/dev/null || true
    if ! command -v ollama >/dev/null 2>&1; then
        echo "  ERROR: Ollama install failed (binary not on PATH). Check"
        echo "  network access to ollama.com and try again."
        exit 1
    fi
fi

# --- 3. make sure the service is up -------------------------------
echo
echo "  [3] Ollama service"
if command -v systemctl >/dev/null 2>&1; then
    # Don't swallow with `|| true`: if enable/start fails (malformed unit
    # from a bad official installer release, permission denied, etc) the
    # operator will see "service enabled + started" but the API probe
    # below will time out and the error message will blame the network.
    # Better to surface the real cause here.

    # Self-heal: the official installer writes ollama.service itself, but
    # if the `ollama` group lingered from a prior (incomplete) uninstall,
    # its `useradd` aborts ("group ollama exists") and it skips creating
    # the unit. We also skip the official installer on re-run (binary
    # already present), so the unit would never reappear. If it's missing,
    # create the user (reusing the existing group) and write a standard
    # unit ourselves. Idempotent — a no-op once the unit exists.
    if ! systemctl cat ollama.service >/dev/null 2>&1; then
        echo "      ollama.service not found -- creating it."
        if ! id ollama >/dev/null 2>&1; then
            sudo useradd -r -g ollama -d /usr/share/ollama \
                 -s /usr/sbin/nologin ollama 2>/dev/null \
              || sudo useradd -r -d /usr/share/ollama \
                 -s /usr/sbin/nologin ollama 2>/dev/null || true
        fi
        sudo mkdir -p /usr/share/ollama
        sudo chown -R ollama:ollama /usr/share/ollama 2>/dev/null || true
        _ollama_bin="$(command -v ollama || echo /usr/local/bin/ollama)"
        sudo tee /etc/systemd/system/ollama.service >/dev/null <<UNIT
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=${_ollama_bin} serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

[Install]
WantedBy=multi-user.target
UNIT
        sudo systemctl daemon-reload
    fi

    _ollama_enable_err="$(mktemp)"
    if ! sudo systemctl enable ollama 2>"${_ollama_enable_err}"; then
        sed 's/^/      /' "${_ollama_enable_err}"
        echo "      WARNING: systemctl enable ollama failed (above)."
        echo "      The service may still come up on this boot, but"
        echo "      won't auto-start on reboot."
    fi
    rm -f "${_ollama_enable_err}"
    _ollama_start_err="$(mktemp)"
    if ! sudo systemctl start ollama 2>"${_ollama_start_err}"; then
        sed 's/^/      /' "${_ollama_start_err}"
        rm -f "${_ollama_start_err}"
        echo "      ERROR: systemctl start ollama failed (above)."
        echo "      Bailing rather than waiting 30s for an API that"
        echo "      isn't coming."
        exit 1
    fi
    rm -f "${_ollama_start_err}"
    echo "      systemd service enabled + started."

    # wait for the API to answer before pulling
    echo "      Waiting for the Ollama API at $OLLAMA_HOST ..."
    UP=""
    # Pure-bash counter (loop body doesn't use $i, so no need for `seq`).
    for _ in {1..30}; do
        if curl -fsS "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
            UP="yes"; break
        fi
        sleep 1
    done
    if [ -z "$UP" ]; then
        echo "  ERROR: Ollama API did not come up within 30s."
        echo "  Diagnostic — last curl attempt against $OLLAMA_HOST/api/tags:"
        curl -v --max-time 3 "$OLLAMA_HOST/api/tags" 2>&1 | head -20 | sed 's/^/    /'
        echo
        echo "  Common causes: systemd unit failed (check 'systemctl status"
        echo "  ollama'), port 11434 already in use, or the GPU init crashed."
        echo "  Start it manually with 'ollama serve' to see live logs, then"
        echo "  re-run this script."
        exit 1
    fi
    echo "      Ollama API is responding."
else
    # No systemd. We told the operator to run `ollama serve &` themselves,
    # so the API isn't coming up on its own. Probe briefly in case they
    # left it running from a previous session, but don't wait 30s for an
    # API that we know isn't being started.
    echo "      systemd not present — start Ollama manually with:"
    echo "        ollama serve &"
    echo "      then re-run this script to pull models. (Or run"
    echo "      'ollama pull $VISION_MODEL' and 'ollama pull $TEXT_MODEL'"
    echo "      directly once the server is up.)"
    if curl -fsS --max-time 2 "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
        echo "      (API already responding -- continuing with model pulls)"
    else
        echo "      API not currently responding; exiting before model pulls."
        exit 0
    fi
fi

# --- 4. pull the models BulkDownloader expects --------------------
echo
echo "  [4] Pulling models (this downloads several GB — be patient)"
for M in "$VISION_MODEL" "$TEXT_MODEL"; do
    echo "      pulling $M ..."
    if ollama pull "$M"; then
        echo "      ok: $M"
    else
        echo "      WARNING: 'ollama pull $M' failed. The model name"
        echo "      may have changed in the Ollama library. Check"
        echo "      https://ollama.com/library and pull a current"
        echo "      equivalent, then set it in the app's AI settings."
    fi
done

# --- 5. verify ----------------------------------------------------
echo
echo "  [5] Installed models"
ollama list 2>/dev/null || echo "      (could not list models)"

echo
echo " ================================================================"
echo "  Local AI install complete."
echo
echo "  In BulkDownloader: Settings -> AI -> provider 'Ollama',"
echo "  endpoint $OLLAMA_HOST (the default — no key needed)."
echo "  Vision model : $VISION_MODEL"
echo "  Text model   : $TEXT_MODEL"
echo
echo "  Ollama is LAN-only by design in BulkDownloader; the privacy"
echo "  guard requires a loopback / RFC1918 endpoint. That is the"
echo "  default here, so nothing to change."
echo " ================================================================"
