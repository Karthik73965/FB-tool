"""API tests on a temporary local store with a fake spawner (no network, no GPU, no Modal).

    cloud\\.venv\\Scripts\\python -m pytest cloud\\tests -q
"""
import os, sys, time
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fbclip import auth, jobs, worker
from fbclip.api import create_app
from fbclip.store import LocalStore

ADMIN = "test-admin-key-0001"


class FakeSpawner:
    def __init__(self):
        self.calls, self.cancelled = [], []

    def render(self, job_id):
        self.calls.append(("render", job_id))
        return f"call-{len(self.calls)}"

    def fetch(self, job_id):
        self.calls.append(("fetch", job_id))
        return f"call-{len(self.calls)}"

    def cancel(self, cid):
        self.cancelled.append(cid)


@pytest.fixture
def env(tmp_path):
    st = LocalStore(str(tmp_path / "store"), "http://dev")
    sp = FakeSpawner()
    c = TestClient(create_app(st, sp, ADMIN, "secret", origins=["http://localhost:3000"]))
    return st, sp, c


def login(c, code):
    r = c.post("/auth/login", json={"code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def invite(c, adm, name="Sam"):
    r = c.post("/admin/invites", json={"name": name}, headers=adm)
    assert r.status_code == 200
    return r.json()


def upload(st, c, h, name="Show.mp4"):
    r = c.post("/jobs/upload", json={"filename": name, "size": 10}, headers=h)
    assert r.status_code == 200, r.text
    jid = r.json()["job"]["id"]
    p = st.path(jobs.get(st, jid)["source"]["key"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(b"0123456789")
    return jid


def test_login_and_invites(env):
    st, sp, c = env
    assert c.post("/auth/login", json={"code": "nope"}).status_code == 401
    assert c.get("/jobs").status_code == 401
    adm = login(c, ADMIN)
    assert c.get("/me", headers=adm).json()["admin"] is True
    u = invite(c, adm)
    assert u["code"].startswith("FB-")
    h = login(c, u["code"].lower())                    # codes are case-insensitive
    assert c.get("/me", headers=h).json()["name"] == "Sam"
    assert c.get("/admin/invites", headers=h).status_code == 403
    assert [x["name"] for x in c.get("/admin/invites", headers=adm).json()] == ["Sam"]
    c.delete(f"/admin/invites/{u['id']}", headers=adm)
    assert c.get("/me", headers=h).status_code == 401  # old tokens stop working
    assert c.post("/auth/login", json={"code": u["code"]}).status_code == 401


def test_tokens():
    t = auth.sign({"sub": "x"}, "k")
    assert auth.verify(t, "k")["sub"] == "x"
    assert auth.verify(t, "other") is None
    assert auth.verify(t[:-2] + "AA", "k") is None


def test_upload_flow_and_limits(env):
    st, sp, c = env
    adm = login(c, ADMIN)
    h = login(c, invite(c, adm)["code"])
    assert c.post("/jobs/upload", json={"filename": "a.txt", "size": 1}, headers=h).status_code == 400
    assert c.post("/jobs/upload", json={"filename": "a.mp4", "size": jobs.MAX_BYTES + 1}, headers=h).status_code == 400
    r = c.post("/jobs/upload", json={"filename": "Ghost.mp4", "size": 5}, headers=h)
    assert c.post(f"/jobs/{r.json()['job']['id']}/start", headers=h).status_code == 400   # nothing uploaded
    ids = [upload(st, c, h, f"Show{i}.mp4") for i in range(3)]
    for jid in ids:
        r = c.post(f"/jobs/{jid}/start", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "queued"
    assert sp.calls == [("render", i) for i in ids]
    r = c.post("/jobs/upload", json={"filename": "Four.mp4", "size": 10}, headers=h)
    assert r.status_code == 429                                              # 3 at a time, refused before uploading
    r = c.post(f"/jobs/{ids[0]}/cancel", headers=h)
    assert r.json()["status"] == "cancelled" and sp.cancelled == ["call-1"]
    extra = upload(st, c, h, "Four.mp4")
    assert c.post(f"/jobs/{extra}/start", headers=h).status_code == 200
    assert c.post(f"/jobs/{ids[0]}/retry", headers=h).status_code == 429
    names = [j["name"] for j in c.get("/jobs", headers=h).json()]
    assert names == ["Four", "Show2", "Show1", "Show0", "Ghost"]
    # someone else can't see or touch them; the admin can
    h2 = login(c, invite(c, adm, "Alex")["code"])
    assert c.get("/jobs", headers=h2).json() == []
    assert c.get(f"/jobs/{ids[1]}", headers=h2).status_code == 404
    assert len(c.get("/jobs", headers=adm).json()) == 5


def test_link_and_bad_ids(env):
    st, sp, c = env
    h = login(c, ADMIN)
    assert c.post("/jobs/link", json={"url": "not a link"}, headers=h).status_code == 400
    r = c.post("/jobs/link", json={"url": "https://example.com/v/123"}, headers=h)
    assert r.status_code == 200 and sp.calls[-1][0] == "fetch"
    assert c.get("/jobs/../../meta", headers=h).status_code == 404
    assert c.get("/jobs/zzzzzzzzzzzz", headers=h).status_code == 404


TR = {"segments": [{"start": 0, "end": 4, "text": "", "words": [
    {"w": " Hello", "s": 0.0, "e": 0.4}, {"w": " bruh", "s": 0.5, "e": 0.8}, {"w": " manager.", "s": 0.9, "e": 1.3},
    {"w": " Call", "s": 2.6, "e": 2.9}, {"w": " Top", "s": 3.0, "e": 3.2}, {"w": " Dog", "s": 3.3, "e": 3.6}]}]}


def finished(st, jid):
    st.put_json(f"jobs/{jid}/transcript.json", TR)
    open(st.path(f"jobs/{jid}/transcript.json").replace("transcript.json", "clip.mp4"), "wb").write(b"x")
    jobs.update(st, jid, status="done", stage="done", result={"duration": 4.0, "end": 4.0, "end_reason": "full",
                                                                 "ad_start": None, "rendered_at": time.time()})


def test_review_fix_apply(env):
    st, sp, c = env
    h = login(c, ADMIN)
    jid = upload(st, c, h)
    c.post(f"/jobs/{jid}/start", headers=h)
    assert c.get(f"/jobs/{jid}/review", headers=h).status_code == 409
    finished(st, jid)
    j = c.get(f"/jobs/{jid}", headers=h).json()
    assert j["download_url"] and "Show_9x16.mp4" in j["download_url"]
    r = c.get(f"/jobs/{jid}/review", headers=h).json()
    assert [s["text"] for s in r["sentences"]] == ["Hello bruh manager.", "Call Top Dog"]
    r = c.post(f"/jobs/{jid}/fix", json={"edits": r["edits"], "si": 0, "text": "Hello Rob manager."}, headers=h).json()
    assert r["edits"]["words"] == [{"at": 0.5, "to": "Rob"}]
    assert r["sentences"][0]["fixed"] and r["sentences"][0]["text"] == "Hello Rob manager."
    edits = dict(r["edits"], end=1.6)
    assert c.post(f"/jobs/{jid}/fix", json={"edits": edits, "si": 9, "text": "x"}, headers=h).status_code == 400
    r = c.post(f"/jobs/{jid}/apply", json={"edits": edits}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert jobs.get(st, jid)["edits"]["end"] == 1.6 and sp.calls[-1] == ("render", jid)
    assert c.post(f"/jobs/{jid}/apply", json={"edits": edits}, headers=h).status_code == 409   # already running
    assert c.delete(f"/jobs/{jid}", headers=h).json() == {"ok": True}
    assert st.keys(f"jobs/{jid}/") == [] and jobs.get(st, jid) is None


def test_clip_with_bad_frames_is_not_handed_out(env):
    st, sp, c = env
    h = login(c, ADMIN)
    jid = upload(st, c, h)
    c.post(f"/jobs/{jid}/start", headers=h)
    finished(st, jid)
    jobs.update(st, jid, status="failed", result=dict(jobs.get(st, jid)["result"], problems=[12.3]))
    j = c.get(f"/jobs/{jid}", headers=h).json()
    assert j["download_url"] is None and j["video_url"] is None
    assert c.get(f"/jobs/{jid}/review", headers=h).status_code == 200        # the ending can still be fixed


def test_progress_and_stale(env):
    st, sp, c = env
    assert jobs.progress("work", {"transcribe": 1, "analyze": 1, "render": 1})[0] == 90
    assert jobs.progress("work", {"transcribe": 0.5})[1] == "Transcribing and tracking faces"
    h = login(c, ADMIN)
    jid = upload(st, c, h)
    c.post(f"/jobs/{jid}/start", headers=h)
    j = jobs.get(st, jid)
    j["updated"] -= jobs.STALE_SECONDS + 1
    st.put_json(jobs.key(jid), j)
    assert c.get(f"/jobs/{jid}", headers=h).json()["status"] == "failed"


def test_cancelled_job_stays_cancelled(env):
    st, sp, c = env
    h = login(c, ADMIN)
    jid = upload(st, c, h)
    c.post(f"/jobs/{jid}/start", headers=h)
    c.post(f"/jobs/{jid}/cancel", headers=h)
    assert jobs.update(st, jid, stage="work", parts={"render": 0.5}) is None
    assert jobs.get(st, jid)["status"] == "cancelled"


def test_worker_reports_failures(env, tmp_path):
    st, sp, c = env
    h = login(c, ADMIN)
    jid = c.post("/jobs/upload", json={"filename": "Gone.mp4", "size": 5}, headers=h).json()["job"]["id"]
    jobs.update(st, jid, status="queued")
    worker.render(st, jid, str(tmp_path / "work"))                     # the upload isn't there
    j = jobs.get(st, jid)
    assert j["status"] == "failed" and "Something went wrong" in j["error"]


def test_cleanup(env, tmp_path):
    st, sp, c = env
    h = login(c, ADMIN)
    old, new = upload(st, c, h, "Old.mp4"), upload(st, c, h, "New.mp4")
    for jid in (old, new):
        jobs.update(st, jid, status="done")
    j = jobs.get(st, old)
    j["created"] -= (jobs.KEEP_DAYS + 1) * 86400
    st.put_json(jobs.key(old), j)
    work = tmp_path / "work"
    for d in (old, new, "orphan"):
        (work / d).mkdir(parents=True)
    assert jobs.cleanup(st, str(work)) == [old]
    assert sorted(os.listdir(work)) == [new]
    assert st.keys(f"uploads/{old}/") == [] and st.keys(f"uploads/{new}/")
