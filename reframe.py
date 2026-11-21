"""One-command vertical reframe for talk-show clips (Facebook Reels / feed).

    .venv\\Scripts\\python reframe.py ..\\Saucy.mp4

Output: <video>_9x16.mp4 next to the source, 1080x1920. Faces, captions and the waveform stay inside
the 4:5 feed-safe band.
  - keeps the person on screen centred (face tracking, smooth follow, hard cuts on camera cuts)
  - word-by-word captions (Whisper large-v3 on the GPU), profanity masked on screen
  - waveform card layout when nobody is on screen / the frame is a still image / a small graphic inset
Optional <video>.edits.json next to the source (created on first run) for corrections:
  {"end": 280.25, "words": [{"at": 75.92, "to": "Rob"}], "wave": [[41, 60]], "face": [[10, 12]]}
Everything is cached in work/<name>/: re-running after an edit only re-renders the chunks that changed.
"""
import argparse, hashlib, json, os, re, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FPS_NUM, FPS_DEN = 30000, 1001
SW, SH, OW, OH = 1920, 1080, 1080, 1920
AW, AH = 640, 360            # analysis resolution
STEP = 3                     # face analysis every 3rd frame
RENDER_VERSION = "5"         # bump to invalidate cached chunks after changing the look


def ffmpeg_exe():
    if os.environ.get("REFRAME_FFMPEG"):     # cloud worker: NVENC-capable Linux ffmpeg (unset on desktop / CLI)
        return os.environ["REFRAME_FFMPEG"]
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FONTS_ESC = os.path.join(HERE, "fonts").replace("\\", "/").replace(":", "\\:")   # bundled Montserrat Black


def hwdec():
    """NVIDIA hardware decoding when an NVIDIA GPU is present, plain CPU decoding otherwise."""
    global _HW
    if "_HW" not in globals():
        try:
            ok = subprocess.run(["nvidia-smi", "-L"], capture_output=True).returncode == 0
        except OSError:
            ok = False
        _HW = ["-hwaccel", "cuda"] if ok else []
    return list(_HW)


def add_cuda_dll_dirs():
    """faster-whisper (ctranslate2) on Windows needs cuBLAS/cuDNN DLLs from the nvidia-* wheels
    (the desktop app downloads them instead and passes the folder in REFRAME_CUDA_DIR)."""
    if os.name != "nt":
        return
    import site
    dirs = [os.environ.get("REFRAME_CUDA_DIR", "")]
    for sp in getattr(site, "getsitepackages", lambda: [])():
        dirs += [os.path.join(sp, "nvidia", sub, "bin") for sub in ("cublas", "cudnn")]
    for d in dirs:
        if d and os.path.isdir(d):
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def log(t0, msg):
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def progress(stage, frac):
    """machine-readable progress line for the desktop app (only when REFRAME_PROGRESS is set)"""
    if os.environ.get("REFRAME_PROGRESS"):
        print(f"@@progress {stage} {min(1.0, max(0.0, frac)):.3f}", flush=True)


