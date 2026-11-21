@echo off
REM Builds the shareable Windows app -> dist\FBClipMaker-Windows.zip  (needs setup.bat to have run once)
cd /d "%~dp0\.."
.venv\Scripts\python -m pip install -q -r packaging\requirements-build.txt || exit /b 1
if not exist bin\ffmpeg_nvenc.exe .venv\Scripts\python setup_helper.py
.venv\Scripts\python packaging\make_icon.py || exit /b 1
.venv\Scripts\python -m PyInstaller packaging\fbclip.spec --noconfirm --distpath dist --workpath build || exit /b 1
echo Running selftest...
powershell -NoProfile -Command "$p = Start-Process -FilePath 'dist\FBClipMaker\FBClipMaker.exe' -ArgumentList '--selftest','dist\selftest.txt' -Wait -PassThru; Get-Content dist\selftest.txt; exit $p.ExitCode" || exit /b 1
powershell -NoProfile -Command "Compress-Archive -Path 'dist\FBClipMaker' -DestinationPath 'dist\FBClipMaker-Windows.zip' -Force" || exit /b 1
echo.
echo Built dist\FBClipMaker-Windows.zip  (unzip anywhere, run FBClipMaker.exe)
