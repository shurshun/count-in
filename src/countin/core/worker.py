"""Separation in a process of its own, so the app stays responsive.

Core ML's predict holds Python's global lock for the whole 1–3 s of a chunk; run in the app's process it froze
the interface and starved the audio callback. Here the model lives in one long-running child process that the
app talks to through queues, with the audio in shared memory both ways.

An urgent request (a preview excerpt) jumps the queue: the running job hands over between two chunks, the
excerpt gets separated, and the job carries on where it stopped.
"""

import itertools
import logging
import multiprocessing as mp
import queue
import threading
from multiprocessing import shared_memory

import numpy as np

log = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


# --- the child process ---

def _serve(requests, replies):
    from . import stems

    waiting: list[tuple] = []  # requests read while a job ran, urgent ones first
    cancelled: set[int] = set()

    def read(block: bool):
        """Takes the requests that arrived; cancels are noted, urgent jobs go to the front."""
        try:
            msg = requests.get(block)
            while True:
                if msg is None:
                    raise SystemExit
                if msg[0] == "cancel":
                    cancelled.add(msg[1])
                elif msg[0] == "warm":
                    from . import accel

                    stems._model()
                    accel.load()
                elif msg[3]:
                    waiting.insert(sum(1 for w in waiting if w[3]), msg)
                else:
                    waiting.append(msg)
                msg = requests.get_nowait()
        except queue.Empty:
            pass

    def run(msg):
        _kind, rid, (in_name, shape, sr, only, out_name), _urgent = msg
        if rid in cancelled:
            replies.put(("cancelled", rid))
            return
        src = shared_memory.SharedMemory(name=in_name)
        dst = shared_memory.SharedMemory(name=out_name)
        try:
            pcm = np.ndarray(shape, np.float32, buffer=src.buf)
            out = np.ndarray((len(only), *shape), np.float32, buffer=dst.buf)

            def progress(done, total):
                replies.put(("progress", rid, done, total))
                read(False)
                if rid in cancelled:
                    raise Cancelled
                while waiting and waiting[0][3] and not msg[3]:  # an urgent excerpt goes first
                    run(waiting.pop(0))

            result = stems.separate(pcm, sr, progress, only=only)
            for i, name in enumerate(only):
                out[i] = result[name]
            replies.put(("done", rid, stems.last))
        except Cancelled:
            replies.put(("cancelled", rid))
        except Exception as e:
            log.exception("separation failed")
            replies.put(("error", rid, f"{type(e).__name__}: {e}"))
        finally:
            src.close()
            dst.close()

    while True:
        read(not waiting)
        while waiting:
            run(waiting.pop(0))


# --- the app side ---

class Separator:
    """Client of the separation process. separate() blocks its calling thread like stems.separate did."""

    def __init__(self):
        self.ctx = mp.get_context("spawn")
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.waiting: dict[int, queue.Queue] = {}
        self.proc = None
        self.last = None  # (seconds, backend) of the latest separation

    def _ensure(self):
        with self.lock:
            if self.proc is not None and self.proc.is_alive():
                return
            self.requests, self.replies = self.ctx.Queue(), self.ctx.Queue()
            self.proc = self.ctx.Process(target=_serve, args=(self.requests, self.replies), daemon=True,
                                         name="countin-separator")
            self.proc.start()
            threading.Thread(target=self._read, args=(self.proc, self.replies), daemon=True).start()

    def _read(self, proc, replies):
        """Hands each reply to the thread that waits for it; a dead process fails everything pending."""
        while True:
            try:
                msg = replies.get(timeout=1.0)
            except queue.Empty:
                if not proc.is_alive():
                    for q in list(self.waiting.values()):
                        q.put(("error", None, "the separation process stopped"))
                    return
                continue
            q = self.waiting.get(msg[1])
            if q is not None:
                q.put(msg)

    def stop(self):
        """Ends the process and frees its models, unless a separation is running."""
        with self.lock:
            if self.proc is not None and self.proc.is_alive() and not self.waiting:
                self.proc.terminate()
                self.proc = None

    def warm(self):
        """Starts the process and loads the models in it."""
        self._ensure()
        self.requests.put(("warm",))

    def separate(self, pcm: np.ndarray, sr: int, progress=None, only: tuple[str, ...] = (), urgent: bool = False,
                 cancelled=lambda: False) -> dict[str, np.ndarray]:
        self._ensure()
        pcm = np.ascontiguousarray(pcm, np.float32)
        src = shared_memory.SharedMemory(create=True, size=max(pcm.nbytes, 1))
        dst = shared_memory.SharedMemory(create=True, size=max(pcm.nbytes * len(only), 1))
        rid, q = next(self.ids), queue.Queue()
        self.waiting[rid] = q
        try:
            np.ndarray(pcm.shape, np.float32, buffer=src.buf)[:] = pcm
            self.requests.put(("separate", rid, (src.name, pcm.shape, sr, tuple(only), dst.name), urgent))
            sent_cancel = False
            while True:
                try:
                    msg = q.get(timeout=0.2)
                except queue.Empty:
                    msg = None
                if cancelled() and not sent_cancel:
                    self.requests.put(("cancel", rid))
                    sent_cancel = True
                if msg is None:
                    continue
                if msg[0] == "progress":
                    if progress:
                        progress(msg[2], msg[3])  # may raise to cancel
                elif msg[0] == "done":
                    self.last = msg[2]
                    out = np.ndarray((len(only), *pcm.shape), np.float32, buffer=dst.buf)
                    return {name: out[i].copy() for i, name in enumerate(only)}
                elif msg[0] == "cancelled":
                    raise Cancelled
                else:
                    raise RuntimeError(msg[2])
        except BaseException:
            self.requests.put(("cancel", rid))  # the worker drops it, finished or not
            raise
        finally:
            self.waiting.pop(rid, None)
            for shm in (src, dst):
                shm.close()
                shm.unlink()


_separator: Separator | None = None


def separator() -> Separator:
    global _separator
    if _separator is None:
        _separator = Separator()
    return _separator
