#!/usr/bin/env bash
# capture.sh — la cámara del webcam, convertida en HLS.
#
# Qué hace, en una línea: agarra el video de /dev/video0 y lo escribe como
# segmentos .m4s + un índice .m3u8 en /out. Caddy sirve esos archivos; no
# hay servidor de video propio.
#
# Sobre el retraso: en HLS, el piso lo pone el tamaño del segmento. ffmpeg no
# publica un segmento hasta que lo terminó de escribir, así que con
# -hls_time 1 el mínimo es ~1s. Con 2s eran 2s, y encima el reproductor suma
# su propio colchón. Por eso 1s y keyframe cada 1s: si el GOP es más largo
# que el segmento, los segmentos no son independientes y el reproductor no
# puede arrancar en cualquiera de ellos.
#
# El audio del webcam NO se toca (`-an`). La radio sigue siendo la fuente
# de audio: acá sólo viaja imagen.
#
# Si la cámara está desconectada, ffmpeg falla, se borra el índice viejo y
# se reintenta. El índice VA PRIMERO: un playlist que apunta a segmentos
# que ya no existen deja reproductores colgados para siempre.
#
# Variables:
#   CAM_DEVICE  device dentro del contenedor (default /dev/video0)

set -uo pipefail

DEV="${CAM_DEVICE:-/dev/video0}"
OUT="${CAM_OUT:-/out}"

mkdir -p "$OUT"

limpiar() {
  rm -f "$OUT"/index.m3u8 "$OUT"/init.mp4 "$OUT"/seg*.m4s "$OUT"/seg*.m4s.tmp
}

salir() {
  limpiar
  exit 0
}

trap salir TERM INT

reintento=3
while :; do
  limpiar

  # Las opciones de encode están en un array porque hay DOS salidas y en
  # ffmpeg los -c:v aplican sólo al próximo output: si no se repiten, la
  # segunda salida se va con el encoder por defecto.
  ENC=(
    -an
    -c:v libx264 -preset veryfast -tune zerolatency
    -profile:v baseline -pix_fmt yuv420p
    -b:v 600k -maxrate 700k -bufsize 1200k
    -g 15 -keyint_min 15 -sc_threshold 0
    -force_key_frames "expr:gte(t,n_forced*1)"
  )

  # CAM_CLOCK=1 quema la hora en el cuadro. Es para medir retraso de verdad
  # (vidrio a vidrio): se mira la hora del video y se compara con el reloj.
  # Apagado por defecto; no lo dejes prendido en uso normal.
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
    "$OUT/index.m3u8"

  # temp_file: ffmpeg escribe segNNN.m4s.tmp y renombra al terminar.
  # Caddy nunca ve un segmento a medio escribir.

  if (( reintento < 30 )); then
    reintento=$(( reintento * 2 ))
  fi
  echo "capture: ffmpeg salió, reintento en ${reintento}s" >&2
  sleep "$reintento"
done
