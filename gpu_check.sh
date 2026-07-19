#!/usr/bin/env bash
# gpu_check.sh - detect GPU(s) on Linux and (optionally) install drivers.
#
# Standalone: safe to run on any Linux machine; detection is read-only
# with no install and no sudo. Pass --install-drivers to opt into
# actually running the printed suggestions; that mode prompts before
# every sudo command, never silently reboots, and refuses to run on
# non-Debian-family systems.
#
# Detects: NVIDIA (name, VRAM, driver, CUDA, compute capability),
#          AMD discrete (ROCm-eligible cards), Intel iGPU + Arc,
#          Apple Silicon (rare on Linux but cheap to look for).
#
# Outputs a per-card line plus a recommendation block:
#   - which 7B model quantization fits
#   - GPU vs CPU fallback
#   - driver install commands the operator can copy-paste, OR run via
#     --install-drivers
#
# Exit codes:
#   0 - at least one usable accelerator detected, OR --install-drivers
#       completed and the operator should now reboot
#   1 - no GPU found, or GPU present but no working runtime (CPU-only)
#   2 - detection itself failed, OR an --install-drivers step failed
#       in a way the operator should look at
#
# Flags:
#   --quiet            : only print the final recommendation
#   --json             : print a JSON blob (for installer/dev-tool use)
#   --install-drivers  : opt in to executing the printed suggestions.
#                        Prompts before each sudo command. Currently
#                        supports NVIDIA on Ubuntu/Debian via
#                        `ubuntu-drivers autoinstall`. AMD ROCm and
#                        Intel Arc are deliberately not automated; the
#                        compatibility matrices are narrow and the
#                        install paths are complex enough that an
#                        operator-driven manual install is safer.
#                        On Secure-Boot systems the NVIDIA install
#                        triggers Ubuntu's MOK enrollment, which needs
#                        a one-time-password and a reboot to confirm.
#   --help             : print this header
set -u
set -o pipefail

QUIET="no"
JSON="no"
DO_INSTALL="no"
for arg in "$@"; do
    case "$arg" in
        --quiet|-q) QUIET="yes" ;;
        --json|-j)  JSON="yes" ;;
        --install-drivers) DO_INSTALL="yes" ;;
        --help|-h)
            awk 'NR==1 {next}
                 /^[^#]/ && !/^$/ {exit}
                 /^#/ {sub(/^# ?/, ""); print}' "$0"
            exit 0
            ;;
        *)
            echo "  ERROR: unknown argument: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

# --install-drivers and --json together makes no sense (the prompts go to
# the operator while the JSON consumer expects clean machine output).
# Reject this combination instead of silently doing something half-broken.
if [ "$DO_INSTALL" = "yes" ] && [ "$JSON" = "yes" ]; then
    echo "  ERROR: --install-drivers and --json are mutually exclusive" >&2
    exit 2
fi
# --install-drivers and --quiet together is also a bad combo: --quiet
# suppresses p() (the descriptive text before prompts) but printf-based
# prompts still appear, so the operator would see bare "[y/N]:" prompts
# with no context for what they're agreeing to. Better to refuse.
if [ "$DO_INSTALL" = "yes" ] && [ "$QUIET" = "yes" ]; then
    echo "  ERROR: --install-drivers and --quiet are mutually exclusive" >&2
    echo "  (you would see bare y/N prompts with no context)" >&2
    exit 2
fi

# --- Output helpers -----------------------------------------------
# In JSON mode we collect every fact and emit one structured blob at the
# end. In human mode we print live. `j()` queues a JSON field; `p()`
# prints to the human-mode stream.
#
# JSON shape:
#   {
#     "nvidia": [{"name", "vram_mib", "driver", "cuda", "compute_cap"}],
#     "amd":    [{"name", "vram_mib", "rocm"}],
#     "intel":  [{"name", "kind"}],   # kind = "igpu" | "arc"
#     "apple":  [{"name"}],
#     "any_gpu": bool,
#     "any_accelerator": bool,         # something Ollama can use
#     "recommendations": ["..."],      # operator-facing strings
#     "suggested_commands": ["..."]    # copy-pasteable install hints
#   }
NVIDIA_JSON=""
AMD_JSON=""
INTEL_JSON=""
APPLE_JSON=""
RECS=()
CMDS=()
# Parallel array of per-card VRAM in MiB. Populated alongside
# NVIDIA_JSON in section 2 and consumed by the TOP_VRAM loop in
# section 7. Carrying VRAM as a real bash array (instead of parsing
# it back out of NVIDIA_JSON with grep+sed) decouples the sizing
# code from the JSON shape: future schema edits to NVIDIA_JSON can
# happen without silently breaking the recommendation branch.
NVIDIA_VRAMS=()
ANY_GPU="no"
ANY_ACCEL="no"
# Set when PCI shows an NVIDIA card but no driver is loaded; used by
# the recommendation block to distinguish "driver needed" from
# "driver loaded but VRAM unreadable" (both produce VRAM=0).
NVIDIA_NO_DRIVER="no"

