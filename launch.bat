@echo off
setlocal
cd /d "%~dp0"
python -c "import PySide6" >nul 2>&1
if errorlevel 1 (
  echo Python 3.12+ and PySide6 are required.
  echo Install dependencies: python -m pip install -r requirements.txt
  pause
  exit /b 1
)
pythonw -m q_pixelate %*
