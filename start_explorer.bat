@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. See README.md.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -X utf8 -m nms_scanner.gui
