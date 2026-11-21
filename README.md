# Reframe tool: talk-show clips → vertical Facebook videos

Turns a 16:9 talk-show/podcast clip into a **1080×1920 (9:16)** video ready for Facebook Reels and feed:

- **Speaker always centered.** Face tracking with a smooth "camera operator" follow and hard cuts on camera cuts. When several people are in the shot, it follows whoever's mouth is moving.
- **Word-by-word captions.** Bold Montserrat Black, the active word turns yellow, and each group of words pops in. Whisper large-v3 does the transcription. Profanity is masked on screen only; the audio is untouched.
- **Waveform card when nobody's on camera.** Still photos, graphic inserts and logo screens are shown as a rounded card over a blurred background, with an animated audio waveform below.
- **Feed-safe.** Faces, captions and the waveform stay inside the middle 4:5 band, so nothing important is lost when the feed crops the video.
- **Fast.** About 3–4 minutes for a 5–6 minute video on an RTX 3050 Ti laptop. It uses the GPU for transcription, decoding and encoding, and renders in parallel chunks.

## Requirements

- Windows 10/11
- Python **3.10, 3.11 or 3.12** (https://www.python.org/downloads/; tick "Add to PATH")
- An NVIDIA GPU is recommended. Without one it still works, just much slower: CPU transcription takes about 20 minutes.
- About 6 GB free disk space. The first run also downloads the Whisper model (~3 GB).

## Setup (once)

Double-click `setup.bat`, or run it in a terminal:

```bat
setup.bat
```

## Make a clip

```bat
make_clip.bat "C:\path\to\Video.mp4"
```

Output: `Video_9x16.mp4` next to the source. When the render finishes, `make_clip.bat` also runs `verify.py`, which checks every camera cut for glitch frames.

## Desktop app (to share with friends)

`FB Clip Maker` is the same pipeline in a window, for people without Python. Add videos and they render one
after another, fully automatic: speaker crop, captions, and the ad read / bumper at the end is detected from the
transcript and cut off. Afterwards the review panel can move the ending or fix misheard caption words, and a
re-render takes seconds.

- **Windows:** unzip `FBClipMaker-Windows.zip` anywhere and run `FBClipMaker.exe`. On an NVIDIA laptop the
  first video downloads the GPU pack (~1 GB, cuBLAS/cuDNN) and the speech model (~1.6 GB, Whisper
  large-v3-turbo). Without NVIDIA everything runs on the CPU (slower). If Windows SmartScreen warns, click
  *More info → Run anyway* (the app isn't code-signed).
- **macOS (Apple Silicon, macOS 12+):** unzip `FBClipMaker-macOS.zip`, move the app to Applications, then
  **right-click → Open** the first time (it isn't notarized by Apple). Runs on the CPU, so expect roughly
  10–20 minutes for a 6-minute video.
- Output: `<video>_9x16.mp4` next to the original. Cache and settings live in `%LOCALAPPDATA%\FBClipMaker`
  (Windows) or `~/Library/Application Support/FBClipMaker` (macOS); delete that folder to free space.

**Building it.** Windows, on this PC: `packaging\build_app.bat` → `dist\FBClipMaker-Windows.zip`. Both
platforms: push this folder to a GitHub repo; `.github/workflows/build-app.yml` builds the Windows zip and the
Mac zip on every push (download them from the run's *Artifacts*), and pushing a tag like `v1.0` also publishes
them as a GitHub Release. Each build runs a selftest that checks the models, fonts and ffmpeg made it in.

| file | purpose |
|---|---|
| `app.py` | the window, the job queue, and the render engine it runs as a child process |
| `gpu_pack.py` | downloads the NVIDIA DLLs on first use (Windows app only) |
| `packaging/` | PyInstaller spec, build scripts, icon, pinned build requirements |

## Fixing things (edits file)

The first run creates `Video.edits.json` next to the source. Edit it and run `make_clip.bat` again. Only the parts that changed get re-rendered, usually in seconds.

```json
{
  "end": 321.3,
  "words": [{"at": 75.92, "to": "Rob"}],
  "wave": [[41.0, 60.0]],
  "face": [[10.0, 12.5]]
}
```

| key | what it does |
|---|---|
| `end` | Cut the video at this second (removes the outro, ad reads, bumpers). If it's `null`, a trailing logo screen is trimmed automatically, but an ad read or bumper before it isn't. Always check the ending. |
| `words` | Fix a misheard caption word: `at` = the word's time in seconds (±0.6 s), `to` = the correct text. |
| `wave` | Force the waveform layout for these time ranges `[start, end]`. |
| `face` | Force the speaker-crop layout for these time ranges. |
| `keep_outro` | Set to `true` to turn off automatic outro trimming. |

`work/<Video>/report.txt` lists the waveform sections, the camera cuts and the last 90 seconds of transcript with timestamps. Use it to find where the show ends and the ad or outro begins.

## Files

| file | purpose |
|---|---|
| `reframe.py` | the whole pipeline (analysis → plan → captions → parallel render → mux) |
| `verify.py` | glitch check for a finished clip |
| `make_clip.bat` / `setup.bat` | Windows helpers |
| `setup_helper.py` | fetches an ffmpeg build whose GPU encoder works with older NVIDIA drivers |
| `fonts/Montserrat-Black.ttf` | caption font (SIL Open Font License 1.1, https://openfontlicense.org) |
| `examples/` | edits files used for two real clips |
| `AGENTS.md` | instructions for AI coding agents (Antigravity, etc.) |

To change the look (caption size/position, colors, waveform), edit `reframe.py`: `CAP_Y`, `ASS_HEAD` and `ACCENT` in `render_chunk`. Then bump `RENDER_VERSION` so cached chunks get re-rendered.