p() { [ "$QUIET" = "yes" ] || [ "$JSON" = "yes" ] || echo "$*"; }
header() { p ""; p "$*"; }

json_escape() {
    # minimal JSON string escape for the fields we actually produce.
    # Driver strings can contain quotes; names can contain backslashes.
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\r//g'
}

# --- Header -------------------------------------------------------
p " ================================================================"
p "  BulkDownloader - GPU detection (read-only, no install)"
p " ================================================================"

# --- 1. PCI inventory (vendor-agnostic) ---------------------------
# Try lspci first (clearer output). Fall back to sysfs walk when
# pciutils isn't installed (it ships on most desktops but not every
# minimal server image). Vendor IDs: 0x10de = NVIDIA, 0x1002 = AMD,
# 0x8086 = Intel, 0x106b = Apple.
header "  [1] PCI inventory"
PCI_RAW=""
if command -v lspci >/dev/null 2>&1; then
    # -nn shows both vendor:device IDs and the textual name.
    PCI_RAW="$(lspci -nn 2>/dev/null | grep -iE 'vga|3d|display' || true)"
    if [ -n "$PCI_RAW" ]; then
        p "      lspci view:"
        printf '%s\n' "$PCI_RAW" | sed 's/^/        /'
    fi
else
    # sysfs fallback: PCI class 0x030000 (VGA), 0x030200 (3D), 0x038000 (display)
    p "      lspci not installed; using /sys/bus/pci/devices/ fallback"
    if [ -d /sys/bus/pci/devices ]; then
        for d in /sys/bus/pci/devices/*; do
            [ -e "$d/class" ] || continue
            CLS="$(tr -d '\n' < "$d/class" 2>/dev/null)"
            case "$CLS" in
                0x030000|0x030200|0x038000)
                    VEN="$(tr -d '\n' < "$d/vendor" 2>/dev/null)"
                    DEV="$(tr -d '\n' < "$d/device" 2>/dev/null)"
                    PCI_RAW="${PCI_RAW}${VEN} ${DEV}"$'\n'
                    p "        $(basename "$d") class=$CLS vendor=$VEN device=$DEV"
                    ;;
            esac
        done
    fi
fi
if [ -z "$PCI_RAW" ]; then
    p "      No display/VGA/3D devices found in PCI inventory."
fi

# --- 2. NVIDIA ---------------------------------------------------
header "  [2] NVIDIA"
if command -v nvidia-smi >/dev/null 2>&1; then
    # nvidia-smi --query-gpu output: one row per GPU, comma-separated.
    # If the driver isn't loaded, the binary still runs but reports
    # "No devices were found" on stderr; capture both.
    NV_RAW="$(nvidia-smi \
        --query-gpu=name,memory.total,driver_version,compute_cap \
        --format=csv,noheader,nounits 2>/dev/null || true)"
    CUDA_VER="$(nvidia-smi --query 2>/dev/null | grep -i 'CUDA Version' | head -1 | sed 's/.*: //;s/ *$//' || true)"
    # Fallback: nvidia-smi -q has it on the very first line
    if [ -z "$CUDA_VER" ]; then
        CUDA_VER="$(nvidia-smi 2>/dev/null | grep -i 'CUDA Version' | head -1 | sed 's/.*CUDA Version: *//;s/ *|.*//' || true)"
    fi

    if [ -n "$NV_RAW" ]; then
        ANY_GPU="yes"
        ANY_ACCEL="yes"
        # parse each row
        FIRST=1
        NVIDIA_JSON="["
        while IFS= read -r ROW; do
            [ -z "$ROW" ] && continue
            # name, vram_mib, driver, compute_cap  (csv with optional spaces).
            # Single-pass parse with IFS= -- cheaper than 4× awk forks per
            # row and identical output for the field shapes nvidia-smi
            # produces here (no embedded commas in name/driver in practice).
            IFS=',' read -r NAME VRAM DRV CC <<<"$ROW"
            # trim leading + trailing whitespace from each field
            NAME="${NAME#"${NAME%%[![:space:]]*}"}"; NAME="${NAME%"${NAME##*[![:space:]]}"}"
            VRAM="${VRAM#"${VRAM%%[![:space:]]*}"}"; VRAM="${VRAM%"${VRAM##*[![:space:]]}"}"
            DRV="${DRV#"${DRV%%[![:space:]]*}"}";    DRV="${DRV%"${DRV##*[![:space:]]}"}"
            CC="${CC#"${CC%%[![:space:]]*}"}";       CC="${CC%"${CC##*[![:space:]]}"}"
            # Force VRAM to a non-negative integer for the JSON payload.
            # nvidia-smi sometimes returns "[Unknown]" or "[Not Supported]"
            # for memory.total on vGPU / Tesla MIG / pre-Pascal setups; if
            # we passed that straight through we'd produce invalid JSON
            # ("vram_mib":[Unknown]). Below: if non-numeric, store 0 in
            # JSON; the human-facing line still shows the raw string.
            case "$VRAM" in
                ''|*[!0-9]*) VRAM_JSON=0 ;;
                *)           VRAM_JSON="$VRAM" ;;
            esac
            # Push into the parallel array used by section 7's TOP_VRAM
            # sizing loop. Same integer-or-zero coercion as VRAM_JSON.
            NVIDIA_VRAMS+=("$VRAM_JSON")
            p "      NVIDIA: $NAME"
            p "        VRAM         : ${VRAM} MiB"
            p "        driver       : ${DRV}"
            p "        CUDA runtime : ${CUDA_VER:-unknown}"
            p "        compute cap  : ${CC}"
            if [ "$FIRST" -eq 0 ]; then NVIDIA_JSON="${NVIDIA_JSON},"; fi
            FIRST=0
            NVIDIA_JSON="${NVIDIA_JSON}{\"name\":\"$(json_escape "$NAME")\",\"vram_mib\":${VRAM_JSON},\"driver\":\"$(json_escape "$DRV")\",\"cuda\":\"$(json_escape "$CUDA_VER")\",\"compute_cap\":\"$(json_escape "$CC")\"}"
        done <<EOF
$NV_RAW
EOF
        NVIDIA_JSON="${NVIDIA_JSON}]"
    else
        # nvidia-smi present but no devices: usually means the driver
        # didn't bind, or there's no NVIDIA card despite the tool being
        # installed (common on shared CI images).
        p "      nvidia-smi present but reports no devices."
        NVIDIA_JSON="[]"
    fi
else
    # Check PCI for an unbound NVIDIA card — points the operator at a
    # driver install instead of letting them assume no GPU.
    if printf '%s' "$PCI_RAW" | grep -iqE '10de|nvidia'; then
        p "      NVIDIA hardware detected via PCI, but nvidia-smi is not"
        p "      installed. The NVIDIA driver is almost certainly missing."
        ANY_GPU="yes"
        NVIDIA_NO_DRIVER="yes"
        RECS+=("NVIDIA card present but no driver loaded.")
        CMDS+=("sudo ubuntu-drivers autoinstall  # then reboot")
        CMDS+=("# OR find the highest available series and install:")
        CMDS+=("#   apt-cache search ^nvidia-driver-[0-9]")
        CMDS+=("#   sudo apt install nvidia-driver-NNN-server  # NNN = highest series")
        NVIDIA_JSON='[{"name":"unknown (no driver)","vram_mib":0,"driver":"","cuda":"","compute_cap":""}]'
        # Keep NVIDIA_VRAMS in lock-step with NVIDIA_JSON. The single
        # zero matches the single JSON entry above; section 7 will see
        # TOP_VRAM=0 and take the NVIDIA_NO_DRIVER branch.
        NVIDIA_VRAMS+=(0)
    else
        p "      No NVIDIA hardware found."
        NVIDIA_JSON="[]"
    fi
fi

# --- 3. AMD discrete (ROCm-eligible) ------------------------------
header "  [3] AMD"
AMD_JSON="[]"
if command -v rocminfo >/dev/null 2>&1; then
    # rocminfo lists agents (CPUs and GPUs). We want the Marketing Name
    # of each GPU agent. The previous awk version assumed Marketing Name
    # appears before Device Type within an agent block and emitted on
    # the next line after both were set -- that worked on current
    # rocminfo but was order-dependent. This version collects both
    # fields within an agent and emits at the agent boundary (or END),
    # so field ordering inside the block doesn't matter.
    #
    # Anchored patterns: only match labels that are the FIRST token on
    # the line (after whitespace). rocminfo nests some pseudo-tables
    # with `Pool 1 Device Type:` etc; without anchoring, those would
    # spuriously set type="GPU" and emit a wrong Marketing Name.
    ROCM_RAW="$(rocminfo 2>/dev/null | awk '
        function flush() {
            if (type == "GPU" && name != "") print name
            name = ""; type = ""
        }
        /^Agent /                          { flush() }
        /^[[:space:]]*Device Type:/        { if ($0 ~ /GPU/) type = "GPU" }
        /^[[:space:]]*Marketing Name:/     { sub(/^[[:space:]]*Marketing Name:[[:space:]]*/,""); name = $0 }
        END                                { flush() }
    ' || true)"
    if [ -n "$ROCM_RAW" ]; then
        ANY_GPU="yes"
        ANY_ACCEL="yes"
        FIRST=1
        AMD_JSON="["
        while IFS= read -r NAME; do
            [ -z "$NAME" ] && continue
            p "      AMD: $NAME"
            p "        ROCm        : present"
            if [ "$FIRST" -eq 0 ]; then AMD_JSON="${AMD_JSON},"; fi
            FIRST=0
            AMD_JSON="${AMD_JSON}{\"name\":\"$(json_escape "$NAME")\",\"vram_mib\":0,\"rocm\":true}"
        done <<EOF
$ROCM_RAW
EOF
        AMD_JSON="${AMD_JSON}]"
    else
        p "      rocminfo present but reports no GPU agents."
    fi
elif printf '%s' "$PCI_RAW" | grep -iqE '1002|amd/ati|advanced micro devices'; then
    # AMD in PCI but no ROCm: could be an integrated GPU (no Ollama
    # support) OR a discrete card that just needs ROCm installed.
    # We can't tell from PCI alone whether ROCm supports the chip.
    p "      AMD hardware detected via PCI; ROCm is NOT installed."
    p "      Ollama's AMD path requires ROCm and a supported card."
    ANY_GPU="yes"
    RECS+=("AMD card present but no ROCm runtime — Ollama will fall back to CPU.")
    CMDS+=("# AMD driver + ROCm install (only some discrete cards supported):")
    CMDS+=("# https://rocm.docs.amd.com/projects/install-on-linux/en/latest/")
    AMD_JSON='[{"name":"unknown (no ROCm)","vram_mib":0,"rocm":false}]'
else
    p "      No AMD hardware found."
fi

# --- 4. Intel (iGPU + Arc) ----------------------------------------
header "  [4] Intel"
INTEL_JSON="[]"
if printf '%s' "$PCI_RAW" | grep -iqE '8086|intel.*(graphics|arc|iris|xe |hd graphics)'; then
    ANY_GPU="yes"
    # Distinguish Arc from integrated. Arc cards show up with names
    # like "DG2" or "Arc A###"; everything else is treated as an iGPU.
    if printf '%s' "$PCI_RAW" | grep -iqE 'arc|dg2'; then
        NAME="$(printf '%s' "$PCI_RAW" | grep -iE 'arc|dg2' | head -1 | sed 's/.*: //;s/ \[.*//' )"
        p "      Intel discrete: ${NAME:-Intel Arc}"
        p "        Ollama       : limited support via IPEX-LLM (not first-class)"
        INTEL_JSON="[{\"name\":\"$(json_escape "${NAME:-Intel Arc}")\",\"kind\":\"arc\"}]"
        RECS+=("Intel Arc detected; Ollama supports it via IPEX-LLM but it is not first-class.")
    else
        NAME="$(printf '%s' "$PCI_RAW" | grep -iE 'intel' | head -1 | sed 's/.*: //;s/ \[.*//' )"
        p "      Intel integrated: ${NAME:-Intel iGPU}"
        p "        Ollama       : NOT supported on iGPU; CPU fallback only"
        INTEL_JSON="[{\"name\":\"$(json_escape "${NAME:-Intel iGPU}")\",\"kind\":\"igpu\"}]"
    fi
else
    p "      No Intel display hardware found."
fi

# --- 5. Apple Silicon (rare on Linux) -----------------------------
header "  [5] Apple Silicon"
APPLE_JSON="[]"
if [ -r /proc/device-tree/model ] && grep -qi 'apple' /proc/device-tree/model 2>/dev/null; then
    # device-tree strings end with \0 and may include trailing newlines
    # depending on how the bootloader wrote them. Strip both.
    NAME="$(tr -d '\0\n\r' </proc/device-tree/model 2>/dev/null)"
    p "      $NAME"
    p "      (Asahi Linux or similar; Ollama support varies)"
    APPLE_JSON="[{\"name\":\"$(json_escape "$NAME")\"}]"
    ANY_GPU="yes"
else
    p "      Not detected (this would only appear on Asahi Linux or VMs)."
fi

# --- 6. Secure Boot status (matters for NVIDIA install) -----------
header "  [6] Boot environment"
SECURE_BOOT="unknown"
if [ -d /sys/firmware/efi ]; then
    BOOT_KIND="UEFI"
    if command -v mokutil >/dev/null 2>&1; then
        if mokutil --sb-state 2>/dev/null | grep -qi 'enabled'; then
            SECURE_BOOT="enabled"
        elif mokutil --sb-state 2>/dev/null | grep -qi 'disabled'; then
            SECURE_BOOT="disabled"
        fi
    fi
    p "      Firmware     : UEFI"
    p "      Secure Boot  : $SECURE_BOOT"
    if [ "$SECURE_BOOT" = "enabled" ]; then
        p "      NOTE: with Secure Boot ON, the proprietary NVIDIA kernel"
        p "      module must be signed. Ubuntu walks you through MOK"
        p "      enrollment during the driver install; you will need a"
        p "      one-time-password and a reboot to confirm."
    fi
else
    BOOT_KIND="BIOS"
    p "      Firmware     : BIOS (legacy)"
fi

# --- 7. Build the recommendation block ----------------------------
header "  [7] Recommendation"

# Determine the "best" GPU for sizing the recommendation.
TOP_VRAM=0
TOP_VENDOR=""
if [ "$NVIDIA_JSON" != "[]" ]; then
    # Any NVIDIA entry pins the vendor; VRAM tracks the largest card.
    # This separation matters when nvidia-smi reports a card with
    # VRAM=0 ('[Unknown]' on some virt/vGPU setups) -- TOP_VENDOR
    # still gets set so the NVIDIA-specific branch below runs and
    # tells the operator what's happening, rather than falling
    # through to the generic "GPU present but Ollama cannot use it".
    TOP_VENDOR="nvidia"
    # Read from the parallel NVIDIA_VRAMS array populated in section
    # 2 (alongside NVIDIA_JSON). This used to grep VRAM out of
    # NVIDIA_JSON, which silently broke if the JSON shape ever
    # drifted. The array is the single source of truth for sizing.
    # `${arr[@]:-}` guards against `set -u` when the array is empty
    # (e.g. nvidia-smi present but reported no devices).
    if [ "${#NVIDIA_VRAMS[@]}" -gt 0 ]; then
        for VRAM in "${NVIDIA_VRAMS[@]}"; do
            if [ "$VRAM" -gt "$TOP_VRAM" ]; then TOP_VRAM="$VRAM"; fi
        done
    fi
fi
# AMD VRAM detection without rocm-smi is hard; skip sizing on AMD-only
# boxes and just note ROCm presence.
if [ -z "$TOP_VENDOR" ] && [ "$AMD_JSON" != "[]" ]; then
    TOP_VENDOR="amd"
fi

if [ "$TOP_VENDOR" = "nvidia" ]; then
    if [ "$TOP_VRAM" -ge 12000 ]; then
        p "      NVIDIA with ${TOP_VRAM} MiB: both default 7B models (vision"
        p "      + text) fit comfortably; Ollama runs entirely on the GPU."
        RECS+=("Both default 7B models fit on the GPU; expect fast inference.")
    elif [ "$TOP_VRAM" -ge 6000 ]; then
        p "      NVIDIA with ${TOP_VRAM} MiB: one 7B model fits on the GPU"
        p "      at a time. Loading the other will swap layers; this is fine"
        p "      but adds ~1-2s latency on each switch between vision and text."
        RECS+=("Tight fit for two 7B models; layer-swap on context switch.")
    elif [ "$TOP_VRAM" -gt 0 ]; then
        p "      NVIDIA with ${TOP_VRAM} MiB: under the comfortable threshold."
        p "      Ollama will offload most layers to CPU; throughput drops"
        p "      sharply. Consider a 3B-class model in AI settings, or accept"
        p "      slower inference."
        RECS+=("Low VRAM for 7B; consider a smaller model.")
    else
        # TOP_VRAM=0 with TOP_VENDOR=nvidia: either (a) no driver loaded
        # and the PCI-fallback path set NVIDIA_JSON to a single zero-
        # VRAM stub (driver install needed), or (b) the driver is loaded
        # but nvidia-smi reported "[Unknown]"/"[Not Supported]" for
        # memory.total (common on virtualization platforms). The flag
        # NVIDIA_NO_DRIVER, set at the PCI-fallback site, distinguishes
        # them. (Previous version inferred this from ${#CMDS[@]}, which
        # would have given the wrong answer on mixed AMD+NVIDIA boxes
        # where the AMD path also populates CMDS.)
        if [ "$NVIDIA_NO_DRIVER" = "yes" ]; then
            p "      NVIDIA hardware visible via PCI but no driver loaded."
            p "      Ollama will fall back to CPU until the driver is"
            p "      installed (see [8] below for the commands)."
            RECS+=("NVIDIA hardware visible but no driver; CPU fallback until installed.")
        else
            p "      NVIDIA detected but VRAM could not be sized (0 MiB reported)."
            p "      This is usually a driver/virtualization quirk. Check with:"
            p "        nvidia-smi -q -d MEMORY"
            p "      Ollama will still try the GPU; performance varies."
            RECS+=("NVIDIA present but VRAM unreadable; verify with nvidia-smi -q.")
        fi
    fi
elif [ "$TOP_VENDOR" = "amd" ]; then
    p "      AMD + ROCm detected. Ollama supports a subset of AMD cards;"
    p "      check the Ollama release notes for your chip's status."
    RECS+=("AMD ROCm present; verify chip is supported by current Ollama.")
elif [ "$ANY_GPU" = "no" ]; then
    p "      No GPU detected. Ollama will run on CPU only (slow: roughly"
    p "      0.5-3 tokens/sec on a recent x86 box for a 7B q4 model)."
    p "      The app still works; AI calls will simply be slow."
    RECS+=("No GPU; CPU-only inference will be slow but functional.")
else
    p "      A GPU is present but Ollama cannot use it (Intel iGPU, or an"
    p "      AMD/NVIDIA card without its runtime installed). Ollama will"
    p "      fall back to CPU."
    RECS+=("GPU present but no usable runtime; CPU fallback.")
fi

# --- 8. Suggested commands (printed in detection mode; executed in install mode) ---
#
# Detection mode: just print the copy-paste hints. The operator runs
# them by hand if and when they want to.
#
# Install mode (--install-drivers): only the NVIDIA-no-driver path is
# automatable today. The AMD-no-ROCm path also pushes into CMDS but
# only as informational comments (the URL to AMD's docs); there's no
# safe one-shot install for ROCm. Intel cases set RECS but no CMDS.
# Gate on the explicit NVIDIA_NO_DRIVER flag instead of ${#CMDS[@]}
# to avoid asking the operator to "install drivers" on a mixed box
# where NVIDIA is already working and only AMD lacks ROCm.
if [ "$DO_INSTALL" = "yes" ] && [ "$NVIDIA_NO_DRIVER" != "yes" ]; then
    header "  [8] Driver install (--install-drivers)"
    p "      Nothing to install. Either a working NVIDIA driver is"
    p "      already in place, no NVIDIA GPU was detected, or only AMD/"
    p "      Intel hardware is present (which this script does not"
    p "      automate -- see the manual install URL in the suggestions)."
    exit 0
fi
if [ ${#CMDS[@]} -gt 0 ]; then
    if [ "$DO_INSTALL" != "yes" ]; then
        header "  [8] Suggested commands (copy-paste; this script does NOT run them)"
        for C in "${CMDS[@]}"; do
            p "      $C"
        done
        p ""
        p "      Pass --install-drivers to have this script execute the"
        p "      NVIDIA-driver suggestion after prompting. AMD/Intel"
        p "      paths print URLs only and must be run manually."
    else
        header "  [8] Driver install (--install-drivers)"
        # --- 8a. Refuse if not Debian/Ubuntu family ---
        if ! command -v apt-get >/dev/null 2>&1; then
            p "      ERROR: --install-drivers currently supports only Ubuntu/"
            p "      Debian (apt-get not found). On other distros, copy the"
            p "      suggestions above and run them manually."
            exit 2
        fi

        # --- 8b. Refuse if we won't be able to escalate ---
        if ! command -v sudo >/dev/null 2>&1; then
            p "      ERROR: sudo not found. Driver installs need root."
            exit 2
        fi

        # --- 8c. NVIDIA path ---
        # Guard is now NVIDIA_NO_DRIVER above; this nested check stays
        # as a belt-and-suspenders sanity net in case future code paths
        # set NVIDIA_NO_DRIVER spuriously.
        if [ "$NVIDIA_JSON" = "[]" ] && ! printf '%s' "$PCI_RAW" | grep -iqE '10de|nvidia'; then
            p "      ERROR: --install-drivers only handles NVIDIA on Linux"
            p "      today. No NVIDIA hardware was found, so there's nothing"
            p "      to install. AMD and Intel paths are deferred."
            exit 2
        fi

        # --- 8d. Confirm. Default no — driver installs can break a display. ---
        p "      About to install the proprietary NVIDIA driver via apt."
        p "      This will:"
        p "        * add/update kernel modules (DKMS rebuild)"
        p "        * pull ~500 MB of packages"
        p "        * require a reboot to activate"
        if [ "$SECURE_BOOT" = "enabled" ]; then
            p ""
            p "      Secure Boot is ENABLED on this box. The Ubuntu installer"
            p "      will prompt for a one-time-password and enroll a MOK key."
            p "      Write the password down — you will be asked for it during"
            p "      the reboot before the driver becomes active. If you skip"
            p "      MOK enrollment, the module won't load and you'll have no"
            p "      working driver after reboot."
        fi
        p ""
        printf "      Proceed with sudo ubuntu-drivers autoinstall? [y/N]: "
        read -r INSTALL_ANS
        case "${INSTALL_ANS:-N}" in
            y|Y|yes|YES) ;;
            *)
                p "      Aborted by operator."
                exit 1
                ;;
        esac

        # --- 8e. Prefer ubuntu-drivers (knows which series suits the card) ---
        if command -v ubuntu-drivers >/dev/null 2>&1; then
            p ""
            p "      Running: sudo ubuntu-drivers autoinstall"
            if sudo ubuntu-drivers autoinstall; then
                p "      ubuntu-drivers autoinstall completed."
            else
                p "      ERROR: ubuntu-drivers autoinstall exited non-zero."
                p "      Check the output above. Common causes: held packages,"
                p "      conflicting nouveau module, apt lock held elsewhere."
                exit 2
            fi
        else
            # Fallback: try to install a current driver metapackage
            # directly. Hard-coding a specific series (we used to ship
            # 535, then 550) goes stale every ~6 months as NVIDIA
            # publishes new branches (580, 590, 595, ...) and old
            # series fall out of the LTS repository. Probe apt for
            # the highest -server metapackage actually available, and
            # only fall back to a known-stable name if that fails.
            p ""
            p "      ubuntu-drivers helper not found. Looking up the"
            p "      latest nvidia-driver-NNN-server metapackage..."
            # apt-cache pkgnames matches the prefix; filter to NNN-server
            # form and pick the highest series numerically.
            CANDIDATE="$(apt-cache pkgnames nvidia-driver- 2>/dev/null \
                | grep -E '^nvidia-driver-[0-9]+-server$' \
                | sort -t- -k3 -n \
                | tail -1)"
            if [ -z "$CANDIDATE" ]; then
                # No -server metapackage? Try the non-server form.
                CANDIDATE="$(apt-cache pkgnames nvidia-driver- 2>/dev/null \
                    | grep -E '^nvidia-driver-[0-9]+$' \
                    | sort -t- -k3 -n \
                    | tail -1)"
            fi
            if [ -z "$CANDIDATE" ]; then
                # Last resort: a known-recent name. May be stale; the
                # operator will see the apt error and can install a
                # current one by hand.
                CANDIDATE="nvidia-driver-550-server"
                p "      WARNING: could not find any nvidia-driver-NNN package"
                p "      in apt's cache. Falling back to $CANDIDATE; if apt"
                p "      reports it doesn't exist, run"
                p "        apt-cache search ^nvidia-driver-"
                p "      and install the highest series shown."
            else
                p "      Latest available: $CANDIDATE"
            fi
            p "      Will run: sudo apt-get install -y $CANDIDATE"
            p "      (If your card needs a different series, install that"
            p "      instead and re-run this script to confirm.)"
            printf "      Proceed with the fallback apt-get install? [y/N]: "
            read -r FB_ANS
            case "${FB_ANS:-N}" in
                y|Y|yes|YES) ;;
                *) p "      Aborted by operator."; exit 1 ;;
            esac
            sudo apt-get update -qq || { p "      apt-get update failed."; exit 2; }
            if sudo apt-get install -y "$CANDIDATE"; then
                p "      $CANDIDATE installed."
            else
                p "      ERROR: apt-get install failed. See output above."
                exit 2
            fi
        fi

        # --- 8f. Post-install summary + reboot prompt ---
        p ""
        p "      Driver installed. A REBOOT is required before the new"
        p "      kernel module is loaded; nvidia-smi will keep reporting"
        p "      the old (or no) driver until then."
        if [ "$SECURE_BOOT" = "enabled" ]; then
            p ""
            p "      MOK enrollment reminder: during the reboot, watch for the"
            p "      blue MOK Manager screen. Press a key to enter, then:"
            p "        Enroll MOK -> Continue -> Yes -> (enter your password)"
            p "        -> Reboot"
            p "      If you miss the MOK screen, the driver won't load and"
            p "      you'll need to either re-trigger MOK or disable Secure"
            p "      Boot in firmware."
        fi
        p ""
        printf "      Reboot now? [y/N]: "
        read -r REBOOT_ANS
        case "${REBOOT_ANS:-N}" in
            y|Y|yes|YES)
                p "      Rebooting in 5s. Ctrl+C to abort."
                sleep 5
                sudo reboot
                ;;
            *)
                p "      Reboot deferred. Run 'sudo reboot' when you're ready."
                ;;
        esac
        exit 0
    fi
