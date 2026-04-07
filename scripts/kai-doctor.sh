#!/usr/bin/env bash
set -Eeuo pipefail

DOCTOR_CONFIG_FILE="${KAI_DOCTOR_CONFIG_FILE:-/etc/kai/bootstrap.env}"

KAI_ROOT="/opt/kai"
KAI_CONFIG_DIR="/etc/kai"
KAI_STATE_DIR="/var/lib/kai"
KAI_LOG_DIR="/var/log/kai"
KAI_VENV_DIR="/opt/kai/venv"
CREATE_VENV="1"
INSTALL_AVAHI="1"
SSH_SNIPPET="/etc/ssh/sshd_config.d/60-kai-hardening.conf"
SYSTEMD_UNIT="/etc/systemd/system/kai-edge.service"

ok_count=0
warn_count=0
fail_count=0

usage() {
  cat <<'EOF'
usage: kai-doctor

validates whether the node looks ready after bootstrap.
returns exit 1 when one or more important checks fail.
EOF
}

ok() {
  printf 'ok   %s\n' "$*"
  ok_count=$((ok_count + 1))
}

warn() {
  printf 'warn %s\n' "$*"
  warn_count=$((warn_count + 1))
}

fail() {
  printf 'fail %s\n' "$*"
  fail_count=$((fail_count + 1))
}

have_command() {
  command -v "$1" >/dev/null 2>&1
}

resolve_sshd() {
  local path

  path="$(command -v sshd 2>/dev/null || true)"
  if [[ -n "$path" ]]; then
    printf '%s\n' "$path"
    return 0
  fi

  for path in /usr/sbin/sshd /usr/local/sbin/sshd /sbin/sshd; do
    if [[ -x "$path" ]]; then
      printf '%s\n' "$path"
      return 0
    fi
  done
}

load_config() {
  if [[ -f "$DOCTOR_CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$DOCTOR_CONFIG_FILE"
    ok "loaded bootstrap state: $DOCTOR_CONFIG_FILE"
  else
    warn "bootstrap state file not found, using built-in defaults: $DOCTOR_CONFIG_FILE"
  fi
}

check_directory() {
  local path=$1

  if [[ -d "$path" ]]; then
    ok "directory present: $path"
  else
    fail "missing directory: $path"
  fi
}

check_command() {
  local name=$1

  if have_command "$name"; then
    ok "command present: $name"
  else
    fail "missing command: $name"
  fi
}

check_required_directories() {
  check_directory "$KAI_ROOT"
  check_directory "$KAI_CONFIG_DIR"
  check_directory "$KAI_STATE_DIR"
  check_directory "$KAI_LOG_DIR"
}

check_required_commands() {
  check_command tailscale
  check_command jq
  check_command ffmpeg
  check_command python3
  check_command systemctl
}

check_ssh_state() {
  local sshd_bin

  if [[ -f "$SSH_SNIPPET" ]]; then
    ok "managed ssh snippet present: $SSH_SNIPPET"
  else
    fail "managed ssh snippet missing: $SSH_SNIPPET"
  fi

  sshd_bin="$(resolve_sshd)"
  if [[ -z "$sshd_bin" ]]; then
    fail "sshd binary not found"
    return 0
  fi

  if "$sshd_bin" -t >/dev/null 2>&1; then
    ok "sshd -t passed"
  else
    fail "sshd -t failed"
  fi
}

check_tailscale_state() {
  local status_json backend_state prefs_json run_ssh

  if ! have_command systemctl; then
    warn "skipping tailscaled service check because systemctl is unavailable"
    return 0
  fi

  if systemctl is-active --quiet tailscaled; then
    ok "tailscaled service active"
  else
    fail "tailscaled service not active"
  fi

  if ! have_command tailscale || ! have_command jq; then
    warn "skipping tailscale status checks because tailscale or jq is missing"
    return 0
  fi

  status_json="$(tailscale status --json 2>/dev/null || true)"
  if [[ -z "$status_json" ]]; then
    warn "could not read tailscale status"
  else
    backend_state="$(jq -r '.BackendState // "unknown"' <<<"$status_json")"
    case "$backend_state" in
      Running)
        ok "tailscale authenticated (BackendState=Running)"
        ;;
      Starting)
        warn "tailscale backend still starting"
        ;;
      NeedsLogin)
        warn "tailscale not authenticated yet (BackendState=NeedsLogin)"
        ;;
      NeedsMachineAuth)
        warn "tailscale waiting for tailnet admin approval"
        ;;
      Stopped)
        fail "tailscale backend state is Stopped"
        ;;
      *)
        warn "tailscale backend state: $backend_state"
        ;;
    esac
  fi

  prefs_json="$(tailscale debug prefs 2>/dev/null || true)"
  if [[ -z "$prefs_json" ]]; then
    warn "could not read tailscale debug prefs"
    return 0
  fi

  run_ssh="$(jq -r '.RunSSH // "unknown"' <<<"$prefs_json")"
  case "$run_ssh" in
    true)
      ok "tailscale ssh enabled"
      ;;
    false)
      warn "tailscale ssh disabled"
      ;;
    *)
      warn "tailscale ssh state unknown"
      ;;
  esac
}

