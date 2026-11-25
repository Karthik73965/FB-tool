"""FB Clip Maker: desktop app around reframe.py (Windows .exe / macOS .app, built with PyInstaller).

    python app.py                  window: add videos, they render one after another, fully automatic
    python app.py --pipeline ...   internal: one render job (the window runs it as a child process)
    python app.py --selftest FILE  checks a packaged build can load everything (writes the result to FILE)

Each video is transcribed (Whisper large-v3-turbo, downloaded on first use), reframed, cut before a detected ad
read / bumper, rendered next to the original as <name>_9x16.mp4 and glitch-checked. Afterwards the review panel
can move the ending or fix caption words; a re-render only redoes the chunks that changed.
"""
import os, sys

if os.name == "nt":   # the windowed build has no console: keep ffmpeg & co. from flashing console windows
    import subprocess as _sp

    class _NoWindowPopen(_sp.Popen):
        def __init__(self, *a, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | 0x08000000   # CREATE_NO_WINDOW
            super().__init__(*a, **kw)

    _sp.Popen = _NoWindowPopen
for _name in ("stdout", "stderr"):   # windowed builds start with sys.stdout = None
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w"))

import difflib, hashlib, http.server, json, mimetypes, multiprocessing, platform, re, subprocess, threading, traceback
import urllib.parse

APP_NAME = "FB Clip Maker"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL = "large-v3-turbo"
MODEL_BYTES = 1.62e9          # model.bin of large-v3-turbo, for the first-run download progress
def ram_gb():
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("rest", ctypes.c_ulonglong * 6)]
            m = MS(); m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2 ** 30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:
        return 8.0


