"""count-in-gpu-bench.exe: times the BS-RoFormer core on the CPU and on the GPU (DirectML) with debug output.

Everything ships inside the exe (onnxruntime-directml, numpy, the ONNX model). The full log also goes to
count-in-gpu-bench.txt next to the exe, so it can be sent back as a file.

    count-in-gpu-bench.exe [--verbose] [--device N] [--runs N]
"""

import os
import platform
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
EXE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LOG = EXE_DIR / "count-in-gpu-bench.txt"
CHUNKS_PER_4_MIN = 52
_log = open(LOG, "w", encoding="utf-8")


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    _log.write(line + "\n")
    _log.flush()


def arg(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def ps(cmd):
    """PowerShell one-liner output, or the error."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=30)
        return (r.stdout or r.stderr).strip()
    except Exception as e:
        return f"({e})"


def system_info():
    say("== System")
    say("OS:", platform.platform(), "|", platform.version())
    say("Python:", sys.version.split()[0], "| frozen:", getattr(sys, "frozen", False), "| exe:", sys.executable)
    say("CPU:", platform.processor(), "| logical cores:", os.cpu_count())
    if os.name == "nt":
        say(ps("Get-CimInstance Win32_Processor | ForEach-Object { $_.Name + ' | cores ' + $_.NumberOfCores + ' | threads ' + $_.NumberOfLogicalProcessors }"))
        say("RAM:", ps("[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)"), "GB")
        say("GPUs:")
        say(ps("Get-CimInstance Win32_VideoController | ForEach-Object { '  ' + $_.Name + ' | driver ' + $_.DriverVersion + ' | ' + $_.DriverDate + ' | VRAM(reported) ' + [math]::Round($_.AdapterRAM/1MB) + ' MB' }"))


def timed(fn, runs):
    t = time.perf_counter()
    out = fn()
    say(f"   first run (includes graph setup): {time.perf_counter() - t:.2f} s")
    times = []
    for i in range(runs):
        t = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t)
        say(f"   run {i + 1}: {times[-1]:.2f} s")
    return min(times), out


def main():
    system_info()
    import numpy as np
    import onnxruntime as ort

    say("== onnxruntime", ort.__version__, "| providers:", ", ".join(ort.get_available_providers()))
    if "--verbose" in sys.argv:
        ort.set_default_logger_severity(0)
    model = HERE / "roformer-core.onnx"
    if not model.exists():  # running from the repo
        model = HERE.parents[1] / "models" / "roformer-core.onnx"
    say("Model:", model, f"{model.stat().st_size / 1e6:.0f} MB")
    runs, device = arg("--runs", 3), arg("--device", 0)

    rng = np.random.default_rng(0)
    spec = (rng.standard_normal((1, 2050, 801, 2)) * 0.5).astype(np.float32)
    results, ref = {}, None
    for prov, name in [("CPUExecutionProvider", "CPU"), ("DmlExecutionProvider", f"GPU (DirectML, device {device})")]:
        say(f"== {name}")
        if prov not in ort.get_available_providers():
            say("   provider not available")
            continue
        try:
            opts = ort.SessionOptions()
            opts.log_severity_level = 0 if "--verbose" in sys.argv else 2
            popts = {}
            if prov == "DmlExecutionProvider":
                opts.enable_mem_pattern = False
                opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                popts = {"device_id": device}
            t = time.perf_counter()
            sess = ort.InferenceSession(str(model), opts, providers=[(prov, popts)] + ([] if prov == "CPUExecutionProvider" else ["CPUExecutionProvider"]))
            say(f"   session created in {time.perf_counter() - t:.1f} s, providers in use: {sess.get_providers()}")
            sec, out = timed(lambda: sess.run(None, {"stft": spec})[0], runs)
            results[name] = sec
            if ref is None:
                ref = out
            else:
                err = np.sqrt(np.mean((out - ref) ** 2)) / np.sqrt(np.mean(ref ** 2))
                say(f"   difference from CPU result: {20 * np.log10(err + 1e-30):.0f} dB (below -60 dB is fine)")
        except Exception:
            say("   FAILED:")
            say(traceback.format_exc())

    say("== Summary (one 9.3 s chunk; a 4-minute song is about 52 chunks)")
    for name, sec in results.items():
        say(f"   {name:28s} {sec:6.2f} s per chunk  ~{sec * CHUNKS_PER_4_MIN / 60:5.1f} min per 4-min song")
    if len(results) == 2:
        cpu, gpu = results.values()
        say(f"   GPU is {cpu / gpu:.1f}x the speed of the CPU here")
    say("Log saved to:", LOG)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        say(traceback.format_exc())
    if getattr(sys, "frozen", False) and "--no-pause" not in sys.argv:
        input("\nPress Enter to close")
