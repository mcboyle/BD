@echo off
setlocal EnableDelayedExpansion
title BulkDownloader - GPU detection (Windows, read-only)
REM ============================================================================
REM  gpu_check.bat - detect GPU(s) on Windows and report what Ollama can use.
REM
REM  Standalone: safe to run on any Windows 10+/Server 2019+ machine. Read-
REM  only by default. Also called from install_ai_ollama.bat in place of its
REM  old inline 8-line GPU check.
REM
REM  Detects: NVIDIA (name, VRAM, driver, CUDA, compute capability via
REM           nvidia-smi), plus AMD/Intel/Apple presence via Win32_
REM           VideoController. CUDA toolkit version via nvcc when on PATH.
REM
REM  Flags:
REM    --quiet            : only print the final recommendation
REM    --json             : print a JSON blob (parsed by the installer)
REM    --install-drivers  : opt in to executing the printed suggestions.
REM                         Prompts before every action. Windows reality:
REM                         there is no clean winget package for "just
REM                         install the NVIDIA driver" as of May 2026
REM                         (CUDA 13 stopped bundling the driver; NVIDIA
REM                         App is not yet a stable winget package).
REM                         This script offers two honest paths and
REM                         declines to silently winget-install CUDA as
REM                         a way to maybe get a driver. Mutually
REM                         exclusive with --json.
REM    --help             : print this header
REM
REM  Exit codes:
REM    0 - at least one usable accelerator detected
REM    1 - no GPU found, or GPU present but no working runtime (CPU-only)
REM    2 - detection itself failed (bad arg, missing wmic+powershell, ...)
REM
REM  Pure 7-bit ASCII / CRLF / no unquoted redirection inside for-loops.
REM ============================================================================

set "QUIET=no"
set "JSON=no"
set "DO_INSTALL=no"

:argloop
if "%~1"=="" goto :argdone
if /i "%~1"=="--quiet"           ( set "QUIET=yes" & shift & goto :argloop )
if /i "%~1"=="-q"                ( set "QUIET=yes" & shift & goto :argloop )
if /i "%~1"=="--json"            ( set "JSON=yes"  & shift & goto :argloop )
if /i "%~1"=="-j"                ( set "JSON=yes"  & shift & goto :argloop )
if /i "%~1"=="--install-drivers" ( set "DO_INSTALL=yes" & shift & goto :argloop )
if /i "%~1"=="--help"            ( goto :help )
if /i "%~1"=="-h"                ( goto :help )
echo   ERROR: unknown argument: %~1 (try --help) 1>&2
exit /b 2

:help
findstr /b /c:"REM " "%~f0" | findstr /v /c:"REM ====" | findstr /v /c:"REM @echo"
exit /b 0

:argdone

REM --- Mutual exclusion check ---
REM --install-drivers prompts the operator; --json is for machine output.
REM The two modes cannot coexist sanely.
if "%DO_INSTALL%"=="yes" if "%JSON%"=="yes" (
    echo   ERROR: --install-drivers and --json are mutually exclusive 1>&2
    exit /b 2
)

REM --- Output helpers (no associative arrays in cmd; collect as scalars) ---
set "NVIDIA_JSON=[]"
set "AMD_JSON=[]"
set "INTEL_JSON=[]"
set "ANY_GPU=no"
set "ANY_ACCEL=no"
set "REC_LINE="
set "CMD_LINE="
REM Sentinel for "any non-empty assignment lives in this variable":
REM cmd makes ASCII-newline-in-variable awkward, so we use this delimiter.
set "DELIM=%%%%"

REM In JSON mode the human header is suppressed.
if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo.
    echo  ================================================================
    echo   BulkDownloader - GPU detection ^(read-only, no install^)
    echo  ================================================================
)

