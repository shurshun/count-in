"""Guitar and vocal attenuation with BS-RoFormer SW, a six-stem model (bass, drums, other, vocals, guitar, piano).

One pass gives a guitar stem that keeps screamed vocals out better than Demucs htdemucs_6s did.
"""

from functools import lru_cache

import numpy as np
import soxr

from .io import resource

MODEL = "BS-Roformer-SW"
MODEL_SR = 44100

# Two overlapping windows per chunk: 4x faster than eight, the stems differ by about -35 dB.
OVERLAP = 2


def _device():
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


@lru_cache(maxsize=1)
def _model():
    """(model, stem names, chunk length in samples)."""
    import torch
    import yaml

    from .roformer.bs_roformer import BSRoformer

    folder = resource("models", "separator")
    cfg = yaml.load((folder / f"{MODEL}.yaml").read_text(), Loader=yaml.FullLoader)
    model = BSRoformer(**cfg["model"])
    state = torch.load(folder / f"{MODEL}.ckpt", map_location="cpu", weights_only=True)
    model.load_state_dict(state.get("state_dict", state))
    model.eval()
    chunk = cfg["model"]["stft_hop_length"] * (cfg["inference"]["dim_t"] - 1)
    return model, cfg["training"]["instruments"], chunk


def _chunk_starts(n: int, chunk: int, step: int) -> list[int]:
    """Chunk offsets covering n samples; the last chunk ends at n."""
    starts = list(range(0, max(n - chunk, 0), step))
    return starts + [max(n - chunk, 0)]


def separate(pcm: np.ndarray, sr: int, progress=None, only: tuple[str, ...] | None = None) -> dict[str, np.ndarray]:
    """Stems (all, or the ones named in only), sample-aligned with pcm and of its shape.

    progress(done, total) gets called per chunk.
    """
    import torch

    if pcm.shape[1] > 2:
        raise ValueError(f"guitar attenuation supports mono and stereo, not {pcm.shape[1]} channels")
    model, names, chunk = _model()
    x = pcm if pcm.shape[1] == 2 else np.repeat(pcm, 2, axis=1)
    if sr != MODEL_SR:
        x = soxr.resample(x, sr, MODEL_SR)

    mix = torch.from_numpy(np.ascontiguousarray(x.T, dtype=np.float32))
    n = mix.shape[1]
    window = torch.from_numpy(np.hamming(chunk).astype(np.float32))
    keep = [i for i, name in enumerate(names) if only is None or name in only]
    out = torch.zeros(len(keep), 2, n)
    weight = torch.zeros(n)
    starts = _chunk_starts(n, chunk, chunk // OVERLAP)

    device = _device()
    model.to(device)
    with torch.no_grad():
        for i, s in enumerate(starts):
            part = mix[None, :, s : s + chunk]
            try:
                y = model(part.to(device))[0].cpu()
            except NotImplementedError:
                device = "cpu"  # an op MPS lacks on this macOS
                model.to(device)
                y = model(part)[0]
            w = window[: y.shape[-1]]
            out[..., s : s + y.shape[-1]] += y[keep] * w
            weight[s : s + y.shape[-1]] += w
            if progress:
                progress(i + 1, len(starts))
    out /= weight.clamp(min=1e-10)

    stems = {}
    for name, y in zip([names[i] for i in keep], out.numpy()):
        y = y.T
        if sr != MODEL_SR:
            y = soxr.resample(y, MODEL_SR, sr)
        y = y[: len(pcm)]
        if len(y) < len(pcm):
            y = np.pad(y, ((0, len(pcm) - len(y)), (0, 0)))
        stems[name] = (y if pcm.shape[1] == 2 else y.mean(axis=1, keepdims=True)).astype(np.float32)
    return stems


def attenuate(pcm: np.ndarray, stems: dict[str, np.ndarray], levels: dict[str, float]) -> np.ndarray:
    """Original minus the removed share of each stem in levels (name -> dB, -inf removes it).

    Subtracting from the original keeps the other instruments as they were, instead of re-summing stems with their artifacts.
    """
    out = pcm.astype(np.float32, copy=True)
    for name, db in levels.items():
        keep = 0.0 if db == float("-inf") else 10 ** (db / 20)
        out -= (1 - keep) * stems[name]
    return out
