#!/bin/sh
# Downloads model weights into models/ (beat_this final0, BS-RoFormer SW) and checks their sha256.
set -eu
cd "$(dirname "$0")/.."
mkdir -p models/separator

sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

# fetch <file> <url> <sha256>: downloads when missing, from the original publisher and then from this project's
# assets-v1 release; fails and removes the file on a hash mismatch.
A=https://github.com/shurshun/count-in/releases/download/assets-v1
fetch() {
  if [ ! -f "$1" ] && ! curl -fsSL --retry 5 --retry-all-errors -o "$1" "$2"; then
    rm -f "$1"
    curl -fsSL --retry 5 --retry-all-errors -o "$1" "$A/$(basename "$1")"
  fi
  got=$(sha "$1")
  if [ "$got" != "$3" ]; then
    rm -f "$1"
    echo "sha256 mismatch for $1: got $got" >&2
    exit 1
  fi
}

fetch models/final0.ckpt https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt \
  8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331
M=https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs
fetch models/separator/BS-Roformer-SW.ckpt $M/BS-Roformer-SW.ckpt \
  24e7d35ee9c64415673d3fd33e06a67cac2c103c5df6267ba1576459c775916e
fetch models/separator/BS-Roformer-SW.yaml $M/BS-Roformer-SW.yaml \
  b558996f1e25eb48798bd6502505a5de94c4f966d6edfb1a0420f06cc40b501a

# Windows separates through DirectML with the ONNX export of the RoFormer core, kept only in this project's release
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    fetch models/separator/BS-Roformer-SW-core.onnx $A/BS-Roformer-SW-core.onnx \
      09593a8ef0027cd1f281c6a3cf6241a474249c573a665606102de35611b7bccb ;;
esac
