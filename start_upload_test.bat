@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv. Read README.md first.
  pause
  exit /b 1
)
echo One-cycle upload test: after READY press F2 to enable upload, then F1 to start.
".venv\Scripts\python.exe" -u -X utf8 -m nms_scanner.launcher --loop --max-warps 1 --max-runtime-seconds 180
echo.
echo Launcher finished. Keep this window and the logs if an error was shown.
pause
endlocal