REM --- 1. WMI inventory (vendor-agnostic) ----------------------------------
REM wmic is deprecated as of Win10 21H1 but still ships through Server 2025;
REM PowerShell's Get-CimInstance is the modern equivalent. Use wmic if
REM present (faster, no PS startup cost), otherwise fall back.
if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo.
    echo   [1] Video controller inventory
)
set "WMI_FOUND="
where wmic >nul 2>nul
if not errorlevel 1 (
    REM /format:list emits Key=Value per line, with blank lines between records.
    REM Strip blanks and just keep Name lines so the parse is trivial.
    for /f "usebackq tokens=1,* delims==" %%K in (`wmic path win32_VideoController get Name /format:list 2^>nul`) do (
        if /i "%%K"=="Name" (
            set "VC_NAME=%%L"
            REM Some controllers stub out as "Microsoft Basic Display Adapter" when
            REM no real driver is present; surface that distinctly.
            if defined VC_NAME (
                if "%JSON%"=="no" if "%QUIET%"=="no" echo         !VC_NAME!
                set "WMI_FOUND=yes"
                set "ANY_GPU=yes"
                REM Vendor-route by name substring. Multiple controllers fine; we
                REM accumulate the last name into per-vendor scalars.
                echo !VC_NAME! | findstr /i "NVIDIA" >nul && set "NVIDIA_NAME=!VC_NAME!"
                echo !VC_NAME! | findstr /i "AMD ATI Radeon" >nul && set "AMD_NAME=!VC_NAME!"
                echo !VC_NAME! | findstr /i "Intel" >nul && set "INTEL_NAME=!VC_NAME!"
            )
        )
    )
)
if not defined WMI_FOUND (
    REM PowerShell fallback. Slower but works everywhere.
    where powershell >nul 2>nul
    if errorlevel 1 (
        if "%JSON%"=="no" echo         ERROR: neither wmic nor powershell available; cannot detect GPUs. 1>&2
        exit /b 2
    )
    for /f "usebackq tokens=*" %%V in (`powershell -NoProfile -Command "(Get-CimInstance Win32_VideoController).Name" 2^>nul`) do (
        set "VC_NAME=%%V"
        if defined VC_NAME (
            if "%JSON%"=="no" if "%QUIET%"=="no" echo         !VC_NAME!
            set "ANY_GPU=yes"
            echo !VC_NAME! | findstr /i "NVIDIA" >nul && set "NVIDIA_NAME=!VC_NAME!"
            echo !VC_NAME! | findstr /i "AMD ATI Radeon" >nul && set "AMD_NAME=!VC_NAME!"
            echo !VC_NAME! | findstr /i "Intel" >nul && set "INTEL_NAME=!VC_NAME!"
        )
    )
)
if "%ANY_GPU%"=="no" (
    if "%JSON%"=="no" if "%QUIET%"=="no" echo         No video controllers found.
)

REM --- 2. NVIDIA (deep dive via nvidia-smi) --------------------------------
if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo.
    echo   [2] NVIDIA
)
where nvidia-smi >nul 2>nul
if not errorlevel 1 (
    REM Parse the CSV row: name, memory.total (MiB), driver_version, compute_cap
    for /f "usebackq tokens=*" %%R in (`nvidia-smi --query-gpu^=name^,memory.total^,driver_version^,compute_cap --format^=csv^,noheader^,nounits 2^>nul`) do (
        set "NV_ROW=%%R"
        if defined NV_ROW (
            REM tokens=1,2,3,4 with comma delim
            for /f "tokens=1,2,3,4 delims=," %%a in ("!NV_ROW!") do (
                set "NV_NAME=%%a"
                set "NV_VRAM=%%b"
                set "NV_DRV=%%c"
                set "NV_CC=%%d"
                REM strip leading spaces
                for /f "tokens=* delims= " %%x in ("!NV_NAME!") do set "NV_NAME=%%x"
                for /f "tokens=* delims= " %%x in ("!NV_VRAM!") do set "NV_VRAM=%%x"
                for /f "tokens=* delims= " %%x in ("!NV_DRV!")  do set "NV_DRV=%%x"
                for /f "tokens=* delims= " %%x in ("!NV_CC!")   do set "NV_CC=%%x"
                set "ANY_ACCEL=yes"
                if "%JSON%"=="no" if "%QUIET%"=="no" (
                    echo         NVIDIA: !NV_NAME!
                    echo           VRAM         : !NV_VRAM! MiB
                    echo           driver       : !NV_DRV!
                    echo           compute cap  : !NV_CC!
                )
            )
        )
    )
    REM CUDA runtime: nvidia-smi prints "CUDA Version: X.Y" in its dashboard view.
    for /f "usebackq tokens=*" %%L in (`nvidia-smi 2^>nul ^| findstr /c:"CUDA Version"`) do (
        for /f "tokens=2 delims=:" %%C in ("%%L") do (
            for /f "tokens=1 delims=| " %%D in ("%%C") do set "CUDA_VER=%%D"
        )
    )
    if defined CUDA_VER (
        if "%JSON%"=="no" if "%QUIET%"=="no" echo           CUDA runtime : !CUDA_VER!
    )
    REM CUDA toolkit (nvcc) is separate from runtime; many machines have only
    REM the runtime via the driver. Don't penalize if missing.
    where nvcc >nul 2>nul && (
        for /f "usebackq tokens=*" %%V in (`nvcc --version 2^>nul ^| findstr /i "release"`) do set "NVCC_VER=%%V"
        if defined NVCC_VER (
            if "%JSON%"=="no" if "%QUIET%"=="no" echo           nvcc toolkit : !NVCC_VER!
        )
    )
) else (
    if defined NVIDIA_NAME (
        if "%JSON%"=="no" if "%QUIET%"=="no" (
            echo         NVIDIA hardware detected via WMI ^(!NVIDIA_NAME!^),
            echo         but nvidia-smi is not on PATH. The NVIDIA driver may
            echo         be too old, missing, or installed but the bin dir is
            echo         not in the system PATH.
        )
        set "CMD_LINE=winget install --id Nvidia.CUDA --accept-package-agreements --accept-source-agreements"
    ) else (
        if "%JSON%"=="no" if "%QUIET%"=="no" echo         No NVIDIA hardware found.
    )
)

