"""B-roll: full-screen cutaways (5-10 s) from YouTube footage of whoever / whatever the speaker is talking about.

    1. an LLM (OpenRouter, Claude Haiku) reads the transcript and picks about one moment per 35 s where the
       speaker names a person, team, place or event that has footage (subjects may repeat with other footage)
    2. yt-dlp searches the Breakfast Club channel first, then all of YouTube; auto-captions locate the subject inside
       each candidate video and ffmpeg grabs frames straight from the stream
    3. OpenCV face recognition drops every frame showing a host (a vision model can't reliably tell faces apart),
       the model ranks the rest, and the clip around the best frame is split into shots: each shot must show the
       same face as that frame (or, for places / events, no host), the crop follows the face per shot, and the
       longest such run (5-10 s) becomes the cutaway; the model checks the finished clip once more
Spend is capped per video (BUDGET, persisted in work/<name>/broll/cost.json). Picks are saved under "broll" in
<video>.edits.json: delete an entry (or set "broll": []) and re-run to drop it; delete the key to pick again.
Needs OPENROUTER_API_KEY; without it the clip renders without B-roll.
"""
import base64, difflib, json, os, re, subprocess, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

CHANNEL = os.environ.get("REFRAME_BROLL_CHANNEL", "https://www.youtube.com/@BreakfastClubPower1051FM")
MODEL = os.environ.get("REFRAME_BROLL_MODEL", "anthropic/claude-haiku-5.5")     # text + image input, solid JSON
BUDGET = float(os.environ.get("REFRAME_BROLL_BUDGET", "0.10"))                     # USD per video
EVERY = 35                    # about one cutaway per EVERY seconds of clip
MIN_DUR, MAX_DUR = 5.0, 10.0  # cutaway length (s)
GAP = 10.0                    # at least this much speaker between two cutaways (s)
PRICE = (0.10, 0.50)          # USD per 1M tokens in / out, only for the worst-case estimate before a call
IMG_TOKENS = 1500             # generous per-image estimate (480-720 px wide JPEG)
HOSTS = re.compile(r"charlamagne|charlemagne|\benvy\b|jess hilarious|\bjess\b|angela yee|\byee\b|loren lorosa", re.I)
OW, OH, FPS_NUM, FPS_DEN = 1080, 1920, 30000, 1001
FPS = FPS_NUM / FPS_DEN
HERE = os.path.dirname(os.path.abspath(__file__))
FACE_MODELS = {   # OpenCV zoo: YuNet face detector + SFace face embeddings (downloaded once into models/)
    "face_detection_yunet_2023mar.onnx": ("face_detection_yunet",
                                          "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx": ("face_recognition_sface",
                                            "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79")}
SAME_PERSON = 0.363           # SFace cosine threshold for "same person"


class OutOfBudget(Exception):
    pass


def api_key():
    k = os.environ.get("OPENROUTER_API_KEY")
    if not k and os.name == "nt":            # set with setx: not in this process's environment until a new shell
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
                k = winreg.QueryValueEx(h, "OPENROUTER_API_KEY")[0]
        except OSError:
            pass
    return k


class Budget:
    def __init__(self, path, cap=BUDGET):
        self.path, self.cap, self.lock = path, cap, threading.Lock()
        self.spent = json.load(open(path)).get("spent", 0.0) if os.path.exists(path) else 0.0
        self.calls = 0

    def reserve(self, est):
        with self.lock:
            if self.spent + est > self.cap:
                raise OutOfBudget(f"B-roll budget ${self.cap:.2f} reached (spent ${self.spent:.4f})")

    def add(self, cost):
        with self.lock:
            self.spent += cost; self.calls += 1
            json.dump({"spent": round(self.spent, 6), "cap": self.cap}, open(self.path, "w"))


def chat(budget, content, max_tokens=2500, _retry=False):
    """one OpenRouter call -> parsed JSON object. content: str or list of message parts (text / image_url)."""
    parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
    tin = sum(len(p.get("text", "")) // 3 + (IMG_TOKENS if p["type"] == "image_url" else 0) for p in parts)
    budget.reserve((tin * PRICE[0] + max_tokens * PRICE[1]) / 1e6)
    body = {"model": MODEL, "messages": [{"role": "user", "content": parts}], "max_tokens": max_tokens,
            "temperature": 0.2, "usage": {"include": True},
            "response_format": {"type": "json_object"},
            # thinking would eat max_tokens on long prompts (deepseek can't switch it off)
            "reasoning": {"effort": "low"} if "deepseek" in MODEL else {"enabled": False},
            # never route to a provider that charges more than ~2x the list price
            "provider": {"max_price": {"prompt": PRICE[0] * 2, "completion": PRICE[1] * 2}}}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", json.dumps(body).encode(),
                                 {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json",
                                  "X-Title": "FB Clip Maker"})
    for attempt in range(3):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=120))
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 + 3 * attempt)
    u = r.get("usage") or {}
    budget.add(float(u.get("cost") or (u.get("prompt_tokens", tin) * PRICE[0] +
                                       u.get("completion_tokens", max_tokens) * PRICE[1]) / 1e6))
    text = r["choices"][0]["message"].get("content") or ""
    if not text.strip():
        raise RuntimeError(f"empty answer from {MODEL} (finish: {r['choices'][0].get('finish_reason')})")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        if _retry:                            # cheap models sometimes break their JSON (a stray quote): ask again
            raise
        return chat(budget, parts + [{"type": "text", "text": "Reply with valid JSON only. Inside strings, use "
                                      "single quotes instead of double quotes."}], max_tokens, _retry=True)


