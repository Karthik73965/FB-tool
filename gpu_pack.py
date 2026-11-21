"""GPU pack for the packaged Windows app: the cuBLAS/cuDNN DLLs faster-whisper needs on NVIDIA GPUs (~2 GB unpacked).
They are not shipped inside the app (most of it would be dead weight on laptops without NVIDIA); instead the app
downloads the same nvidia-* wheels as requirements.txt from PyPI on first use and unpacks just the DLLs."""
import json, os, re, subprocess, sys, urllib.request, zipfile

WHEELS = [("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.28.1.4"), ("nvidia-cuda-nvrtc-cu12", "12.9.86")]


def has_nvidia():
    try:
        return subprocess.run(["nvidia-smi", "-L"], capture_output=True).returncode == 0
    except OSError:
        return False


def wanted():
    """only the frozen Windows app needs it; running from source uses the wheels in .venv"""
    return sys.platform == "win32" and getattr(sys, "frozen", False) and has_nvidia()


def ready(dest):
    return os.path.exists(os.path.join(dest, ".complete"))


def ensure(dest, progress=lambda f: None):
    if ready(dest):
        return
    os.makedirs(dest, exist_ok=True)
    files = []
    for name, ver in WHEELS:
        meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=60))
        files.append(next(u for u in meta["urls"] if u["filename"].endswith("win_amd64.whl")))
    total, done = sum(f["size"] for f in files), 0
    for f in files:
        whl = os.path.join(dest, f["filename"])
        with urllib.request.urlopen(f["url"], timeout=60) as r, open(whl + ".part", "wb") as o:
            while chunk := r.read(1 << 20):
                o.write(chunk)
                done += len(chunk)
                progress(done / total)
        os.replace(whl + ".part", whl)
        with zipfile.ZipFile(whl) as z:
            for m in z.namelist():
                if re.match(r"nvidia/[^/]+/bin/[^/]+\.dll$", m):
                    with z.open(m) as i, open(os.path.join(dest, os.path.basename(m)), "wb") as o:
                        while chunk := i.read(1 << 20):
                            o.write(chunk)
        os.remove(whl)
    open(os.path.join(dest, ".complete"), "w").write("\n".join(f"{n}=={v}" for n, v in WHEELS))