check_avahi_state() {
  local enabled_state

  if [[ "$INSTALL_AVAHI" != "1" ]]; then
    ok "avahi disabled in bootstrap config"
    return 0
  fi

  if ! have_command systemctl; then
    warn "skipping avahi check because systemctl is unavailable"
    return 0
  fi

  enabled_state="$(systemctl is-enabled avahi-daemon 2>/dev/null || true)"
  case "$enabled_state" in
    enabled|enabled-runtime)
      if systemctl is-active --quiet avahi-daemon; then
        ok "avahi-daemon active"
      else
        fail "avahi-daemon enabled but not active"
      fi
      ;;
    *)
      fail "avahi-daemon expected by bootstrap config but not enabled"
      ;;
  esac
}

check_systemd_state() {
  local enabled_state active_state

  if [[ ! -f "$SYSTEMD_UNIT" ]]; then
    fail "kai-edge.service unit file missing: $SYSTEMD_UNIT"
    return 0
  fi

  if ! have_command systemctl; then
    ok "kai-edge.service unit present: $SYSTEMD_UNIT"
    warn "skipping kai-edge.service state check because systemctl is unavailable"
    return 0
  fi

  enabled_state="$(systemctl is-enabled kai-edge.service 2>/dev/null || true)"
  active_state="$(systemctl is-active kai-edge.service 2>/dev/null || true)"

  case "$enabled_state" in
    enabled|enabled-runtime)
      ok "kai-edge.service installed and enabled (active: ${active_state:-unknown})"
      ;;
    *)
      ok "kai-edge.service installed (enabled: ${enabled_state:-disabled}, active: ${active_state:-inactive})"
      ;;
  esac
}

check_python_venv() {
  if [[ "$CREATE_VENV" != "1" ]]; then
    ok "python venv disabled in bootstrap config"
    return 0
  fi

  if [[ -d "$KAI_VENV_DIR" ]]; then
    ok "python venv directory present: $KAI_VENV_DIR"
  else
    fail "python venv directory missing: $KAI_VENV_DIR"
  fi

  if [[ -x "$KAI_VENV_DIR/bin/python" ]]; then
    ok "python venv interpreter present: $KAI_VENV_DIR/bin/python"
  else
    fail "python venv interpreter missing: $KAI_VENV_DIR/bin/python"
  fi
}

count_alsa_devices() {
  local tool=$1
  local output

  output="$("$tool" -l 2>&1 || true)"
  grep -Ec '^card [0-9]+:' <<<"$output" || true
}

check_audio_visibility() {
  local playback_count capture_count

  if ! have_command aplay || ! have_command arecord; then
    warn "alsa tools not available; skipping audio visibility check"
    return 0
  fi

  playback_count="$(count_alsa_devices aplay)"
  if [[ "$playback_count" -gt 0 ]]; then
    ok "alsa playback devices visible: $playback_count"
  else
    warn "no alsa playback devices visible"
  fi

  capture_count="$(count_alsa_devices arecord)"
  if [[ "$capture_count" -gt 0 ]]; then
    ok "alsa capture devices visible: $capture_count"
  else
    warn "no alsa capture devices visible"
  fi
}

print_summary() {
  printf '\nsummary: %s ok, %s warn, %s fail\n' "$ok_count" "$warn_count" "$fail_count"
  if [[ "$fail_count" -gt 0 ]]; then
    printf 'result: not ready\n'
  elif [[ "$warn_count" -gt 0 ]]; then
    printf 'result: ready with warnings\n'
  else
    printf 'result: ready\n'
  fi
}

main() {
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

  load_config
  check_required_directories
  check_required_commands
  check_ssh_state
  check_tailscale_state
  check_avahi_state
  check_systemd_state
  check_python_venv
  check_audio_visibility
  print_summary

  if [[ "$fail_count" -gt 0 ]]; then
    exit 1
  fi
}

main "$@"
