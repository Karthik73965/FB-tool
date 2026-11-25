"""HTTP API for the website. Runs on Modal (modal_app.py) or locally (dev_server.py).

The browser sends `Authorization: Bearer <token>` from /auth/login. Videos never pass through here: uploads go
straight to storage through a presigned URL, and clips come back the same way.
"""
import os, re, threading, time
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from . import auth, jobs, review


class Login(BaseModel):
    code: str


class NewInvite(BaseModel):
    name: str


class NewUpload(BaseModel):
    filename: str
    size: int


class NewLink(BaseModel):
    url: str


class Fix(BaseModel):
    edits: dict
    si: int
    text: str


class Apply(BaseModel):
    edits: dict


def create_app(store, spawner, admin_key, session_secret, origins=(), origin_regex=None):
    """spawner: .render(job_id) / .fetch(job_id) -> call id, .cancel(call id)"""
    if not session_secret:
        raise RuntimeError("SESSION_SECRET is not set")
    app = FastAPI(title="FB Clip Maker API", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_origin_regex=origin_regex,
                       allow_methods=["*"], allow_headers=["*"], max_age=3600)
    transcripts, tr_lock = {}, threading.Lock()

    # ------------------------------------------------------------------ auth
    def user(authorization: str = Header(default="")):
        p = auth.verify(authorization.removeprefix("Bearer ").strip(), session_secret)
        if not p:
            raise HTTPException(401, "Please log in again.")
        if not p.get("admin"):
            u = store.get_json(f"meta/users/{p['sub']}.json")
            if not u or u.get("revoked"):
                raise HTTPException(401, "This invite code has been revoked.")
        return p

    def admin(u=Depends(user)):
        if not u.get("admin"):
            raise HTTPException(403, "Admins only.")
        return u

    @app.post("/auth/login")
    def login(body: Login):
        code = body.code.strip()
        if admin_key and len(code) >= 12 and auth.hmac.compare_digest(code, admin_key):
            p = {"sub": "admin", "name": "Admin", "admin": True}
        else:
            idx = store.get_json(f"meta/codes/{auth.code_hash(code)}.json")
            u = idx and store.get_json(f"meta/users/{idx['user']}.json")
            if not u or u.get("revoked"):
                time.sleep(0.5)                    # slows down guessing
                raise HTTPException(401, "That code doesn't work.")
            p = {"sub": u["id"], "name": u["name"], "admin": False}
        return {"token": auth.sign(p, session_secret), "user": {"id": p["sub"], "name": p["name"], "admin": p["admin"]}}

    @app.get("/me")
    def me(u=Depends(user)):
        return {"id": u["sub"], "name": u["name"], "admin": u["admin"],
                "limits": {"active": jobs.MAX_ACTIVE, "bytes": jobs.MAX_BYTES, "seconds": jobs.MAX_SECONDS,
                           "keep_days": jobs.KEEP_DAYS, "ext": sorted(jobs.VIDEO_EXT)}}

    # ------------------------------------------------------------------ invite codes (admin)
    @app.get("/admin/invites")
    def invites(_=Depends(admin)):
        us = [store.get_json(k) for k in store.keys("meta/users/")]
        counts = {}
        for j in jobs.all_jobs(store):
            counts[j["owner"]] = counts.get(j["owner"], 0) + 1
        return [dict(u, jobs=counts.get(u["id"], 0)) for u in sorted(filter(None, us), key=lambda u: -u["created"])]

    @app.post("/admin/invites")
    def create_invite(body: NewInvite, _=Depends(admin)):
        name = body.name.strip()[:60]
        if not name:
            raise HTTPException(400, "Give the person a name.")
        code = auth.new_code()
        u = {"id": auth.secrets.token_hex(5), "name": name, "code": code, "created": time.time(), "revoked": False}
        store.put_json(f"meta/users/{u['id']}.json", u)
        store.put_json(f"meta/codes/{auth.code_hash(code)}.json", {"user": u["id"]})
        return dict(u, jobs=0)

    @app.delete("/admin/invites/{uid}")
    def revoke(uid: str, _=Depends(admin)):
        u = store.get_json(f"meta/users/{uid}.json")
        if not u:
            raise HTTPException(404, "No such person.")
        store.delete(f"meta/codes/{auth.code_hash(u['code'])}.json")
        u["revoked"] = True
        store.put_json(f"meta/users/{uid}.json", u)
        return u

    # ------------------------------------------------------------------ jobs
    def view(j):
        v = {k: j.get(k) for k in ("id", "name", "status", "pct", "stage_text", "error", "created", "updated",
                                   "owner_name", "renders", "edits", "result")}
        s = j["source"]
        v["source"] = {"kind": s["kind"], "url": s.get("url"), "filename": s.get("filename"), "size": s.get("size")}
        v["log_tail"] = j.get("log_tail", []) if j["status"] == "failed" else []
        v["video_url"] = v["download_url"] = None
        if j.get("result") and not j["result"].get("problems"):      # a clip with bad frames is never handed out
            fn = re.sub(r'[\\/:*?"<>|]+', "_", j["name"]).strip() or "clip"
            v["video_url"] = store.get_url(f"jobs/{j['id']}/clip.mp4", f"{fn}_9x16.mp4", inline=True)
            v["download_url"] = store.get_url(f"jobs/{j['id']}/clip.mp4", f"{fn}_9x16.mp4")
        return v

    def own(job_id, u):
        j = jobs.get_live(store, job_id) if re.fullmatch(r"[0-9a-f]{12}", job_id) else None
        if not j or (j["owner"] != u["sub"] and not u.get("admin")):
            raise HTTPException(404, "No such job.")
        return j

    def check_active(u):
        n = sum(1 for j in jobs.all_jobs(store) if j["owner"] == u["sub"] and j["status"] in jobs.ACTIVE)
        if n >= jobs.MAX_ACTIVE:
            raise HTTPException(429, f"You already have {n} videos in progress. Wait for one to finish.")

    def launch(j, kind):
        try:
            j["call_id"] = getattr(spawner, kind)(j["id"])
        except Exception as e:
            j.update(status="failed", error=f"Couldn't start the {kind} job ({e}).")
        return jobs.save(store, j)

    @app.get("/jobs")
    def list_jobs(u=Depends(user)):
        js = [j for j in jobs.all_jobs(store) if u.get("admin") or j["owner"] == u["sub"]]
        return [view(j) for j in sorted(js, key=lambda j: -j["created"]) if j["status"] != "uploading"
                or time.time() - j["created"] < 86400]

    @app.post("/jobs/upload")
    def new_upload(body: NewUpload, u=Depends(user)):
        stem, ext = os.path.splitext(os.path.basename(body.filename))
        if ext.lower() not in jobs.VIDEO_EXT:
            raise HTTPException(400, "Upload a video file (" + ", ".join(sorted(jobs.VIDEO_EXT)) + ").")
        if body.size > jobs.MAX_BYTES:
            raise HTTPException(400, "The video is over 4 GB.")
        check_active(u)
        j = jobs.new(u["sub"], u["name"], stem[:100] or "video", {"kind": "upload", "filename": body.filename[:200],
                                                                 "size": body.size})
        j["source"]["key"] = f"uploads/{j['id']}/source{ext.lower()}"
        jobs.save(store, j)
        return {"job": view(j), "upload_url": store.put_url(j["source"]["key"])}

    @app.post("/jobs/{job_id}/start")
    def start(job_id: str, u=Depends(user)):
        j = own(job_id, u)
        if j["status"] != "uploading":
            raise HTTPException(409, "This job has already started.")
        size = store.size(j["source"]["key"])
        if size is None:
            raise HTTPException(400, "The upload didn't arrive. Try again.")
        if size > jobs.MAX_BYTES:
            store.delete(j["source"]["key"])
            raise HTTPException(400, "The video is over 4 GB.")
        check_active(u)
        j.update(status="queued", stage="", pct=0, stage_text="Waiting for a GPU")
        return view(launch(j, "render"))

    @app.post("/jobs/link")
    def new_link(body: NewLink, u=Depends(user)):
        url = body.url.strip()
        if not re.match(r"https?://[^\s/]+\.[^\s]+$", url) or len(url) > 2000:
            raise HTTPException(400, "That doesn't look like a link.")
        check_active(u)
        j = jobs.new(u["sub"], u["name"], re.sub(r"^https?://(www\.)?", "", url)[:60], {"kind": "link", "url": url})
        j["stage_text"] = "Waiting to download"
        return view(launch(j, "fetch"))

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str, u=Depends(user)):
        return view(own(job_id, u))

    @app.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str, u=Depends(user)):
        j = own(job_id, u)
        if j["status"] in jobs.ACTIVE:
            j.update(status="cancelled", stage_text="Cancelled")
            jobs.save(store, j)
            if j.get("call_id"):
                try:
                    spawner.cancel(j["call_id"])
                except Exception:
                    pass
        return view(j)

    @app.post("/jobs/{job_id}/retry")
    def retry(job_id: str, u=Depends(user)):
        j = own(job_id, u)
        if j["status"] not in ("failed", "cancelled"):
            raise HTTPException(409, "Only failed or cancelled jobs can be retried.")
        check_active(u)
        j.update(status="queued", stage="", pct=0, error=None, log_tail=[], stage_text="Waiting for a GPU")
        return view(launch(j, "fetch" if j["source"]["kind"] == "link" else "render"))

    @app.delete("/jobs/{job_id}")
    def delete(job_id: str, u=Depends(user)):
        j = own(job_id, u)
        if j["status"] in jobs.ACTIVE and j.get("call_id"):
            try:
                spawner.cancel(j["call_id"])
            except Exception:
                pass
        store.delete_prefix(f"jobs/{job_id}/")
        store.delete_prefix(f"uploads/{job_id}/")
        store.delete(jobs.key(job_id))        # the work cache goes with the next cleanup run
        return {"ok": True}

    # ------------------------------------------------------------------ review (ending + captions)
    def transcript(j):
        ck = (j["id"], (j.get("result") or {}).get("rendered_at"))
        with tr_lock:
            if ck not in transcripts:
                tr = store.get_json(f"jobs/{j['id']}/transcript.json")
                if tr is None:
                    raise HTTPException(409, "This job has no transcript yet.")
                if len(transcripts) > 50:
                    transcripts.clear()
                transcripts[ck] = tr
            return transcripts[ck]

    @app.get("/jobs/{job_id}/review")
    def get_review(job_id: str, u=Depends(user)):
        j = own(job_id, u)
        if not j.get("result"):
            raise HTTPException(409, "The clip isn't ready yet.")
        return {"result": j["result"], "edits": j["edits"], "sentences": review.review(transcript(j), j["edits"])}

    @app.post("/jobs/{job_id}/fix")
    def fix(job_id: str, body: Fix, u=Depends(user)):
        j = own(job_id, u)
        tr = transcript(j)
        n = len(review.reframe.sentences(tr))
        if not 0 <= body.si < n:
            raise HTTPException(400, "No such sentence.")
        edits, sentences = review.fix_caption(tr, review.clean_edits(body.edits), body.si, body.text[:2000])
        return {"edits": edits, "sentences": sentences}

    @app.post("/jobs/{job_id}/preview")
    def preview(job_id: str, body: Apply, u=Depends(user)):
        """sentences with unsaved edits applied (after an undo, a reset, ...)"""
        j = own(job_id, u)
        return {"sentences": review.review(transcript(j), review.clean_edits(body.edits))}

    @app.post("/jobs/{job_id}/apply")
    def apply(job_id: str, body: Apply, u=Depends(user)):
        j = own(job_id, u)
        if j["status"] in jobs.ACTIVE or j["status"] == "uploading":
            raise HTTPException(409, "Wait for the current render to finish.")
        check_active(u)
        j.update(edits=review.keep_broll(review.clean_edits(body.edits), body.edits, j.get("edits") or {}), status="queued", stage="", pct=0, error=None, log_tail=[],
                 stage_text="Waiting for a GPU")
        return view(launch(j, "render"))

    @app.get("/health")
    def health():
        return {"ok": True}

    return app
