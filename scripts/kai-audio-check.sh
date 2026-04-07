#!/usr/bin/env bash
set -Eeuo pipefail

record_seconds=3
smoke_test=0

usage() {
  cat <<'EOF'
usage: kai-audio-check.sh [--smoke-test] [--seconds n]

lists alsa playback and capture devices.
with --smoke-test, records a short sample from the default input
and plays it back through the default output.
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke-test)
      smoke_test=1
      ;;
    --seconds)
      shift
      [[ $# -gt 0 ]] || die "--seconds requires a value"
      [[ "$1" =~ ^[0-9]+$ ]] || die "--seconds must be an integer"
      record_seconds="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
  shift
done

log "playback devices"
aplay -l || true
printf '\n'

log "capture devices"
arecord -l || true
printf '\n'

log "alsa cards"
cat /proc/asound/cards || true
printf '\n'

if [[ "$smoke_test" != "1" ]]; then
  log "run with --smoke-test to record and replay a short sample"
  exit 0
fi

sample_path="$(mktemp /tmp/kai-audio-check.XXXXXX.wav)"
trap 'rm -f "$sample_path"' EXIT

log "recording ${record_seconds}s from the default capture device"
arecord -d "$record_seconds" -f S16_LE -r 16000 -c 1 "$sample_path"

log "playing the captured sample through the default playback device"
aplay "$sample_path"
