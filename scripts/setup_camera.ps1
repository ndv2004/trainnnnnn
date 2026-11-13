param(
    [string]$Python = "",
    [switch]$CheckOnly,
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$taskEnv = Join-Path $taskRoot ".venv-camera"
$taskEnvPython = Join-Path $taskEnv "Scripts\python.exe"
$taskProbe = "import sys,struct,tkinter,venv,ensurepip; assert sys.version_info[:2] == (3,12), 'Python 3.12 required'; assert struct.calcsize('P') == 8, '64-bit Python required'; root=tkinter.Tk(); root.withdraw(); root.update_idletasks(); root.destroy(); print(sys.executable)"
$script:taskProbeErrors = New-Object System.Collections.Generic.List[string]

function Test-CameraPython([string]$candidate) {
    if (-not $candidate -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $false }
    $taskSavedErrorPreference = $ErrorActionPreference
    try {
        # Preserve the full native stderr (not just the first traceback line).
        $ErrorActionPreference = "Continue"
        $taskOutput = & $candidate -X utf8 -c $taskProbe 2>&1 | Out-String
        $taskProbeExit = $LASTEXITCODE
        $ErrorActionPreference = $taskSavedErrorPreference
        if ($taskProbeExit -ne 0) {
            $script:taskProbeErrors.Add("$candidate : $taskOutput")
        }
        return ($taskProbeExit -eq 0)
    } catch {
        $script:taskProbeErrors.Add("$candidate : $($_.Exception.Message)")
        return $false
    } finally { $ErrorActionPreference = $taskSavedErrorPreference }
}

function Find-CameraPython {
    if ($Python) {
        $taskExplicit = $Python
        if (-not (Test-Path -LiteralPath $taskExplicit -PathType Leaf)) {
            $taskCommand = Get-Command $Python -ErrorAction SilentlyContinue
            if ($taskCommand) { $taskExplicit = $taskCommand.Source }
        }
        if (-not (Test-CameraPython $taskExplicit)) {
            throw "The -Python path must run Python 3.12 x64 with usable Tcl/Tk, venv and ensurepip: $Python. Repair/install Tcl/Tk through that Python installer if missing. Probe: $($script:taskProbeErrors -join [Environment]::NewLine)"
        }
        return $taskExplicit
    }

    $taskCandidates = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $taskEnvPython) { $taskCandidates.Add($taskEnvPython) }
    # An existing project venv can reveal Python even when py/PATH is not configured.
    # Only read it; never install packages into or alter that environment.
    $taskOldPython = Join-Path $taskRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $taskOldPython) {
        try {
            $taskBase = & $taskOldPython -c "import sys; print(sys._base_executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $taskBase) { $taskCandidates.Add([string]$taskBase) }
        } catch {}
    }
    $taskLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($taskLauncher) {
        try {
            $taskFromLauncher = & $taskLauncher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $taskFromLauncher) { $taskCandidates.Add([string]$taskFromLauncher) }
        } catch {}
    }
    foreach ($taskName in @("python3.12.exe", "python.exe")) {
        $taskCommand = Get-Command $taskName -ErrorAction SilentlyContinue
        if ($taskCommand) { $taskCandidates.Add($taskCommand.Source) }
    }
    foreach ($taskRegistry in @(
        "HKCU:\Software\Python\PythonCore\3.12\InstallPath",
        "HKLM:\Software\Python\PythonCore\3.12\InstallPath"
    )) {
        if (Test-Path $taskRegistry) {
            $taskInstall = (Get-Item $taskRegistry).GetValue("")
            if ($taskInstall) { $taskCandidates.Add((Join-Path $taskInstall "python.exe")) }
        }
    }
    if ($env:LOCALAPPDATA) {
        $taskCandidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"))
    }
    if ($env:ProgramFiles) { $taskCandidates.Add((Join-Path $env:ProgramFiles "Python312\python.exe")) }
    foreach ($taskCandidate in ($taskCandidates | Select-Object -Unique)) {
        if (Test-CameraPython $taskCandidate) { return $taskCandidate }
    }
    throw @"
Python 3.12 x64 with a usable Tk window was not found. Nothing was installed.
Install Python 3.12 64-bit from https://www.python.org/downloads/windows/ .
In the installer enable 'pip', 'tcl/tk and IDLE', and 'Python launcher'.
If Python already exists, repair its Tcl/Tk components; existing environments are preserved.
Then run setup_camera.bat again, or provide an existing interpreter explicitly:
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_camera.ps1 -Python 'C:\path\to\Python312\python.exe'
Probe diagnostics:
$($script:taskProbeErrors -join [Environment]::NewLine)
"@
}

try {
    $taskSelected = Find-CameraPython
    Write-Host "Python 3.12 x64 / Tk window OK: $taskSelected"
    if ($CheckOnly) { exit 0 }
    if (Test-Path -LiteralPath $taskEnvPython) {
        if (-not (Test-CameraPython $taskEnvPython)) {
            throw "Existing .venv-camera is incompatible. It has been preserved. Rename that folder, then run setup again."
        }
    } elseif (Test-Path -LiteralPath $taskEnv) {
        throw "Incomplete .venv-camera exists and has been preserved. Rename that folder, then run setup again."
    } else {
        Write-Host "Creating isolated .venv-camera ..."
        & $taskSelected -m venv $taskEnv
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $taskEnvPython)) {
            throw "Creating .venv-camera failed. No dependency install was attempted. Check disk access and Python's venv/ensurepip components."
        }
    }
    if (-not (Test-CameraPython $taskEnvPython)) {
        throw "The new environment failed validation. No dependency install was attempted."
    }
    & $taskEnvPython -m pip install --only-binary=:all: -r (Join-Path $taskRoot "requirements-camera.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Fix the pip error above and rerun setup; the application was not started." }
    & $taskEnvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency compatibility check failed. The application was not started." }
    & $taskEnvPython -c "import cv2, mediapipe, PIL, cryptography, tkinter; assert hasattr(cv2, 'FaceDetectorYN_create') and hasattr(cv2, 'FaceRecognizerSF_create'); print('Camera imports OK')"
    if ($LASTEXITCODE -ne 0) { throw "Runtime import check failed. Read the missing module/runtime message above." }
    if (-not $SkipModels) {
        & $taskEnvPython (Join-Path $PSScriptRoot "check_camera_models.py") --download --smoke
        if ($LASTEXITCODE -ne 0) { throw "Model verification/inference failed. Existing weights were preserved. Read the module error above." }
    }
    Write-Host "Installed. Open start_camera.bat for the camera window."
    exit 0
} catch {
    Write-Host "CAMERA SETUP FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
