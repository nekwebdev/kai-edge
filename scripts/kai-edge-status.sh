#!/usr/bin/env bash
set -Eeuo pipefail

EDGE_ENV_FILE="${KAI_EDGE_ENV_FILE:-/etc/kai/edge.env}"
SERVICE_NAME="${KAI_EDGE_SERVICE_NAME:-kai-edge.service}"
DEFAULT_STATUS_FILE="/run/kai-edge/status.json"
JOURNAL_LINES="${KAI_STATUS_JOURNAL_LINES:-8}"

usage() {
  cat <<'EOF'
usage: kai-edge-status

shows kai-edge service state, configured trigger mode, runtime observability summary,
and recent kai-edge journal lines.
EOF
}

is_truthy() {
  case "${1,,}" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [[ -f "$EDGE_ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$EDGE_ENV_FILE"
fi

trigger_mode="${KAI_TRIGGER_MODE:-manual}"
status_file="${KAI_OBS_STATUS_FILE_PATH:-$DEFAULT_STATUS_FILE}"
status_enabled_raw="${KAI_OBS_STATUS_FILE_ENABLED:-1}"

service_active="unknown"
service_enabled="unknown"
if command -v systemctl >/dev/null 2>&1; then
  service_active="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  service_enabled="$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
  [[ -n "$service_active" ]] || service_active="unknown"
  [[ -n "$service_enabled" ]] || service_enabled="unknown"
fi

printf 'service: %s (enabled: %s)\n' "$service_active" "$service_enabled"
printf 'trigger mode: %s\n' "$trigger_mode"
printf 'status artifact: %s\n' "$status_file"

if is_truthy "$status_enabled_raw"; then
  if [[ -r "$status_file" ]]; then
    if command -v jq >/dev/null 2>&1; then
      jq -r '
        "daemon state: " + (.state // "unknown"),
        "vad backend: " + (.vad_backend // "n/a"),
        "state since: " + (.state_since // "unknown"),
        "updated: " + (.updated_at // "unknown"),
        "interactions: " + ((.counters.interactions // 0) | tostring),
        "accepted: " + ((.counters.accepted_utterances // 0) | tostring),
        "rejected: " + ((.counters.rejected_utterances // 0) | tostring),
        "errors: " + ((.counters.errors // 0) | tostring),
        "avg accepted utterance (ms): " + ((.counters.avg_accepted_utterance_ms // 0) | tostring),
        "last accepted utterance (ms): " + ((.counters.last_accepted_utterance_ms // "n/a") | tostring),
        "last rejection reason: " + ((.counters.last_rejection_reason // "n/a") | tostring),
        "last error summary: " + ((.counters.last_error_summary // "n/a") | tostring),
        "rejection reasons: " + ((.counters.rejection_reasons // {}) | tostring),
        "stop reasons: " + ((.counters.stop_reasons // {}) | tostring)
      ' "$status_file"
    else
      printf 'status artifact is readable but jq is unavailable: %s\n' "$status_file"
    fi
  else
    printf 'status artifact is enabled but not readable yet: %s\n' "$status_file"
  fi
else
  printf 'status artifact writing is disabled (KAI_OBS_STATUS_FILE_ENABLED=%s)\n' "$status_enabled_raw"
fi

printf '\nrecent journal lines (%s)\n' "$SERVICE_NAME"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -u "$SERVICE_NAME" -n "$JOURNAL_LINES" --no-pager -o short-iso 2>/dev/null || \
    printf 'journal unavailable for service: %s\n' "$SERVICE_NAME"
else
  printf 'journalctl is not available on this host\n'
fi
printf '\nfollow logs with: sudo journalctl -u %s -f\n' "$SERVICE_NAME"
