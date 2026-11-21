"""Quality check for a finished clip: every camera cut must change on exactly one frame (no flash frames).

    .venv\\Scripts\\python verify.py "C:\\path\\to\\video.mp4"     (checks video_9x16.mp4 next to it)
Exit code 0 = clean, 1 = problems found (times printed)."""
import json, os, subprocess, sys
import numpy as np
import reframe


def jumps(path, w, h):
    raw = subprocess.run([reframe.ffmpeg_exe(), "-v", "error", "-i", path, "-vf", f"scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)
    return np.r_[0, np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))]


def check(out, analysis_path, edits):
    """-> (cuts checked, frames, seconds, sorted flash-frame times in s)"""
    do = jumps(out, 54, 96)
    a = json.load(open(analysis_path))
    plan, cuts = reframe.make_plan(a, edits)
    fps = a["fps"]
    bad = [round(c / fps, 2) for c in cuts[1:-1] if c + 2 < len(do) and (do[c - 1] > 15 or do[c + 1] > 15)]
    dbl = [round(i / fps, 2) for i in range(1, len(do) - 1) if do[i] > 20 and do[i + 1] > 20]
    return len(cuts) - 2, len(do), len(do) / fps, sorted(set(bad + dbl))


if __name__ == "__main__":
    src = os.path.abspath(sys.argv[1])
    name = os.path.splitext(os.path.basename(src))[0]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(src), f"{name}_9x16.mp4")
    work = os.path.join(reframe.HERE, "work", name)
    edits_path = os.path.join(os.path.dirname(src), f"{name}.edits.json")
    edits = json.load(open(edits_path)) if os.path.exists(edits_path) else {}
    n_cuts, n_frames, secs, problems = check(out, os.path.join(work, "analysis.json"), edits)
    print(f"verify: {n_cuts} cuts checked, {n_frames} frames ({secs:.2f}s)")
    if problems:
        print("PROBLEMS - flash frames at (s):", problems)
        sys.exit(1)
    print("verify: OK - no flash frames")
