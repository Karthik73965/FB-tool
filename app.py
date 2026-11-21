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

import difflib, hashlib, json, multiprocessing, queue, re, subprocess, threading, traceback

APP_NAME = "FB Clip Maker"
MODEL = "large-v3-turbo"
MODEL_BYTES = 1.62e9          # model.bin of large-v3-turbo, for the first-run download progress
WORKERS = max(2, min(6, (os.cpu_count() or 4) // 2))
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
        import numpy, cv2, tkinter, faster_whisper, ctranslate2, reframe, verify, gpu_pack  # noqa: F401
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
    except Exception:
        ok = False
        lines.append(traceback.format_exc())
    lines.append("SELFTEST " + ("OK" if ok else "FAILED"))
    open(report, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(0 if ok else 1)


# ----------------------------------------------------------------------------------------------
# window
# ----------------------------------------------------------------------------------------------
BG, PANEL, LINE, FG, MUTED = "#121417", "#1b1e24", "#2a2f38", "#e9ecf1", "#8b93a1"
ACCENT, ACCENT_DIM, WARN, BAD, GOOD = "#ffe600", "#c9b600", "#ffb347", "#ff6b6b", "#5ee38f"


def fmt(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


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
        self.status, self.pct, self.error, self.log = "Waiting", 0, None, []
        self.result = None
        os.makedirs(self.work, exist_ok=True)
        if os.path.exists(os.path.join(self.work, "result.json")) and os.path.exists(self.out):
            self.result = json.load(open(os.path.join(self.work, "result.json")))

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


def gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        DND_FILES, root = None, tk.Tk()
    App(root, tk, ttk, filedialog, messagebox, DND_FILES)
    root.mainloop()


class App:
    def __init__(self, root, tk, ttk, filedialog, messagebox, dnd):
        self.root, self.tk, self.ttk, self.fd, self.mb = root, tk, ttk, filedialog, messagebox
        self.jobs, self.proc, self.cur, self.sel = [], None, None, None
        self.q = queue.Queue()
        self.stage, self.parts = "", {}
        root.title(APP_NAME)
        root.geometry("1120x780")
        root.minsize(920, 640)
        root.configure(bg=BG)
        self._style()
        self._layout()
        if dnd:
            root.drop_target_register(dnd)
            root.dnd_bind("<<Drop>>", lambda e: self.add(root.tk.splitlist(e.data)))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(100, self._poll)
        self.show(None)

    # -- look ------------------------------------------------------------------------------
    def _style(self):
        tk, ttk = self.tk, self.ttk
        from tkinter import font
        base = font.nametofont("TkDefaultFont")
        base.configure(size=10)
        self.f_title = base.copy(); self.f_title.configure(size=17, weight="bold")
        self.f_h = base.copy(); self.f_h.configure(size=12, weight="bold")
        self.f_small = base.copy(); self.f_small.configure(size=9)
        self.f_list = base.copy(); self.f_list.configure(size=10)
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=LINE, lightcolor=PANEL,
                    darkcolor=PANEL, troughcolor=PANEL, focuscolor=ACCENT)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Panel.TLabel", background=PANEL, foreground=FG)
        s.configure("Muted.TLabel", background=BG, foreground=MUTED)
        s.configure("PMuted.TLabel", background=PANEL, foreground=MUTED)
        s.configure("TButton", background=PANEL, foreground=FG, padding=(12, 6), borderwidth=1, relief="flat")
        s.map("TButton", background=[("disabled", BG), ("active", LINE)], foreground=[("disabled", "#555b66")])
        s.configure("Accent.TButton", background=ACCENT, foreground="#111", font=self.f_h, padding=(16, 8))
        s.map("Accent.TButton", background=[("disabled", LINE), ("active", ACCENT_DIM)],
              foreground=[("disabled", MUTED)])
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=30, borderwidth=0)
        s.map("Treeview", background=[("selected", LINE)], foreground=[("selected", ACCENT)])
        s.configure("Treeview.Heading", background=BG, foreground=MUTED, relief="flat", borderwidth=0)
        s.map("Treeview.Heading", background=[("active", BG)])
        s.configure("TNotebook", background=PANEL, borderwidth=0, tabmargins=0)
        s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(14, 6), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", ACCENT)])
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL, bordercolor=PANEL,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
        s.configure("Vertical.TScrollbar", background=PANEL, troughcolor=PANEL, arrowcolor=MUTED, bordercolor=PANEL)

    def _layout(self):
        tk, ttk = self.tk, self.ttk
        top = ttk.Frame(self.root, padding=(20, 16, 20, 8))
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, font=self.f_title).pack(side="left")
        ttk.Label(top, text="   talk-show video → vertical 1080×1920 clip with captions", style="Muted.TLabel"
                  ).pack(side="left", pady=(6, 0))
        ttk.Button(top, text="+  Add videos", style="Accent.TButton", command=self.browse).pack(side="right")

        bot = ttk.Frame(self.root, padding=(20, 4, 20, 16))
        bot.pack(side="bottom", fill="x")
        body = ttk.Frame(self.root, padding=(20, 8, 20, 8))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, style="Panel.TFrame", padding=1)
        left.pack(side="left", fill="y")
        self.tree = ttk.Treeview(left, columns=("status",), show="tree headings", selectmode="browse", height=12)
        self.tree.heading("#0", text="  Video", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("#0", width=270, stretch=False)
        self.tree.column("status", width=110, stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.tree.selection() and self.show(
            self._job(self.tree.selection()[0])))
        self.right = ttk.Frame(body, style="Panel.TFrame", padding=18)
        self.right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self.status = ttk.Label(bot, text="Ready", style="Muted.TLabel")
        self.status.pack(side="top", anchor="w")
        row = ttk.Frame(bot)
        row.pack(fill="x", pady=(6, 0))
        self.bar = ttk.Progressbar(row, maximum=100)
        self.bar.pack(side="left", fill="x", expand=True)
        self.btn_cancel = ttk.Button(row, text="Cancel", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=(12, 0))
        ttk.Button(row, text="Details", command=self.details).pack(side="left", padx=(8, 0))

    # -- jobs ------------------------------------------------------------------------------
    def browse(self):
        files = self.fd.askopenfilenames(title="Choose videos",
                                         filetypes=[("Videos", " ".join("*" + e for e in VIDEO_TYPES)), ("All", "*")])
        self.add(files)

    def add(self, paths):
        for p in paths:
            if os.path.isdir(p) or not p.lower().endswith(VIDEO_TYPES) or p.lower().endswith("_9x16.mp4"):
                continue
            job = next((j for j in self.jobs if j.src == os.path.abspath(p)), None)
            if job is None:
                job = Job(p)
                self.jobs.append(job)
                self.tree.insert("", "end", iid=job.key, text="  " + job.name, values=("",))
            if job is not self.cur:
                job.status, job.error, job.result = "Waiting", None, None
            self._row(job)
            if self.sel is None:
                self.tree.selection_set(job.key); self.tree.focus(job.key)
        self._next()

    def _job(self, key):
        return next((j for j in self.jobs if j.key == key), None)

    def _row(self, job):
        txt = job.status + (f"  {job.pct}%" if job.status == "Working" else "")
        if job.status == "Done" and job.result and job.result.get("problems"):
            txt = "Done (check)"
        self.tree.item(job.key, values=(txt,))

    def _next(self):
        if self.proc:
            return
        job = next((j for j in self.jobs if j.status == "Waiting"), None)
        if not job:
            done = sum(j.status == "Done" for j in self.jobs)
            self.status.configure(text=f"All done: {done} clip{'s' * (done != 1)} ready" if done else "Ready")
            self.btn_cancel.configure(state="disabled")
            return
        self.cur, self.stage, self.parts = job, "", {}
        job.status, job.pct, job.error, job.log = "Working", 0, None, []
        self._row(job)
        cmd = engine_cmd() + [job.src, "--out", job.out, "--work", job.work, "--edits", job.edits_path,
                              "--model", MODEL, "--workers", str(WORKERS)]
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore",
                   HF_HUB_DISABLE_SYMLINKS_WARNING="1", HF_HUB_DISABLE_PROGRESS_BARS="1")
        kw = {"start_new_session": True} if os.name != "nt" else {}
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1, env=env, **kw)
        threading.Thread(target=self._reader, args=(self.proc, job), daemon=True).start()
        self.btn_cancel.configure(state="normal")
        self._status()
        if self.sel is job:
            self.show(job)

    def _reader(self, proc, job):
        for line in proc.stdout:
            self.q.put((job, line.rstrip("\n")))
        self.q.put((job, None))

    def _poll(self):
        try:
            while True:
                job, line = self.q.get_nowait()
                if line is None:
                    self._finished(job)
                else:
                    self._line(job, line)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _line(self, job, line):
        job.log.append(line)
        if not line.startswith("@@"):
            if "transcription done" in line:
                self.parts["transcribe"] = 1.0
            elif "analysis done" in line:
                self.parts["analyze"] = 1.0
            return
        kind, _, rest = line[2:].partition(" ")
        if kind == "progress":
            stage, frac = rest.split()
            self.parts[stage] = float(frac)
            if stage == "render":
                self.parts["transcribe"] = self.parts["analyze"] = 1.0
        elif kind == "stage":
            self.stage = rest
        elif kind == "done":
            job.result = json.loads(rest)
        elif kind == "error":
            job.error = rest
        self._status()

    def _status(self):
        p, st = self.parts, self.stage
        if st == "gpupack":
            frac, txt = p.get("gpupack", 0), "Downloading the NVIDIA speed-up pack (first time only, ~1 GB)"
        elif st == "model":
            frac, txt = p.get("model", 0), "Downloading the speech model (first time only, ~1.6 GB)"
        elif st == "verify":
            frac, txt = 0.98, "Checking the clip for glitches"
        elif st == "work":
            tr, an, rd = p.get("transcribe", 0), p.get("analyze", 0), p.get("render", 0)
            frac = 0.6 * (tr + an) / 2 + 0.37 * rd
            txt = (f"Rendering  {rd:.0%}" if "render" in p or (tr >= 1 and an >= 1)
                   else f"Transcribing  {tr:.0%}   ·   Finding faces  {an:.0%}")
        else:
            frac, txt = 0, "Starting"
        if self.cur:
            self.cur.pct = int(frac * 100)
            self._row(self.cur)
            self.status.configure(text=f"{self.cur.name}:  {txt}")
        self.bar.configure(value=frac * 100)

    def _finished(self, job):
        code = self.proc.wait() if self.proc else -1
        self.proc, self.cur = None, None
        try:
            open(os.path.join(job.work, "log.txt"), "w", encoding="utf-8").write("\n".join(job.log) + "\n")
        except OSError:
            pass
        if job.status == "Cancelled":
            pass
        elif code == 0 and job.result:
            job.status = "Done"
        else:
            job.status = "Failed"
            job.error = job.error or f"The render stopped unexpectedly (code {code}). Click Details for the log."
        self.bar.configure(value=100 if job.status == "Done" else 0)
        self._row(job)
        if self.sel is job or self.sel is None:
            self.show(job)
        self._next()

    def cancel(self):
        if not self.proc:
            return
        self.cur.status = "Cancelled"
        self._kill()

    def _kill(self):
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

    def quit(self):
        if self.proc and not self.mb.askyesno(APP_NAME, "A video is still rendering. Stop it and quit?"):
            return
        self._kill()
        self.root.destroy()

    def details(self):
        job = self.cur or self.sel
        tk = self.tk
        w = tk.Toplevel(self.root, bg=BG)
        w.title(f"Details: {job.name}" if job else "Details")
        w.geometry("900x500")
        t = tk.Text(w, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", wrap="word", font=self.f_small)
        t.pack(fill="both", expand=True, padx=10, pady=10)
        t.insert("end", "\n".join(job.log) if job else "Nothing has run yet.")
        t.see("end")

    # -- review panel ----------------------------------------------------------------------
    def _clear(self):
        for w in self.right.winfo_children():
            w.destroy()

    def _lbl(self, text, style="Panel.TLabel", **kw):
        lab = self.ttk.Label(self.right, text=text, style=style, wraplength=680, justify="left", **kw)
        lab.pack(anchor="w")
        return lab

    def show(self, job):
        self.sel = job
        self._clear()
        ttk = self.ttk
        if job is None:
            self._lbl("Drop videos here", font=self.f_title).pack(anchor="w", pady=(120, 6))
            self._lbl("…or click  + Add videos.  Each one becomes a vertical clip saved next to the original, "
                      "with the speaker centered, word-by-word captions and the ad / outro at the end cut off. "
                      "You can add several; they render one after another.", "PMuted.TLabel")
            return
        self._lbl(job.name, font=self.f_h)
        if job.status in ("Waiting", "Working"):
            self._lbl("Waiting for its turn…" if job.status == "Waiting" else
                      "Working on it. The first video also downloads the speech model, so it takes longer.",
                      "PMuted.TLabel").pack(anchor="w", pady=(6, 0))
            return
        if job.status in ("Failed", "Cancelled"):
            self._lbl(job.error or "Cancelled.", "PMuted.TLabel").pack(anchor="w", pady=(6, 10))
            ttk.Button(self.right, text="Try again", command=lambda: self.add([job.src])).pack(anchor="w")
            return
        r = job.result
        row = ttk.Frame(self.right, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 4))
        ttk.Button(row, text="Show in folder", command=lambda: open_path(r["out"], True)).pack(side="right")
        ttk.Button(row, text="▶  Play", command=lambda: open_path(r["out"])).pack(side="right", padx=(0, 8))
        ttk.Label(row, text=f"Saved: {os.path.basename(r['out'])}\nin {os.path.dirname(r['out'])}",
                  style="PMuted.TLabel").pack(side="left", fill="x")
        why = {"ad": f"cut before the ad / outro that starts at {fmt(r['ad_start'] or 0)}",
               "logo": "end logo screen trimmed", "manual": "ending set by you",
               "full": "nothing to trim at the end"}[r["end_reason"]]
        self._lbl(f"Length {fmt(r['end'])} of {fmt(r['duration'])}  ·  {why}  ·  "
                  f"{'GPU' if r.get('device') == 'cuda' else 'CPU'} transcription, {r.get('encoder')} encoding",
                  "PMuted.TLabel")
        if r.get("problems"):
            self._lbl(f"⚠  Possible glitch frames at {', '.join(fmt(t) for t in r['problems'][:6])}. "
                      "Play those moments before posting.", foreground=WARN).pack(anchor="w", pady=(4, 0))
        tr = job.transcript()
        if not tr:
            return
        self.edits = job.edits()
        self.pending = tk_var = self.tk.StringVar(value="")
        foot = ttk.Frame(self.right, style="Panel.TFrame")
        foot.pack(side="bottom", fill="x")
        nb = ttk.Notebook(self.right)
        nb.pack(fill="both", expand=True, pady=(12, 8))
        self._ending_tab(nb, job, tr, r)
        self._captions_tab(nb, job, tr)
        self.btn_apply = ttk.Button(foot, text="Re-render with changes", style="Accent.TButton", state="disabled",
                                    command=lambda: self.apply(job))
        self.btn_apply.pack(side="right")
        ttk.Label(foot, textvariable=tk_var, style="PMuted.TLabel").pack(side="right", padx=12)

    def _listbox(self, parent):
        tk, ttk = self.tk, self.ttk
        fr = ttk.Frame(parent, style="Panel.TFrame")
        lb = tk.Listbox(fr, bg=BG, fg=FG, selectbackground=LINE, selectforeground=ACCENT, highlightthickness=0,
                        borderwidth=0, activestyle="none", font=self.f_list, height=8)
        sb = ttk.Scrollbar(fr, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        return fr, lb

    def _ending_tab(self, nb, job, tr, r):
        ttk = self.ttk
        tab = ttk.Frame(nb, style="Panel.TFrame", padding=(0, 10, 0, 0))
        nb.add(tab, text="Ending")
        ttk.Label(tab, text="Last two minutes of the video. Lines below the ✂ line are cut off. To change it, "
                            "select the last line that should stay and click End after this line.",
                  style="PMuted.TLabel", wraplength=600, justify="left").pack(anchor="w", pady=(0, 8))
        btns = ttk.Frame(tab, style="Panel.TFrame")
        btns.pack(side="bottom", fill="x", pady=(8, 0))
        fr, lb = self._listbox(tab)
        fr.pack(fill="both", expand=True)
        import reframe
        sn = reframe.sentences(tr)
        tail = [k for k, s in enumerate(sn) if s[1] > r["duration"] - 120]
        rows, cut_drawn = [], False
        for k in tail:
            if not cut_drawn and sn[k][0] >= r["end"] - 0.01:
                lb.insert("end", f"   ✂ ─────────  clip ends at {fmt(r['end'])}  ─────────")
                lb.itemconfig("end", fg=ACCENT)
                rows.append(None)
                cut_drawn = True
            lb.insert("end", f"   {fmt(sn[k][0]):>6}    {sn[k][2]}")
            if cut_drawn:
                lb.itemconfig("end", fg=MUTED)
            rows.append(k)
        if not cut_drawn:
            lb.insert("end", f"   ✂ ─────────  clip ends at {fmt(r['end'])}  ─────────")
            lb.itemconfig("end", fg=ACCENT)
            rows.append(None)
        lb.update_idletasks()
        lb.yview(max(0, rows.index(None) - 4))      # cut line near the top, with a few kept lines above it

        def end_after():
            sel = lb.curselection()
            k = rows[sel[0]] if sel else None
            if k is None:
                return
            last = sn[k][1]
            nxt = sn[k + 1][0] if k + 1 < len(sn) else None
            end = min(last + 0.3, nxt - 0.05) if nxt else last + 0.3
            self.edits.update(end=round(end, 2), keep_outro=False)
            self._dirty(f"Ending → {fmt(end)}")

        ttk.Button(btns, text="End after this line", command=end_after).pack(side="left")
        ttk.Button(btns, text="Keep everything", command=lambda: (self.edits.update(end=None, keep_outro=True),
                                                                  self._dirty("Ending → keep everything"))
                   ).pack(side="left", padx=8)
        ttk.Button(btns, text="Auto-detect", command=lambda: (self.edits.update(end=None, keep_outro=False),
                                                              self._dirty("Ending → auto-detect"))
                   ).pack(side="left")

    def _captions_tab(self, nb, job, tr):
        ttk = self.ttk
        tab = ttk.Frame(nb, style="Panel.TFrame", padding=(0, 10, 0, 0))
        nb.add(tab, text="Captions")
        ttk.Label(tab, text="Double-click a line to fix a misheard word (for example a name). "
                            "Swear words are masked on screen automatically.",
                  style="PMuted.TLabel", wraplength=600, justify="left").pack(anchor="w", pady=(0, 8))
        fr, lb = self._listbox(tab)
        fr.pack(fill="both", expand=True)
        import reframe
        segs = [{"start": s[0], "words": s[3]} for s in reframe.sentences(tr)]
        allw = [(si, wi, w["s"]) for si, s in enumerate(segs) for wi, w in enumerate(s["words"])]

        def fixmap():
            m = {}
            for f in self.edits.get("words", []):
                si, wi, t = min(allw, key=lambda x: abs(x[2] - f["at"]))
                if abs(t - f["at"]) < 0.6:
                    m[(si, wi)] = f["to"]
                    f["_k"] = (si, wi)
            return m

        def line(si, fm):
            ws = [fm.get((si, wi), w["w"].strip()) for wi, w in enumerate(segs[si]["words"])]
            return " ".join(x for x in ws if x)

        def fill():
            fm = fixmap()
            lb.delete(0, "end")
            for si, s in enumerate(segs):
                lb.insert("end", f"   {fmt(s['start']):>6}    {line(si, fm)}")
                if any(k[0] == si for k in fm):
                    lb.itemconfig("end", fg=ACCENT)

        def edit(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            si = sel[0]
            fm = fixmap()
            new = self._ask(f"Fix caption at {fmt(segs[si]['start'])}", line(si, fm))
            if new is None:
                return
            words = segs[si]["words"]
            cur = [fm.get((si, wi), w["w"].strip()) for wi, w in enumerate(words)]
            toks, target = new.split(), list(cur)
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
            fixes = [f for f in self.edits.get("words", []) if f.get("_k", (None,))[0] != si]
            fixes += [{"at": round(w["s"], 2), "to": target[wi]} for wi, w in enumerate(words)
                      if target[wi] != w["w"].strip()]
            self.edits["words"] = fixes
            n = len(fixes)
            self._dirty(f"{n} caption fix{'es' * (n != 1)}")
            fill()
            lb.see(si)

        fill()
        lb.bind("<Double-Button-1>", edit)
        lb.bind("<Return>", edit)

    def _ask(self, title, value):
        tk, ttk = self.tk, self.ttk
        w = tk.Toplevel(self.root, bg=PANEL)
        w.title(title)
        w.transient(self.root)
        w.resizable(False, False)
        var, out = tk.StringVar(value=value), [None]
        ttk.Label(w, text=title, style="Panel.TLabel", font=self.f_h).pack(anchor="w", padx=16, pady=(14, 6))
        e = tk.Entry(w, textvariable=var, width=70, bg=BG, fg=FG, insertbackground=FG, relief="flat",
                     font=self.f_list, highlightthickness=1, highlightcolor=ACCENT, highlightbackground=LINE)
        e.pack(padx=16, ipady=6)
        e.focus_set(); e.icursor("end")
        b = ttk.Frame(w, style="Panel.TFrame")
        b.pack(fill="x", padx=16, pady=12)

        def ok(_e=None):
            out[0] = var.get().strip()
            w.destroy()

        ttk.Button(b, text="Save", style="Accent.TButton", command=ok).pack(side="right")
        ttk.Button(b, text="Cancel", command=w.destroy).pack(side="right", padx=8)
        w.bind("<Return>", ok)
        w.bind("<Escape>", lambda _e: w.destroy())
        w.grab_set()
        self.root.wait_window(w)
        return out[0]

    def _dirty(self, msg):
        self.pending.set(msg)
        self.btn_apply.configure(state="normal")

    def apply(self, job):
        e = {k: v for k, v in self.edits.items()}
        e["words"] = [{"at": f["at"], "to": f["to"]} for f in e.get("words", [])]
        job.save_edits(e)
        self.add([job.src])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--pipeline":
        engine(sys.argv[2:])
    elif len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
    else:
        gui()
