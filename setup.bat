@echo off
REM One-time setup: creates .venv and installs everything (about 3 GB download incl. CUDA libs).
cd /d "%~dp0"
set PY=
for %%V in (3.12 3.11 3.10) do (
  if not defined PY ( py -%%V --version >nul 2>nul && set PY=py -%%V )
)
if not defined PY set PY=python
echo Using: %PY%
%PY% -c "import sys; assert (3,10)<=sys.version_info[:2]<=(3,12), sys.version" || (echo Python 3.10, 3.11 or 3.12 is required: https://www.python.org/downloads/ & exit /b 1)
if not exist .venv %PY% -m venv .venv || exit /b 1
.venv\Scripts\python -m pip install --upgrade pip -q
.venv\Scripts\python -m pip install -r requirements.txt --retries 10 --timeout 60 || exit /b 1
.venv\Scripts\python setup_helper.py
echo.
echo Setup done. Make a clip with:   make_clip.bat "C:\path\to\video.mp4"