REM --- 3. AMD ---------------------------------------------------------------
if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo.
    echo   [3] AMD
)
if defined AMD_NAME (
    if "%JSON%"=="no" if "%QUIET%"=="no" (
        echo         AMD: !AMD_NAME!
        echo           Ollama on Windows : narrow AMD support; check Ollama
        echo           release notes for your specific chip.
    )
) else (
    if "%JSON%"=="no" if "%QUIET%"=="no" echo         No AMD hardware found.
)

REM --- 4. Intel -------------------------------------------------------------
if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo.
    echo   [4] Intel
)
if defined INTEL_NAME (
    REM Distinguish Arc from integrated by name substring.
    set "INTEL_KIND=igpu"
    echo !INTEL_NAME! | findstr /i "Arc" >nul && set "INTEL_KIND=arc"
    if "%JSON%"=="no" if "%QUIET%"=="no" (
        if "!INTEL_KIND!"=="arc" (
            echo         Intel discrete: !INTEL_NAME!
            echo           Ollama       : limited support via IPEX-LLM
        ) else (
            echo         Intel integrated: !INTEL_NAME!
            echo           Ollama       : NOT supported on iGPU; CPU fallback only
        )
    )
) else (
    if "%JSON%"=="no" if "%QUIET%"=="no" echo         No Intel display hardware found.
)

REM --- 5. Boot environment (Secure Boot affects NVIDIA modules) -------------
if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo.
    echo   [5] Boot environment
)
set "SECURE_BOOT=unknown"
where powershell >nul 2>nul
if not errorlevel 1 (
    for /f "usebackq tokens=*" %%S in (`powershell -NoProfile -Command "try { Confirm-SecureBootUEFI } catch { 'unknown' }" 2^>nul`) do set "SB_RAW=%%S"
    if /i "!SB_RAW!"=="True"  set "SECURE_BOOT=enabled"
    if /i "!SB_RAW!"=="False" set "SECURE_BOOT=disabled"
)
if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo         Secure Boot  : !SECURE_BOOT!
    echo         Note: Windows handles signed NVIDIA modules automatically;
    echo         Secure Boot is informational here, not a blocker.
)

