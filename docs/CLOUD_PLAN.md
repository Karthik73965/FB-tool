# Cloud version: plan

**Goal:** a private website where you and a few invited people upload a raw talk-show video or paste a link, and
get back the same vertical 1080×1920 clip the desktop app makes, downloaded from the page.

## Decisions so far

| | choice |
|---|---|
| Users | you + a few invited people (invite codes, no email) |
| Input | upload a file **or** paste a link |
| Compute | pay-per-video serverless GPU on Modal; nothing runs (or costs) when idle |
| Frontend | Next.js on Vercel (`web/`), deployed from GitHub |
| Output | download on the page; same ending / caption review as the desktop app; no emails |
| Isolation | cloud code only in `cloud/` and `web/`; desktop app and CLI unchanged |

## Architecture

```
 browser ──► Vercel: Next.js site (web/)
   │            │ HTTPS API calls (invite-code session token)
   │            ▼
   │         Modal (cloud/)                       ┌─────────────────────────────┐
   │  presigned upload URL                        │ api: FastAPI (CPU, tiny)    │
   └──────► Cloudflare R2 (uploads, clips) ◄─────►│ worker: reframe.run() on an │
                                                  │   NVIDIA L4 GPU             │
                                                  │ link fetch: yt-dlp (CPU)    │
                                                  └─────────────────────────────┘
```

- **Vercel** hosts the Next.js site only. It talks to the Modal API and holds no secrets beyond the API URL.
- **Modal** hosts the API and the GPU worker. It bills per second, includes $30 of free credit a month, and
  scales to zero when idle. Use an **L4** GPU: it has the NVENC video encoder (A100/H100 don't) and costs about
  $0.80 an hour.
- **Cloudflare R2** stores the files. The browser uploads straight to R2 through a presigned URL, so multi-GB
  files never pass through any server, and downloads are free (no egress fees). Files are deleted after 7 days.
  The bucket is created with Wrangler (already logged in); R2 API keys are stored as a Modal secret.
- **Login:** invite codes. The admin (you) creates one code per person; entering it gives a long-lived session
  token. Codes can be revoked one at a time.
- **Links** are fetched with `yt-dlp` before rendering. Only use links to videos you have the rights to repost;
  downloading from YouTube is against its terms of service.
- **Same pipeline:** the worker calls `reframe.run(..., auto_end=True)`, the same code as the desktop app.
  Ending and caption fixes re-render only the changed chunks, using the job's cache kept on a Modal Volume.

## Cost estimate

A 6-minute video takes about 4 minutes on an L4, roughly **$0.06–0.10 per video**, plus a few cents for the API.
50 videos a month comes to about $5, which the free credit covers. R2 and Vercel fit in their free tiers at this
size.

## Not interfering with the desktop app

- All cloud code is in `cloud/` (own venv `cloud/.venv`, own requirements) and `web/`. `make_clip.bat`, `app.py`,
  `setup.bat`, `packaging/` and the desktop `.venv` are untouched.
- `reframe.py` gets one opt-in hook: if the `REFRAME_FFMPEG` environment variable is set, use that ffmpeg. Unset (desktop,
  CLI) means the same behavior as today, so `RENDER_VERSION` stays the same. (Done; the desktop selftest still passes.)
- After the change: run the CLI on a test video (`verify: OK`, same output) and the app selftest.

## Changes the pipeline needs

1. Linux support in `reframe.py`: the `REFRAME_FFMPEG` env override above, pointing at an NVENC-capable ffmpeg in the
   container (the imageio-ffmpeg Linux build has no NVENC); `bin/ffmpeg_nvenc.exe` and the DLL paths are
   Windows-only and already skipped elsewhere.
2. Model: `large-v3` (best captions; the GPU makes the speed difference small) baked into the container image,
   so nothing downloads at run time.
3. Progress: the worker already prints `@@progress` lines (as in the desktop app); the API stores them per job
   and the page polls them.

## Build phases

1. **Worker:** container image (CUDA, ffmpeg with NVENC, Python deps, model). Run `reframe.run` on Modal
   against a test video and check timing, cost and the verify result.
2. **API + site:** invite-code login, upload (presigned R2) and paste-a-link, job list with live progress,
   download button. Next.js on Vercel.
3. **Review:** ending and caption fixes with quick re-render.
4. **Hardening:** limits (e.g. 30 min and 4 GB per video, 3 jobs at a time per person), automatic cleanup,
   showing failed jobs clearly, an admin page for invite codes.

## Status

All four phases are built and tested locally; nothing is deployed yet. Setup and deploy steps:
[../cloud/README.md](../cloud/README.md).

- [x] Phase 0: GitHub repo, Wrangler login, Modal account + token
- [x] Phase 1: worker (`cloud/fbclip/worker.py`, GPU image in `cloud/modal_app.py`). Not run on Modal yet.
- [x] Phase 2: API (`cloud/fbclip/api.py`) + Next.js site (`web/`): invite codes, upload, links, live progress, download
- [x] Phase 3: ending + caption review with quick re-render
- [x] Phase 4: limits, 7-day cleanup cron, stuck-job detection, failure log on the page, invite-code admin page
- [ ] Deploy: R2 bucket + CORS, R2 API token, Modal secret, `modal deploy`, Vercel project (needs your go-ahead)
- [ ] First real job on Modal: check timing, cost, encoder (NVENC vs x264) and the glitch check
