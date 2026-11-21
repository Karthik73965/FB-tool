"""FB Clip Maker cloud version: API, GPU worker and link fetcher around the unchanged reframe.py pipeline."""
import os, sys

# reframe.py / verify.py / fonts/: the repo root locally, /app in the Modal image
REFRAME_DIR = os.environ.get("FBCLIP_REFRAME_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REFRAME_DIR not in sys.path:
    sys.path.insert(0, REFRAME_DIR)
