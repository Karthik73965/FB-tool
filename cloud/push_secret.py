"""Create / update the Modal secret "fbclip" from cloud/.secrets.env (git-ignored), so keys never go in a command line
you'd paste somewhere.

    cloud\\.venv\\Scripts\\python cloud\\push_secret.py
Changing the secret later needs `modal deploy` again to take effect.
"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "ADMIN_KEY", "SESSION_SECRET",
            "ALLOWED_ORIGINS"]

vals = {}
for line in open(os.path.join(HERE, ".secrets.env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip()
OPTIONAL = ["OPENROUTER_API_KEY"]      # B-roll picker (broll.py); without it clips render without B-roll
missing = [k for k in REQUIRED if not vals.get(k)]
if missing:
    sys.exit("fill these in cloud/.secrets.env first: " + ", ".join(missing))
modal = os.path.join(HERE, ".venv", "Scripts" if os.name == "nt" else "bin", "modal")
r = subprocess.run([modal, "secret", "create", "fbclip", "--force"] + [f"{k}={vals[k]}" for k in REQUIRED + OPTIONAL if vals.get(k)],
                   capture_output=True, text=True)
print("Modal secret 'fbclip' saved." if r.returncode == 0 else r.stderr or r.stdout)
sys.exit(r.returncode)
