# FB Clip Maker: cloud version

The website version of the clip maker. People with an invite code upload a video, or paste a link, on a site hosted
on Vercel (`web/`). A cloud GPU on Modal runs the same `reframe.py` as the desktop app, and the clip comes back to the
page for review and download. See [../docs/WEBSITE.md](../docs/WEBSITE.md) for the overview.

```
web/            Next.js site (Vercel): login, upload / link, job list, review, invite codes
cloud/
  modal_app.py  Modal: API (FastAPI), GPU renderer (L4), link fetcher, daily cleanup
  fbclip/       the code those run: api.py, worker.py, jobs.py, review.py, auth.py, store.py
  dev_server.py run everything on this PC, no Modal / R2 (uses the desktop .venv for renders)
  tests/        API tests (no network, no GPU)
```

**The desktop app is unaffected.** The cloud code only lives in `cloud/` and `web/`, with its own venv
(`cloud/.venv`). The one hook in `reframe.py` is `REFRAME_FFMPEG`, which does nothing unless it's set.

## Run it locally (free, uses your GPU)

```
cloud\.venv\Scripts\python cloud\dev_server.py          (API on :8000, prints the admin key)
cd web && copy .env.example .env.local && npm run dev   (site on :3000)
```
Log in with the admin key, create invite codes under **Access**, and upload a video. Files go to `cloud/.dev/`.

Tests: `cloud\.venv\Scripts\python -m pytest cloud\tests -q`

## Deploy (one time)

### 1. Cloudflare R2 (storage)
```
npx wrangler r2 bucket create fbclip
```
Put your Vercel address in `cloud/r2-cors.json` (replace `YOUR-SITE`; you get the address in step 3, so come back
then), and run:
```
npx wrangler r2 bucket cors set fbclip --file cloud/r2-cors.json
```
API keys: Cloudflare dashboard → R2 → **Manage API tokens** → Create → *Object Read & Write*, bucket `fbclip`. Note
the **Access Key ID** and **Secret Access Key**. Your account ID is `5fd5c79d8b2f112743136f8c0207db41`.

### 2. Modal (API + GPU)
Create the secret (pick a long random admin key: it's your admin login):
```
cloud\.venv\Scripts\modal secret create fbclip ^
  R2_ACCOUNT_ID=5fd5c79d8b2f112743136f8c0207db41 R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=fbclip ^
  ADMIN_KEY=<long random string> SESSION_SECRET=<another long random string> ^
  ALLOWED_ORIGINS=https://YOUR-SITE.vercel.app
```
Deploy:
```
cloud\.venv\Scripts\modal deploy cloud/modal_app.py
```
The first deploy builds the GPU image (CUDA, ffmpeg with NVENC, the large-v3 model), which takes about 10 minutes.
It prints the API address: `https://karthik73965--fbclip-api.modal.run`. Nothing runs, or costs anything, until a
video is submitted.

### 3. Vercel (website)
vercel.com → **Add New Project** → import `Karthik73965/FB-tool` → **Root Directory: `web`** → environment variable
`NEXT_PUBLIC_API_URL=https://karthik73965--fbclip-api.modal.run` → Deploy. Then put the Vercel address into
`cloud/r2-cors.json` and the `ALLOWED_ORIGINS` secret (and re-run those two commands). Changing a secret needs a
redeploy (`modal deploy`).

## How it works

- **Upload:** `POST /jobs/upload` creates a job and returns a presigned R2 PUT URL. The browser uploads straight to
  R2, then calls `/jobs/<id>/start`, which spawns `render` on an L4 GPU.
- **Link:** `POST /jobs/link` spawns `fetch` (CPU, yt-dlp) into the job's folder on the `fbclip-work` volume, then
  `render`.
- **Render:** `worker.render` copies the source to the volume, checks it (16:9, ≤ 30 min), and runs `reframe.py`
  with `--auto-end` as a subprocess. It turns `@@progress` lines into the job's progress, runs `verify.check`, and
  uploads `clip.mp4`, `transcript.json` and `report.txt` to R2. A clip with flash frames is marked failed and never
  offered for download (same as the desktop app).
- **Review:** ending and caption fixes use the desktop app's logic (`review.py`). Apply re-renders using the cache on
  the volume, so only the changed chunks render.
- **Records:** jobs, users and invite codes are JSON files in R2 under `meta/`.
- **Limits:** 3 active jobs per person, 4 GB, 30 min, at most 4 GPUs at once. Everything is deleted after 7 days
  (cron at 04:00 UTC). A job with no progress for 90 min is marked failed.
- If the container can't use CUDA decoding, the worker turns it off (`FBCLIP_HWDEC=0`). If NVENC isn't available,
  `reframe.pick_encoder` falls back to x264 by itself. The first real job will show which one ran (`encoder` in
  the job's result).

## Cost

About $0.06–0.10 per 6-minute video on an L4 (~$0.80/h, billed per second), and $0 when idle. Modal's $30/month free
credit covers roughly 300 videos. R2 and Vercel stay within their free tiers at this size.
