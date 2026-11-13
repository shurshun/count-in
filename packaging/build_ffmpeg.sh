#!/bin/sh
# LGPL ffmpeg/ffprobe for the bundle: audio formats only, LAME linked statically, no network, no GPL parts.
set -eu
VER=7.1.1
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$ROOT/build/ffmpeg
LAME=$(brew --prefix lame)
mkdir -p "$WORK/lame" "$ROOT/bin"

# A folder holding only the .a files makes the linker take LAME and its mpg123 dependency statically.
install -m 644 "$LAME/lib/libmp3lame.a" "$(brew --prefix mpg123)/lib/libmpg123.a" "$WORK/lame/"
cd "$WORK"
[ -d ffmpeg-$VER ] || curl -sL https://ffmpeg.org/releases/ffmpeg-$VER.tar.xz | tar xJ
cd ffmpeg-$VER
./configure --prefix="$WORK/out" --disable-everything --disable-autodetect --disable-network --disable-doc \
  --disable-debug --enable-static --disable-shared --disable-ffplay \
  --enable-libmp3lame --extra-cflags="-I$LAME/include -mmacosx-version-min=12.0" \
  --extra-ldflags="-L$WORK/lame -mmacosx-version-min=12.0" \
  --enable-protocol=file,pipe,fd \
  --enable-demuxer=mp3,flac,mov,wav,aiff,ogg,pcm_f32le,image2 \
  --enable-muxer=mp3,flac,ipod,mp4,wav,pcm_f32le \
  --enable-decoder=mp3,mp3float,flac,alac,aac,vorbis,opus,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_s16be,pcm_s24be,pcm_f32be,png,mjpeg \
  --enable-encoder=libmp3lame,flac,alac,pcm_s16le,pcm_s24le,pcm_f32le \
  --enable-parser=mpegaudio,flac,aac,vorbis,opus,png,mjpeg \
  --enable-filter=aresample,aformat,anull,null,format,scale \
  --enable-swresample --enable-swscale --extra-libs="-lmpg123"
make -j"$(sysctl -n hw.ncpu)" ffmpeg ffprobe
cp ffmpeg ffprobe "$ROOT/bin/"
cp COPYING.LGPLv2.1 "$ROOT/bin/FFMPEG-LICENSE.txt"
"$ROOT/bin/ffmpeg" -hide_banner -version | head -1
