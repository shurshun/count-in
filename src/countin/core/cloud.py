"""Separation on the Count-In backend (api.count-in.app), for users with a key the author issued.

The key lives in the system store (Keychain, Windows Credential Manager). The song goes up as 16-bit FLAC; the
backend runs it on a RunPod GPU or returns the parts it kept from an earlier run of the same file, which costs
nothing from the monthly limit. Parts come back as 16-bit FLAC, the same as the local stem cache.
"""

import io
import json
import os
import ssl
import time
import urllib.error
import urllib.request

import numpy as np

API = os.environ.get("COUNTIN_API", "https://api.count-in.app")
SERVICE = "Count-In"
ACCOUNT = "api-key"
POLL_S = 2.0


class CloudError(Exception):
    """The backend refused or failed; the message is for the user."""


class Cancelled(Exception):
    pass


def key() -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, ACCOUNT)
    except Exception:
        return None


def set_key(value: str | None) -> None:
    import keyring

    if value:
        keyring.set_password(SERVICE, ACCOUNT, value)
    else:
        try:
            keyring.delete_password(SERVICE, ACCOUNT)
        except keyring.errors.PasswordDeleteError:
            pass


def _tls() -> ssl.SSLContext | None:
    if API.startswith("http://"):
        return None
    import truststore

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _call(method: str, path: str, token: str, body: bytes | dict | None = None, timeout: float = 30):
    """JSON for API routes, bytes for files."""
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Count-In"}
    data = None
    if isinstance(body, dict):
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif body is not None:
        data, headers["Content-Type"] = body, "audio/flac"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_tls()) as r:
            raw = r.read()
            return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", "")
        except Exception:
            msg = ""
        raise CloudError(f"{e.code} {msg}".strip()) from None
    except OSError as e:
        raise CloudError(f"no connection to {API}: {e}") from None


def me(token: str | None = None) -> dict:
    """{"name", "used", "limit", "max_seconds"} for the key; CloudError when it is invalid or revoked."""
    token = token or key()
    if not token:
        raise CloudError("no key")
    return _call("GET", "/v1/me", token)


def _flac(x: np.ndarray, sr: int) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, x, sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def separate(pcm: np.ndarray, sr: int, song_hash: str, report, cancelled) -> dict[str, np.ndarray]:
    """Stems for the whole song, sample-aligned with pcm. report(text, done, total) shows progress;
    cancelled() stops waiting (the GPU job still finishes and stays in the cache)."""
    import soundfile as sf

    from ..i18n import _

    token = key()
    if not token:
        raise CloudError("no key")

    # Loud masters decode above full scale; the song goes up scaled down, the parts come back scaled up.
    peak = float(np.abs(pcm).max()) if len(pcm) else 0.0
    gain = 1.0 / peak if peak > 1.0 else 1.0

    job = _call("POST", "/v1/jobs", token, {"duration_s": len(pcm) / sr, "hash": song_hash})
    if job["status"] == "uploading":
        report(_("Загрузка в облако"), 0, 0)
        _call("PUT", job["upload"], token, _flac(pcm * gain, sr), timeout=600)
        _call("POST", f"/v1/jobs/{job['id']}/start", token)
        while True:
            if cancelled():
                raise Cancelled
            time.sleep(POLL_S)
            job = _call("GET", f"/v1/jobs/{job['id']}", token)
            if job["status"] == "queued":
                report(_("В очереди на облачный GPU"), 0, 0)
            elif job["status"] == "running":
                p = job.get("progress") or {}
                report(_("Разделение в облаке"), p.get("done", 0), p.get("total", 0))
            elif job["status"] == "done":
                break
            else:
                raise CloudError(job.get("error") or job["status"])
    out = {}
    names = list(job["stems"])
    for i, name in enumerate(names):
        if cancelled():
            raise Cancelled
        report(_("Скачивание партий"), i, len(names))
        x, _sr = sf.read(io.BytesIO(_call("GET", job["stems"][name], token, timeout=600)), dtype="float32", always_2d=True)
        x = x[: len(pcm)]
        if len(x) < len(pcm):
            x = np.pad(x, ((0, len(pcm) - len(x)), (0, 0)))
        out[name] = x / gain if gain != 1.0 else x
    return out
