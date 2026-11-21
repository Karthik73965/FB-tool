# FB Clip Maker website: what we're building

A private website where you and a few invited people upload a raw talk-show video, or paste a link, and get back
the same vertical 1080×1920 clip the desktop app makes. It runs on the same `reframe.py` pipeline, on a rented GPU
in the cloud. For the technical plan, costs and build phases, see [CLOUD_PLAN.md](CLOUD_PLAN.md).

## The pieces

### 1. GPU worker (on Modal): the main part
The `reframe.py` pipeline, packaged to run on a cloud NVIDIA L4 GPU:
- It receives a job: "render this video".
- It transcribes the audio, tracks the speaker's face, renders captions and the waveform card, cuts the ad at the
  end, and runs the glitch check.
- It saves the finished `_9x16.mp4` to storage and reports progress as it goes.
- The GPU only switches on while a video is rendering: about **$0.06–0.10 per video**, and $0 when nobody's
  using it.

### 2. Backend API (on Modal): small, no GPU
The part the website talks to:
- **Login:** invite codes. You create one code per person on an admin page; they enter it once and stay logged
  in. No passwords, no emails.
- **Upload:** gives your browser a secure link to upload the raw video straight to storage. Big files never go
  through the server.
- **Paste a link:** a small job downloads the video first.
- **Jobs:** starts the GPU worker and reports the job list and progress.
- **Review:** the same ending and caption fixes as the desktop app, with a quick re-render.

### 3. Website (on Vercel)
A Next.js site (`web/`): login, upload, job list with a progress bar, review, and download. Vercel deploys it from
the GitHub repo.

### 4. Storage
- **Cloudflare R2:** stores the files (uploads and finished clips, deleted after 7 days). Cheap, and downloads are
  free.

There are no emails. You see that a clip is ready on the job list.

## How a job flows

```
You (phone/PC) ──► website (Vercel, Next.js) ──API──► backend (Modal, small)
      │                                                   │ starts job
      │ upload straight to R2                             ▼
      ▼                                       GPU worker (Modal, L4 GPU)
 Cloudflare R2  ◄──────── finished clip ────── runs reframe.py → clip
      │
      └──► download button on the job list
```

## Keeping the desktop app safe
All cloud code lives in `cloud/` (with its own `cloud/.venv`) and `web/`. The desktop app, `make_clip.bat` and
`app.py` don't use them. The only change to `reframe.py` is an optional `FFMPEG` environment variable that does
nothing unless it's set.

## What's needed to start
- Accounts: **Modal** (done), **Cloudflare** (Wrangler logged in), **Vercel**
- GitHub repo: https://github.com/Karthik73965/FB-tool

Only use pasted links for videos you have the rights to repost; downloading from YouTube is against its terms of
service.
