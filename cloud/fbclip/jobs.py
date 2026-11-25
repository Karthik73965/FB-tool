"""Job records (meta/jobs/<id>.json), limits, progress text and the 7-day cleanup."""
import os, secrets, shutil, time
from concurrent.futures import ThreadPoolExecutor

MAX_ACTIVE = 3                  # jobs queued / fetching / running at once, per person
MAX_BYTES = 4 * 1024 ** 3       # 4 GB per video
MAX_SECONDS = 30 * 60           # 30 min per video
KEEP_DAYS = 7
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"}
ACTIVE = ("queued", "fetching", "running")
# status: uploading -> queued -> (fetching ->) running -> done | failed | cancelled


def key(job_id):
    return f"meta/jobs/{job_id}.json"


def new(owner, owner_name, name, source):
    now = time.time()
    return {"id": secrets.token_hex(6), "owner": owner, "owner_name": owner_name, "name": name, "source": source,
            "status": "uploading" if source["kind"] == "upload" else "queued", "stage": "", "parts": {}, "pct": 0,
            "stage_text": "", "error": None, "log_tail": [], "edits": {"end": None, "words": [], "wave": [], "face": []},
            "result": None, "renders": 0, "call_id": None, "created": now, "updated": now}


def get(store, job_id):
    return store.get_json(key(job_id))


def save(store, job):
    job["updated"] = time.time()
    store.put_json(key(job["id"]), job)
    return job


def update(store, job_id, **fields):
    """read-modify-write; a cancelled job stays cancelled (returns None so the worker can stop)"""
    job = get(store, job_id)
    if job is None or (job["status"] == "cancelled" and fields.get("status") != "queued"):
        return None
    job.update(fields)
    if "parts" in fields or "stage" in fields:
        job["pct"], job["stage_text"] = progress(job["stage"], job["parts"])
    return save(store, job)


STALE_SECONDS = 90 * 60         # an active job with no progress for this long has died (renders time out at 60)


def get_live(store, job_id):
    """the job record, with a job that stopped reporting progress marked as failed"""
    j = get(store, job_id)
    if j and j["status"] in ACTIVE and time.time() - j["updated"] > STALE_SECONDS:
        j.update(status="failed", error="The job stopped responding. Press Retry to run it again.")
        save(store, j)
    return j


def all_jobs(store):
    ks = [k.rsplit("/", 1)[-1][:-5] for k in store.keys("meta/jobs/")]
    with ThreadPoolExecutor(16) as ex:
        return [j for j in ex.map(lambda i: get_live(store, i), ks) if j]


def progress(stage, parts):
    """(percent, text), weighted like the desktop app's progress bar"""
    if stage == "fetch":
        return int(4 * parts.get("fetch", 0)), "Downloading the video"
    if stage == "prepare":
        return 5, "Getting the video ready"
    if stage == "work":
        tr, an, rd = parts.get("transcribe", 0), parts.get("analyze", 0), parts.get("render", 0)
        frac = 0.6 * (tr + an) / 2 + 0.37 * rd
        txt = "Rendering" if "render" in parts else "Finding B-roll" if "broll" in parts else \
            "Rendering" if tr >= 1 and an >= 1 else "Transcribing and tracking faces"
        return int(5 + 88 * frac), txt
    if stage == "verify":
        return 95, "Checking for glitches"
    if stage == "upload":
        return 97, "Saving the clip"
    if stage == "done":
        return 100, "Ready"
    return 0, "Waiting for a GPU"


def cleanup(store, work_root, now=None):
    """delete jobs older than KEEP_DAYS (and uploads nobody finished after a day): records, files, cache"""
    now = now or time.time()
    gone = []
    for j in all_jobs(store):
        old = now - j["created"] > KEEP_DAYS * 86400
        stale_upload = j["status"] == "uploading" and now - j["created"] > 86400
        if (old and j["status"] not in ACTIVE) or stale_upload:
            store.delete_prefix(f"jobs/{j['id']}/")
            store.delete_prefix(f"uploads/{j['id']}/")
            store.delete(key(j["id"]))
            gone.append(j["id"])
    if work_root and os.path.isdir(work_root):
        live = {k.rsplit("/", 1)[-1][:-5] for k in store.keys("meta/jobs/")}
        for d in os.listdir(work_root):
            if d not in live:
                shutil.rmtree(os.path.join(work_root, d), ignore_errors=True)
    return gone
