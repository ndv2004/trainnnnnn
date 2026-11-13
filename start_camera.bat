@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONUTF8=1"
cd /d "%~dp0"
set "CAMERA_PY=%~dp0.venv-camera\Scripts\python.exe"
if not exist "%CAMERA_PY%" (
  echo Camera environment is missing. Run setup_camera.bat first.
  pause
  exit /b 1
)
"%CAMERA_PY%" -X utf8 -c "import sys, struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8; import tkinter, cv2, mediapipe, PIL, cryptography; root=tkinter.Tk(); root.withdraw(); root.update_idletasks(); root.destroy()" >nul 2>&1
if errorlevel 1 (
  echo Camera environment is incomplete, Tcl/Tk is unavailable, or Python is incompatible.
  echo Run setup_camera.bat and read its diagnostic message. Existing environments are preserved.
  pause
  exit /b 1
)
"%CAMERA_PY%" -X utf8 "%~dp0run_camera.py" %*
set "CAMERA_EXIT=%ERRORLEVEL%"
if not "%CAMERA_EXIT%"=="0" (
  echo.
  echo The camera application reported an error. Read the message above.
  pause
)
exit /b %CAMERA_EXIT%
