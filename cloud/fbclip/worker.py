"""The two background jobs. render: run reframe.py on a job's video, check the result with verify.py, upload the
clip. fetch: download a pasted link with yt-dlp into the job's work folder.
Both write progress into the job record, and both stop if the job is cancelled.

    python -m fbclip.worker render|fetch <job id>    (local dev; on Modal, modal_app.py calls these functions)
"""
import glob, json, os, re, subprocess, sys, time
from collections import deque
from . import REFRAME_DIR, jobs

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # folder holding fbclip/


class JobError(Exception):
    """a failure with a message meant for the person who submitted the video"""


class Cancelled(Exception):
    pass


def _sources(work):
    return [p for p in glob.glob(os.path.join(work, "source.*")) if not p.endswith((".part", ".ytdl", ".tmp"))]


def probe(ff, src):
    """(duration s, width, height) from ffmpeg's banner"""
    r = subprocess.run([ff, "-hide_banner", "-i", src], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r)
    v = re.search(r"Stream #.*Video:.*?(\d{2,5})x(\d{2,5})", r)
    if not m or not v:
        raise JobError("That file doesn't look like a video.")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), int(v.group(1)), int(v.group(2))


def cuda_decode_ok(ff, tmp_dir):
    """can this container decode with -hwaccel cuda? (reframe uses it whenever nvidia-smi works)"""
    p = os.path.join(tmp_dir, "hwprobe.mp4")
    try:
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", p], check=True, capture_output=True)
        return subprocess.run([ff, "-v", "error", "-hwaccel", "cuda", "-i", p, "-f", "null", "-"],
                              capture_output=True).returncode == 0
    except (OSError, subprocess.CalledProcessError):
        return False
    finally:
        if os.path.exists(p):
            os.remove(p)


def _fail(store, job_id, e, tail=()):
    msg = str(e) if isinstance(e, JobError) else f"Something went wrong ({type(e).__name__}: {str(e)[:300]})"
    jobs.update(store, job_id, status="failed", error=msg, log_tail=list(tail)[-25:])


# ----------------------------------------------------------------------------------------------
def render(store, job_id, work_root, model="large-v3", workers=6, commit=lambda: None):
    job = jobs.update(store, job_id, status="running", stage="prepare", parts={}, error=None, log_tail=[])
    if job is None:
        return
    tail = deque(maxlen=60)
    try:
        _render(store, job, work_root, model, workers, commit, tail)
    except Cancelled:
        pass
    except Exception as e:
        _fail(store, job_id, e, tail)


def _render(store, job, work_root, model, workers, commit, tail):
    import reframe, verify
    job_id = job["id"]
    ff = reframe.ffmpeg_exe()
    work = os.path.join(work_root, job_id)
    os.makedirs(work, exist_ok=True)
    found = _sources(work)
    if found:
        src = found[0]
    else:
        ukey = job["source"]["key"]
        src = os.path.join(work, "source" + os.path.splitext(ukey)[1].lower())
        store.download_file(ukey, src + ".part")
        os.replace(src + ".part", src)
        commit()
        store.delete_prefix(f"uploads/{job_id}/")      # the work volume has it now
    dur, w, h = probe(ff, src)
    if dur > jobs.MAX_SECONDS:
        raise JobError(f"The video is {dur / 60:.0f} minutes long. The limit is {jobs.MAX_SECONDS // 60} minutes.")
    if abs(w / h - 16 / 9) > 0.05:                   # reframe scales any 16:9 source to 1920x1080
        raise JobError(f"The video is {w}×{h}. The clip maker needs a landscape 16:9 video (e.g. 1920×1080).")

    env = dict(os.environ, REFRAME_PROGRESS="1", PYTHONPATH=PKG_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
               FBCLIP_REFRAME_DIR=REFRAME_DIR)
    if os.name != "nt" and not cuda_decode_ok(ff, work):
        env["FBCLIP_HWDEC"] = "0"
        tail.append("CUDA decoding unavailable here; decoding on the CPU")
    edits = job["edits"]
    edits_path, cache, out = os.path.join(work, "edits.json"), os.path.join(work, "cache"), os.path.join(work, "clip.mp4")
    json.dump(edits, open(edits_path, "w"), indent=2)
    cmd = [sys.executable, "-m", "fbclip.run_reframe", src, "--out", out, "--work", cache, "--edits", edits_path,
           "--model", model, "--workers", str(workers), "--auto-end"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=env, cwd=work)
    parts, last = {}, 0.0
    jobs.update(store, job_id, stage="work", parts=parts)
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("@@progress "):
            _, st, f = line.split()
            parts[st] = float(f)
        elif line:
            tail.append(line)
            print(line, flush=True)
        if time.time() - last > 3:
            last = time.time()
            if jobs.update(store, job_id, stage="work", parts=dict(parts)) is None:
                proc.kill()
                raise Cancelled()
    if proc.wait():
        raise JobError("The renderer stopped with an error. The log below says where.")

    if jobs.update(store, job_id, stage="verify") is None:
        raise Cancelled()
    edits = json.load(open(edits_path))            # reframe adds the B-roll it picked (kept for re-renders)
    problems = verify.check(out, os.path.join(cache, "analysis.json"), edits)[3]
    res = json.load(open(os.path.join(cache, "result.json")))
    if jobs.update(store, job_id, stage="upload") is None:
        raise Cancelled()
    store.upload_file(out, f"jobs/{job_id}/clip.mp4", "video/mp4")
    for f, ct in (("transcript.json", "application/json"), ("report.txt", "text/plain")):
        store.upload_file(os.path.join(cache, f), f"jobs/{job_id}/{f}", ct)
    commit()
    # like the desktop app: never hand over a clip with flash / garbage frames (the API hides its download)
    bad = {"status": "failed", "error": f"The glitch check found {len(problems)} bad frames, so this clip shouldn't "
           "be posted. Try changing the ending and re-rendering, or tell the admin."} if problems else {"status": "done"}
    jobs.update(store, job_id, stage="done", log_tail=list(tail)[-25:], renders=job.get("renders", 0) + 1, **bad,
                edits=edits,
                result={"duration": res["duration"], "end": res["end"], "end_reason": res["end_reason"],
                        "ad_start": res["ad_start"], "encoder": res["encoder"], "device": res["device"],
                        "problems": problems, "size": os.path.getsize(out), "rendered_at": time.time(),
                        "broll": res.get("broll", 0), "broll_cost": res.get("broll_cost", 0),
                        "edits": edits})


