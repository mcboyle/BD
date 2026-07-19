#!/usr/bin/env bash
# uninstall_all.sh - run every BulkDownloader uninstaller in safe order.
#
# Order matters:
#   1. remote_teach -- depends on the bulkdownloader service drop-in
#                      dir; removed first so its restart-bulkdownloader
#                      step actually has a service to restart.
#   2. service      -- removes the bulkdownloader systemd unit and
#                      (with --purge) the user data.
#   3. ai_ollama    -- removes the ollama service + binary + models.
#   4. linux        -- removes the venv + frontend builds + Playwright
#                      cache. Done last because if everything above
#                      failed we still might want the venv for diagnosis.
#   5. gpu_check    -- side-effect uninstaller (NVIDIA driver removal).
#                      Only acts when --remove-nvidia is passed; this
#                      orchestrator does NOT pass it, so gpu_check is
#                      effectively a no-op here. Run uninstall_gpu_check.sh
#                      directly with --remove-nvidia if you want that.
#
# Flags are forwarded to each sub-uninstaller verbatim, with one
# exception: --remove-nvidia is intentionally NOT forwarded -- removing
# the display driver is too risky to default-on as part of a "wipe it
# all" command. Run uninstall_gpu_check.sh by hand for that.
#
# Flags:
#   --dry-run     forwarded
#   --yes         forwarded; suppresses prompts including the big-confirm
#   --purge       forwarded; the full-wipe behavior you asked for
#   --keep-models forwarded; spares Ollama's 30+ GB model store
#
# Run from the project folder (same place the install scripts ran):
#     chmod +x uninstall_all.sh && ./uninstall_all.sh --purge
set -u
set -o pipefail

DRY_RUN="no"
ASSUME_YES="no"
PURGE="no"
KEEP_MODELS="no"
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN="yes" ;;
        --yes|-y)      ASSUME_YES="yes" ;;
        --purge)       PURGE="yes" ;;
        --keep-models) KEEP_MODELS="yes" ;;
        -h|--help)
            awk 'NR==1 {next}
                 /^[^#]/ && !/^$/ {exit}
                 /^#/ {sub(/^# ?/, ""); print}' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown arg: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

cd "$(dirname "$(readlink -f "$0")")" || {
    echo "ERROR: could not cd to script directory" >&2
    exit 1
}
HERE="$(pwd)"

# Build the forwarded args once. Use an array, not a string, so that
# whitespace in (hypothetical) future flag values isn't mangled by
# word-splitting.
FORWARD=()
[ "$DRY_RUN" = "yes" ]     && FORWARD+=(--dry-run)
[ "$ASSUME_YES" = "yes" ]  && FORWARD+=(--yes)
[ "$PURGE" = "yes" ]       && FORWARD+=(--purge)
[ "$KEEP_MODELS" = "yes" ] && FORWARD+=(--keep-models)

# Order is documented at the top. Skip any uninstaller that isn't
# present alongside this script -- a partial install layout (e.g.
# someone only kept uninstall_service.sh) should still work for
# what's there. Missing files are not an error.
SCRIPTS=(
    uninstall_remote_teach.sh
    uninstall_service.sh
    uninstall_ai_ollama.sh
    uninstall_linux.sh
    uninstall_gpu_check.sh
)

echo " ================================================================"
echo "  BulkDownloader - uninstall (all components)"
echo " ================================================================"
echo "  Order          : ${SCRIPTS[*]}"
echo "  Forwarded args : ${FORWARD[*]:-(none)}"
echo "  Working dir    : $HERE"
echo

# Two prompts, in order, when --purge is set without --yes:
#   1. A normal y/N "continue?" prompt covering the non-data actions.
#   2. A typed-WIPE prompt covering the data deletion specifically.
# Without --purge only the first prompt fires. --yes suppresses both;
# that's the documented CI escape. The typed prompt has to live here
# (not in uninstall_service.sh) because this script auto-forwards
# --yes to its children, which would otherwise bypass the inner
# WIPE prompt while still allowing the orchestrator-level y/N to
# stand in for a data wipe -- not loud enough.
if [ "$ASSUME_YES" != "yes" ] && [ "$DRY_RUN" = "no" ]; then
    if [ "$PURGE" = "yes" ]; then
        echo "  This will run all uninstallers WITH --purge:"
        echo "    - systemd services stopped, disabled, units removed"
        echo "    - venv, node_modules, frontend/dist removed"
        echo "    - Ollama binary + models removed (unless --keep-models)"
        echo "    - apt packages installed by remote_teach removed"
        echo "    - \$BD_HOME (DB, downloads, cookies) WIPED"
        echo "  NVIDIA driver removal is NOT included (run"
        echo "  uninstall_gpu_check.sh --remove-nvidia for that)."
    else
        echo "  This will run all uninstallers (without --purge):"
        echo "    - systemd services stopped, disabled, units removed"
        echo "    - venv, node_modules, frontend/dist removed"
        echo "    - Ollama binary + service removed; models kept unless"
        echo "      --keep-models is missing"
        echo "    - User data in \$BD_HOME KEPT"
        echo "    - apt packages KEPT"
    fi
    echo
    printf "  Continue? [y/N]: "
    read -r ANS
    case "${ANS:-N}" in
        y|Y|yes|YES) ;;
        *) echo "  Aborted."; exit 1 ;;
    esac

    # Second prompt: typed WIPE confirm, only when --purge is set.
    # This one is unbypassable by anything except --yes (which is the
    # explicit CI escape hatch). Typed (case-sensitive) rather than
    # y/N because the cost of a wrong keystroke here is permanent
    # data loss.
    if [ "$PURGE" = "yes" ]; then
        echo
        echo " *****************************************************************"
        echo " *  --purge will DELETE all user data in \$BD_HOME"
        echo " *  including bulk_downloader.db, downloads/, and sites_config.json."
        echo " *  This cannot be undone."
        echo " *****************************************************************"
        printf "  Type 'WIPE' to confirm full data wipe: "
        read -r WIPE_ANS
        if [ "$WIPE_ANS" != "WIPE" ]; then
            echo "  Aborted (you did not type WIPE)."
            exit 1
        fi
    fi
