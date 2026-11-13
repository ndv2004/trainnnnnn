@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_camera.ps1" %*
set "CAMERA_EXIT=%ERRORLEVEL%"
if not "%CAMERA_EXIT%"=="0" (
  echo.
  echo Camera setup failed. Read the error above; no camera process was started.
  pause
  exit /b %CAMERA_EXIT%
)
echo.
echo Setup complete. Open start_camera.bat to start the webcam.
pause
exit /b 0
