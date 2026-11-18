"""RunPod serverless handler: separate one song into parts.

Input: {"input_url": signed GET of a FLAC, "outputs": {stem: signed PUT}, "stems": [names], "overlap": windows per chunk}.
Output: {"seconds": separation time, "device": "cuda" or "cpu"}. Progress goes out as {"progress": {"done", "total"}}.
The parts go up as 16-bit FLAC clipped to full scale, the same as the app's own stem cache.
"""

import io
import time
import urllib.request

import numpy as np
import soundfile as sf

from countin.core import stems


# Cloudflare's bot check answers Python's default User-Agent with 403.
UA = {"User-Agent": "Count-In-Worker"}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
        return r.read()


def put(url: str, data: bytes) -> None:
    req = urllib.request.Request(url, data=data, method="PUT", headers={"content-type": "audio/flac", **UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        r.read()


def run(job_input: dict, progress=lambda done, total: None) -> dict:
    pcm, sr = sf.read(io.BytesIO(fetch(job_input["input_url"])), dtype="float32", always_2d=True)
    began = time.monotonic()
    stems.OVERLAP = int(job_input.get("overlap", stems.OVERLAP))  # the GPU affords more overlap than a laptop
    parts = stems.separate(pcm, sr, progress, only=tuple(job_input["stems"]))
    seconds = round(time.monotonic() - began, 1)
    print(f"separated {len(pcm) / sr:.0f} s of audio in {seconds} s on {stems._device()}, overlap {stems.OVERLAP}", flush=True)
    for name, x in parts.items():
        if name not in job_input.get("outputs", {}):  # a speed test sends no outputs
            continue
        buf = io.BytesIO()
        sf.write(buf, np.clip(x, -1.0, 1.0), sr, format="FLAC", subtype="PCM_16")
        put(job_input["outputs"][name], buf.getvalue())
    return {"seconds": seconds, "device": stems._device(), "overlap": stems.OVERLAP}


def handler(job: dict) -> dict:
    import runpod

    return run(job["input"], lambda d, n: runpod.serverless.progress_update(job, {"progress": {"done": d, "total": n}}))


if __name__ == "__main__":
    import runpod

    stems._model()  # load the weights before the first job, while the worker starts
    runpod.serverless.start({"handler": handler})
