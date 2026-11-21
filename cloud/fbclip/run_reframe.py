"""Runs `reframe.py` exactly like the CLI. Sets one option first: FBCLIP_HWDEC=0 turns off CUDA decoding when the
container can't use it. Encoding still falls back to x264 inside reframe.pick_encoder."""
import os
import fbclip  # noqa: F401  (puts reframe.py on sys.path)
import reframe

if __name__ == "__main__":          # guarded: ProcessPoolExecutor children re-import this module on Windows
    if os.environ.get("FBCLIP_HWDEC") == "0":
        reframe._HW = []
    reframe.main()
