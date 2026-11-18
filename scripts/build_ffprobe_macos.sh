#!/bin/zsh
set -euo pipefail
task_root="${0:A:h:h}"
task_source="${1:?Provide an unpacked FFmpeg source directory}"
task_arch="${XMLBRIDGE_BUILD_ARCH:-arm64}"
task_build="$task_root/build/ffprobe-$task_arch"
task_destination="$task_root/vendor/ffprobe/$task_arch"
mkdir -p "$task_build" "$task_destination"
cd "$task_build"
"$task_source/configure" --disable-autodetect --disable-programs --enable-ffprobe \
  --disable-doc --disable-debug --disable-network --disable-avdevice --disable-avfilter \
  --disable-swscale --disable-swresample --disable-everything --enable-protocol=file \
  --enable-demuxer=mov,mxf,matroska,avi,asf,flv,mpegps,mpegts,mpegvideo,h264,hevc,wav,aac,aiff,mp3,image2 \
  --enable-parser=h264,hevc,mpegvideo,mpeg4video,mjpeg,aac,mpegaudio,ac3,flac,vp8,vp9,av1 \
  --enable-decoder=h264,hevc,mpeg2video,mpeg4,mjpeg,prores,dnxhd,rawvideo,aac,mp3,pcm_s16le,pcm_s24le,pcm_s32le,ac3,eac3,flac,alac,vp8,vp9 \
  --extra-cflags="-arch $task_arch -mmacosx-version-min=13.0 -ffile-prefix-map=/Users=/build" \
  --extra-ldflags="-arch $task_arch -mmacosx-version-min=13.0"
make -j8 ffprobe
cp ffprobe "$task_destination/ffprobe"
