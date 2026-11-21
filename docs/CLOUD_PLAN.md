# Cloud version: plan

**Goal:** a private website where you and a few invited people upload a raw talk-show video or paste a link, and
get back the same vertical 1080×1920 clip the desktop app makes. You can download it on the page, and you get an
email when it's ready.

## Decisions so far

| | choice |
|---|---|
| Users | you + a few invited people (invite-only, email login) |
| Input | upload a file **or** paste a link |
| Compute | pay-per-video serverless GPU; nothing runs (or costs) when idle |
| Output | download on the page + email when done; same ending / caption review as the desktop app |

## Architecture

```
 browser ──► web app (login, upload, job list, review)          Modal (one account, one deploy)
   │            │  presigned upload URL                         ┌─────────────────────────────┐
   │            ▼                                               │ web: FastAPI (CPU, tiny)    │
   └──────► Cloudflare R2  (raw uploads, finished clips) ◄─────►│ worker: reframe.run() on an │
                                                                │   NVIDIA L4 GPU             │
            Resend (email "your clip is ready")  ◄──────────────│ link fetch: yt-dlp (CPU)    │
                                                                └─────────────────────────────┘
```

- **Modal** hosts both the small web app and the GPU worker. It bills per second, includes $30 of free credit a
  month, and scales to zero when idle. Use an **L4** GPU: it has the NVENC video encoder (A100/H100 don't) and
  costs about $0.80 an hour.
- **Cloudflare R2** stores the files. The browser uploads straight to R2 through a presigned URL, so multi-GB
  files never pass through the web server, and downloads are free (no egress fees). Files are deleted after 7
  days.
- **Links** are fetched with `yt-dlp` before rendering. Only use links to videos you have the rights to repost;
  downloading from YouTube is against its terms of service.
- **Login:** a magic link sent by email (Resend) to addresses on an invite list. No passwords.
- **Same pipeline:** the worker calls `reframe.run(..., auto_end=True)`, the same code as the desktop app.
  Ending and caption fixes re-render only the changed chunks, using the job's cache kept on a Modal Volume.

## Cost estimate

A 6-minute video takes about 4 minutes on an L4, roughly **$0.06–0.10 per video**, plus a few cents for the web
app. 50 videos a month comes to about $5, which the free credit covers. R2 and Resend fit in their free tiers at
this size.

## Changes the pipeline needs

1. Linux support in `reframe.py`: an `FFMPEG` env override that points at an NVENC-capable ffmpeg in the
   container (the imageio-ffmpeg Linux build has no NVENC); `bin/ffmpeg_nvenc.exe` and the DLL paths are
   Windows-only and already skipped elsewhere.
2. Model: `large-v3` (best captions; the GPU makes the speed difference small) baked into the container image,
   so nothing downloads at run time.
3. Progress: the worker already prints `@@progress` lines (as in the desktop app); the web app stores them per
   job and the page polls them.

## Build phases

1. **Worker:** container image (CUDA, ffmpeg with NVENC, Python deps, model). Run `reframe.run` on Modal
   against a test video and check timing, cost and the verify result.
2. **Web app:** invite-only login, upload (presigned R2) and paste-a-link, job list with live progress,
   download button.
3. **Review + email:** ending and caption fixes with quick re-render; "clip ready" email with a link.
4. **Hardening:** limits (e.g. 30 min and 4 GB per video, 3 jobs at a time per person), automatic cleanup,
   failure emails, a simple admin page for the invite list.

## What's needed from you to start

- Accounts: **Modal** (GitHub login), **Cloudflare** (R2, needs a card on file but has a free tier), **Resend**
  (email).
- Optional: a domain name (e.g. `clips.yourdomain.com`); otherwise the site lives on a `*.modal.run` address.
- The email addresses to invite.
