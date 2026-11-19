"""BS-RoFormer core on a GPU outside torch: Core ML on a Mac, ONNX through DirectML on Windows.

Only the middle of the network (band split to masks) runs there; it is all the compute and has no complex
numbers. STFT, the complex multiply and iSTFT stay in torch on the CPU.

- Core ML (Apple GPU) is about twice as fast per chunk as torch on MPS. The Neural Engine stays off: the graph
  does not fit it, Core ML cuts it into dozens of pieces that take minutes to compile and run 20x slower.
  The compiled model gets built from the torch weights on the first separation (about 40 s) into the data
  folder.
- ONNX (`separator/BS-Roformer-SW-core.onnx`) runs on any DirectX 12 GPU (NVIDIA, Intel, AMD) through
  DirectML. OpenVINO was tried for Intel GPUs; DirectML stayed after tests on NVIDIA and Intel machines.
- COUNTIN_ACCEL (`coreml`, `directml`, `torch`) forces a backend.

`python -m countin.core.accel coreml|onnx [dest]` builds a model by hand.
"""

import logging
import os
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from . import assets

COREML = "separator/BS-Roformer-SW-core.mlmodelc"
ONNX = "separator/BS-Roformer-SW-core.onnx"

log = logging.getLogger(__name__)
_lock = threading.Lock()


@dataclass
class Backend:
    name: str  # shown to the user, e.g. "Core ML (GPU)"
    predict: Callable  # real STFT (1, f*s, t, 2) float32 array -> masks array


def _core(m):
    import torch
    from einops import rearrange
    from torch import nn

    class Core(nn.Module):
        """Real STFT (b, f*s, t, 2) -> masks (b, n, f*s, t, 2)."""

        def __init__(self):
            super().__init__()
            self.m = m

        def forward(self, stft_repr):
            x = rearrange(stft_repr, "b f t c -> b t (f c)")
            x = m.band_split(x)
            for time_t, freq_t in m.layers:
                x = rearrange(x, "b t f d -> b f t d")
                b, f, t, d = x.shape
                x = time_t(x.reshape(b * f, t, d)).reshape(b, f, t, d)
                x = rearrange(x, "b f t d -> b t f d")
                b, t, f, d = x.shape
                x = freq_t(x.reshape(b * t, f, d)).reshape(b, t, f, d)
            x = m.final_norm(x)
            mask = torch.stack([fn(x) for fn in m.mask_estimators], dim=1)
            return rearrange(mask, "b n t (f c) -> b n f t c", c=2)

    return Core().eval()


def stft_in(model, audio):
    """(1, 2, n) audio -> real STFT (1, f*s, t, 2), as the model computes it."""
    import torch
    from einops import rearrange

    spec = torch.stft(audio.reshape(2, -1), **model.stft_kwargs, window=model.stft_window_fn(), return_complex=True)
    return rearrange(torch.view_as_real(spec), "s f t c -> 1 (f s) t c")


def stft_out(model, stft_repr, mask):
    """Applies the masks and goes back to audio: (n, 2, samples)."""
    import torch
    from einops import rearrange

    spec = torch.view_as_complex(stft_repr[:, None].contiguous()) * torch.view_as_complex(mask.contiguous())
    spec = rearrange(spec, "b n (f s) t -> (b n s) f t", s=2)
    audio = torch.istft(spec, **model.stft_kwargs, window=model.stft_window_fn(), return_complex=False)
    return rearrange(audio, "(n s) t -> n s t", s=2)


def load() -> Backend | None:
    """The fastest backend this machine and build have; None leaves the separation to torch."""
    with _lock:
        return _load()


@lru_cache(maxsize=1)
def _load() -> Backend | None:
    want = os.environ.get("COUNTIN_ACCEL", "")
    if want == "torch":
        return None
    for name, fn in (("coreml", _coreml), ("directml", _directml)):
        if want and not want.startswith(name):
            continue
        try:
            b = fn()
        except Exception:
            log.exception("%s unavailable", name)
            continue
        if b is not None:
            log.info("separation backend: %s", b.name)
            return b
    return None


def _coreml() -> Backend | None:
    if sys.platform != "darwin":
        return None
    import coremltools as ct

    path = assets.path(COREML)
    if not path.is_dir():
        path = assets.data_dir() / COREML
        export_coreml(path)
    try:
        ml = ct.models.CompiledMLModel(str(path), compute_units=ct.ComputeUnit.CPU_AND_GPU)
    except Exception:
        # Builds up to 0.8.1 compiled for macOS 15, which macOS 14 cannot load
        log.warning("compiled model does not load, rebuilding it", exc_info=True)
        path = assets.data_dir() / COREML
        export_coreml(path)
        ml = ct.models.CompiledMLModel(str(path), compute_units=ct.ComputeUnit.CPU_AND_GPU)
    return Backend("Core ML (GPU)", lambda x: ml.predict({"stft": x})["mask"])


