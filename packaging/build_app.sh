#!/bin/bash
# Builds the macOS app -> dist/FBClipMaker-macOS.zip (Apple Silicon). Used by CI; also works on a Mac with Python 3.11.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv-build
. .venv-build/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r packaging/requirements-app.txt
# numpy's default wheel on macOS 14 runners needs macOS 14+; the macOS 11 wheel keeps older Macs supported
python -m pip download -q numpy==2.2.6 --only-binary=:all: --no-deps --platform macosx_11_0_arm64 \
  --python-version 3.11 --implementation cp --abi cp311 -d build/wheels
python -m pip install -q --force-reinstall --no-deps build/wheels/numpy-*.whl
python packaging/make_icon.py
python -m PyInstaller packaging/fbclip.spec --noconfirm --distpath dist --workpath build
dist/FBClipMaker.app/Contents/MacOS/FBClipMaker --selftest dist/selftest.txt
ditto -c -k --keepParent dist/FBClipMaker.app dist/FBClipMaker-macOS.zip
echo "Built dist/FBClipMaker-macOS.zip"