# ---------------------------------------------------------------- faces
_face_lock, _face = threading.Lock(), None


def _face_models():
    """(detector, recognizer); a vision model can't reliably tell faces apart, so identity is checked here"""
    global _face
    import cv2, hashlib
    with _face_lock:
        if _face is None:
            d = os.environ.get("REFRAME_MODELS") or os.path.join(HERE, "models")
            os.makedirs(d, exist_ok=True)
            for f, (sub, sha) in FACE_MODELS.items():
                path = os.path.join(d, f)
                if not os.path.exists(path):
                    data = urllib.request.urlopen("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
                                                  f"models/{sub}/{f}", timeout=120).read()
                    if hashlib.sha256(data).hexdigest() != sha:
                        raise RuntimeError(f"{f}: checksum mismatch")
                    open(path + ".part", "wb").write(data)
                    os.replace(path + ".part", path)
            _face = (cv2.FaceDetectorYN.create(os.path.join(d, "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.8),
                     cv2.FaceRecognizerSF.create(os.path.join(d, "face_recognition_sface_2021dec.onnx"), ""))
        return _face


def faces(img):
    """[(horizontal centre 0-1, embedding, height / image height)] of the faces in a BGR image, largest first"""
    det, rec = _face_models()
    with _face_lock:
        det.setInputSize((img.shape[1], img.shape[0]))
        _, f = det.detect(img)
        out = [(r[2] * r[3], (r[0] + r[2] / 2) / img.shape[1], rec.feature(rec.alignCrop(img, r)).copy(),
                r[3] / img.shape[0]) for r in (f if f is not None else [])]
    return [(cx, e, h) for _, cx, e, h in sorted(out, key=lambda x: -x[0])]


def _letterboxed(img):
    """black bars above and below (a 4:3 / cinema video inside a 16:9 frame): ugly when cropped to 9:16"""
    g = img.mean(axis=2)
    b = max(2, img.shape[0] // 12)
    return g[:b].mean() < 16 and g[-b:].mean() < 16 and g[b:-b].mean() > 30


def sim(a, refs):
    import cv2
    rec = _face_models()[1]
    return max((rec.match(a, h, cv2.FaceRecognizerSF_FR_COSINE) for h in refs), default=0.0)


def _imread(path):
    import cv2
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)     # cv2.imread: no non-ASCII paths on Windows


def _imwrite(path, img):
    import cv2
    cv2.imencode(".jpg", img)[1].tofile(path)


def _read(ff, path, t):
    """one BGR frame at time t (reference / probe pictures only)"""
    import cv2
    raw = subprocess.run([ff, "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-f", "image2pipe",
                          "-vcodec", "png", "-"], capture_output=True, timeout=60).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None


# ---------------------------------------------------------------- 1. moments
def _lines(tr, limit_t, sentences, most=10.0):
    """transcript sentences, long ones (unpunctuated Whisper output) split into pieces of at most `most` s"""
    words = [w for s in tr["segments"] for w in s["words"] if w["w"].strip()]
    out = []
    for s, e, t, _ in sentences(tr):
        if e >= limit_t:
            continue
        ws = [w for w in words if s - 0.05 <= w["s"] <= e + 0.05]
        if e - s <= most * 1.5 or not ws:
            out.append((s, e, t))
            continue
        cur = [ws[0]]
        for w in ws[1:]:
            if w["s"] - cur[0]["s"] >= most:
                out.append((cur[0]["s"], cur[-1]["e"], " ".join(x["w"].strip() for x in cur)))
                cur = []
            cur.append(w)
        out.append((cur[0]["s"], min(e, cur[-1]["e"]), " ".join(x["w"].strip() for x in cur)))
    return out


def pick_moments(budget, tr, title, limit_t, sentences):
    lines = _lines(tr, limit_t, sentences)
    listing = "\n".join(f"[{k}] {s:.1f}s: {t}" for k, (s, e, t) in enumerate(lines))
    want = int(np.clip(round(limit_t / EVERY), 3, 12))
    edges = np.linspace(3, limit_t - MIN_DUR, want + 1)
    windows = ", ".join(f"{a:.0f}-{b:.0f}s" for a, b in zip(edges, edges[1:]))
    prompt = f"""You edit vertical clips from The Breakfast Club (Power 105.1) for Facebook.
Clip title: "{title}"   length: {limit_t:.0f} s

Transcript, one numbered sentence per line:
{listing}

Plan about {want + 4} full-screen B-roll cutaways (we keep the best {want}). Each one covers {MIN_DUR:.0f} to
{MAX_DUR:.0f} seconds of the speaker's talk with real video footage of what they are talking about, found on YouTube.
Good subjects: a named person (athlete, artist, celebrity, politician), their team or organisation (e.g. an NFL
team), a city or place that is the topic, a specific event (a game, a press conference, an interview, an incident).
The same subject may come back several times when it is the main topic, but then each cutaway needs DIFFERENT
footage (e.g. game highlights, then a press conference, then an interview), so give different search queries.
Cover the whole clip: one or two moments in EACH of these windows: {windows}.
If a window names no new subject, use the clip's main subject (who the title is about) again, with different footage.
Also describe the main subject with 6 DIFFERENT kinds of footage of it (each its own query), used to fill gaps.
Do NOT use: the radio hosts (Charlamagne, DJ Envy, Jess Hilarious, Angela Yee, Loren Lorosa), abstract ideas
("money", "racism") or generic stock footage ("radio controversy", "people arguing"): each cutaway must show a
specific, nameable person / team / place / event.
"dur": how long the speaker stays on that subject, {MIN_DUR:.0f}-{MAX_DUR:.0f}.
"kind": person | team | place | event.
"queries": 2 or 3 YouTube search queries that find that footage (specific: names, team, "highlights",
"press conference", "interview", "drone", ...).

Answer as JSON: {{"moments": [{{"line": <line number>, "phrase": "<exact words from that line that start the
moment>", "subject": "<full name / team / place / event>", "kind": "...", "footage": "<what the cutaway should
show>", "queries": ["...", "..."], "dur": <seconds>, "why": "<few words>"}}],
"main": {{"subject": "<who / what the clip is about>", "kind": "...", "footage": [{{"footage": "<e.g. game
highlights>", "query": "<YouTube search>"}}, ...]}}}}"""
    ans = chat(budget, prompt, max_tokens=6000)
    out, main = ans.get("moments", []), ans.get("main") or {}
    words = [w for s in tr["segments"] for w in s["words"] if w["w"].strip()]
    res = []
    for m in out:
        try:
            s, e, _ = lines[int(m["line"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if HOSTS.search(m.get("subject", "")):
            continue
        toks = re.findall(r"[a-z0-9']+", m.get("phrase", "").lower())
        cand = [w for w in words if s - 0.05 <= w["s"] <= e + 0.05]
        at = next((w["s"] for w in cand if toks and re.sub(r"[^a-z0-9']", "", w["w"].lower()) == toks[0]), s)
        try:
            dur = float(np.clip(float(m.get("dur") or 7), MIN_DUR, MAX_DUR))
        except (TypeError, ValueError):
            dur = 7.0
        if at < 3 or at + MIN_DUR > limit_t - 0.5:
            continue
        dur = min(dur, limit_t - 0.5 - at)
        qs = [q for q in m.get("queries", []) if isinstance(q, str) and q.strip()][:3] or [m.get("subject", "")]
        res.append({"at": round(at, 2), "dur": round(dur, 2), "subject": m.get("subject", ""),
                    "kind": str(m.get("kind", "person")).lower(), "footage": m.get("footage", ""),
                    "phrase": m.get("phrase", ""), "queries": qs, "why": m.get("why", "")})
    # every window gets one: the model leaves gaps where nobody new is named, fill them with the main subject
    fills = [f for f in main.get("footage") or [] if isinstance(f, dict) and str(f.get("query", "")).strip()]
    gaps = {"lines": lines, "edges": edges, "limit": limit_t, "main": main, "fills": fills, "k": 0}
    res = sorted(res + fill_gaps(res, gaps), key=lambda r: r["at"])
    return res, want, gaps


def fill_gaps(taken, g):
    """a main-subject moment for every window without one in `taken` (each with the next footage idea)"""
    main, fills, out = g["main"], g["fills"], []
    if not (main.get("subject") and fills) or HOSTS.search(main["subject"]):
        return out
    for a, b in zip(g["edges"], g["edges"][1:]):
        busy = taken + out
        if any(a <= r["at"] < b for r in busy):
            continue
        free = [(s, t) for s, e, t in g["lines"] if a <= s < b - MIN_DUR and s + MIN_DUR < g["limit"] - 0.5 and
                all(s >= r["at"] + r["dur"] + GAP or s + 7 + GAP <= r["at"] for r in busy)]
        if not free:
            continue
        s, t = min(free, key=lambda x: abs(x[0] - (a + b) / 2))
        f = fills[g["k"] % len(fills)]; g["k"] += 1
        out.append({"at": round(s, 2), "dur": round(min(7.0, g["limit"] - 0.5 - s), 2), "subject": main["subject"],
                    "kind": str(main.get("kind", "person")).lower(), "footage": f.get("footage", ""),
                    "phrase": t[:60], "queries": [f["query"]] + [x["query"] for x in fills if x is not f][:1],
                    "why": "main subject (fills a gap)"})
    return out


def schedule(items, want):
    """non-overlapping cutaways, GAP s of speaker in between, at most `want`; earlier ones first"""
    keep = []
    for it in sorted(items, key=lambda r: r["at"]):
        if not keep or it["at"] >= keep[-1]["at"] + keep[-1]["dur"] + GAP:
            keep.append(it)
    while len(keep) > want:                       # drop the one closest to its neighbours
        gaps = [(keep[i + 1]["at"] - keep[i]["at"]) for i in range(len(keep) - 1)]
        keep.pop(int(np.argmin(gaps)) + 1)
    return keep


# ---------------------------------------------------------------- 2. candidates on YouTube
def _ydl(**kw):
    import yt_dlp
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, **kw})


def _norm(t):
    t = re.sub(r"\(\d{3,4}p\)|breakfast club power 105\.?1 ?fm|the breakfast club", " ", t.lower())
    return " ".join(re.findall(r"[a-z0-9]+", t))


def search(query, exclude_title, channel=True, n=6):
    url = f"{CHANNEL}/search?query={urllib.parse.quote(query)}" if channel else f"ytsearch{n}:{query}"
    with _ydl(extract_flat=True, playlistend=n) as y:
        info = y.extract_info(url, download=False)
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id") or not e.get("duration") or not 15 < e["duration"] < 3 * 3600:
            continue
        if difflib.SequenceMatcher(None, _norm(e.get("title", "")), _norm(exclude_title)).ratio() > 0.75:
            continue                                  # the source clip itself (or a re-upload)
        out.append({"id": e["id"], "title": e.get("title", ""), "duration": e["duration"], "channel": channel})
    return out


def video_info(vid):
    """stream URL of a video-only <=1080p format + auto captions as [(t, word)]"""
    with _ydl(format="bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]/b[height<=1080]") as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    words = []
    caps = (info.get("subtitles") or {}).get("en") or (info.get("automatic_captions") or {}).get("en") or []
    j3 = next((c["url"] for c in caps if c.get("ext") == "json3"), None)
    if j3:
        try:
            data = json.load(urllib.request.urlopen(j3, timeout=30))
            for ev in data.get("events", []):
                for sg in ev.get("segs") or []:
                    w = sg.get("utf8", "").strip()
                    if w:
                        words.append(((ev.get("tStartMs", 0) + sg.get("tOffsetMs", 0)) / 1000, w))
        except Exception:
            pass
    hdr = "".join(f"{k}: {v}\r\n" for k, v in (info.get("http_headers") or {}).items())
    return {"id": vid, "url": info["url"], "headers": hdr, "w": info.get("width") or 1920,
            "h": info.get("height") or 1080, "duration": info.get("duration") or 0, "words": words}


def probe_times(v, subject, title, k=4):
    """where in the candidate video the subject is likely on screen"""
    toks = [t for t in re.findall(r"[a-z0-9]+", subject.lower()) if len(t) > 2]
    d = v["duration"]
    times = []
    for t, w in v["words"]:
        if re.sub(r"[^a-z0-9]", "", w.lower()) in toks and all(abs(t - u) > 20 for u in times):
            times.append(t + 1.0)
    spread = (0.15, 0.35, 0.55, 0.75) if d > 120 else (0.25, 0.5, 0.75)
    if any(t in title.lower() for t in toks) or not times:     # about them: spread over the video
        times = times[:k // 2] + [d * f for f in spread]
    return [round(t, 2) for t in times if 3 < t < d - MIN_DUR - 1][:k]


def grab(ff, v, t, path, width=720):
    hdr = ["-headers", v["headers"]] if v["headers"] else []
    for attempt in range(2):                 # googlevideo sometimes answers 403 to one of several range requests
        r = subprocess.run([ff, "-v", "error", "-y", "-ss", f"{t:.2f}"] + hdr + ["-i", v["url"], "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-q:v", "4", path], timeout=60, capture_output=True)
        if r.returncode == 0 and os.path.exists(path):
            return path
        time.sleep(1.5)
    raise RuntimeError(r.stderr.decode(errors="replace")[-200:])


def _img(path):
    return {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(open(path, "rb").read()).decode()}}


def _context(tr, at, dur):
    return " ".join(w["w"].strip() for s in tr["segments"] for w in s["words"] if at - 5 <= w["s"] <= at + dur)


def _ref_parts(refs):
    if not refs:
        return []
    return [{"type": "text", "text": "REFERENCE: frames from the clip itself, showing the people already on screen "
             "(mostly the radio hosts). The B-roll must show someone / something else:"}] + [_img(p) for p in refs]


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


# ---------------------------------------------------------------- 3. cut the cutaway
def _frames(ff, path, w, h):
    """one sequential decode of a file at w x h: yields BGR frames (streamed, windows are long)"""
    p = subprocess.Popen([ff, "-v", "error", "-i", path, "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    sz = w * h * 3
    try:
        while True:
            b = p.stdout.read(sz)
            if len(b) < sz:
                break
            yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    finally:
        p.kill(); p.wait()


def _render(ff, win, Wf, a0, L, shot_x, out):
    """frames a0..a0+L-1 of the window, each cropped to 9:16 around its shot's centre, -> out (OW x OH)"""
    import cv2
    cw = min(Wf, int(round(1080 * OW / OH / 2)) * 2)
    enc = subprocess.Popen([ff, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OW}x{OH}",
                            "-r", f"{FPS_NUM}/{FPS_DEN}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "14", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    n = 0
    for f, img in enumerate(_frames(ff, win, Wf, 1080)):
        if f < a0:
            continue
        if f >= a0 + L:
            break
        x = [x for k, x in shot_x if k <= f - a0][-1]
        x0 = int(np.clip(x * Wf - cw / 2, 0, Wf - cw))
        enc.stdin.write(cv2.resize(img[:, x0:x0 + cw], (OW, OH), interpolation=cv2.INTER_CUBIC).tobytes())
        n += 1
    enc.stdin.close(); enc.wait()
    return n if enc.returncode == 0 else 0


def _window(ff, v, t0, secs, path, frames=None):
    hdr = ["-headers", v["headers"]] if v["headers"] else []
    lim = ["-frames:v", str(frames)] if frames else ["-t", f"{secs:.3f}"]
    subprocess.run([ff, "-v", "error", "-y", "-ss", f"{t0:.3f}"] + hdr + ["-i", v["url"]] + lim +
                   ["-an", "-vf", f"fps={FPS_NUM}/{FPS_DEN},scale=-2:1080,setsar=1", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", path], timeout=300, check=True)


def cut_clip(ff, v, t, dur, kind, fx, anchor, hosts, out):
    """the window around t, split into shots. Usable shot: no host; for a person, the anchor face (the face in the
    picked frame) is in it; a shot without any face only when short. The longest usable run of shots that holds t
    (MIN_DUR..dur s) becomes the cutaway, cropped to 9:16 around the face of each shot.
    -> {"frames", "src_t", "shots": [[first frame, crop centre]]} or None"""
    t0 = max(0.0, t - dur)
    win = out + ".win.mp4"
    _window(ff, v, t0, 2 * dur + 1, win)
    try:
        return _cut_window(ff, win, t - t0, t0, dur, kind, fx, anchor, hosts, out)
    finally:
        if os.path.exists(win):
            os.remove(win)


def _cut_window(ff, win, ta, t0, dur, kind, fx, anchor, hosts, out):
    a = np.array([f.mean(axis=2) for f in _frames(ff, win, 96, 54)], np.float32)
    n = len(a)
    if n < MIN_DUR * FPS:
        return None
    d = np.r_[0, np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))]
    cuts = [0]
    for i in range(1, n):          # hard cuts, and both frames of a flash / fast transition
        if (d[i] > 30 and d[i] > 3 * np.median(d[max(1, i - 8):i + 8]) + 4) or (d[i] > 20 and i + 1 < n and d[i + 1] > 20):
            cuts.append(i)
    cuts.append(n)
    shots = [(s, e) for s, e in zip(cuts, cuts[1:]) if e > s]
    # judge each shot from two of its frames (one sequential decode at 720p)
    Wf = win_w(ff, win)
    W7 = int(round(Wf * 720 / 1080 / 2)) * 2
    want = {k for s, e in shots for k in (s + (e - s) // 3, s + 2 * (e - s) // 3)}
    probe = {k: f.copy() for k, f in enumerate(_frames(ff, win, W7, 720)) if k in want}
    info = []
    for s, e in shots:
        fs = [faces(probe[k]) for k in (s + (e - s) // 3, s + 2 * (e - s) // 3) if k in probe]
        mains = [f[0] for f in fs if f]
        host = any(sim(m[1], hosts) >= SAME_PERSON for m in mains)
        if kind == "person" and anchor is not None:
            match = [m for f in fs for m in f if sim(m[1], [anchor]) >= SAME_PERSON]
            ok = (bool(match) or (not mains and e - s <= 2 * FPS)) and not host   # short wide / action shot: fine
            cx = float(np.mean([m[0] for m in match])) if match else None
        else:                                   # place / team / event: footage, not someone talking to camera
            ok = not host and not any(m[2] > 0.22 for m in mains)
            cx = float(np.mean([m[0] for m in mains])) if mains else None
        if any(_letterboxed(probe[k]) for k in (s + (e - s) // 3, s + 2 * (e - s) // 3) if k in probe):
            ok = False
        run = longest = 0
        for x in d[s + 1:e] > 20:               # fast motion for 1.3 s+ (verify.py would call it garbled)
            run = run + 1 if x else 0
            longest = max(longest, run)
        info.append((s, e, ok and e - s >= 12 and longest < 40, cx))
    ti = int(round(ta * FPS))
    k = next((i for i, (s, e, _, _) in enumerate(info) if s <= ti < e), None)
    if k is None or not info[k][2]:
        return None
    i0 = i1 = k
    while i0 > 0 and info[i0 - 1][2]:
        i0 -= 1
    while i1 + 1 < len(info) and info[i1 + 1][2]:
        i1 += 1
    s0, e0 = info[i0][0] + (2 if info[i0][0] else 0), info[i1][1] - 2     # clear of the cut frames themselves
    L = min(int(round(dur * FPS)), e0 - s0)
    if L < int(MIN_DUR * FPS):
        return None
    a0 = int(np.clip(ti - L // 3, s0, e0 - L))              # the picked moment early in the cutaway
    shot_x, cur = [], fx
    for s, e, _, cx in info[i0:i1 + 1]:
        cur = cx if cx is not None else cur
        if e > a0 and s < a0 + L:
            shot_x.append([max(0, s - a0), round(cur, 3)])
    if _render(ff, win, Wf, a0, L, shot_x, out) != L:
        return None
    return {"frames": L, "src_t": round(t0 + a0 / FPS, 3), "shots": shot_x}


def win_w(ff, path):
    r = subprocess.run([ff, "-i", path], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Video:.*?(\d{2,5})x1080", r)
    return int(m.group(1)) if m else 1920


def clip_frames(ff, path, n, out_dir, tag):
    """first / middle / last frame of a finished clip (one sequential decode)"""
    ks = {1: 0, n // 2: 1, n - 2: 2}
    paths = [os.path.join(out_dir, f"{tag}_chk{i}.jpg") for i in range(3)]
    for k, img in enumerate(_frames(ff, path, 360, 640)):
        if k in ks:
            _imwrite(paths[ks[k]], img)
    return [p for p in paths if os.path.exists(p)]



# ---------------------------------------------------------------- one moment
class Used:
    """footage already in a cutaway (video id, start, end): never reused"""
    def __init__(self):
        self.lock, self.spans = threading.Lock(), []

    def free(self, vid, t):
        with self.lock:
            return not any(v == vid and s - 5 <= t <= e + 5 for v, s, e in self.spans)

    def take(self, vid, s, e):
        with self.lock:
            if any(v == vid and s < e2 + 5 and e > s2 - 5 for v, s2, e2 in self.spans):
                return False
            self.spans.append((vid, s, e))
            return True


def find_one(ff, budget, m, tr, title, bdir, tag, refs, hosts, used):
    cands, seen = [], set()
    for channel in (True, False):                     # the show's own channel first, then all of YouTube
        for q in m["queries"]:
            for c in _safe(search, q, title, channel) or []:
                if c["id"] not in seen:
                    seen.add(c["id"]); cands.append(c)
    if not cands:
        return None, "no search results"
    toks = [t for t in re.findall(r"[a-z0-9]+", m["subject"].lower()) if len(t) > 2]
    cands.sort(key=lambda c: (-sum(t in c["title"].lower() for t in toks), not c["channel"]))
    cands = cands[:4]
    infos = list(ThreadPoolExecutor(4).map(lambda c: _safe(video_info, c["id"]), cands))
    probes = []
    for c, v in zip(cands, infos):
        if v:
            probes += [(c, v, t) for t in probe_times(v, m["subject"], c["title"], k=4) if used.free(c["id"], t)]
    probes = probes[:14]
    paths = list(ThreadPoolExecutor(2).map(
        lambda kp: _safe(grab, ff, kp[1][1], kp[1][2], os.path.join(bdir, f"{tag}_p{kp[0]}.jpg")),
        enumerate(probes)))
    ok, n_all = [], 0
    for p, f in zip(probes, paths):
        if not f:
            continue
        n_all += 1
        fs = faces(_imread(f))
        if fs and sim(fs[0][1], hosts) >= SAME_PERSON:
            continue                                  # a host: never
        ok.append((p, f, fs[0] if fs else None))
    if not ok:
        return None, f"no usable frames ({n_all} of {len(probes)} grabbed, the rest showed hosts)"
    parts = [{"type": "text", "text":
              f"""A vertical Breakfast Club clip cuts away for {m['dur']:.0f} s to full-screen B-roll while the speaker
says: "...{_context(tr, m['at'], m['dur'])}..."
The B-roll must show: {m['subject']} ({m['kind']}). Ideal footage: {m['footage']}.
Candidate frames from YouTube videos follow (#index, video title, time)."""}]
    parts += _ref_parts(refs)
    for k, ((c, v, t), f, _) in enumerate(ok):
        parts += [{"type": "text", "text": f"#{k}: \"{c['title']}\" at {t:.0f}s"}, _img(f)]
    parts.append({"type": "text", "text": """Rank up to 3 frames that clearly show the subject itself (the person's
face / them playing / a photo of them, the team in action, the place, the event) and would look good full-screen in
a 9:16 crop. Reject: frames whose main person is a REFERENCE person or someone else, logos, title cards, mostly
text, black or blurry frames, other shows' talk-show hosts. A title naming the subject does NOT mean the frame shows
them. If nothing really fits, return an empty list.
Answer JSON: {"picks": [{"i": <index>, "score": <0-10 how clearly it shows the subject>, "who": "<who/what is
shown>", "x": <horizontal centre of the subject, 0 = left edge, 1 = right edge>}]}"""})
    ans = chat(budget, parts, max_tokens=3000)
    picks = [p for p in ans.get("picks") or [] if isinstance(p, dict) and isinstance(p.get("i"), (int, float))
             and 0 <= int(p["i"]) < len(ok) and float(p.get("score") or 0) >= 7]
    if not picks:
        return None, "no frame shows it"
    why = []
    for p in picks:
        (c, v, t), f, face = ok[int(p["i"])]
        if not used.free(c["id"], t):
            continue
        fx = float(np.clip(float(p.get("x") or 0.5), 0, 1))
        anchor = face[1] if face is not None and m["kind"] == "person" else None
        out = os.path.join(bdir, f"{c['id']}_{t:.1f}_{m['dur']:.1f}.mp4")
        try:
            r = cut_clip(ff, v, t, m["dur"], m["kind"], fx, anchor, hosts, out)
        except Exception as e:
            why.append(f"cut failed ({e})"[:120]); continue
        if not r:
            why.append(f"#{int(p['i'])}: under {MIN_DUR:.0f} s of usable shots"); continue
        chk = clip_frames(ff, out, r["frames"], bdir, tag)
        if m["kind"] == "person":
            rule = ("Face recognition has already matched the person across the clip, so you don't have to be sure "
                    "who it is. Usable: it shows the subject or plausibly them (a close-up, them playing in their "
                    "team's uniform, at their press conference, ...); other people in the frame are fine. NOT "
                    "usable: the main person is a REFERENCE person or clearly someone else (a named different "
                    "person, another show's host), or the subject's face is cut off.")
        else:
            rule = ("No person has to be in it: city views, buildings, streets, a stadium, game action or crowds "
                    "are what this B-roll is for. Usable: it plausibly shows that place / team / event. NOT "
                    "usable: it is clearly somewhere / something else, or its main person is a REFERENCE person "
                    "or a talk-show host, or it is a YouTuber / vlogger / reporter talking to the camera.")
        q = [{"type": "text", "text": f"First, middle and last frame of a {r['frames'] / FPS:.1f} s B-roll clip "
              f"(9:16) about {m['subject']} ({m['kind']}). The video is \"{c['title']}\". {rule} Never usable: "
              "a black frame, black bars, a logo / title card, mostly text, or burned-in subtitles / captions."}] + \
            [_img(x) for x in chk] + _ref_parts(refs) + [{"type": "text", "text":
             'Is this usable B-roll for that subject? Answer JSON: {"who": "<who/what the clip shows>", '
             '"ok": true|false, "reason": "<short>"}'}]
        chk_ans = chat(budget, q, max_tokens=1500)
        if not chk_ans.get("ok"):
            os.remove(out); why.append(f"final check: {chk_ans.get('who', '')}; {chk_ans.get('reason', '')}"[:160])
            continue
        if not used.take(c["id"], r["src_t"], r["src_t"] + r["frames"] / FPS):
            os.remove(out); why.append("footage already used"); continue
        item = {"at": m["at"], "dur": round(r["frames"] / FPS, 2), "frames": r["frames"], "file": out,
                "subject": m["subject"], "kind": m["kind"], "phrase": m["phrase"], "why": m["why"],
                "src": {"id": c["id"], "title": c["title"], "t": r["src_t"], "shots": r["shots"]},
                "thumb": chk[1], "shows": chk_ans.get("who", p.get("who", ""))}
        return item, f"ok ({item['dur']:.1f}s from \"{c['title'][:60]}\")"
    return None, "; ".join(why) or "picked footage already used"


def find(ff, tr, title, limit_t, work, sentences, log=print, src=None, ref_times=()):
    """-> (items for edits["broll"] or None when the picker failed (try again next run), USD spent, notes).
    ref_times: times in src showing the people already on screen (their faces are never used as B-roll)."""
    bdir = os.path.join(work, "broll"); os.makedirs(bdir, exist_ok=True)
    budget = Budget(os.path.join(bdir, "cost.json"))
    refs, hosts = [], []
    for k, t in enumerate(ref_times):
        img = _safe(_read, ff, src, t)
        if img is None:
            continue
        hosts += [e for _, e, _ in (_safe(faces, img) or [])[:2]]
        if k < 4:                             # a few reference pictures for the vision model too
            import cv2
            p = os.path.join(bdir, f"ref{k}.jpg")
            _imwrite(p, cv2.resize(img, (360, int(360 * img.shape[0] / img.shape[1]))))
            refs.append(p)
    try:
        moments, want, gaps = pick_moments(budget, tr, title, limit_t, sentences)
    except OutOfBudget as e:
        return [], budget.spent, [str(e)]
    except Exception as e:
        return None, budget.spent, [f"moment picker failed: {e}"[:300]]
    log(f"b-roll: {len(moments)} candidate moments for {want} cutaways: " +
        ", ".join(f"{m['at']:.0f}s {m['subject']}" for m in moments))
    used, notes = Used(), []

    def one(km):
        k, m = km
        try:
            return find_one(ff, budget, m, tr, title, bdir, f"m{k}", refs, hosts, used)
        except OutOfBudget as e:
            return None, str(e)
        except Exception as e:
            return None, f"error: {e}"[:200]
    found, errors = [], 0
    for m, (it, why) in zip(moments, ThreadPoolExecutor(6).map(one, enumerate(moments))):
        notes.append(f"{m['at']:7.2f}s {m['subject']}: {why}")
        errors += why.startswith("error:")
        if it:
            found.append(it)
    if moments and errors == len(moments):          # broken, not "nothing fits": try again next run
        return None, budget.spent, notes
    again = fill_gaps(schedule(found, want), gaps)  # windows still empty: the main subject, other footage
    if again:
        log(f"b-roll: second pass for {len(again)} empty windows: " + ", ".join(f"{m['at']:.0f}s" for m in again))
    for m, (it, why) in zip(again, ThreadPoolExecutor(6).map(one, enumerate(again, len(moments)))):
        notes.append(f"{m['at']:7.2f}s {m['subject']} (2nd pass): {why}")
        if it:
            found.append(it)
    items = schedule(found, want)
    for it in found:
        if it not in items:
            notes.append(f"{it['at']:7.2f}s {it['subject']}: found but not used (too close to another cutaway)")
            if os.path.exists(it["file"]):
                os.remove(it["file"])
    return items, budget.spent, notes


def restore(ff, items, work):
    """re-cut clips whose file is gone (cache deleted) from the saved source id / time / crops; no LLM calls"""
    for it in items:
        if os.path.exists(it.get("file", "")) or not it.get("src"):
            continue
        s = it["src"]
        os.makedirs(os.path.join(work, "broll"), exist_ok=True)
        it["file"] = os.path.join(work, "broll", f"{s['id']}_{s['t']:.1f}_r.mp4")
        win = it["file"] + ".win.mp4"
        _window(ff, video_info(s["id"]), s["t"], 0, win, frames=it["frames"])
        _render(ff, win, win_w(ff, win), 0, it["frames"], s.get("shots") or [[0, s.get("x", 0.5)]], it["file"])
        os.remove(win)
    return items
