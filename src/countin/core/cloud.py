"""Separation on the Count-In backend (api.count-in.app), for users with a key the author issued.

The key lives in the system store (Keychain, Windows Credential Manager). The song goes up as 16-bit FLAC; the
backend runs it on a RunPod GPU or returns the parts it kept from an earlier run of the same file, which costs
nothing from the monthly limit. Parts come back as 16-bit FLAC, the same as the local stem cache.
"""

import http.client
import io
import ipaddress
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

API = os.environ.get("COUNTIN_API", "https://api.count-in.app")

SERVICE = "Count-In"
ACCOUNT = "api-key"
PROXY_ACCOUNT = "socks5-proxy"
POLL_S = 2.0
OVERLAPS = (2, 4, 8)  # windows per chunk the backend accepts; more is slower and a little cleaner
BLOCK = 256 * 1024
STEPS = 1000  # progress resolution while files move


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


def proxy_url() -> str:
    import keyring

    try:
        return keyring.get_password(SERVICE, PROXY_ACCOUNT) or ""
    except keyring.errors.NoKeyringError:
        return ""
    except Exception:
        raise OSError("Could not read the proxy from system credential storage") from None


def _parse_proxy(value: str) -> urllib.parse.SplitResult:
    try:
        proxy = urllib.parse.urlsplit(value)
        if (proxy.scheme != "socks5" or not proxy.hostname or proxy.port == 0
                or proxy.path or proxy.query or proxy.fragment
                or any(char.isspace() for char in value)):
            raise ValueError
        if proxy.username is not None:
            for field in (proxy.username, proxy.password):
                if not field or len(urllib.parse.unquote(field).encode()) > 255:
                    raise ValueError
        return proxy
    except ValueError:
        raise ValueError("Invalid SOCKS5 proxy URL") from None


def set_proxy_url(value: str) -> None:
    import keyring

    if value:
        _parse_proxy(value)
        keyring.set_password(SERVICE, PROXY_ACCOUNT, value)
    elif proxy_url():
        keyring.delete_password(SERVICE, PROXY_ACCOUNT)


def proxy_link(value: str) -> tuple[str, str]:
    """Return the SOCKS5 authority and username from a dev setup link."""
    try:
        link = urllib.parse.urlsplit(value)
        if (link.scheme != "countin-dev" or link.netloc != "proxy"
                or link.path not in ("", "/") or link.fragment):
            raise ValueError
        pairs = urllib.parse.parse_qsl(link.query, strict_parsing=True, max_num_fields=3, errors="strict")
        fields = dict(pairs)
        if len(pairs) != 3 or fields.keys() != {"host", "port", "user"}:
            raise ValueError
        host, port, user = fields["host"], fields["port"], fields["user"]
        if ":" in host:
            if "%" in host:
                raise ValueError
            host = f"[{ipaddress.IPv6Address(host.removeprefix('[').removesuffix(']'))}]"
        elif not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", host):
            raise ValueError
        if (not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535
                or not user.isprintable() or len(user.encode()) > 255):
            raise ValueError
        return f"{host}:{int(port)}", user
    except ValueError:
        raise ValueError("Invalid proxy setup link") from None


def _tls() -> ssl.SSLContext | None:
    if API.startswith("http://"):
        return None
    import truststore

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _socks_connect(proxy: urllib.parse.SplitResult, host: str, port: int, timeout: float) -> socket.socket:
    """Open a TCP stream through SOCKS5 (RFC 1928 and RFC 1929)."""
    sock = socket.create_connection((proxy.hostname, proxy.port or 1080), timeout)

    def read(n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise OSError("SOCKS proxy closed the connection")
            data += chunk
        return data

    try:
        user = urllib.parse.unquote(proxy.username or "").encode()
        password = urllib.parse.unquote(proxy.password or "").encode()
        sock.sendall(b"\x05\x01\x02" if user else b"\x05\x01\x00")
        method = read(2)[1]
        if method == 2 and user:
            sock.sendall(bytes([1, len(user)]) + user + bytes([len(password)]) + password)
            if read(2)[1] != 0:
                raise OSError("SOCKS proxy refused the password")
        elif method != 0 or user:
            raise OSError("SOCKS proxy wants another login method")
        name = host.encode()
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + port.to_bytes(2, "big"))
        reply = read(4)
        if reply[1] != 0:
            raise OSError(f"SOCKS proxy could not connect, code {reply[1]}")
        read({1: 4, 4: 16}.get(reply[3]) or read(1)[0])
        read(2)
        return sock
    except BaseException:
        sock.close()
        raise