REM --- 6. Recommendation ----------------------------------------------------
if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo.
    echo   [6] Recommendation
)
if defined NV_VRAM (
    REM Compare VRAM as plain integer; cmd does this natively with /A.
    set /a "VRAM_INT=NV_VRAM" 2>nul
    if !VRAM_INT! GEQ 12000 (
        if "%QUIET%"=="no" (
            if "%JSON%"=="no" (
                echo         NVIDIA with !NV_VRAM! MiB: both default 7B models
                echo         ^(vision + text^) fit comfortably on the GPU.
            )
        )
        set "REC_LINE=Both default 7B models fit on the GPU; fast inference expected."
    ) else if !VRAM_INT! GEQ 6000 (
        if "%QUIET%"=="no" (
            if "%JSON%"=="no" (
                echo         NVIDIA with !NV_VRAM! MiB: one 7B model fits on the
                echo         GPU at a time; layer-swap on switch ^(~1-2s extra^).
            )
        )
        set "REC_LINE=Tight fit for two 7B models; layer-swap on context switch."
    ) else (
        if "%QUIET%"=="no" (
            if "%JSON%"=="no" (
                echo         NVIDIA with !NV_VRAM! MiB: under the comfortable
                echo         threshold. Ollama will offload most layers to CPU.
            )
        )
        set "REC_LINE=Low VRAM for 7B; consider a 3B-class model in AI settings."
    )
) else if defined NVIDIA_NAME (
    if "%QUIET%"=="no" if "%JSON%"=="no" (
        echo         NVIDIA card present but driver/CUDA not loaded.
        echo         Install via winget and reboot, then re-run this check.
    )
    set "REC_LINE=NVIDIA card detected but driver missing."
) else if defined AMD_NAME (
    if "%QUIET%"=="no" if "%JSON%"=="no" (
        echo         AMD card present; Ollama's Windows AMD support is narrow.
        echo         Verify your chip against current Ollama release notes.
    )
    set "REC_LINE=AMD present; verify chip is supported by current Ollama Windows build."
) else if "%ANY_GPU%"=="no" (
    if "%QUIET%"=="no" if "%JSON%"=="no" (
        echo         No GPU detected. Ollama will run on CPU only ^(slow^).
    )
    set "REC_LINE=No GPU; CPU-only inference will be slow but functional."
) else (
    if "%QUIET%"=="no" if "%JSON%"=="no" (
        echo         GPU present but Ollama cannot use it ^(Intel iGPU, or
        echo         AMD/NVIDIA without runtime^). CPU fallback.
    )
    set "REC_LINE=GPU present but no usable runtime; CPU fallback."
)

REM --- 7. Suggested commands (printed only if a missing-driver case is real)
if defined CMD_LINE (
    if "%QUIET%"=="no" if "%JSON%"=="no" (
        echo.
        echo   [7] Suggested commands ^(copy-paste, or run with --install-drivers^)
        echo         !CMD_LINE!
        echo.
        echo         Pass --install-drivers to execute the runnable parts of
        echo         the recommendation after prompting.
    )
)

REM --- 8. --install-drivers execution --------------------------------------
REM
REM Windows reality check (May 2026): there is no clean winget package that
REM "just installs the latest NVIDIA driver". `Nvidia.CUDA` in winget bundled
REM the driver historically but as of CUDA 13 it ships toolkit-only. The new
REM `NVIDIA App` is the canonical driver-install path but is not yet on
REM winget as a stable package (the request is open but blocked on a known
REM manifest issue). The Microsoft Store has `9NF8H0H7WMLT` for "NVIDIA
REM Control Panel" but that does not install drivers.
REM
REM Honest paths:
REM   1. Browser to https://www.nvidia.com/en-us/software/nvidia-app/ -- the
REM      operator downloads NVIDIA App, which then handles the driver.
REM      This is the recommended path.
REM   2. winget install Nvidia.CUDA -- installs the CUDA Toolkit. On versions
REM      <13 the driver was bundled; on 13+ it is not. Useful if the operator
REM      also wants nvcc.
REM
REM We do NOT silently winget-install Nvidia.CUDA as a driver install -- that
REM would mislead an operator who only wants a driver into a 3 GB toolkit
REM install that may not actually update their driver.
if "%DO_INSTALL%"=="yes" (
    if "%JSON%"=="no" (
        echo.
        echo   [8] Driver install ^(--install-drivers^)
        if defined NV_VRAM (
            REM nvidia-smi already works -> we know the driver is present.
            echo         The NVIDIA driver is already loaded ^(nvidia-smi reports
            echo         version !NV_DRV!^). Nothing to install. To update to a
            echo         newer driver, run the NVIDIA App or download from
            echo         https://www.nvidia.com/Download/index.aspx.
            goto :install_done_skip
        )
        if not defined NVIDIA_NAME if not defined AMD_NAME (
            echo         No NVIDIA or AMD hardware detected. There is nothing
            echo         to install on this box.
            goto :install_done_skip
        )
        if defined AMD_NAME (
            echo         --install-drivers does not automate AMD driver
            echo         installs on Windows. Download from
            echo         https://www.amd.com/en/support and run the installer
            echo         manually. AMD on Windows + Ollama support is narrow;
            echo         verify your chip against the current Ollama release
            echo         notes before investing time in this path.
            goto :install_done_skip
        )

        echo         An NVIDIA card was detected but the driver is not loaded.
        echo         Windows has no clean winget-package path for "just install
        echo         the latest NVIDIA driver" as of May 2026. The two honest
        echo         options:
        echo.
        echo         [1] NVIDIA App ^(recommended^): handles drivers, no login.
        echo             We will open https://www.nvidia.com/en-us/software/nvidia-app/
        echo             so you can download and run the installer.
        echo.
        echo         [2] winget install Nvidia.CUDA: installs the CUDA Toolkit
        echo             ^(~3 GB^). Older CUDA versions also installed the
        echo             driver; as of CUDA 13 they do not. Pick this only if
        echo             you also want nvcc.
        echo.
        set "NV_CHOICE=1"
        set /p "NV_CHOICE=         Choose [1=NVIDIA App browser, 2=winget Nvidia.CUDA, S=skip, default 1]: "
        if /i "!NV_CHOICE!"=="S" goto :install_done_skip
        if "!NV_CHOICE!"=="2" goto :install_cuda
        REM Default and "1": browser path.
        echo.
        echo         Opening the NVIDIA App download page in your default
        echo         browser. After install, the App will offer to install
        echo         the latest GeForce driver. Re-run this script after
        echo         the driver install + reboot to verify.
        start "" "https://www.nvidia.com/en-us/software/nvidia-app/"
        goto :install_done_browser

        :install_cuda
        where winget >nul 2>nul
        if errorlevel 1 (
            echo         ERROR: winget not found on PATH. Windows 10 1809+
            echo         normally ships it; install "App Installer" from the
            echo         Microsoft Store and re-run.
            exit /b 2
        )
        echo.
        echo         Running: winget install --id Nvidia.CUDA --accept-package-agreements --accept-source-agreements
        echo         A UAC prompt may appear. The download is ~3 GB.
        winget install --id Nvidia.CUDA --accept-package-agreements --accept-source-agreements
        set "WG_RC=!ERRORLEVEL!"
        if not "!WG_RC!"=="0" (
            echo         ERROR: winget exited with code !WG_RC!. See output above.
            exit /b 2
        )
        echo         CUDA Toolkit install completed. If your CUDA version is
        echo         12.x or older, a driver was installed too; on 13.x you
        echo         still need to install the driver via NVIDIA App or
        echo         direct download.
        goto :install_done_browser

        :install_done_browser
        echo.
        echo         Driver install path launched. Reboot once the driver
        echo         install completes, then re-run gpu_check.bat to verify
        echo         nvidia-smi sees the card.
        exit /b 0

        :install_done_skip
        echo         Skipped.
        exit /b 0
    )
)

