"""Time one separation chunk (409,600 samples, 9.3 s) per backend and check the stems against torch on the CPU.

    uv run --with onnxruntime python prototypes/onnx/bench.py --skip-coreml       # macOS: torch cpu/mps, ORT cpu
    uv run --with onnxruntime-directml python prototypes/onnx/bench.py            # Windows: torch cpu, ORT cpu and DirectML

A 4-minute song takes about 52 chunks at overlap 2, so per-chunk time x 52 is the separation time.
"""

import platform
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

ONNX = Path(__file__).resolve().parents[2] / "models" / "roformer-core.onnx"
if not ONNX.exists():  # copied next to the script on another machine
    ONNX = Path(__file__).resolve().parent / "roformer-core.onnx"
CHUNKS_PER_4_MIN = 52
RUNS = 3


def timed(fn):
    fn()  # warm-up: graph optimisation, kernel compilation
    times = []
    for _ in range(RUNS):
        t = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t)
    return min(times), out


def report(name, sec, mask, ref):
    err = 20 * np.log10(np.sqrt(np.mean((mask - ref) ** 2)) / np.sqrt(np.mean(ref ** 2)) + 1e-30) if ref is not None else float("nan")
    print(f"{name:28s} {sec:6.2f} s/chunk   ~{sec * CHUNKS_PER_4_MIN / 60:4.1f} min per 4-min song   mask error {err:5.0f} dB", flush=True)


def main():
    onnx_only = "--onnx-only" in sys.argv
    print(platform.platform(), "| onnxruntime", ort.__version__, "| providers:", ", ".join(ort.get_available_providers()), flush=True)
    rng = np.random.default_rng(0)
    spec = (rng.standard_normal((1, 2050, 801, 2)) * 0.5).astype(np.float32)
    ref = None

    if not onnx_only:
        import torch
        from common import load
        _, core, _ = load()
        x = torch.from_numpy(spec)
        with torch.no_grad():
            sec, out = timed(lambda: core(x))
            ref = out.numpy()
            report("torch cpu", sec, ref, ref)
            if torch.backends.mps.is_available():
                core.to("mps")
                xm = x.to("mps")
                def run():
                    y = core(xm)
                    torch.mps.synchronize()
                    return y
                sec, out = timed(run)
                report("torch mps (current app)", sec, out.cpu().numpy(), ref)
                core.to("cpu")

    wanted = [("CPUExecutionProvider", "onnxruntime cpu"), ("CoreMLExecutionProvider", "onnxruntime CoreML"),
              ("DmlExecutionProvider", "onnxruntime DirectML"), ("CUDAExecutionProvider", "onnxruntime CUDA")]
    for prov, name in wanted:
        if prov not in ort.get_available_providers() or f"--skip-{name.split()[-1].lower()}" in sys.argv:
            continue
        try:
            opts = ort.SessionOptions()
            if prov == "DmlExecutionProvider":  # DirectML wants these off
                opts.enable_mem_pattern = False
                opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            sess = ort.InferenceSession(str(ONNX), opts, providers=[prov, "CPUExecutionProvider"])
            sec, out = timed(lambda: sess.run(None, {"stft": spec})[0])
            if ref is None and prov == "CPUExecutionProvider":
                ref = out
            report(name, sec, out, ref)
        except Exception as e:
            print(f"{name:28s} failed: {str(e).splitlines()[0][:150]}", flush=True)


if __name__ == "__main__":
    main()
