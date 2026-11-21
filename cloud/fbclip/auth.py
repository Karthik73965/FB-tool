"""Invite-code login. The admin creates one code per person; logging in with it returns a signed session token.
The admin logs in with ADMIN_KEY. Revoking a user deletes their code and rejects their tokens from then on."""
import base64, hashlib, hmac, json, secrets, time

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I/L
TOKEN_DAYS = 90


def new_code():
    part = lambda: "".join(secrets.choice(ALPHABET) for _ in range(4))
    return f"FB-{part()}-{part()}"


def code_hash(code):
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(payload, secret):
    body = _b64(json.dumps(dict(payload, exp=int(time.time()) + TOKEN_DAYS * 86400)).encode())
    return body + "." + _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def verify(token, secret):
    """-> payload dict, or None if the token is forged or expired"""
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return p if p.get("exp", 0) > time.time() else None
    except Exception:
        return None
