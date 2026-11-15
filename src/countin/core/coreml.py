"""BS-RoFormer core on Core ML (Apple GPU): about twice as fast per chunk as torch on MPS.

Only the middle of the network (band split to masks) runs in Core ML; it is all the compute and has no
complex numbers. STFT, the complex multiply and iSTFT stay in torch on the CPU. The Neural Engine is left
out on purpose: the graph does not fit it, Core ML splits it into dozens of pieces that take minutes to
compile and then run about 20x slower than the GPU.

The compiled model (`separator/BS-Roformer-SW-core.mlmodelc`) gets built from the torch weights on the
first separation (about 40 s) and kept in the data folder. `python -m countin.core.coreml` builds it into
`models/` by hand.
"""

import logging
import sys
import threading
from functools import lru_cache
from pathlib import Path

from . import assets

NAME = "separator/BS-Roformer-SW-core.mlmodelc"

log = logging.getLogger(__name__)
_lock = threading.Lock()


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


def load():
    """The compiled core on CPU and GPU, built on first use; None off macOS or when Core ML fails."""
    with _lock:
        return _load()


@lru_cache(maxsize=1)
def _load():
    if sys.platform != "darwin":
        return None
    try:
        import coremltools as ct

        path = assets.path(NAME)
        if not path.is_dir():
            path = assets.data_dir() / NAME
            export(path)
        return ct.models.CompiledMLModel(str(path), compute_units=ct.ComputeUnit.CPU_AND_GPU)
    except Exception:
        log.exception("Core ML unavailable, separating with torch")
        return None


def run(ml, model, part):
    """(1, 2, chunk) audio -> (stems, 2, chunk) through the Core ML core."""
    import numpy as np
    import torch

    spec = stft_in(model, part)
    mask = ml.predict({"stft": spec.numpy()})["mask"]
    return stft_out(model, spec, torch.from_numpy(np.asarray(mask, np.float32)))[..., : part.shape[-1]]


def export(dest: Path) -> None:
    """Traces the core, converts it to an fp16 ML program and compiles it into dest (.mlmodelc)."""
    import shutil
    import tempfile

    import coremltools as ct
    import torch
    from coremltools.converters.mil.frontend.torch import ops

    from . import stems

    # Traced shapes reach int() as one-element arrays, which coremltools only takes as scalars.
    cast = ops._cast

    def scalar_cast(context, node, dtype, dtype_name):
        x = ops._get_inputs(context, node, expected=1)[0]
        if x.val is not None and getattr(x.val, "ndim", 0) > 0 and x.val.size == 1:
            context.add(ops.mb.const(val=dtype(x.val.item()), name=node.name))
            return
        cast(context, node, dtype, dtype_name)

    ops._cast = scalar_cast
    model, _names, chunk = stems._model()
    model.float().cpu()
    spec = stft_in(model, torch.randn(1, 2, chunk, generator=torch.Generator().manual_seed(0)) * 0.1)
    core = _core(model)
    with torch.no_grad():
        core(spec)  # fills the rotary cache, so the trace reads it instead of building it
        traced = torch.jit.trace(core, (spec,), check_trace=False)
    ml = ct.convert(traced, inputs=[ct.TensorType(name="stft", shape=tuple(spec.shape))],
                    outputs=[ct.TensorType(name="mask")], convert_to="mlprogram",
                    compute_precision=ct.precision.FLOAT16, minimum_deployment_target=ct.target.macOS15,
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
    out = Path(__file__).resolve().parents[3] / "models" / NAME
    export(out)
    print(out)
