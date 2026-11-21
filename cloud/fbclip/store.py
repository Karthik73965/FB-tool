"""Where the cloud version keeps files and records: Cloudflare R2 in production, a local folder for dev and tests.

Keys:  meta/users/<id>.json  meta/codes/<sha256 of code>.json  meta/jobs/<id>.json
       uploads/<id>/source<ext>  (raw upload, deleted once the worker has it)
       jobs/<id>/clip.mp4  transcript.json  report.txt
"""
import json, os, shutil, time, urllib.parse


def from_env():
    if os.environ.get("FBCLIP_LOCAL_STORE"):
        return LocalStore(os.environ["FBCLIP_LOCAL_STORE"], os.environ.get("FBCLIP_LOCAL_URL", "http://127.0.0.1:8000"))
    return R2Store(os.environ["R2_ACCOUNT_ID"], os.environ["R2_ACCESS_KEY_ID"], os.environ["R2_SECRET_ACCESS_KEY"],
                   os.environ.get("R2_BUCKET", "fbclip"))


def _disposition(filename, inline):
    q = urllib.parse.quote(filename)
    return f"{'inline' if inline else 'attachment'}; filename=\"{q}\"; filename*=UTF-8''{q}"


class R2Store:
    def __init__(self, account_id, key_id, secret, bucket):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                               aws_access_key_id=key_id, aws_secret_access_key=secret, region_name="auto",
                               config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}))

    @staticmethod
    def _missing(e):
        return getattr(e, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound")

    def get_json(self, key):
        try:
            return json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
        except Exception as e:
            if self._missing(e):
                return None
            raise

    def put_json(self, key, obj):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(obj).encode(), ContentType="application/json")

    def size(self, key):
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except Exception as e:
            if self._missing(e):
                return None
            raise

    def keys(self, prefix):
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            out += [o["Key"] for o in page.get("Contents", [])]
        return out

    def delete(self, key):
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix):
        ks = self.keys(prefix)
        for i in range(0, len(ks), 1000):
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in ks[i:i + 1000]]})

    def upload_file(self, path, key, content_type="application/octet-stream"):
        self.s3.upload_file(path, self.bucket, key, ExtraArgs={"ContentType": content_type})

    def download_file(self, key, path):
        self.s3.download_file(self.bucket, key, path)

    def put_url(self, key, expires=6 * 3600):
        """presigned PUT: the browser uploads straight to R2, nothing big passes through the API"""
        return self.s3.generate_presigned_url("put_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires)

    def get_url(self, key, filename, inline=False, expires=6 * 3600):
        return self.s3.generate_presigned_url(
            "get_object", ExpiresIn=expires,
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentDisposition": _disposition(filename, inline)})


class LocalStore:
    """same interface on a local folder; dev_server.py serves the /blob URLs it hands out"""

    def __init__(self, root, base_url):
        self.root, self.base_url = os.path.abspath(root), base_url.rstrip("/")
        os.makedirs(self.root, exist_ok=True)

    def path(self, key):
        p = os.path.abspath(os.path.join(self.root, key))
        if not p.startswith(self.root + os.sep):
            raise ValueError("bad key")
        return p

    def get_json(self, key):
        try:
            return json.load(open(self.path(key), encoding="utf-8"))
        except FileNotFoundError:
            return None

    def put_json(self, key, obj):
        p = self.path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{os.getpid()}.{time.time_ns()}.tmp"
        json.dump(obj, open(tmp, "w", encoding="utf-8"))
        for _ in range(20):                      # Windows: a reader may hold the file for a moment
            try:
                os.replace(tmp, p)
                return
            except PermissionError:
                time.sleep(0.05)
        os.replace(tmp, p)

    def size(self, key):
        p = self.path(key)
        return os.path.getsize(p) if os.path.isfile(p) else None

    def keys(self, prefix):
        out = []
        for d, _, fs in os.walk(self.root):
            for f in fs:
                k = os.path.relpath(os.path.join(d, f), self.root).replace(os.sep, "/")
                if k.startswith(prefix) and not k.endswith(".tmp"):
                    out.append(k)
        return sorted(out)

    def delete(self, key):
        try:
            os.remove(self.path(key))
        except FileNotFoundError:
            pass

    def delete_prefix(self, prefix):
        for k in self.keys(prefix):
            self.delete(k)

    def upload_file(self, path, key, content_type=None):
        p = self.path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copyfile(path, p)

    def download_file(self, key, path):
        shutil.copyfile(self.path(key), path)

    def put_url(self, key, expires=0):
        return f"{self.base_url}/blob/{urllib.parse.quote(key)}"

    def get_url(self, key, filename, inline=False, expires=0):
        return (f"{self.base_url}/blob/{urllib.parse.quote(key)}?"
                + urllib.parse.urlencode({"filename": filename, "inline": int(inline)}))
