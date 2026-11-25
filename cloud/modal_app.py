"""FB Clip Maker on Modal: the website's API, the GPU renderer, the link fetcher and the daily cleanup.

    cloud\\.venv\\Scripts\\modal deploy cloud/modal_app.py

Needs the Modal secret "fbclip" (see cloud/README.md). Nothing runs, or costs anything, until a video is submitted;
the API container sleeps when idle.
"""
from pathlib import Path
import modal

ROOT = Path(__file__).resolve().parent.parent
app = modal.App("fb-clip-maker")
work_vol = modal.Volume.from_name("fbclip-work", create_if_missing=True)   # per-job source + render cache
secret = modal.Secret.from_name("fbclip")
WORK = "/work"
SITE = "/usr/local/lib/python3.11/site-packages"
FFMPEG_URLS = [  # BtbN static Linux builds: NVENC / NVDEC / libass included
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
]


def _bake_model():
    from faster_whisper.utils import download_model
    download_model("large-v3")          # in the image, so nothing downloads while a job runs


def _code(img, render=False):
    img = (img.add_local_dir(ROOT / "cloud" / "fbclip", "/root/fbclip", ignore=["**/__pycache__/**"])
              .add_local_file(ROOT / "reframe.py", "/app/reframe.py").add_local_file(ROOT / "broll.py", "/app/broll.py"))
    if render:
        img = img.add_local_file(ROOT / "verify.py", "/app/verify.py").add_local_dir(ROOT / "fonts", "/app/fonts")
    return img


render_image = _code(
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "curl", "xz-utils", "ca-certificates")
    .run_commands(
        "mkdir -p /opt/ffmpeg && (" + " || ".join(f"curl -fsSL {u} -o /tmp/ff.tar.xz" for u in FFMPEG_URLS) + ")"
        " && tar -xJf /tmp/ff.tar.xz -C /opt/ffmpeg --strip-components=1 && rm /tmp/ff.tar.xz",
        "/opt/ffmpeg/bin/ffmpeg -hide_banner -encoders | grep -q h264_nvenc",
        "/opt/ffmpeg/bin/ffmpeg -hide_banner -filters | grep -q ' ass '",
        # B-roll face check (broll.FACE_MODELS; same files and checksums)
        "mkdir -p /opt/models && cd /opt/models"
        " && curl -fsSLO https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
        " && curl -fsSLO https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
        " && echo '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4  face_detection_yunet_2023mar.onnx' | sha256sum -c"
        " && echo '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79  face_recognition_sface_2021dec.onnx' | sha256sum -c")
    .pip_install_from_requirements(str(ROOT / "requirements.txt"))
    .pip_install("boto3==1.44.9")
    .env({"REFRAME_FFMPEG": "/opt/ffmpeg/bin/ffmpeg", "FBCLIP_REFRAME_DIR": "/app", "REFRAME_MODELS": "/opt/models",
          "LD_LIBRARY_PATH": f"{SITE}/nvidia/cublas/lib:{SITE}/nvidia/cudnn/lib",
          "NVIDIA_DRIVER_CAPABILITIES": "compute,utility,video"})
    .run_function(_bake_model),
    render=True)

api_image = _code(
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")                                  # yt-dlp merges video + audio with it
    .pip_install_from_requirements(str(ROOT / "cloud" / "requirements-api.txt"))
    .env({"FBCLIP_REFRAME_DIR": "/app"}))


def _store():
    from fbclip import store
    return store.from_env()


@app.function(image=render_image, gpu="L4", cpu=8.0, memory=16384, timeout=3600, max_containers=4,
              volumes={WORK: work_vol}, secrets=[secret],
              retries=modal.Retries(max_retries=1, initial_delay=5.0))   # only on a lost container: errors are caught
def render(job_id: str):
    from fbclip import worker
    work_vol.reload()
    worker.render(_store(), job_id, WORK, model="large-v3", workers=6, commit=work_vol.commit)
    work_vol.commit()


@app.function(image=api_image, cpu=2.0, memory=2048, timeout=1800, max_containers=4,
              volumes={WORK: work_vol}, secrets=[secret])
def fetch(job_id: str):
    from fbclip import jobs, worker
    st = _store()
    work_vol.reload()
    if worker.fetch(st, job_id, WORK, ffmpeg="/usr/bin/ffmpeg", commit=work_vol.commit):
        jobs.update(st, job_id, call_id=render.spawn(job_id).object_id)


@app.function(image=api_image, schedule=modal.Cron("0 4 * * *"), volumes={WORK: work_vol}, secrets=[secret],
              timeout=900)
def cleanup():
    from fbclip import jobs
    work_vol.reload()
    print("deleted:", jobs.cleanup(_store(), WORK))
    work_vol.commit()


class ModalSpawner:
    def render(self, job_id):
        return render.spawn(job_id).object_id

    def fetch(self, job_id):
        return fetch.spawn(job_id).object_id

    def cancel(self, call_id):
        modal.FunctionCall.from_id(call_id).cancel(terminate_containers=True)


@app.function(image=api_image, secrets=[secret], scaledown_window=300)
@modal.concurrent(max_inputs=100)
@modal.asgi_app(label="fbclip-api")
def api():
    import os
    from fbclip.api import create_app
    origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return create_app(_store(), ModalSpawner(), os.environ.get("ADMIN_KEY", ""), os.environ["SESSION_SECRET"],
                      origins, os.environ.get("ALLOWED_ORIGIN_REGEX") or None)