def _adapters() -> list[tuple[int, str, int, bool]]:
    """Windows GPUs in DXGI order, which DirectML's device_id follows: (index, name, dedicated video memory,
    software). Plain ctypes COM calls, so no extra package; any failure returns an empty list."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    class DESC1(ctypes.Structure):
        _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                    ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                    ("LuidLow", wintypes.DWORD), ("LuidHigh", wintypes.LONG), ("Flags", wintypes.UINT)]

    def method(obj, index, *argtypes):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])

    iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    factory = ctypes.c_void_p()
    if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
        return []
    out = []
    try:
        for i in range(16):
            adapter = ctypes.c_void_p()
            if method(factory, 12, wintypes.UINT, ctypes.c_void_p)(factory, i, ctypes.byref(adapter)) != 0:
                break  # DXGI_ERROR_NOT_FOUND after the last one
            d = DESC1()
            if method(adapter, 10, ctypes.c_void_p)(adapter, ctypes.byref(d)) == 0:
                out.append((i, d.Description, int(d.DedicatedVideoMemory), bool(d.Flags & 2)))
            method(adapter, 2)(adapter)  # Release
    finally:
        method(factory, 2)(factory)
    return out


def best_adapter() -> tuple[int, str] | None:
    """The hardware GPU with the most dedicated memory: on a laptop with two GPUs that is the discrete one,
    while DirectML's default (adapter 0, the display GPU) is usually the integrated one. None when only the
    software rasterizer is there, which runs the network slower than the CPU."""
    try:
        gpus = [a for a in _adapters() if not a[3]]
    except Exception:
        log.exception("GPU list unavailable")
        return 0, "default"
    if not gpus:
        return None
    i, name, _mem, _sw = max(gpus, key=lambda a: a[2])
    return i, name


def _directml() -> Backend | None:
    if not assets.path(ONNX).is_file():
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    if "DmlExecutionProvider" not in ort.get_available_providers():
        return None
    opts = ort.SessionOptions()
    opts.enable_mem_pattern = False  # DirectML wants both off
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    gpu = best_adapter()
    if gpu is None:
        log.info("no hardware GPU for DirectML")
        return None
    sess = ort.InferenceSession(str(assets.path(ONNX)), opts,
                                providers=[("DmlExecutionProvider", {"device_id": gpu[0]}), "CPUExecutionProvider"])
    if "DmlExecutionProvider" not in sess.get_providers():
        return None
    return Backend(f"DirectML (GPU: {gpu[1]})", lambda x: sess.run(None, {"stft": x})[0])


def run(backend: Backend, model, part):
    """(1, 2, chunk) audio -> (stems, 2, chunk) through the backend's core."""
    import numpy as np
    import torch

    spec = stft_in(model, part)
    mask = backend.predict(spec.numpy())
    return stft_out(model, spec, torch.from_numpy(np.asarray(mask, np.float32)))[..., : part.shape[-1]]


def _traced_input():
    """(model, real STFT of a chunk of noise, core with its rotary cache filled)."""
    import torch

    from . import stems

    model, _names, chunk = stems._model()
    model.float().cpu()
    spec = stft_in(model, torch.randn(1, 2, chunk, generator=torch.Generator().manual_seed(0)) * 0.1)
    core = _core(model)
    with torch.no_grad():
        core(spec)  # fills the rotary cache, so the trace reads it instead of building it
    return model, spec, core


def export_onnx(dest: Path) -> None:
    """Exports the core as one fp32 ONNX file (opset 18) for DirectML."""
    import torch

    _model, spec, core = _traced_input()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(core, (spec,), str(dest), input_names=["stft"], output_names=["mask"], opset_version=18,
                          dynamo=True, external_data=False)


def export_coreml(dest: Path) -> None:
    """Traces the core, converts it to an fp16 ML program and compiles it into dest (.mlmodelc)."""
    import shutil
    import tempfile

    import coremltools as ct
    import torch
    from coremltools.converters.mil.frontend.torch import ops

    # Traced shapes reach int() as one-element arrays, which coremltools only takes as scalars.
    cast = ops._cast

    def scalar_cast(context, node, dtype, dtype_name):
        x = ops._get_inputs(context, node, expected=1)[0]
        if x.val is not None and getattr(x.val, "ndim", 0) > 0 and x.val.size == 1:
            context.add(ops.mb.const(val=dtype(x.val.item()), name=node.name))
            return
        cast(context, node, dtype, dtype_name)

    ops._cast = scalar_cast
    _model, spec, core = _traced_input()
    with torch.no_grad():
        traced = torch.jit.trace(core, (spec,), check_trace=False)
    ml = ct.convert(traced, inputs=[ct.TensorType(name="stft", shape=tuple(spec.shape))],
                    outputs=[ct.TensorType(name="mask")], convert_to="mlprogram",
                    compute_precision=ct.precision.FLOAT16, minimum_deployment_target=ct.target.macOS14,
                    skip_model_load=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "core.mlpackage"
        ml.save(str(pkg))
        compiled = ct.utils.compile_model(str(pkg))
        part = dest.with_name(dest.name + ".part")  # a half-copied model never looks complete
        shutil.rmtree(part, ignore_errors=True)
        shutil.move(compiled, part)
        shutil.rmtree(dest, ignore_errors=True)
        part.rename(dest)


if __name__ == "__main__":
    kind = sys.argv[1]
    name = COREML if kind == "coreml" else ONNX
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[3] / "models" / name
    (export_coreml if kind == "coreml" else export_onnx)(out)
    print(out)