REM --- 9. JSON output ------------------------------------------------------
if "%JSON%"=="yes" (
    REM Build minimal JSON inline. AMD/Intel arrays carry only name + kind.
    set "NJ=[]"
    if defined NV_NAME (
        set "NJ=[{\"name\":\"!NV_NAME!\",\"vram_mib\":!NV_VRAM!,\"driver\":\"!NV_DRV!\",\"cuda\":\"!CUDA_VER!\",\"compute_cap\":\"!NV_CC!\"}]"
    ) else if defined NVIDIA_NAME (
        set "NJ=[{\"name\":\"!NVIDIA_NAME!\",\"vram_mib\":0,\"driver\":\"\",\"cuda\":\"\",\"compute_cap\":\"\"}]"
    )
    set "AJ=[]"
    if defined AMD_NAME ( set "AJ=[{\"name\":\"!AMD_NAME!\",\"vram_mib\":0}]" )
    set "IJ=[]"
    if defined INTEL_NAME ( set "IJ=[{\"name\":\"!INTEL_NAME!\",\"kind\":\"!INTEL_KIND!\"}]" )
    set "AGPU=false"
    if "%ANY_GPU%"=="yes" set "AGPU=true"
    set "AACC=false"
    if "%ANY_ACCEL%"=="yes" set "AACC=true"
    set "REC_JSON=[]"
    if defined REC_LINE set "REC_JSON=[\"!REC_LINE!\"]"
    set "CMD_JSON=[]"
    if defined CMD_LINE set "CMD_JSON=[\"!CMD_LINE!\"]"
    echo {"nvidia":!NJ!,"amd":!AJ!,"intel":!IJ!,"any_gpu":!AGPU!,"any_accelerator":!AACC!,"boot":{"secure_boot":"!SECURE_BOOT!"},"recommendations":!REC_JSON!,"suggested_commands":!CMD_JSON!}
)

if "%JSON%"=="no" if "%QUIET%"=="no" (
    echo.
    echo  ================================================================
)

if "%ANY_ACCEL%"=="yes" exit /b 0
exit /b 1
