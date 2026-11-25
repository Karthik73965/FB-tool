# PyInstaller spec for FB Clip Maker (Windows .exe folder / macOS .app). Build: packaging\build_app.bat or build_app.sh
import os, sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(SPECPATH, "icon.ico" if sys.platform == "win32" else "icon.icns")

datas = [(os.path.join(ROOT, "fonts"), "fonts"), (os.path.join(ROOT, "ui"), "ui")]
for pkg in ("mediapipe", "faster_whisper", "imageio_ffmpeg", "webview"):
    datas += collect_data_files(pkg)
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
nvenc = os.path.join(ROOT, "bin", "ffmpeg_nvenc.exe")     # ffmpeg 4.2 for NVENC on older drivers (setup_helper.py)
if sys.platform == "win32" and os.path.exists(nvenc):
    binaries.append((nvenc, "bin"))

a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=["reframe", "verify", "broll", "gpu_pack", "webview", "clr"] + collect_submodules("yt_dlp"),
    excludes=["nvidia", "torch", "tensorflow", "jax", "jaxlib", "IPython", "pytest", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="FBClipMaker", console=False, icon=ICON,
          upx=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name="FBClipMaker", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(coll, name="FBClipMaker.app", icon=ICON, bundle_identifier="local.fbclipmaker",
                 info_plist={"CFBundleDisplayName": "FB Clip Maker", "CFBundleName": "FB Clip Maker",
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0"})
