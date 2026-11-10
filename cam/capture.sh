#!/usr/bin/env bash
# capture.sh — the webcam's camera, turned into HLS.
#
# What it does, in one line: it takes the video from /dev/video0 and writes it as
# .m4s segments + an .m3u8 index in /out. Caddy serves those files; there is no
# video server of our own.
#
# About the delay: in HLS the floor is set by the segment size. ffmpeg does not
# publish a segment until it has finished writing it, so with -hls_time 1 the
# minimum is ~1s. With 2s it was 2s, and on top of that the player adds its own
# cushion. That is why 1s and a keyframe every 1s: if the GOP is longer than the
# segment, the segments are not independent and the player cannot start on any
# of them.
#
# The webcam's audio is NOT touched (`-an`). The radio is still the audio source:
# only image travels here.
#
# If the camera is disconnected, ffmpeg fails, the old index is deleted and it
# retries. The index goes FIRST: a playlist pointing at segments that no longer
# exist leaves players hung forever.
#
# Variables:
#   CAM_DEVICE  device inside the container (default /dev/video0)

set -uo pipefail

DEV="${CAM_DEVICE:-/dev/video0}"
OUT="${CAM_OUT:-/out}"

mkdir -p "$OUT"

limpiar() {
  rm -f "$OUT"/index.m3u8 "$OUT"/init.mp4 "$OUT"/seg*.m4s "$OUT"/seg*.m4s.tmp
}

salir() {
  # Kill ffmpeg BEFORE deleting the playlist: if the process stays alive while
  # we delete the .m4s files, the index points at segments that no longer exist
  # and players hang forever.
  if [ -n "${ff_pid:-}" ]; then
    kill "$ff_pid" 2>/dev/null || true
    wait "$ff_pid" 2>/dev/null || true
  fi
  limpiar
  exit 0
}

trap salir TERM INT

reintento=3
while :; do
  limpiar

  # The encode options are in an array because there are TWO outputs and in
  # ffmpeg -c:v applies only to the next output: if they are not repeated, the
  # second output goes with the default encoder.
  ENC=(
    -an
    -c:v libx264 -preset veryfast -tune zerolatency
    -profile:v baseline -pix_fmt yuv420p
    -b:v 600k -maxrate 700k -bufsize 1200k
    -g 15 -keyint_min 15 -sc_threshold 0
  )

  # CAM_CLOCK=1 burns the time into the frame. It is for measuring real delay
  # (glass to glass): you look at the time in the video and compare it with the
  # clock. Off by default; do not leave it on in normal use.
  if [[ "${CAM_CLOCK:-0}" == "1" ]]; then
    ENC+=(
      -vf "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='%{localtime\:%H%M%S}':fontsize=32:fontcolor=white:box=1:boxcolor=black@0.5:x=10:y=10"
    )
  fi

  ffmpeg -hide_banner -loglevel warning -nostdin \
    -f v4l2 \
    -input_format mjpeg \
    -video_size 640x480 \
    -framerate 15 \
    -i "$DEV" \
    "${ENC[@]}" \
    -f rtsp -rtsp_transport tcp \
    "rtsp://mediamtx:8554/cam" \
    "${ENC[@]}" \
    -f hls \
    -hls_time 1 \
    -hls_init_time 1 \
    -hls_list_size 6 \
    -hls_flags delete_segments+independent_segments+omit_endlist+temp_file+program_date_time \
    -hls_segment_type fmp4 \
    -hls_fmp4_init_filename init.mp4 \
    -hls_segment_filename "$OUT/seg%03d.m4s" \
    "$OUT/index.m3u8" &
  ff_pid=$!
  wait "$ff_pid"

  # temp_file: ffmpeg writes segNNN.m4s.tmp and renames when it finishes.
  # Caddy never sees a half-written segment.

  if (( reintento < 30 )); then
    reintento=$(( reintento * 2 ))
  fi
  echo "capture: ffmpeg exited, retrying in ${reintento}s" >&2
  sleep "$reintento"
done
