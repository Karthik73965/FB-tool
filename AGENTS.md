# Instructions for AI agents (Antigravity, Claude Code, Cursor, …)

This folder is a finished, tested tool. **Run it; don't rebuild it.** The user wants exactly this output style:
a vertical 1080×1920 clip with the speaker centered, word-highlight captions, a waveform card when nobody is on
screen, and no outro.

## Standard job: "reframe this video"

1. First time only: run `setup.bat` (creates `.venv`). Check: `.venv\Scripts\python -c "import reframe"`.
2. Run: `make_clip.bat "<full path to video.mp4>"`. Run it in the background or with a long timeout; it takes about
   3–4 minutes for a 6-minute video on an NVIDIA GPU. It prints stage timings, then `verify: OK`.
3. **Always check the ending.** Read `work/<name>/report.txt`, the last 90 s of transcript.
   Talk-show uploads usually end with: an ad read (e.g. "Call Top Dog Law…", "go to …com", often on a different
   set), then a bumper ("Hold up… every day I wake up… The Breakfast Club"), then a logo screen. The user wants all
   of that removed. Set `"end"` in `<name>.edits.json` (next to the video) to just after the last word of the real
   conversation and **before the first word of the ad/outro audio**, since the voice often starts slightly before
   the picture cut. Then run `make_clip.bat` again (only the last chunk re-renders, ~10 s).
4. Check the captions: skim the transcript in `work/<name>/transcript.json` for obviously misheard names
   (e.g. "bruh manager" when the speaker said "Rob manager") and add `{"at": <seconds>, "to": "<word>"}` entries
   under `"words"`. Ask the user if unsure.
5. Check the B-roll (needs `OPENROUTER_API_KEY`; without it there is none). The first run picks about one full-screen
   cutaway per 35 s, each 5–10 s, from YouTube (Breakfast Club channel first, then anywhere; `broll.py`: Claude
   Haiku via OpenRouter picks the moments, yt-dlp + captions find footage, an OpenCV face match keeps each shot on
   the named person and drops anything showing the hosts, the model checks the frames). A subject can come back,
   but never with the same footage. They're listed in `report.txt` and saved under `"broll"` in `<name>.edits.json`. **Look at a frame
   from the middle of each cutaway** (it must show the named person/event, not a host); delete bad entries and
   re-run. Delete the `"broll"` key to pick again; `"broll": []` turns it off. Spend is capped at $0.10 per video
   (`work/<name>/broll/cost.json`).
6. Look at a few frames to confirm: `ffmpeg -ss <t> -i <name>_9x16.mp4 -frames:v 1 check.png`, using the ffmpeg
   from `.venv\Scripts\python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"`.
7. Report: output path, final length, where it was cut and why, the B-roll used (and its cost), and the
   `verify.py` result.

## Desktop app

`app.py` wraps the same pipeline for people without Python (`packaging\build_app.bat` builds the Windows zip,
`.github/workflows/build-app.yml` builds Windows + macOS). It calls `reframe.run(..., model="large-v3-turbo",
auto_end=True)`: `detect_outro()` cuts ad reads / bumpers from the transcript, which the CLI leaves to you. If you
change `reframe.py`, rebuild the app and make sure its selftest still passes. B-roll in the app needs
`OPENROUTER_API_KEY` in the environment or the key in `<app data>\openrouter_key.txt`; never bundle a key in the
zip. The cloud worker reads it from the optional `OPENROUTER_API_KEY` in `cloud/.secrets.env` (push_secret.py).

## Cloud website

`cloud/` (Modal API + GPU worker, own venv `cloud/.venv`) and `web/` (Next.js on Vercel) wrap the same pipeline for
invited users; see `cloud/README.md`. They don't touch the desktop app or CLI. The only hook in `reframe.py` is
`REFRAME_FFMPEG` (unset on desktop). Never deploy to Modal or run jobs there without the user's permission. After
changing `cloud/`, run `cloud\.venv\Scripts\python -m pytest cloud\tests -q`; after changing `web/`, run `npx next build`.

## Rules

- Don't change the look or the pipeline unless the user asks. If you do change the rendering, bump
  `RENDER_VERSION` in `reframe.py`.
- If `verify.py` reports flash frames, don't hand the clip over; investigate. Frame indices must come from ONE
  sequential decode (analysis) and timestamp-exact selects (render). Never index frames from `-ss` seek
  positions.
- Captions mask profanity on screen (N***A, SH*T, …) for Facebook; the audio is untouched. Keep that.
- Cache: `work/<name>/` holds transcript.json, analysis.json and rendered chunks. Delete `analysis.json` after
  changing analysis code, and `transcript.json` to re-transcribe.
- Output format: H.264 High, yuv420p, 29.97 fps, AAC 48 kHz 192 kbps, faststart. That's correct for Facebook.