fi

# Auto-add --yes to the args we forward, so each sub-uninstaller
# doesn't reprompt. The orchestrator-level prompts above (including
# the typed-WIPE for --purge) have already done the safety work; the
# per-script y/N prompts would just be noise here.
FORWARD_AUTO=("${FORWARD[@]}")
case " ${FORWARD_AUTO[*]} " in
    *' --yes '*) ;;  # already there
    *) FORWARD_AUTO+=(--yes) ;;
esac

OVERALL_RC=0
for s in "${SCRIPTS[@]}"; do
    SCRIPT_PATH="$HERE/$s"
    if [ ! -f "$SCRIPT_PATH" ]; then
        echo "  -- $s not found at $SCRIPT_PATH; skipping."
        continue
    fi
    if [ ! -x "$SCRIPT_PATH" ]; then
        # chmod +x ourselves -- the operator may have unzipped without
        # preserving exec bits. Cheap.
        chmod +x "$SCRIPT_PATH" 2>/dev/null || true
    fi
    echo
    echo " ----------------------------------------------------------------"
    echo "  Running: $s ${FORWARD_AUTO[*]}"
    echo " ----------------------------------------------------------------"
    # Don't let one failed sub-uninstaller stop the others: each is
    # idempotent and addresses a different scope. Track the worst exit
    # code seen and return it at the end so CI can flag partial failure.
    #
    # Capture $? BEFORE any test: `if ! cmd; then rc=$?; fi` would
    # overwrite $? with the negation's result (0 or 1) instead of the
    # underlying command's real exit code, silently breaking the
    # worst-rc contract this script promises.
    "$SCRIPT_PATH" "${FORWARD_AUTO[@]}"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "  WARNING: $s exited $rc; continuing with remaining uninstallers."
        if [ "$rc" -gt "$OVERALL_RC" ]; then OVERALL_RC=$rc; fi
    fi
done

echo
echo " ================================================================"
if [ "$OVERALL_RC" -eq 0 ]; then
    echo "  All uninstallers completed without errors."
else
    echo "  Uninstall finished, but at least one step exited non-zero"
    echo "  (overall rc=$OVERALL_RC). See output above. Re-run individual"
    echo "  uninstallers to retry specific scopes."
fi
echo " ================================================================"
exit "$OVERALL_RC"