class _SocksConnection(http.client.HTTPSConnection):
    def __init__(self, host, *, proxy, **kwargs):
        super().__init__(host, **kwargs)
        self.proxy = proxy

    def connect(self):
        sock = _socks_connect(self.proxy, self.host, self.port or 443, self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _SocksHandler(urllib.request.HTTPSHandler):
    def __init__(self, proxy, *, context):
        super().__init__(context=context)
        self.proxy = proxy

    def https_open(self, req):
        return self.do_open(_SocksConnection, req, proxy=self.proxy, context=self._context)


def _open(req: urllib.request.Request, timeout: float):
    value = proxy_url() if req.type == "https" else ""
    if not value:
        return urllib.request.urlopen(req, timeout=timeout, context=_tls())
    try:
        proxy = _parse_proxy(value)
    except ValueError as exc:
        raise OSError(str(exc)) from None
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _SocksHandler(proxy, context=_tls())
    ).open(req, timeout=timeout)


class _Upload(io.BytesIO):
    """A request body that reports how much of it has gone out."""

    def __init__(self, data: bytes, progress):
        super().__init__(data)
        self.size, self.progress = len(data), progress

    def read(self, n=-1):
        chunk = super().read(BLOCK if n is None or n < 0 else min(n, BLOCK))
        self.progress(self.tell(), self.size)
        return chunk


def _call(method: str, path: str, token: str, body: bytes | dict | None = None, timeout: float = 30, progress=None):
    """JSON for API routes, bytes for files. progress(done, total) follows the bytes of a file going up or down."""
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Count-In"}
    data = None
    if isinstance(body, dict):
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif body is not None:
        data, headers["Content-Type"] = body, "audio/flac"
        headers["Content-Length"] = str(len(body))
        if progress:
            data = _Upload(body, progress)
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with _open(req, timeout) as r:
            if r.headers.get_content_type() == "application/json":
                return json.loads(r.read())
            size, raw = int(r.headers.get("Content-Length") or 0), bytearray()
            while chunk := r.read(BLOCK):
                raw += chunk
                if progress:
                    progress(len(raw), size)
            return bytes(raw)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read())
            msg = payload.get("error") or payload.get("detail", "")
        except Exception:
            msg = ""
        raise CloudError(f"{e.code} {msg}".strip()) from None
    except OSError as e:
        raise CloudError(f"no connection to {API}: {e}") from None


def me(token: str | None = None) -> dict:
    """{"name", "used", "limit", "max_seconds", "music_search"} for the key; CloudError when it is invalid or revoked."""
    token = token or key()
    if not token:
        raise CloudError("no key")
    return _call("GET", "/v1/me", token)


def report(payload: dict) -> dict:
    """Sends a bug report; it needs no key, the server limits how many one address may send."""
    return _call("POST", "/v1/report", "", payload)


def _flac(x: np.ndarray, sr: int) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, x, sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def separate(pcm: np.ndarray, sr: int, song_hash: str, report, cancelled, overlap: int = 4,
             *, track_title: str | None = None) -> dict[str, np.ndarray]:
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

    report(_("Проверка песни на сервере"), 0, 0)
    payload = {"duration_s": len(pcm) / sr, "hash": song_hash, "overlap": overlap}
    if track_title:
        payload["track_title"] = track_title
    job = _call("POST", "/v1/jobs", token, payload)
    if job["status"] == "uploading":
        report(_("Подготовка песни к загрузке"), 0, 0)
        flac = _flac(pcm * gain, sr)
        mb = len(flac) / 1e6
        _call("PUT", job["upload"], token, flac, timeout=600,
              progress=lambda d, n: report(_("Загрузка песни на сервер, {mb:.0f} МБ", mb=mb), int(d * STEPS / n), STEPS))
        report(_("Песня загружена, отправляю на обработку"), 0, 0)
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
    else:
        report(_("Дорожки этой песни уже есть на сервере"), 0, 0)
    out = {}
    names = list(job["stems"])
    for i, name in enumerate(names):
        if cancelled():
            raise Cancelled
        got = _call("GET", job["stems"][name], token, timeout=600,
                    progress=lambda d, n, i=i: report(_("Скачивание дорожек"), int((i + (d / n if n else 0)) * STEPS / len(names)),
                                                      STEPS))
        x, _sr = sf.read(io.BytesIO(got), dtype="float32", always_2d=True)
        x = x[: len(pcm)]
        if len(x) < len(pcm):
            x = np.pad(x, ((0, len(pcm) - len(x)), (0, 0)))
        out[name] = x / gain if gain != 1.0 else x
    return out