# ----------------------------------------------------------------------------------------------
# 1. transcription (GPU)
# ----------------------------------------------------------------------------------------------
def transcribe(src, out_json, model="large-v3"):
    add_cuda_dll_dirs()
    from faster_whisper import WhisperModel
    FF = ffmpeg_exe()
    wav = out_json.replace(".json", ".wav")
    subprocess.run([FF, "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)

    def run(device, compute_type):
        m = WhisperModel(model, device=device, compute_type=compute_type)
        segs, info = m.transcribe(wav, word_timestamps=True, vad_filter=True, beam_size=5)
        out = []
        for s in segs:
            out.append(s)
            progress("transcribe", s.end / max(info.duration, 1))
        return out, info

    try:
        segs, info = run("cuda", "int8_float16")
        dev = "cuda"
    except Exception as e:  # fall back so the pipeline still finishes
        print("GPU transcription failed, using CPU:", e, flush=True)
        segs, info = run("cpu", "int8")
        dev = "cpu"
    out = {"language": info.language, "device": dev,
           "segments": [{"start": s.start, "end": s.end, "text": s.text,
                         "words": [{"w": w.word, "s": w.start, "e": w.end} for w in (s.words or [])]}
                        for s in segs]}
    json.dump(out, open(out_json, "w", encoding="utf-8"), ensure_ascii=False)
    return dev


# ----------------------------------------------------------------------------------------------
# 2. analysis: ONE sequential decode (frame numbers can never drift), face detection in parallel
# ----------------------------------------------------------------------------------------------
_det = _mesh = None


def _face_init():
    global _det, _mesh
    import mediapipe as mp
    _det = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
    _mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=1, min_detection_confidence=0.3)


def _faces(job):
    import cv2
    i, buf = job
    rgb = cv2.cvtColor(np.frombuffer(buf, np.uint8).reshape(AH, AW, 3), cv2.COLOR_BGR2RGB)
    faces = []
    for d in (_det.process(rgb).detections or []):
        bb = d.location_data.relative_bounding_box
        x, y, w, h = bb.xmin * AW, bb.ymin * AH, bb.width * AW, bb.height * AH
        if w < 12:
            continue
        x0, y0 = int(max(0, x - w * .6)), int(max(0, y - h * .6))
        x1, y1 = int(min(AW, x + w * 1.6)), int(min(AH, y + h * 1.6))
        mouth, crop = None, rgb[y0:y1, x0:x1]
        if crop.size:
            sc = 256 / max(crop.shape[:2])
            r = _mesh.process(cv2.resize(crop, None, fx=sc, fy=sc))
            if r.multi_face_landmarks:
                lm = r.multi_face_landmarks[0].landmark
                mouth = abs(lm[14].y - lm[13].y) / (abs(lm[152].y - lm[10].y) + 1e-6)
        faces.append({"cx": (x + w / 2) / AW, "cy": (y + h / 2) / AH, "w": w / AW, "h": h / AH, "mouth": mouth})
    return {"f": i, "faces": faces}


def frame_count(src):
    FF = ffmpeg_exe()
    r = subprocess.run([FF, "-hide_banner", "-i", src], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return int(dur * FPS_NUM / FPS_DEN), dur


def analyze(src, out_json, n, workers):
    import cv2
    from collections import deque
    FF = ffmpeg_exe()
    dec = subprocess.Popen([FF, "-v", "error"] + hwdec() + ["-i", src, "-vf", f"scale={AW}:{AH}",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, bufsize=0)
    cut, mot, samples, pending = [], [], [], deque()
    prev_h = prev_s = None
    fsz = AW * AH * 3
    i = 0
    with ProcessPoolExecutor(workers, initializer=_face_init) as ex:
        while True:
            buf = bytearray(fsz); mv = memoryview(buf); got = 0
            while got < fsz:
                r = dec.stdout.readinto(mv[got:])
                if not r:
                    break
                got += r
            if got < fsz:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(AH, AW, 3)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256]); cv2.normalize(hist, hist)
            small = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            cut.append(0.0 if prev_h is None else float(1 - cv2.compareHist(prev_h, hist, cv2.HISTCMP_CORREL)))
            mot.append(0.0 if prev_s is None else float(np.mean(np.abs(small - prev_s))))
            prev_h, prev_s = hist, small
            if i % 150 == 0:
                progress("analyze", i / max(n, 1))
            if i % STEP == 0:
                pending.append(ex.submit(_faces, (i, bytes(buf))))
                while len(pending) > workers * 6:          # bounded queue keeps memory flat
                    samples.append(pending.popleft().result())
            i += 1
        samples += [f.result() for f in pending]
    dec.wait()
    a = {"fps": FPS_NUM / FPS_DEN, "n": i, "step": STEP, "cutscore": cut, "motion": mot,
         "samples": sorted(samples, key=lambda s: s["f"])}
    json.dump(a, open(out_json, "w"))


# ----------------------------------------------------------------------------------------------
# 3. plan: per-frame layout (face crop / waveform card)
# ----------------------------------------------------------------------------------------------
def make_plan(a, edits):
    fps, N = a["fps"], a["n"]
    cs, mo = np.array(a["cutscore"]), np.array(a["motion"])
    S = {s["f"]: s["faces"] for s in a["samples"]}
    cuts = [0]
    for i in range(1, N):
        lo, hi = max(1, i - 6), min(N, i + 7)
        neigh = np.median(np.r_[mo[lo:i], mo[i + 1:hi]]) if hi - lo > 1 else 0
        if (cs[i] > 0.2 or (mo[i] > 10 and mo[i] > 4 * neigh + 2)) and i - cuts[-1] > 4:
            cuts.append(i)
    cuts.append(N)
    shots = [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1)]

    def pick(fr):
        tracks = []
        for f in fr:
            for face in S[f]:
                b = min(tracks, key=lambda t: abs(t["cx"] - face["cx"]), default=None)
                if b is not None and abs(b["cx"] - face["cx"]) < 0.1:
                    b["pts"][f] = face; b["cx"] = face["cx"]
                else:
                    tracks.append({"cx": face["cx"], "pts": {f: face}})
        tracks = [t for t in tracks if len(t["pts"]) >= max(2, 0.15 * len(fr))] or tracks
        if len(tracks) <= 1:
            return tracks[0]["pts"] if tracks else {}
        chosen, cur, hold = {}, None, 0
        for idx, f in enumerate(fr):          # several people: follow whoever's mouth is moving
            win = fr[max(0, idx - 8): idx + 9]
            sc = []
            for t in tracks:
                ms = [t["pts"][g]["mouth"] for g in win if g in t["pts"] and t["pts"][g]["mouth"] is not None]
                act = np.mean(np.abs(np.diff(ms))) if len(ms) > 2 else 0
                size = np.mean([t["pts"][g]["h"] for g in win if g in t["pts"]] or [0])
                sc.append(act * 10 + size * 0.5 if any(g in t["pts"] for g in win) else -1)
            b = int(np.argmax(sc))
            if cur is None or (b != cur and sc[b] > 1.3 * sc[cur] and hold > 12):
                cur, hold = b, 0
            hold += 1
            if f in tracks[cur]["pts"]:
                chosen[f] = tracks[cur]["pts"][f]
        return chosen

    plan, prev_mode = [None] * N, None
    for (s, e) in shots:
        fr = [f for f in range((s + STEP - 1) // STEP * STEP, e, STEP) if f in S]
        dur = (e - s) / fps
        withface = np.mean([len(S[f]) > 0 for f in fr]) if fr else 0
        med_mo = np.median(mo[s + 1:e]) if e - s > 2 else 0
        ch = pick(fr)
        med_h = np.median([c["h"] for c in ch.values()]) if ch else 0
        mode = "face"
        if withface < 0.3 or (med_mo < 0.03 and dur > 1) or (med_h < 0.13 and med_mo < 2):
            mode = "wave"
        z = float(np.clip(0.22 / med_h, 1.0, 1.25)) if med_h else 1.0
        if ch:
            kf = sorted(ch)
            xs = np.interp(np.arange(s, e), kf, [ch[f]["cx"] for f in kf])
            ys = np.interp(np.arange(s, e), kf, [ch[f]["cy"] for f in kf])
        else:
            px = plan[s - 1]["x"] if s > 0 else 0.5
            xs, ys = np.full(e - s, px), np.full(e - s, 0.5)
        cam = np.median(xs[:max(1, min(len(xs), int(fps)))])
        sm = np.empty_like(xs)
        for i, t in enumerate(xs):              # dead-zone follow = steady camera operator
            d = t - cam
            if abs(d) > 0.035 / z:
                cam += (d - np.sign(d) * 0.035 / z) * 0.12
            sm[i] = cam
        k = int(fps * 0.25)
        if len(sm) > 2 * k + 1:
            ker = np.exp(-0.5 * (np.arange(-k, k + 1) / (k / 2)) ** 2); ker /= ker.sum()
            sm = np.convolve(np.pad(sm, k, mode="edge"), ker, mode="valid")
        cy = float(np.median(ys)) + 0.13 / z
        for i in range(e - s):
            plan[s + i] = {"m": mode, "x": round(float(sm[i]), 4), "y": round(cy, 4), "z": round(z, 3)}
    # transitions (<0.6 s) take the next shot's layout, so a layout switch lands at the start of a dissolve
    for k in range(len(shots) - 2, -1, -1):
        s, e = shots[k]
        if (e - s) / fps < 0.6:
            for f in range(s, e):
                plan[f] = dict(plan[e])
    # manual overrides
    for key in ("wave", "face"):
        for s_, e_ in edits.get(key, []):
            for f in range(max(0, int(s_ * fps)), min(N, int(e_ * fps))):
                plan[f]["m"] = key
    # no layout flicker: short face runs between wave runs become wave
    runs, i = [], 0
    while i < N:
        j = i
        while j < N and plan[j]["m"] == plan[i]["m"]:
            j += 1
        runs.append((i, j, plan[i]["m"])); i = j
    for k in range(1, len(runs) - 1):
        s, e, m = runs[k]
        if m == "face" and (e - s) / fps < 1.5 and runs[k - 1][2] == "wave" and runs[k + 1][2] == "wave":
            for f in range(s, e):
                plan[f]["m"] = "wave"
    return plan, cuts


# ----------------------------------------------------------------------------------------------
# 3b. ending: ad read / bumper after the conversation (desktop app; with the CLI a person checks report.txt)
# ----------------------------------------------------------------------------------------------
OUTRO_STRONG = re.compile(r"breakfast club|every ?day i wake up|promo code|use code|(\bdot |\.)com\b|www\.|1[- ]?800|"
                          r"brought to you by|sponsored by|link in (the |my )?bio|subscribe|"
                          r"free (case )?(evaluation|consultation)|\bcall\b.{0,40}\b(today|now)\b|download (the|our) app",
                          re.I)
OUTRO_WEAK = re.compile(r"^\W*hold up\b|\bcall\b|\blaw\b|lawyer|attorney|injur|accident|insurance|\bcases?\b|"
                        r"\bclients?\b|compensation|deserve|\bvisit\b|\bgo to\b|head (on )?over to|available (now|on)|"
                        r"\bfollow\b|\bpodcast\b|\bepisode\b|\boffer\b|discount|\bdeal\b|\bsave\b|\bapp\b", re.I)


def sentences(tr):
    """[(first word start, last word end, text, words)] from the word timings; a sentence ends on . ? ! or a 1 s
    gap. (Whisper segments often run from the last line of talk straight into the ad.)"""
    out, cur = [], []
    words = [w for s in tr["segments"] for w in s["words"] if w["w"].strip()]
    for k, w in enumerate(words):
        cur.append(w)
        nxt = words[k + 1] if k + 1 < len(words) else None
        if nxt is None or re.search(r"[.?!]['\"]?$", w["w"].strip()) or nxt["s"] - w["e"] > 1.0:
            out.append((cur[0]["s"], cur[-1]["e"], "".join(x["w"] for x in cur).strip(), cur))
            cur = []
    return out


def detect_outro(tr, dur, window=150):
    """(cut_time, first_ad_word_time) for an ad read / bumper at the end, or None.
    Each sentence in the last `window` s scores +3 (strong ad phrase), +1/+2 (one / several weak ad words) or
    -1 per ~12 words of plain talk; the best-scoring tail that starts on an ad-like sentence is the outro. The cut
    lands just after the last word before it and before its first word (the voice often starts before the cut)."""
    sn = sentences(tr)
    idx = [k for k, s in enumerate(sn) if s[0] >= dur - window]
    sc = []
    for k in idx:
        t = sn[k][2]
        weak = len(OUTRO_WEAK.findall(t))
        sc.append(3 if OUTRO_STRONG.search(t) else min(2, weak) if weak else -max(1.0, len(t.split()) / 12))
    best, best_k, run = 0, None, 0
    for j in range(len(idx) - 1, -1, -1):
        run += sc[j]
        if sc[j] > 0 and run > best:
            best, best_k = run, idx[j]
    if best < 4 or not best_k:
        return None
    ad_t = sn[best_k][0]
    cut = min(sn[best_k - 1][1] + 0.3, ad_t - 0.05)
    return (cut, ad_t) if cut > dur * 0.4 else None


# ----------------------------------------------------------------------------------------------
# 4. captions
# ----------------------------------------------------------------------------------------------
BAD = {r"NIGG(A|AS|AZ|ER|ERS)": lambda m: "N" + "*" * (len(m.group(0)) - 2) + m.group(0)[-1],
       r"F+U+C+K\w*": lambda m: "F*" + m.group(0)[2:],
       r"SH+I+T\w*": lambda m: "SH*" + m.group(0)[3:],
       r"BITCH\w*": lambda m: "B*" + m.group(0)[2:],
       r"DICK\w*": lambda m: "D*" + m.group(0)[2:],
       r"PUSSY": lambda m: "P*SSY",
       r"\bASS(ES)?\b": lambda m: "A**" + m.group(0)[3:]}
CAP_Y = 1390


def clean(t):
    t = t.upper()
    for pat, fn in BAD.items():
        t = re.sub(pat, fn, t)
    t = re.sub(r"[,;:]+$", "", t)
    t = re.sub(r"\.+$", "", t)
    return t.replace("{", "").replace("}", "")


def ts(t):
    t = max(0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_captions(tr, edits, end_t):
    words = []
    for s in tr["segments"]:
        for w in s["words"]:
            t = w["w"].strip()
            if t and t[0] in "'’" and words and w["s"] < end_t:      # Whisper split "y" + "'all" -> "y'all"
                words[-1][0] += t; words[-1][2] = max(words[-1][2], min(w["e"], end_t))
                continue
            if t and w["s"] < end_t and (not words or w["s"] >= words[-1][2] - 0.05):
                words.append([t, w["s"], min(w["e"], end_t)])
    for fix in edits.get("words", []):           # nearest word to the given time
        if words:
            k = min(range(len(words)), key=lambda i: abs(words[i][1] - fix["at"]))
            if abs(words[k][1] - fix["at"]) < 0.6:
                words[k][0] = fix["to"]
    words = [w for w in words if w[0].strip()]   # a fix with "to": "" deletes the word
    chunks, cur = [], []
    for w in words:
        if cur:
            gap = w[1] - cur[-1][2]
            length = sum(len(x[0]) + 1 for x in cur) + len(w[0])
            if len(cur) >= 3 or length > 16 or gap > 0.45 or re.search(r"[.?!,;:]$", cur[-1][0]):
                chunks.append(cur); cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    events = []   # (start, end, text)
    HL, NORM = r"{\1c&H00E6FF&}", r"{\1c&HFFFFFF&}"
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0][1] if ci + 1 < len(chunks) else min(end_t, ch[-1][2] + 0.6)
        c_end = min(ch[-1][2] + 0.35, nxt)
        for wi, w in enumerate(ch):
            s = w[1] if wi else ch[0][1]
            e = ch[wi + 1][1] if wi + 1 < len(ch) else c_end
            if e <= s:
                continue
            txt = " ".join((HL if j == wi else NORM) + clean(x[0]) for j, x in enumerate(ch))
            anim = r"\fscx70\fscy70\t(0,90,\fscx105\fscy105)\t(90,150,\fscx100\fscy100)" if wi == 0 else ""
            events.append((s, e, f"{{\\an5\\pos(540,{CAP_Y}){anim}}}{txt}"))
    return events


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Montserrat Black,92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,4,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(events, path, offset=0.0):
    """offset: shift so a chunk starting at `offset` seconds sees its captions from t=0."""
    lines = [f"Dialogue: 0,{ts(s - offset)},{ts(e - offset)},Cap,,0,0,0,,{t}" for s, e, t in events
             if e - offset > 0]
    open(path, "w", encoding="utf-8").write(ASS_HEAD + "\n".join(lines) + "\n")


# ----------------------------------------------------------------------------------------------
# 5. render (parallel chunks, NVENC)
# ----------------------------------------------------------------------------------------------
def render_chunk(job):
    import cv2
    src, f0, f1, plan, ass_path, out, audio_path, wave_t0, encoder = job
    FF = ffmpeg_exe()
    FPS = FPS_NUM / FPS_DEN
    SR = 44100
    audio = np.fromfile(audio_path, np.float32)
    NB, WIN = 36, 2048
    edges = np.geomspace(70, 9000, NB + 1)
    freqs = np.fft.rfftfreq(WIN, 1 / SR)
    band_idx = [np.where((freqs >= edges[k]) & (freqs < edges[k + 1]))[0] for k in range(NB)]
    hann = np.hanning(WIN)

    def spectrum(fi):
        c = int(fi / FPS * SR)
        seg = audio[max(0, c - WIN // 2): c + WIN // 2]
        if len(seg) < WIN:
            seg = np.pad(seg, (0, WIN - len(seg)))
        mag = np.abs(np.fft.rfft(seg * hann))
        v = np.array([mag[ix].mean() if len(ix) else 0 for ix in band_idx])
        return np.clip((20 * np.log10(v + 1e-6) + 10) / 50, 0, 1)

    ACCENT = (0, 230, 255)
    FG_W = OW - 60; FG_H = int(FG_W * 9 / 16); FG_Y = 330; R = 28
    mask = np.zeros((FG_H, FG_W), np.uint8)
    cv2.rectangle(mask, (R, 0), (FG_W - R - 1, FG_H - 1), 255, -1)
    cv2.rectangle(mask, (0, R), (FG_W - 1, FG_H - R - 1), 255, -1)
    for cx, cy in [(R, R), (FG_W - R - 1, R), (R, FG_H - R - 1), (FG_W - R - 1, FG_H - R - 1)]:
        cv2.circle(mask, (cx, cy), R, 255, -1)
    mask_f = cv2.GaussianBlur(mask, (3, 3), 0).astype(np.float32)[..., None] / 255
    X = (OW - FG_W) // 2
    shadow = np.zeros((OH, OW), np.uint8)
    shadow[FG_Y + 14:FG_Y + FG_H + 14, X:X + FG_W] = mask
    shadow = 1 - cv2.GaussianBlur(shadow, (0, 0), 18).astype(np.float32)[..., None] / 255 * 0.7
    bw_bars = None

    # Frame-exact: seek ~2 s early (seek accuracy no longer matters), keep source timestamps (-copyts)
    # and select frames by timestamp, so chunk frame k is always source frame f0+k.
    # Always scale to SW x SH: the frame reader below assumes that size (720p sources were read as garbage).
    ss = max(0.0, (f0 - 60) / FPS)
    thr = (f0 - 0.5) / FPS
    dec = subprocess.Popen([FF, "-v", "error"] + hwdec() + ["-ss", f"{ss:.4f}", "-copyts", "-i", src,
                            "-vf", f"select='gte(t\\,{thr:.6f})',scale={SW}:{SH}:flags=bicubic,setsar=1",
                            "-fps_mode", "passthrough",
                            "-frames:v", str(f1 - f0), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                           stdout=subprocess.PIPE, bufsize=0)
    ass_esc = ass_path.replace("\\", "/").replace(":", "\\:")
    enc_exe, enc_args = encoder
    enc = subprocess.Popen([enc_exe, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{OW}x{OH}",
                            "-r", f"{FPS_NUM}/{FPS_DEN}", "-i", "-", "-vf", f"ass='{ass_esc}':fontsdir='{FONTS_ESC}'"] + enc_args +
                           ["-g", "60", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    bars = np.zeros(NB)
    t_mode = wave_t0
    prev_m = plan[0]["m"]
    fsz = SW * SH * 3 // 2
    buf = bytearray(fsz); mv = memoryview(buf)
    yuv = np.frombuffer(buf, np.uint8).reshape(SH * 3 // 2, SW)
    for idx, fi in enumerate(range(f0, f1)):
        got = 0
        while got < fsz:                       # unbuffered readinto: no extra copies (pipe was the bottleneck)
            r = dec.stdout.readinto(mv[got:])
            if not r:
                break
            got += r
        if got < fsz and idx == 0:
            buf[:] = bytes(fsz)                # missing tail frames repeat the last one (keeps audio in sync)
        img = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
        p = plan[idx]
        t_mode = t_mode + 1 if p["m"] == prev_m else 0
        prev_m = p["m"]
        if p["m"] == "face":
            z = p["z"]; ch = SH / z; cw = ch * OW / OH
            cx = np.clip(p["x"] * SW, cw / 2, SW - cw / 2); cy = np.clip(p["y"] * SH, ch / 2, SH - ch / 2)
            x0, y0 = cx - cw / 2, cy - ch / 2
            M = np.float32([[OW / cw, 0, -x0 * OW / cw], [0, OH / ch, -y0 * OH / ch]])
            frame = cv2.warpAffine(img, M, (OW, OH), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        else:
            bg_w = int(SW * OH / SH)
            small = cv2.GaussianBlur(cv2.resize(img, (bg_w // 8, OH // 8), interpolation=cv2.INTER_AREA), (0, 0), 6)
            bx = (bg_w - OW) // 16
            small = small[:, bx:bx + OW // 8]
            canvas = cv2.resize(small, (OW, OH), interpolation=cv2.INTER_LINEAR).astype(np.float32) * 0.45
            canvas *= shadow
            fg = cv2.resize(img, (FG_W, FG_H), interpolation=cv2.INTER_AREA)
            roi = canvas[FG_Y:FG_Y + FG_H, X:X + FG_W]
            canvas[FG_Y:FG_Y + FG_H, X:X + FG_W] = fg * mask_f + roi * (1 - mask_f)
            canvas = canvas.astype(np.uint8)
            s = spectrum(fi)
            bars = np.where(s > bars, bars + (s - bars) * 0.6, bars * 0.86 + s * 0.14)
            intro = min(1.0, t_mode / 8)
            cyw, maxh = FG_Y + FG_H + 190, 150
            order = np.concatenate([bars[::-1], bars])
            nb, gap = len(order), 6
            bw = (OW - 120 - gap * (nb - 1)) / nb
            band = np.zeros((2 * maxh + 60, OW, 3), np.uint8)   # draw only the waveform strip
            oy = cyw - maxh - 30
            rects = [(int(60 + k * (bw + gap)), max(6, v * maxh * intro)) for k, v in enumerate(order)]
            for x, h in rects:
                cv2.rectangle(band, (x, int(cyw - h - oy)), (int(x + bw), int(cyw + h - oy)), ACCENT, -1)
            glow = (cv2.GaussianBlur(band, (0, 0), 10) * 0.8).astype(np.uint8)
            strip = cv2.add(canvas[oy:oy + band.shape[0]], glow)
            for x, h in rects:
                cv2.rectangle(strip, (x, int(cyw - h - oy)), (int(x + bw), int(cyw + h - oy)), (255, 255, 255), -1)
            canvas[oy:oy + band.shape[0]] = strip
            frame = canvas
        enc.stdin.write(cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420).tobytes())
    enc.stdin.close(); enc.wait(); dec.kill()
    return out


def pick_encoder():
    """GPU (NVENC) if any bundled ffmpeg can open it with this driver, else CPU x264."""
    nv = [(ffmpeg_exe(), ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                          "-maxrate", "14M", "-bufsize", "28M", "-profile:v", "high", "-bf", "3"])]
    old = os.path.join(HERE, "bin", "ffmpeg_nvenc.exe")     # ffmpeg 4.2 = older NVENC API, works on older drivers
    if os.path.exists(old):
        nv.append((old, ["-c:v", "h264_nvenc", "-preset", "slow", "-rc", "vbr_hq", "-cq", "19", "-b:v", "0",
                         "-maxrate", "14M", "-bufsize", "28M", "-profile:v", "high", "-bf", "3"]))
    for exe, a in nv:
        r = subprocess.run([exe, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30", "-t", "0.5"] + a +
                           ["-f", "null", "-"], capture_output=True)
        if r.returncode == 0:
            return exe, a, "nvenc"
    return ffmpeg_exe(), ["-c:v", "libx264", "-preset", "faster", "-crf", "19", "-profile:v", "high", "-threads", "4"], "x264"


def split_chunks(plan, cuts, n_end, target_frames):
    """chunk boundaries only on camera cuts between two face shots (keeps waveform animation continuous)."""
    cand = [c for c in cuts if 0 < c < n_end and plan[c - 1]["m"] == "face" and plan[c]["m"] == "face"]
    bounds, last = [0], 0
    for c in cand:
        if c - last >= target_frames:
            bounds.append(c); last = c
    if n_end - bounds[-1] < target_frames // 3 and len(bounds) > 1:
        bounds.pop()
    bounds.append(n_end)
    return bounds


# ----------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--model", default="large-v3", help="Whisper model (the desktop app uses large-v3-turbo)")
    ap.add_argument("--work", help="cache folder (default work/<name>)")
    ap.add_argument("--edits", help="edits file (default <video>.edits.json next to the video)")
    ap.add_argument("--auto-end", action="store_true", help="also cut a detected ad read / bumper at the end")
    args = ap.parse_args()
    run(args.video, args.out, args.workers, args.model, args.work, args.edits, args.auto_end)


def run(video, out=None, workers=6, model="large-v3", work=None, edits_path=None, auto_end=False):
    t0 = time.time()
    src = os.path.abspath(video)
    name = os.path.splitext(os.path.basename(src))[0]
    work = work or os.path.join(HERE, "work", name); os.makedirs(os.path.join(work, "chunks"), exist_ok=True)
    out = out or os.path.join(os.path.dirname(src), f"{name}_9x16.mp4")
    edits_path = edits_path or os.path.join(os.path.dirname(src), f"{name}.edits.json")
    if not os.path.exists(edits_path):
        json.dump({"end": None, "words": [], "wave": [], "face": []}, open(edits_path, "w"), indent=2)
    edits = json.load(open(edits_path))
    FF = ffmpeg_exe()
    n, dur = frame_count(src)
    log(t0, f"{name}: {dur:.1f}s, {n} frames")

    tr_path, an_path = os.path.join(work, "transcript.json"), os.path.join(work, "analysis.json")
    audio_path = os.path.join(work, "audio44k.f32")
    with ThreadPoolExecutor(3) as ex:
        ft = ex.submit(transcribe, src, tr_path, model) if not os.path.exists(tr_path) else None
        fa = ex.submit(analyze, src, an_path, n, workers + 2) if not os.path.exists(an_path) else None
        if not os.path.exists(audio_path):
            subprocess.run([FF, "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "44100", "-f", "f32le",
                            audio_path], check=True)
        if fa: fa.result(); log(t0, "analysis done")
        if ft: log(t0, f"transcription done ({ft.result()})")
    a = json.load(open(an_path)); tr = json.load(open(tr_path, encoding="utf-8"))
    fps = a["fps"]
    plan, cuts = make_plan(a, edits)
    n_end = min(len(plan), int(edits["end"] * fps)) if edits.get("end") else len(plan)
    end_reason, ad_t = ("manual" if edits.get("end") else "full"), None
    if not edits.get("end") and not edits.get("keep_outro"):
        outro = detect_outro(tr, len(plan) / fps) if auto_end else None
        if outro:
            n_end, ad_t, end_reason = min(len(plan), int(outro[0] * fps)), outro[1], "ad"
            log(t0, f"ad read / bumper detected at {ad_t:.2f}s, cut at {n_end / fps:.2f}s")
        else:
            # auto-trim outro: final run of "nobody on screen" frames (logo/bumper), if it is short
            k = len(plan)
            while k > 0 and plan[k - 1]["m"] == "wave":
                k -= 1
            if 0 < len(plan) - k < 25 * fps and k > len(plan) * 0.5:
                n_end, end_reason = k, "logo"
                log(t0, f"outro auto-trimmed at {k / fps:.2f}s (set \"end\" in {os.path.basename(edits_path)} to change)")
    end_t = n_end / fps
    events = make_captions(tr, edits, end_t)
    write_ass(events, os.path.join(work, "captions_full.ass"))
    # report.txt: what a human / AI agent needs to check the ending (ads, bumpers) and the waveform parts
    rep = [f"video: {src}", f"duration: {len(plan) / fps:.2f}s   output ends at: {end_t:.2f}s", "", "waveform sections:"]
    i = 0
    while i < len(plan):
        j = i
        while j < len(plan) and plan[j]["m"] == plan[i]["m"]:
            j += 1
        if plan[i]["m"] == "wave":
            rep.append(f"  {i / fps:7.2f}s - {j / fps:7.2f}s")
        i = j
    rep += ["", "camera cuts (s): " + " ".join(f"{c / fps:.2f}" for c in cuts[1:-1]), "",
            "transcript, last 90 s of the source (look for ad reads / outro bumper):"]
    for s in tr["segments"]:
        if s["end"] > len(plan) / fps - 90:
            rep.append(f"  {s['start']:7.2f}-{s['end']:7.2f} {s['text'].strip()}")
    open(os.path.join(work, "report.txt"), "w", encoding="utf-8").write("\n".join(rep) + "\n")
    log(t0, f"plan + {len(events)} caption events; wave {sum(p['m'] == 'wave' for p in plan[:n_end]) / fps:.0f}s")

    bounds = split_chunks(plan, cuts, n_end, target_frames=int(12 * fps))
    enc_exe, enc_args, enc_name = pick_encoder()
    jobs, files = [], []
    for k in range(len(bounds) - 1):
        f0, f1 = bounds[k], bounds[k + 1]
        t_s, t_e = f0 / fps, f1 / fps
        evs = [ev for ev in events if ev[1] > t_s and ev[0] < t_e]
        h = hashlib.sha1(json.dumps([RENDER_VERSION, plan[f0:f1], evs, f0, f1]).encode()).hexdigest()[:12]
        cf = os.path.join(work, "chunks", f"{f0:06d}_{f1:06d}_{h}.mp4")
        files.append(cf)
        if not os.path.exists(cf):
            ass = os.path.join(work, "chunks", f"{f0:06d}_{h}.ass")
            write_ass(evs, ass, offset=t_s)
            jobs.append((src, f0, f1, plan[f0:f1], ass, cf + ".tmp.mp4", audio_path, 0, (enc_exe, enc_args)))
    log(t0, f"{len(bounds) - 1} chunks, {len(jobs)} to render ({enc_name})")
    if jobs:
        jobs.sort(key=lambda j: j[1] - j[2])   # longest first
        total, done = sum(j[2] - j[1] for j in jobs), 0
        with ProcessPoolExecutor(workers) as ex:
            for j, r in zip(jobs, ex.map(render_chunk, jobs)):
                os.replace(r, r[:-8])
                done += j[2] - j[1]
                progress("render", done / total)
        log(t0, "chunks rendered")
    keep = set(os.path.basename(f) for f in files)
    for f in os.listdir(os.path.join(work, "chunks")):     # drop stale chunks
        if f.endswith(".mp4") and f not in keep:
            os.remove(os.path.join(work, "chunks", f))
    lst = os.path.join(work, "concat.txt")
    open(lst, "w").write("".join(f"file '{f.replace(os.sep, '/')}'\n" for f in files))
    subprocess.run([FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", src,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-af", f"atrim=0:{end_t:.4f},afade=t=out:st={max(0, end_t - 0.2):.4f}:d=0.2",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{end_t:.4f}",
                    "-movflags", "+faststart", out], check=True)
    result = {"out": out, "duration": len(plan) / fps, "end": end_t, "end_reason": end_reason, "ad_start": ad_t,
              "encoder": enc_name, "device": tr.get("device")}
    json.dump(result, open(os.path.join(work, "result.json"), "w"), indent=2)
    log(t0, f"done -> {out}")
    log(t0, f"check the ending in {os.path.join(work, 'report.txt')}; corrections go in {edits_path}")


if __name__ == "__main__":
    main()
