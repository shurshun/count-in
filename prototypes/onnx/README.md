# ONNX Runtime + DirectML prototype

Goal: run guitar and vocal separation on any Windows GPU. The Windows bundle ships the CPU-only PyTorch wheel from PyPI, so `stems._device()` never finds CUDA there.

## How it works

- `common.py` splits BS-RoFormer in three. STFT, the complex mask multiply and iSTFT stay in torch on the CPU. The middle part (band split, 12 transformer blocks, mask estimators) has no complex numbers and holds all the compute.
- `export.py` exports that middle part to `models/roformer-core.onnx` (712 MB, opset 18, torch.export exporter) and compares the result with the full torch model.
- `bench.py` times one separation chunk (409,600 samples, 9.3 s of audio) per backend and multiplies by 52 chunks for a 4-minute song.

## Results on Apple M2 Pro (macOS 27, onnxruntime 1.32)

| Backend | Per chunk | 4-min song | Stems vs torch CPU |
|---|---|---|---|
| torch CPU | 5.0 s | ~4.3 min | reference |
| torch MPS (what the app uses on a Mac) | 2.8 s | ~2.4 min | −130 dB |
| onnxruntime CPU | 15.7 s | ~13.6 min | −126 dB |

- The export matches: audio from the ONNX core differs from the torch model by −130 dB.
- onnxruntime on the CPU is about 3x slower than torch, so the CPU fallback should stay on torch.
- Attention must export through `scaled_dot_product_attention` (MatMul + Softmax). The einsum path exported as `Einsum`, which took 42% of the time and made the CPU run 21 s per chunk.
- The CoreML provider splits the graph into 246 partitions and did not finish; the Mac keeps MPS.
- DirectML runs only on Windows. A GitHub runner has no GPU, so the GPU number needs a real Windows PC.

## Running it on Windows

```
git clone https://github.com/shurshun/count-in && cd count-in
git switch onnx-directml-prototype
uv sync
bash packaging/fetch_models.sh
uv run --with onnx --with onnxscript --with onnxruntime-directml python prototypes/onnx/export.py
uv run --with onnxruntime-directml python prototypes/onnx/bench.py
```

`fetch_models.sh` needs Git Bash. The bench prints torch CPU, onnxruntime CPU and onnxruntime DirectML side by side.
