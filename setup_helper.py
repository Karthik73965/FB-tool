"""Fetch the ffmpeg 4.2 build used for GPU (NVENC) encoding on older NVIDIA drivers -> bin/ffmpeg_nvenc.exe.
Newer ffmpeg builds need NVIDIA driver 610+ for NVENC; this one works with older drivers too.
Skipped automatically if the download fails (the tool then encodes on the CPU instead)."""
import glob, os, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
dst = os.path.join(HERE, "bin", "ffmpeg_nvenc.exe")
if os.path.exists(dst):
    print("bin/ffmpeg_nvenc.exe already present")
    sys.exit(0)
os.makedirs(os.path.dirname(dst), exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    r = subprocess.run([sys.executable, "-m", "pip", "download", "imageio-ffmpeg==0.4.9", "--no-deps",
                        "--only-binary=:all:", "--platform", "win_amd64", "-d", tmp, "-q"])
    whl = glob.glob(os.path.join(tmp, "*.whl"))
    if r.returncode or not whl:
        print("WARNING: could not download the NVENC ffmpeg build; GPU encoding falls back to CPU (slower).")
        sys.exit(0)
    with zipfile.ZipFile(whl[0]) as z:
        name = next(n for n in z.namelist() if n.endswith(".exe") and "ffmpeg" in n)
        with z.open(name) as f, open(dst, "wb") as o:
            o.write(f.read())
print("installed", dst)
