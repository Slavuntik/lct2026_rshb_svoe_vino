"""Resolve local vs remote shelf service without starting processes."""

from urllib.parse import urlsplit


def shelf_target(env, port):
    remote = env.get("VINCHIK_SHELF_URL", "").strip().rstrip("/")
    if not remote:
        return f"http://127.0.0.1:{port}", False
    parsed = urlsplit(remote)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise ValueError(
            "VINCHIK_SHELF_URL must be an HTTP(S) origin without credentials or path"
        )
    return remote, True