fi

# --- 9. JSON output (if requested) -------------------------------
if [ "$JSON" = "yes" ]; then
    # Build the recommendations and commands JSON arrays.
    # The ${arr[@]:-} trick relies on Bash 4.3+ behavior under set -u;
    # the explicit length check below is portable to older Bash and
    # easier to read.
    REC_JSON="["
    FIRST=1
    if [ ${#RECS[@]} -gt 0 ]; then
        for R in "${RECS[@]}"; do
            [ -z "$R" ] && continue
            if [ "$FIRST" -eq 0 ]; then REC_JSON="${REC_JSON},"; fi
            FIRST=0
            REC_JSON="${REC_JSON}\"$(json_escape "$R")\""
        done
    fi
    REC_JSON="${REC_JSON}]"

    CMD_JSON="["
    FIRST=1
    if [ ${#CMDS[@]} -gt 0 ]; then
        for C in "${CMDS[@]}"; do
            [ -z "$C" ] && continue
            if [ "$FIRST" -eq 0 ]; then CMD_JSON="${CMD_JSON},"; fi
            FIRST=0
            CMD_JSON="${CMD_JSON}\"$(json_escape "$C")\""
        done
    fi
    CMD_JSON="${CMD_JSON}]"

    # Single-line JSON for easy machine parsing.
    printf '{"nvidia":%s,"amd":%s,"intel":%s,"apple":%s,"any_gpu":%s,"any_accelerator":%s,"boot":{"firmware":"%s","secure_boot":"%s"},"recommendations":%s,"suggested_commands":%s}\n' \
        "$NVIDIA_JSON" "$AMD_JSON" "$INTEL_JSON" "$APPLE_JSON" \
        "$([ "$ANY_GPU" = "yes" ] && echo true || echo false)" \
        "$([ "$ANY_ACCEL" = "yes" ] && echo true || echo false)" \
        "$BOOT_KIND" "$SECURE_BOOT" \
        "$REC_JSON" "$CMD_JSON"
fi

p ""
p " ================================================================"

# Exit code: 0 if Ollama has a real accelerator to use, 1 if it'll be
# CPU-only, 2 only if detection itself crapped out (handled inline).
if [ "$ANY_ACCEL" = "yes" ]; then
    exit 0
else
    exit 1
fi
