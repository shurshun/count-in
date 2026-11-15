"""Exports the RoFormer core to models/roformer-core.onnx and checks it against the full torch model.

    uv run --with onnx --with onnxscript --with onnxruntime python prototypes/onnx/export.py
    (on Windows: --with onnxruntime-directml instead of --with onnxruntime)
"""

import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch

from common import load, stft_in, stft_out

OUT = Path(__file__).resolve().parents[2] / "models" / "roformer-core.onnx"

model, core, chunk = load()
torch.manual_seed(0)
audio = torch.randn(1, 2, chunk) * 0.1
spec = stft_in(model, audio)
print("chunk", chunk, "samples; core input", tuple(spec.shape))

with torch.no_grad():
    ref = model(audio)
    mask = core(spec)
    mine = stft_out(model, spec, mask)
print("core+stft vs full model, max abs diff:", float((ref - mine).abs().max()))

t = time.time()
torch.onnx.export(core, (spec,), str(OUT), input_names=["stft"], output_names=["mask"], opset_version=18, dynamo=True, external_data=False)
print(f"exported in {time.time() - t:.0f} s:", OUT, f"{OUT.stat().st_size / 1e6:.0f} MB")

sess = ort.InferenceSession(str(OUT), providers=["CPUExecutionProvider"])
onnx_mask = torch.from_numpy(sess.run(None, {"stft": spec.numpy()})[0])
onnx_audio = stft_out(model, spec, onnx_mask)
diff = (ref - onnx_audio).abs().max()
rel = 20 * np.log10(float((ref - onnx_audio).pow(2).mean().sqrt() / ref.pow(2).mean().sqrt()))
print(f"onnx vs torch audio: max abs diff {float(diff):.2e}, error {rel:.0f} dB below the signal")
