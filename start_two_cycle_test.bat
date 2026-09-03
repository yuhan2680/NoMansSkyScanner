@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u -X utf8 -m nms_scanner.launcher --loop --max-warps 2 --max-runtime-seconds 180
pause
