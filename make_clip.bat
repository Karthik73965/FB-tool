@echo off
REM Usage: make_clip.bat "C:\path\to\video.mp4"   -> writes video_9x16.mp4 next to the source
cd /d "%~dp0"
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONWARNINGS=ignore
.venv\Scripts\python reframe.py %* || exit /b 1
.venv\Scripts\python verify.py %1
