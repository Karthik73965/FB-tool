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

### 2. Website backend (on Modal): small, no GPU
The part the browser talks to:
- **Login:** invite-only, by an email link. No passwords.
- **Upload:** gives your browser a secure link to upload the raw video straight to storage. Big files never go
  through the server.
- **Paste a link:** a small job downloads the video first.
- **Jobs:** starts the GPU worker and shows the job list and progress bar.
- **Review:** the same ending and caption fixes as the desktop app, with a quick re-render.
- **Notify:** sends the "your clip is ready" email.

### 3. Outside Modal
- **Cloudflare R2:** stores the files (uploads and finished clips, deleted after 7 days). Cheap, and downloads are
  free.
- **Resend:** sends the emails.

## How a job flows

```
You (phone/PC) ──upload or paste link──► website (Modal, small server)
                                              │ starts job
                                              ▼
                                  GPU worker (Modal, L4 GPU)
                                  runs reframe.py → clip
                                              │
                    clip saved to Cloudflare R2 ──► download button + email to you
```

## Why Modal
You write Python, run one deploy command, and it handles the GPUs, scaling to zero when idle, and the web
address. There are no servers to manage, and the free monthly credit ($30) covers roughly 300 videos.

## What's needed to start
- Accounts: **Modal**, **Cloudflare** (R2), **Resend**
- The email addresses to invite
- Optional: a domain name (otherwise the site lives on a `*.modal.run` address)

Only use pasted links for videos you have the rights to repost; downloading from YouTube is against its terms of
service.