# parallel render chunks: limited by cores and by RAM (6 workers ran out of memory on a 16 GB laptop)
WORKERS = max(2, min(6, (os.cpu_count() or 4) // 2, int(ram_gb() // 4)))
VIDEO_TYPES = (".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm")


def data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, "FBClipMaker")
    os.makedirs(d, exist_ok=True)
    return d


def say(kind, *parts):
    print("@@" + " ".join([kind, *map(str, parts)]), flush=True)


# ----------------------------------------------------------------------------------------------
# engine (child process)
# ----------------------------------------------------------------------------------------------
def ensure_model(name):
    """download the Whisper model with progress lines (faster-whisper would otherwise do it silently)"""
    from faster_whisper.utils import _MODELS, download_model
    from huggingface_hub import constants
    try:
        return download_model(name, local_files_only=True)
    except Exception:
        pass
    say("stage", "model")
    cache = os.path.join(constants.HF_HUB_CACHE, "models--" + _MODELS.get(name, name).replace("/", "--"))
    stop = threading.Event()

    def poll():
        while not stop.wait(1):
            size = 0
            for r, _, fs in os.walk(cache):
                for f in fs:
                    p = os.path.join(r, f)
                    if not os.path.islink(p):
                        try:
                            size += os.path.getsize(p)
                        except OSError:
                            pass
            say("progress", "model", f"{min(0.99, size / MODEL_BYTES):.3f}")

    threading.Thread(target=poll, daemon=True).start()
    try:
        return download_model(name)
    finally:
        stop.set()


def engine(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    for a in ("--out", "--work", "--edits"):
        ap.add_argument(a, required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args(argv)
    os.environ["REFRAME_PROGRESS"] = "1"
    keyfile = os.path.join(data_dir(), "openrouter_key.txt")      # B-roll picker key (or OPENROUTER_API_KEY)
    if not os.environ.get("OPENROUTER_API_KEY") and os.path.exists(keyfile):
        os.environ["OPENROUTER_API_KEY"] = open(keyfile).read().strip()
    os.environ.setdefault("REFRAME_MODELS", os.path.join(data_dir(), "models"))   # B-roll face check (downloaded once)
    try:
        import gpu_pack
        if gpu_pack.wanted():
            cuda = os.path.join(data_dir(), "cuda")
            if not gpu_pack.ready(cuda):
                say("stage", "gpupack")
                try:
                    gpu_pack.ensure(cuda, lambda f: say("progress", "gpupack", f"{f:.3f}"))
                except Exception as e:
                    print("GPU pack download failed, transcribing on the CPU instead:", e, flush=True)
            if gpu_pack.ready(cuda):
                os.environ["REFRAME_CUDA_DIR"] = cuda
        try:
            ensure_model(args.model)
        except Exception as e:
            raise RuntimeError(f"Couldn't download the speech model. Check the internet connection. ({e})")
        import reframe, verify
        say("stage", "work")
        reframe.run(args.video, args.out, args.workers, args.model, args.work, args.edits, auto_end=True)
        say("stage", "verify")
        res_path = os.path.join(args.work, "result.json")
        res = json.load(open(res_path))
        edits = json.load(open(args.edits))
        res["problems"] = verify.check(args.out, os.path.join(args.work, "analysis.json"), edits)[3]
        json.dump(res, open(res_path, "w"), indent=2)
        say("done", json.dumps(res))
    except Exception as e:
        traceback.print_exc()
        say("error", str(e).splitlines()[0] if str(e) else type(e).__name__)
        sys.exit(1)


def selftest(report):
    """used by the build: proves the frozen app has its data files (mediapipe models, VAD, fonts, ffmpeg+libass)"""
    lines, ok = [], True
    try:
        import numpy, cv2, webview, faster_whisper, ctranslate2, reframe, verify, gpu_pack  # noqa: F401
        assert os.path.exists(os.path.join(APP_DIR, "ui", "index.html")), "UI files missing"
        from faster_whisper.utils import get_assets_path
        reframe._face_init()
        reframe._faces((0, bytes(reframe.AW * reframe.AH * 3)))
        lines.append("mediapipe face models: ok")
        assert any(f.endswith(".onnx") for f in os.listdir(get_assets_path())), "faster-whisper VAD model missing"
        lines.append("faster-whisper VAD: ok")
        assert os.path.exists(os.path.join(reframe.HERE, "fonts", "Montserrat-Black.ttf")), "caption font missing"
        ff = reframe.ffmpeg_exe()
        filters = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        assert re.search(r"\bass\b", filters), "ffmpeg has no libass (captions)"
        lines.append(f"ffmpeg {ff}: ok (libass)")
        lines.append(f"encoder: {reframe.pick_encoder()[2]}")
        lines.append(f"ctranslate2 {ctranslate2.__version__}, CUDA devices: {ctranslate2.get_cuda_device_count()}")
        import broll, yt_dlp
        assert hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF"), "OpenCV has no face recognizer"
        with yt_dlp.YoutubeDL({"quiet": True}) as y:
            assert y.get_info_extractor("YoutubeTab"), "yt-dlp extractors missing"
        lines.append(f"b-roll: yt-dlp {yt_dlp.version.__version__}, OpenRouter key "
                     f"{'set' if broll.api_key() else 'not set (clips render without B-roll)'}")
    except Exception:
        ok = False
        lines.append(traceback.format_exc())
    lines.append("SELFTEST " + ("OK" if ok else "FAILED"))
    open(report, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(0 if ok else 1)


# ----------------------------------------------------------------------------------------------
# window: the HTML UI in ui/ inside a native webview. Python side: job queue, review data, and a local
# server for the UI files and the finished clips (so the window can play them).
# ----------------------------------------------------------------------------------------------
def norm(w):
    return re.sub(r"[^\w']", "", w.lower())


class Job:
    def __init__(self, src):
        self.src = os.path.abspath(src)
        stem = os.path.splitext(os.path.basename(self.src))[0]
        self.name = stem
        self.key = re.sub(r"[^\w.-]+", "_", stem)[:40] + "_" + hashlib.sha1(self.src.encode()).hexdigest()[:8]
        self.work = os.path.join(data_dir(), "work", self.key)
        self.edits_path = os.path.join(self.work, "edits.json")
        self.out = os.path.join(os.path.dirname(self.src), f"{stem}_9x16.mp4")
        self.status, self.pct, self.error, self.log, self.result = "Waiting", 0, None, [], None
        self.stage, self.parts, self.stage_text = "", {}, ""
        os.makedirs(self.work, exist_ok=True)

    def edits(self):
        if os.path.exists(self.edits_path):
            return json.load(open(self.edits_path))
        return {"end": None, "words": [], "wave": [], "face": []}

    def save_edits(self, e):
        json.dump(e, open(self.edits_path, "w"), indent=2)

    def transcript(self):
        p = os.path.join(self.work, "transcript.json")
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def engine_cmd():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--pipeline"]
    return [sys.executable, os.path.abspath(__file__), "--pipeline"]


def open_path(p, reveal=False):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", p]) if reveal else os.startfile(p)
    else:
        subprocess.Popen(["open", "-R", p] if reveal else ["open", p])


def engine_label():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            name = r.stdout.strip().splitlines()[0].replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
            return {"label": name if name.endswith("GPU") else name + " GPU", "gpu": True}
    except OSError:
        pass
    if sys.platform == "darwin":
        return {"label": "Apple Silicon, CPU" if platform.machine() == "arm64" else "Mac CPU", "gpu": False}
    return {"label": "CPU only", "gpu": False}


class Core:
    """the render queue: one child process at a time, progress parsed from its @@ lines"""

    def __init__(self):
        self.jobs, self.proc, self.cur = [], None, None
        self.lock = threading.RLock()

    def find(self, key):
        return next((j for j in self.jobs if j.key == key), None)

    def add(self, paths):
        with self.lock:
            for p in paths:
                if not p or os.path.isdir(p) or not p.lower().endswith(VIDEO_TYPES) or p.lower().endswith("_9x16.mp4"):
                    continue
                job = next((j for j in self.jobs if j.src == os.path.abspath(p)), None)
                if job is None:
                    job = Job(p)
                    self.jobs.append(job)
                if job is not self.cur:
                    job.status, job.error, job.result = "Waiting", None, None
            self._next()

    def remove(self, key):
        with self.lock:
            job = self.find(key)
            if job and job is not self.cur:
                self.jobs.remove(job)

    def _next(self):
        if self.proc:
            return
        job = next((j for j in self.jobs if j.status == "Waiting"), None)
        if not job:
            return
        self.cur = job
        job.status, job.pct, job.error, job.log, job.stage, job.parts = "Working", 0, None, [], "", {}
        self._progress(job)
        cmd = engine_cmd() + [job.src, "--out", job.out, "--work", job.work, "--edits", job.edits_path,
                              "--model", MODEL, "--workers", str(WORKERS)]
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore",
                   HF_HUB_DISABLE_SYMLINKS_WARNING="1", HF_HUB_DISABLE_PROGRESS_BARS="1")
        kw = {"start_new_session": True} if os.name != "nt" else {}
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1, env=env, **kw)
        threading.Thread(target=self._reader, args=(self.proc, job), daemon=True).start()

    def _reader(self, proc, job):
        for line in proc.stdout:
            with self.lock:
                self._line(job, line.rstrip("\n"))
        code = proc.wait()
        with self.lock:
            self._finished(job, code)

    def _line(self, job, line):
        job.log.append(line)
        if not line.startswith("@@"):
            if "transcription done" in line:
                job.parts["transcribe"] = 1.0
            elif "analysis done" in line:
                job.parts["analyze"] = 1.0
            return
        kind, _, rest = line[2:].partition(" ")
        if kind == "progress":
            stage, frac = rest.split()
            job.parts[stage] = float(frac)
            if stage == "render":
                job.parts["transcribe"] = job.parts["analyze"] = 1.0
        elif kind == "stage":
            job.stage = rest
        elif kind == "done":
            job.result = json.loads(rest)
        elif kind == "error":
            job.error = rest
        self._progress(job)

    def _progress(self, job):
        p, st = job.parts, job.stage
        if st in ("gpupack", "model"):
            frac = p.get(st, 0)
            txt = "Downloading speech model" if st == "model" else "Downloading GPU pack"
        elif st == "verify":
            frac, txt = 0.98, "Checking for glitches"
        elif st == "work":
            tr, an, rd = p.get("transcribe", 0), p.get("analyze", 0), p.get("render", 0)
            frac = 0.6 * (tr + an) / 2 + 0.37 * rd
            txt = "Rendering" if "render" in p else "Finding B-roll" if "broll" in p else \
                "Rendering" if tr >= 1 and an >= 1 else "Transcribing"
        else:
            frac, txt = 0, "Starting"
        job.pct, job.stage_text = int(frac * 100), txt

    def _finished(self, job, code):
        self.proc, self.cur = None, None
        try:
            open(os.path.join(job.work, "log.txt"), "w", encoding="utf-8").write("\n".join(job.log) + "\n")
        except OSError:
            pass
        if job.status != "Cancelled":
            if code == 0 and job.result and job.result.get("problems"):
                job.status = "Failed"      # never hand over a clip with flash / garbage frames
                job.error = (f"The glitch check found {len(job.result['problems'])} bad frames, so this clip "
                             "shouldn't be posted. The Log tab has the details.")
            elif code == 0 and job.result:
                job.status = "Done"
            else:
                job.status = "Failed"
                job.error = job.error or f"The render stopped unexpectedly (code {code}). The Log tab has the details."
        self._next()

    def cancel(self):
        with self.lock:
            if self.proc:
                self.cur.status = "Cancelled"
                self.kill()

    def kill(self):
        if not self.proc:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
        else:
            import signal
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass


class Api:
    """methods the page calls through window.pywebview.api (names starting with _ stay private)"""

    def __init__(self, core, port):
        self._core, self._port, self._window, self._engine = core, port, None, engine_label()

    def state(self):
        with self._core.lock:
            return {"engine": self._engine, "jobs": [self._job(j) for j in self._core.jobs]}

    def _job(self, j):
        video = None
        if j.status == "Done" and os.path.exists(j.out):
            video = f"http://127.0.0.1:{self._port}/video/{j.key}?v={int(os.path.getmtime(j.out))}"
        return {"key": j.key, "name": j.name, "status": j.status, "pct": j.pct, "stage": j.stage, "parts": j.parts,
                "stageText": j.stage_text, "error": j.error, "result": j.result, "video": video}

    def add_dialog(self):
        import webview
        files = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("Video files (" + ";".join("*" + e for e in VIDEO_TYPES) + ")", "All files (*.*)"))
        if files:
            self._core.add(list(files))

    def _lines(self, sn, edits):
        allw = [(si, wi, w["s"]) for si, s in enumerate(sn) for wi, w in enumerate(s[3])]
        fm = {}
        for f in edits.get("words", []):
            si, wi, t = min(allw, key=lambda x: abs(x[2] - f["at"]))
            if abs(t - f["at"]) < 0.6:
                fm[(si, wi)] = f["to"]
        out = []
        for si, s in enumerate(sn):
            ws = [fm.get((si, wi), w["w"].strip()) for wi, w in enumerate(s[3])]
            out.append({"s": s[0], "e": s[1], "text": " ".join(x for x in ws if x),
                        "fixed": any(k[0] == si for k in fm)})
        return out, fm

    def review(self, key, edits=None):
        import reframe
        job = self._core.find(key)
        tr = job.transcript() if job else None
        if not tr or not job.result:
            return None
        saved = job.edits()
        sn = reframe.sentences(tr)
        return {"result": job.result, "edits": saved, "sentences": self._lines(sn, edits or saved)[0]}

    def fix_caption(self, key, edits, si, text):
        """turn an edited sentence into per-word fixes ({"at": word start, "to": new text}, "" deletes a word)"""
        import reframe
        job = self._core.find(key)
        sn = reframe.sentences(job.transcript())
        _, fm = self._lines(sn, edits)
        words = sn[si][3]
        cur = [fm.get((si, wi), w["w"].strip()) for wi, w in enumerate(words)]
        toks, target = text.split(), list(cur)
        sm = difflib.SequenceMatcher(a=[norm(x) for x in cur], b=[norm(x) for x in toks], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            rep = toks[j1:j2]
            if tag == "equal":
                target[i1:i2] = rep
            elif tag in ("replace", "delete"):
                for k in range(i2 - i1):
                    target[i1 + k] = rep[k] if k < len(rep) else ""
                if len(rep) > i2 - i1:
                    target[i2 - 1] = " ".join([target[i2 - 1]] + rep[i2 - i1:])
            elif tag == "insert" and words:
                k = max(0, i1 - 1)
                target[k] = (target[k] + " " + " ".join(rep)) if i1 else (" ".join(rep) + " " + target[0])
        mine = {round(w["s"], 2) for w in words}
        fixes = [f for f in edits.get("words", []) if round(f["at"], 2) not in mine]
        fixes += [{"at": round(w["s"], 2), "to": target[wi]} for wi, w in enumerate(words)
                  if target[wi] != w["w"].strip()]
        edits = dict(edits, words=fixes)
        return {"edits": edits, "sentences": self._lines(sn, edits)[0]}

    def apply(self, key, edits):
        job = self._core.find(key)
        if job:
            new = {"end": edits.get("end"), "keep_outro": bool(edits.get("keep_outro")),
                   "words": [{"at": f["at"], "to": f["to"]} for f in edits.get("words", [])],
                   "wave": edits.get("wave", []), "face": edits.get("face", [])}
            saved = job.edits()
            if "broll" in saved:          # keep the B-roll picked last time; the UI may send the "at"s to keep
                keep = edits.get("broll")
                new["broll"] = saved["broll"] if keep is None else \
                    [b for b in saved["broll"] if any(abs(b["at"] - float(k["at"])) < 0.01 for k in keep)]
            job.save_edits(new)
            self._core.add([job.src])

    def retry(self, key):
        job = self._core.find(key)
        if job:
            self._core.add([job.src])

    def remove(self, key):
        self._core.remove(key)

    def cancel(self):
        self._core.cancel()

    def open_file(self, key):
        job = self._core.find(key)
        if job and os.path.exists(job.out):
            open_path(job.out)

    def reveal(self, key):
        job = self._core.find(key)
        if job and os.path.exists(job.out):
            open_path(job.out, reveal=True)

    def log(self, key):
        job = self._core.find(key)
        if not job:
            return ""
        lines = [l for l in job.log if not l.startswith("@@progress") and "inference_feedback" not in l
                 and not l.startswith(("W0000", "I0000", "INFO:"))]
        return "\n".join(lines[-400:]) or "Nothing logged yet."


def make_handler(core):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path.startswith("/video/"):
                job = core.find(path[7:])
                f, ctype = (job.out if job else None), "video/mp4"
            elif path.startswith("/fonts/"):
                f, ctype = os.path.join(APP_DIR, "fonts", os.path.basename(path)), "font/ttf"
            else:
                name = os.path.basename(path) or "index.html"
                f = os.path.join(APP_DIR, "ui", name)
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if not f or not os.path.isfile(f):
                return self.send_error(404)
            size = os.path.getsize(f)
            start, end, code = 0, size - 1, 200
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(size - 1, int(m.group(2))) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with open(f, "rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0 and (chunk := fh.read(min(left, 1 << 18))):
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (ConnectionError, OSError):
                pass

    return Handler


def dark_titlebar():
    """Windows 11: dark title bar in the app's own background colour"""
    if sys.platform != "win32":
        return
    try:
        import ctypes, ctypes.wintypes as wt
        u, mine = ctypes.windll.user32, []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, _):      # this process's own window (another copy of the app may be open with the same title)
            pid, buf = wt.DWORD(), ctypes.create_unicode_buffer(64)
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            u.GetWindowTextW(h, buf, 64)
            if pid.value == os.getpid() and buf.value == APP_NAME:
                mine.append(h)
            return True
        u.EnumWindows(cb, 0)
        hwnd = mine[0] if mine else u.FindWindowW(None, APP_NAME)
        for attr, val in ((20, 1), (35, 0x000E0706), (36, 0x00F8EEEC)):   # dark mode, caption, caption text
            v = ctypes.c_int(val)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
    except Exception:
        pass


def gui():
    import webview
    from webview.dom import DOMEventHandler
    core = Core()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    api = Api(core, port)
    win = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/", js_api=api, width=1380, height=880,
                                min_size=(1120, 720), background_color="#06070E")
    api._window = win

    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        core.add([f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")])

    def on_loaded():
        win.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        win.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)

    def on_closing():
        if core.proc and not win.create_confirmation_dialog(APP_NAME, "A video is still rendering. Stop it and quit?"):
            return False
        core.kill()

    win.events.loaded += on_loaded
    win.events.shown += dark_titlebar
    win.events.closing += on_closing
    webview.start(gui="edgechromium" if sys.platform == "win32" else None)
    core.kill()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--pipeline":
        engine(sys.argv[2:])
    elif len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
    else:
        gui()
