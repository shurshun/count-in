#!/bin/sh
# Downloads model weights into models/ (beat_this final0, BS-RoFormer SW). Skips files already there.
set -eu
cd "$(dirname "$0")/.."
mkdir -p models/separator
[ -f models/final0.ckpt ] || curl -fsSL -o models/final0.ckpt \
  https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt
M=https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs
for f in BS-Roformer-SW.ckpt BS-Roformer-SW.yaml; do
  [ -f models/separator/$f ] || curl -fsSL -o models/separator/$f $M/$f
done
