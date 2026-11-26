"""Import a confirmed catalog recording through the Count-In API."""
import time
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from .core import assets, cloud
from .i18n import _

ROOT = "/v1/music"


def imports_dir() -> Path:
    return assets.data_dir() / "imports"


def artists(job, token, query):
    return cloud._call("GET", ROOT + "/artists?" + urlencode({"q": query}), token)["results"]


def tracks(job, token, artist, query):
    return cloud._call("GET", f"{ROOT}/artists/{artist}/tracks?" + urlencode({"q": query}), token)["results"]


def quality(job, token, track_id):
    return cloud._call("GET", f"{ROOT}/tracks/{track_id}/quality", token, timeout=100)


def import_track(job, token, track):
    path = f"{ROOT}/jobs/" + cloud._call("POST", ROOT + "/jobs", token, {"track_id": track["id"]})["id"]
    finished = False
    try:
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            info = cloud._call("GET", path, token)
            status = info["status"]
            if status == "ready":
                break
            if status in ("failed", "cancelled"):
                raise cloud.CloudError(info.get("error") or "Import cancelled")
            label = {"queued": _("В очереди…"), "searching": _("Ищу источники…"),
                     "checking": _("Проверяю запись…"), "downloading": _("Скачиваю аудио…")}
            text = label.get(status, status)
            if info.get("format") and status == "downloading":
                text = f'{text.rstrip("…")} · {info["format"]}'
            job.report(text, int(info.get("progress", 0) * 1000),
                       1000 if status == "downloading" else 0)
            if job.cancelled.wait(2):
                job.report(_("Отменено"))
        else:
            raise cloud.CloudError("Music import timed out")
        job.report(_("Получаю аудио…"))
        raw = cloud._call("GET", path + "/audio", token, timeout=120,
                          progress=lambda n, total: job.report(_("Получаю аудио…"), n, total))
        suffix = Path(info["filename"]).suffix.lower()
        if not raw or suffix not in {".flac", ".mp3", ".m4a", ".wav", ".aif", ".aiff", ".ogg"}:
            raise cloud.CloudError("Unsupported audio")
        directory = imports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        name = "".join(char if char.isalnum() or char in " ._-" else "_" for char in track["title"])[:80]
        target = directory / f"{name} - {uuid4().hex[:8]}{suffix}"
        part = target.with_suffix(".part")
        try:
            part.write_bytes(raw)
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
        finished = True
        return target
    finally:
        if not finished:
            try:
                cloud._call("DELETE", path, token, timeout=5)
            except cloud.CloudError:
                pass
