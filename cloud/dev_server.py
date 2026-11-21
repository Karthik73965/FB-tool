"""Run the website's API on this PC with no Modal and no R2: files go to cloud/.dev/, and jobs run on the local GPU
with the desktop .venv (the same reframe.py the CLI uses).

    cloud\\.venv\\Scripts\\python cloud\\dev_server.py        then, in web/:  npm run dev
    log in with the admin key printed below.
"""
import os, subprocess, sys
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEV = os.path.join(HERE, ".dev")
PORT = int(os.environ.get("PORT", "8000"))
os.environ.setdefault("FBCLIP_LOCAL_STORE", os.path.join(DEV, "store"))
os.environ.setdefault("FBCLIP_LOCAL_URL", f"http://127.0.0.1:{PORT}")
os.environ.setdefault("FBCLIP_WORK", os.path.join(DEV, "work"))
os.environ.setdefault("FBCLIP_MODEL", "large-v3-turbo")          # what the desktop app uses; faster locally
sys.path.insert(0, HERE)

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from fbclip import store as store_mod
from fbclip.api import create_app

RENDER_PY = os.path.join(ROOT, ".venv", "Scripts" if os.name == "nt" else "bin", "python")


class DevSpawner:
    """each job is a child process: render with the desktop .venv, fetch with this one (it has yt-dlp)"""

    def __init__(self):
        self.procs = {}

    def _run(self, py, kind, job_id):
        env = dict(os.environ, FBCLIP_RENDER_PYTHON=RENDER_PY)
        if kind == "fetch" and "REFRAME_FFMPEG" not in env:
            ff = subprocess.run([RENDER_PY, "-c", "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"],
                                capture_output=True, text=True).stdout.strip()
            env["REFRAME_FFMPEG"] = ff                  # only used as yt-dlp's ffmpeg here
        p = subprocess.Popen([py, "-m", "fbclip.worker", kind, job_id], cwd=HERE, env=env)
        self.procs[str(p.pid)] = p
        return str(p.pid)

    def render(self, job_id):
        return self._run(RENDER_PY, "render", job_id)

    def fetch(self, job_id):
        return self._run(sys.executable, "fetch", job_id)

    def cancel(self, call_id):
        p = self.procs.pop(call_id, None)
        if p and p.poll() is None:
            if os.name == "nt":                         # take reframe's ffmpeg / worker children with it
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:
                p.kill()


store = store_mod.from_env()
admin_key = os.environ.get("ADMIN_KEY", "dev-admin-key-123")
app = create_app(store, DevSpawner(), admin_key, os.environ.get("SESSION_SECRET", "dev-secret"),
                 origins=["http://localhost:3000", "http://127.0.0.1:3000"])


@app.put("/blob/{key:path}")
async def put_blob(key: str, request: Request):
    p = store.path(unquote(key))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    os.replace(p + ".tmp", p)
    return JSONResponse({"ok": True})


@app.get("/blob/{key:path}")
def get_blob(key: str, filename: str = "file", inline: int = 0):
    p = store.path(unquote(key))
    if not os.path.isfile(p):
        return JSONResponse({"detail": "not found"}, 404)
    return FileResponse(p, filename=filename, content_disposition_type="inline" if inline else "attachment")


if __name__ == "__main__":
    import uvicorn
    if not os.path.exists(RENDER_PY):
        print("note: the desktop .venv is missing (run setup.bat); renders will fail until it exists")
    print(f"dev API on http://127.0.0.1:{PORT}   admin key: {admin_key}")
    uvicorn.run(app, host="127.0.0.1", port=PORT)
