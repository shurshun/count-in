"""A local stand-in for a RunPod serverless endpoint, for testing the backend without spending money.

Serves POST /v2/<endpoint>/run and GET /v2/<endpoint>/status/<id> like RunPod and runs the real handler on this
machine (MPS on a Mac), one job at a time.

    COUNTIN_ASSETS=models uv run python runpod/local.py [port]
"""

import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from handler import run

jobs: dict[str, dict] = {}
gpu = threading.Lock()


def work(job_id: str, job_input: dict) -> None:
    job = jobs[job_id]
    with gpu:
        job["status"], began = "IN_PROGRESS", time.monotonic()
        try:
            job["output"] = run(job_input, lambda d, n: job.update(output={"progress": {"done": d, "total": n}}))
            job["status"] = "COMPLETED"
        except Exception as e:  # reported to the backend like a failed RunPod job
            job["status"], job["error"] = "FAILED", f"{type(e).__name__}: {e}"
        job["executionTime"] = int((time.monotonic() - began) * 1000)


class Api(BaseHTTPRequestHandler):
    def reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[2] != "run":
            return self.reply(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        job_id = str(uuid.uuid4())
        jobs[job_id] = {"id": job_id, "status": "IN_QUEUE"}
        threading.Thread(target=work, args=(job_id, body["input"]), daemon=True).start()
        self.reply(200, {"id": job_id, "status": "IN_QUEUE"})

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if len(parts) != 4 or parts[2] != "status" or parts[3] not in jobs:
            return self.reply(404, {"error": "not found"})
        self.reply(200, jobs[parts[3]])


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8788
    print(f"local RunPod stand-in on http://127.0.0.1:{port}/v2", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Api).serve_forever()
