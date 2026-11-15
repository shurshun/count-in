"""Shared pieces of the ONNX prototype: the network core without STFT, and the STFT around it.

BS-RoFormer = STFT -> band split -> 12 axial transformer blocks -> mask estimators -> complex mask -> iSTFT.
Only the middle (band split to masks) goes to ONNX: it is all the compute and has no complex numbers,
which ONNX and DirectML handle poorly. STFT, the complex multiply and iSTFT stay in torch on the CPU.
"""

import torch
from einops import rearrange
from torch import nn

from countin.core import stems


class Core(nn.Module):
    """Real STFT (b, f*s, t, 2) -> masks (b, n, f*s, t, 2)."""

    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, stft_repr):
        m = self.m
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


def load():
    """(full torch model, Core, chunk length in samples).

    Attention stays on scaled_dot_product_attention: it exports as MatMul + Softmax, while the einsum
    path exports as Einsum, which onnxruntime runs several times slower.
    """
    model, names, chunk = stems._model()
    return model, Core(model).eval(), chunk


def stft_in(model, audio):
    """(1, 2, n) audio -> real STFT (1, f*s, t, 2), as the model computes it."""
    window = model.stft_window_fn()
    spec = torch.stft(audio.reshape(2, -1), **model.stft_kwargs, window=window, return_complex=True)
    spec = torch.view_as_real(spec)  # (s, f, t, 2)
    return rearrange(spec, "s f t c -> 1 (f s) t c")


def stft_out(model, stft_repr, mask):
    """Apply the masks and go back to audio: (1, n, 2, samples)."""
    window = model.stft_window_fn()
    spec = torch.view_as_complex(stft_repr[:, None].contiguous()) * torch.view_as_complex(mask.contiguous())
    spec = rearrange(spec, "b n (f s) t -> (b n s) f t", s=2)
    audio = torch.istft(spec, **model.stft_kwargs, window=window, return_complex=False)
    return rearrange(audio, "(b n s) t -> b n s t", s=2, n=mask.shape[1])