# ----------------------------------------------------------------------------------------------
def fetch(store, job_id, work_root, ffmpeg=None, commit=lambda: None):
    """-> True when the video is in the work folder and the job is queued for rendering"""
    job = jobs.update(store, job_id, status="fetching", stage="fetch", parts={"fetch": 0}, error=None)
    if job is None:
        return False
    try:
        _fetch(store, job, work_root, ffmpeg)
        commit()
        return jobs.update(store, job_id, status="queued", stage="", parts={}) is not None
    except Cancelled:
        return False
    except Exception as e:
        if type(e).__name__ == "DownloadError":
            e = JobError("Couldn't download that link: " + re.sub(r"^ERROR:\s*", "", str(e))[:300])
        _fail(store, job_id, e)
        return False


def _fetch(store, job, work_root, ffmpeg):
    import yt_dlp
    job_id, url = job["id"], job["source"]["url"]
    work = os.path.join(work_root, job_id)
    os.makedirs(work, exist_ok=True)
    if _sources(work):            # retry after a failed render: already downloaded
        return
    last = [0.0]

    def hook(d):
        if d.get("status") != "downloading" or time.time() - last[0] < 3:
            return
        last[0] = time.time()
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        frac = min(1.0, d.get("downloaded_bytes", 0) / total) if total else 0
        if jobs.update(store, job_id, parts={"fetch": frac}) is None:
            raise Cancelled()

    opts = {"outtmpl": os.path.join(work, "source.%(ext)s"), "noplaylist": True, "quiet": True, "no_warnings": True,
            "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b", "merge_output_format": "mp4",
            "max_filesize": jobs.MAX_BYTES, "progress_hooks": [hook], "retries": 5}
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False)
        if info.get("_type") == "playlist":
            raise JobError("That link is a playlist. Paste a link to a single video.")
        if (info.get("duration") or 0) > jobs.MAX_SECONDS:
            raise JobError(f"The video is {info['duration'] / 60:.0f} minutes long. "
                           f"The limit is {jobs.MAX_SECONDS // 60} minutes.")
        y.process_ie_result(info, download=True)
    found = _sources(work)
    if not found:
        raise JobError("Couldn't download that video. It may be private, or over 4 GB.")
    if os.path.getsize(found[0]) > jobs.MAX_BYTES:
        os.remove(found[0])
        raise JobError("The video is over 4 GB.")
    title = (info.get("title") or "").strip()
    if title:
        jobs.update(store, job_id, name=title[:100])


# ----------------------------------------------------------------------------------------------
if __name__ == "__main__":
    from . import store as store_mod
    st, work_root = store_mod.from_env(), os.environ["FBCLIP_WORK"]
    kind, jid = sys.argv[1], sys.argv[2]
    if kind == "render":
        render(st, jid, work_root, model=os.environ.get("FBCLIP_MODEL", "large-v3"))
    elif fetch(st, jid, work_root, ffmpeg=os.environ.get("REFRAME_FFMPEG")):
        # dev: hand over to the renderer's Python (the desktop .venv has the pipeline's packages)
        subprocess.Popen([os.environ.get("FBCLIP_RENDER_PYTHON", sys.executable), "-m", "fbclip.worker", "render", jid],
                         cwd=PKG_ROOT)
