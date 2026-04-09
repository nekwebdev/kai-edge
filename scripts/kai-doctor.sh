#!/usr/bin/env bash
set -Eeuo pipefail

DOCTOR_CONFIG_FILE="${KAI_DOCTOR_CONFIG_FILE:-/etc/kai/bootstrap.env}"

KAI_USER=""
KAI_GROUP=""
KAI_ROOT="/opt/kai"
KAI_APP_DIR="/opt/kai/app"
KAI_BIN_DIR="/opt/kai/bin"
KAI_CONFIG_DIR="/etc/kai"
KAI_STATE_DIR="/var/lib/kai"
KAI_LOG_DIR="/var/log/kai"
KAI_VENV_DIR="/opt/kai/venv"
CREATE_VENV="1"
INSTALL_AVAHI="1"
INSTALL_RASPAP="1"
RASPAP_INSTALL_URL="https://install.raspap.com"
RASPAP_AP_SSID="Kai-Setup"
RASPAP_AP_SUBNET_CIDR="10.42.0.1/24"
RASPAP_AP_DHCP_RANGE="10.42.0.50,10.42.0.150,255.255.255.0,12h"
RASPAP_ENABLE_FALLBACK_AP="1"
RASPAP_ADMIN_USER="admin"
SSH_SNIPPET="/etc/ssh/sshd_config.d/60-kai-hardening.conf"
SYSTEMD_UNIT="/etc/systemd/system/kai-edge.service"
EDGE_ENV_FILE="/etc/kai/edge.env"
EDGE_RUNTIME_PACKAGE_DIR="/opt/kai/app/kai_edge"
EDGE_DAEMON_HELPER="/opt/kai/bin/kai-edge-daemon"
EDGE_TRIGGER_HELPER="/opt/kai/bin/kai-edge-trigger"
EDGE_STATUS_HELPER="/opt/kai/bin/kai-edge-status"
PUSH_TO_TALK_HELPER="/opt/kai/bin/kai-push-to-talk"
ENABLE_KAI_EDGE_SERVICE="0"
MANAGE_JOURNALD_RETENTION="1"
JOURNALD_DROPIN="/etc/systemd/journald.conf.d/90-kai-edge-retention.conf"
JOURNALD_SYSTEM_MAX_USE="200M"
JOURNALD_RUNTIME_MAX_USE="64M"
JOURNALD_MAX_FILE_SEC="7day"
MANAGE_KAI_LOGROTATE="1"
KAI_LOGROTATE_CONFIG="/etc/logrotate.d/kai-edge"

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

is_non_negative_int() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

is_positive_int() {
  is_non_negative_int "$1" && [[ "$1" -gt 0 ]]
}

normalize_bool() {
  local raw="${1,,}"
  case "$raw" in
    1|true|yes|on)
      printf '1\n'
      ;;
    0|false|no|off)
      printf '0\n'
      ;;
    *)
      printf 'invalid\n'
      ;;
  esac
}

check_required_directories() {
  check_directory "$KAI_ROOT"
  check_directory "$KAI_APP_DIR"
  check_directory "$KAI_BIN_DIR"
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
  check_command arecord
  check_command aplay
  check_command logrotate
  check_command journalctl
}

check_runtime_user() {
  if [[ -z "$KAI_USER" ]]; then
    warn "runtime user not recorded in bootstrap state"
    return 0
  fi

  if id "$KAI_USER" >/dev/null 2>&1; then
    ok "runtime user present: $KAI_USER"
  else
    fail "runtime user missing: $KAI_USER"
    return 0
  fi

  if id -nG "$KAI_USER" | tr ' ' '\n' | grep -Fxq audio; then
    ok "runtime user is in audio group: $KAI_USER"
  else
    fail "runtime user is not in audio group: $KAI_USER"
  fi
}

check_runtime_files() {
  local backend_url trigger_mode trigger_socket sample_rate
  local wakeword_backend wakeword_access_key wakeword_builtin_keyword wakeword_keyword_path wakeword_model_path
  local wakeword_sensitivity wakeword_cooldown_ms wakeword_speech_timeout_ms
  local vad_aggressiveness vad_frame_ms vad_min_speech_ms vad_min_speech_run_ms vad_trailing_silence_ms
  local vad_max_utterance_ms vad_cooldown_ms vad_energy_threshold
  local obs_summary_seconds obs_summary_interactions obs_status_enabled_raw obs_status_enabled
  local obs_status_path

  if [[ -x "$PUSH_TO_TALK_HELPER" ]]; then
    ok "push-to-talk helper present: $PUSH_TO_TALK_HELPER"
  else
    fail "push-to-talk helper missing: $PUSH_TO_TALK_HELPER"
  fi

  if [[ -x "$EDGE_DAEMON_HELPER" ]]; then
    ok "edge daemon helper present: $EDGE_DAEMON_HELPER"
  else
    fail "edge daemon helper missing: $EDGE_DAEMON_HELPER"
  fi

  if [[ -x "$EDGE_TRIGGER_HELPER" ]]; then
    ok "edge trigger helper present: $EDGE_TRIGGER_HELPER"
  else
    fail "edge trigger helper missing: $EDGE_TRIGGER_HELPER"
  fi

  if [[ -x "$EDGE_STATUS_HELPER" ]]; then
    ok "edge status helper present: $EDGE_STATUS_HELPER"
  else
    fail "edge status helper missing: $EDGE_STATUS_HELPER"
  fi

  if [[ -d "$EDGE_RUNTIME_PACKAGE_DIR" ]]; then
    ok "edge runtime package present: $EDGE_RUNTIME_PACKAGE_DIR"
  else
    fail "edge runtime package missing: $EDGE_RUNTIME_PACKAGE_DIR"
  fi

  if [[ -f "$EDGE_ENV_FILE" ]]; then
    ok "runtime env file present: $EDGE_ENV_FILE"
  else
    fail "runtime env file missing: $EDGE_ENV_FILE"
    return 0
  fi

  backend_url="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_CORE_BASE_URL:-}"
    ) 2>/dev/null || true
  )"

  if [[ -n "$backend_url" ]]; then
    ok "kai-core base URL configured: $backend_url"
  else
    warn "kai-core base URL is blank in $EDGE_ENV_FILE"
  fi

  trigger_mode="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_TRIGGER_MODE:-manual}"
    ) 2>/dev/null || true
  )"

  case "$trigger_mode" in
    manual|vad|wakeword)
      ok "trigger mode configured: $trigger_mode"
      ;;
    *)
      fail "invalid or blank KAI_TRIGGER_MODE in $EDGE_ENV_FILE: ${trigger_mode:-<blank>}"
      ;;
  esac

  trigger_socket="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_TRIGGER_SOCKET_PATH:-/run/kai-edge/trigger.sock}"
    ) 2>/dev/null || true
  )"

  if [[ "$trigger_mode" == "manual" ]]; then
    if [[ -n "$trigger_socket" ]]; then
      ok "edge trigger socket configured: $trigger_socket"
    else
      fail "edge trigger socket path is blank in $EDGE_ENV_FILE"
    fi
  elif [[ "$trigger_mode" == "vad" || "$trigger_mode" == "wakeword" ]]; then
    if [[ -n "$trigger_socket" ]]; then
      ok "manual trigger socket still configured for fallback use: $trigger_socket"
    else
      warn "KAI_TRIGGER_SOCKET_PATH is blank; manual trigger helper will not work while in $trigger_mode mode"
    fi
  fi

  obs_summary_seconds="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_OBS_SUMMARY_INTERVAL_SECONDS:-300}"
    ) 2>/dev/null || true
  )"
  if is_non_negative_int "$obs_summary_seconds"; then
    ok "observability summary seconds configured: $obs_summary_seconds"
  else
    fail "invalid KAI_OBS_SUMMARY_INTERVAL_SECONDS in $EDGE_ENV_FILE: ${obs_summary_seconds:-<blank>}"
  fi

  obs_summary_interactions="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS:-10}"
    ) 2>/dev/null || true
  )"
  if is_non_negative_int "$obs_summary_interactions"; then
    ok "observability summary interactions configured: $obs_summary_interactions"
  else
    fail "invalid KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS in $EDGE_ENV_FILE: ${obs_summary_interactions:-<blank>}"
  fi

  obs_status_enabled_raw="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_OBS_STATUS_FILE_ENABLED:-1}"
    ) 2>/dev/null || true
  )"
  obs_status_enabled="$(normalize_bool "$obs_status_enabled_raw")"
  case "$obs_status_enabled" in
    1)
      ok "runtime status artifact enabled"
      ;;
    0)
      ok "runtime status artifact disabled"
      ;;
    *)
      fail "invalid KAI_OBS_STATUS_FILE_ENABLED in $EDGE_ENV_FILE: ${obs_status_enabled_raw:-<blank>}"
      ;;
  esac

  obs_status_path="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_OBS_STATUS_FILE_PATH:-/run/kai-edge/status.json}"
    ) 2>/dev/null || true
  )"
  if [[ "$obs_status_path" = /* ]]; then
    ok "runtime status artifact path configured: $obs_status_path"
  else
    fail "invalid KAI_OBS_STATUS_FILE_PATH in $EDGE_ENV_FILE: ${obs_status_path:-<blank>}"
  fi

  if [[ "$trigger_mode" == "wakeword" ]]; then
    wakeword_backend="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_WAKEWORD_BACKEND:-porcupine}"
      ) 2>/dev/null || true
    )"
    case "$wakeword_backend" in
      porcupine)
        ok "wakeword backend configured: $wakeword_backend"
        ;;
      *)
        fail "unsupported KAI_WAKEWORD_BACKEND in $EDGE_ENV_FILE: ${wakeword_backend:-<blank>}"
        ;;
    esac

    wakeword_access_key="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_WAKEWORD_ACCESS_KEY:-}"
      ) 2>/dev/null || true
    )"
    if [[ -n "$wakeword_access_key" ]]; then
      ok "wakeword access key is configured"
    else
      fail "KAI_WAKEWORD_ACCESS_KEY is blank in $EDGE_ENV_FILE"
    fi

    wakeword_builtin_keyword="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_WAKEWORD_BUILTIN_KEYWORD:-porcupine}"
      ) 2>/dev/null || true
    )"
    wakeword_keyword_path="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_WAKEWORD_KEYWORD_PATH:-}"
      ) 2>/dev/null || true
    )"
    if [[ -n "$wakeword_builtin_keyword" ]] || [[ -n "$wakeword_keyword_path" ]]; then
      ok "wakeword keyword source configured"
    else
      fail "set KAI_WAKEWORD_BUILTIN_KEYWORD or KAI_WAKEWORD_KEYWORD_PATH in $EDGE_ENV_FILE"
    fi

    if [[ -n "$wakeword_keyword_path" ]]; then
      if [[ "$wakeword_keyword_path" = /* ]]; then
        ok "wakeword keyword path is absolute: $wakeword_keyword_path"
      else
        fail "KAI_WAKEWORD_KEYWORD_PATH must be absolute: $wakeword_keyword_path"
      fi
      if [[ -f "$wakeword_keyword_path" ]]; then
        ok "wakeword keyword file exists: $wakeword_keyword_path"
      else
        fail "wakeword keyword file missing: $wakeword_keyword_path"
      fi
    fi

    wakeword_model_path="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_WAKEWORD_MODEL_PATH:-}"
      ) 2>/dev/null || true
    )"
    if [[ -n "$wakeword_model_path" ]]; then
      if [[ "$wakeword_model_path" = /* ]]; then
        ok "wakeword model path is absolute: $wakeword_model_path"
      else
        fail "KAI_WAKEWORD_MODEL_PATH must be absolute: $wakeword_model_path"
      fi
      if [[ -f "$wakeword_model_path" ]]; then
        ok "wakeword model file exists: $wakeword_model_path"
      else
        fail "wakeword model file missing: $wakeword_model_path"
      fi
    fi

    wakeword_sensitivity="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_WAKEWORD_SENSITIVITY:-0.5}"
      ) 2>/dev/null || true
    )"
    if [[ "$wakeword_sensitivity" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v value="$wakeword_sensitivity" 'BEGIN { exit !(value >= 0 && value <= 1) }'; then
      ok "wakeword sensitivity configured: $wakeword_sensitivity"
    else
      fail "invalid KAI_WAKEWORD_SENSITIVITY in $EDGE_ENV_FILE: ${wakeword_sensitivity:-<blank>}"
    fi

    wakeword_cooldown_ms="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_WAKEWORD_DETECTION_COOLDOWN_MS:-1500}"
      ) 2>/dev/null || true
    )"
    if is_non_negative_int "$wakeword_cooldown_ms"; then
      ok "wakeword detection cooldown configured: ${wakeword_cooldown_ms}ms"
    else
      fail "invalid KAI_WAKEWORD_DETECTION_COOLDOWN_MS in $EDGE_ENV_FILE: ${wakeword_cooldown_ms:-<blank>}"
    fi

    wakeword_speech_timeout_ms="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS:-3000}"
      ) 2>/dev/null || true
    )"
    if is_non_negative_int "$wakeword_speech_timeout_ms"; then
      ok "wakeword post-wake speech timeout configured: ${wakeword_speech_timeout_ms}ms"
    else
      fail "invalid KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS in $EDGE_ENV_FILE: ${wakeword_speech_timeout_ms:-<blank>}"
    fi

    sample_rate="$(
      (
        # shellcheck source=/dev/null
        source "$EDGE_ENV_FILE"
        printf '%s' "${KAI_AUDIO_SAMPLE_RATE:-16000}"
      ) 2>/dev/null || true
    )"
    if [[ "$sample_rate" == "16000" ]]; then
      ok "wakeword sample rate is compatible: $sample_rate"
    else
      fail "wakeword mode currently requires KAI_AUDIO_SAMPLE_RATE=16000 (got: ${sample_rate:-<blank>})"
    fi
  fi

  if [[ "$trigger_mode" == "manual" ]]; then
    return 0
  fi

  vad_aggressiveness="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_VAD_AGGRESSIVENESS:-}"
    ) 2>/dev/null || true
  )"
  if is_non_negative_int "$vad_aggressiveness" && [[ "$vad_aggressiveness" -le 3 ]]; then
    ok "VAD aggressiveness configured: $vad_aggressiveness"
  else
    fail "invalid KAI_VAD_AGGRESSIVENESS in $EDGE_ENV_FILE: ${vad_aggressiveness:-<blank>}"
  fi

  vad_frame_ms="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_VAD_FRAME_MS:-}"
    ) 2>/dev/null || true
  )"
  case "$vad_frame_ms" in
    10|20|30)
      ok "VAD frame size configured: ${vad_frame_ms}ms"
      ;;
    *)
      fail "invalid KAI_VAD_FRAME_MS in $EDGE_ENV_FILE: ${vad_frame_ms:-<blank>}"
      ;;
  esac

  vad_min_speech_ms="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_VAD_MIN_SPEECH_MS:-}"
    ) 2>/dev/null || true
  )"
  if is_positive_int "$vad_min_speech_ms"; then
    ok "VAD minimum speech duration configured: ${vad_min_speech_ms}ms"
  else
    fail "invalid KAI_VAD_MIN_SPEECH_MS in $EDGE_ENV_FILE: ${vad_min_speech_ms:-<blank>}"
  fi

  vad_min_speech_run_ms="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_VAD_MIN_SPEECH_RUN_MS:-}"
    ) 2>/dev/null || true
  )"
  if is_positive_int "$vad_min_speech_run_ms"; then
    ok "VAD minimum speech run configured: ${vad_min_speech_run_ms}ms"
  else
    fail "invalid KAI_VAD_MIN_SPEECH_RUN_MS in $EDGE_ENV_FILE: ${vad_min_speech_run_ms:-<blank>}"
  fi

  vad_trailing_silence_ms="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_VAD_TRAILING_SILENCE_MS:-}"
    ) 2>/dev/null || true
  )"
  if is_positive_int "$vad_trailing_silence_ms"; then
    ok "VAD trailing silence configured: ${vad_trailing_silence_ms}ms"
  else
    fail "invalid KAI_VAD_TRAILING_SILENCE_MS in $EDGE_ENV_FILE: ${vad_trailing_silence_ms:-<blank>}"
  fi

  vad_max_utterance_ms="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_VAD_MAX_UTTERANCE_MS:-}"
    ) 2>/dev/null || true
  )"
  if is_positive_int "$vad_max_utterance_ms"; then
    ok "VAD max utterance duration configured: ${vad_max_utterance_ms}ms"
  else
    fail "invalid KAI_VAD_MAX_UTTERANCE_MS in $EDGE_ENV_FILE: ${vad_max_utterance_ms:-<blank>}"
  fi

  vad_cooldown_ms="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_VAD_COOLDOWN_MS:-}"
    ) 2>/dev/null || true
  )"
  if is_non_negative_int "$vad_cooldown_ms"; then
    ok "VAD cooldown configured: ${vad_cooldown_ms}ms"
  else
    fail "invalid KAI_VAD_COOLDOWN_MS in $EDGE_ENV_FILE: ${vad_cooldown_ms:-<blank>}"
  fi

  vad_energy_threshold="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_VAD_ENERGY_THRESHOLD:-}"
    ) 2>/dev/null || true
  )"
  if is_positive_int "$vad_energy_threshold"; then
    ok "VAD fallback energy threshold configured: $vad_energy_threshold"
  else
    fail "invalid KAI_VAD_ENERGY_THRESHOLD in $EDGE_ENV_FILE: ${vad_energy_threshold:-<blank>}"
  fi

  if is_positive_int "$vad_min_speech_ms" && is_positive_int "$vad_max_utterance_ms" && [[ "$vad_max_utterance_ms" -le "$vad_min_speech_ms" ]]; then
    fail "KAI_VAD_MAX_UTTERANCE_MS must be greater than KAI_VAD_MIN_SPEECH_MS"
  fi
  if is_positive_int "$vad_min_speech_run_ms" && is_positive_int "$vad_max_utterance_ms" && [[ "$vad_max_utterance_ms" -le "$vad_min_speech_run_ms" ]]; then
    fail "KAI_VAD_MAX_UTTERANCE_MS must be greater than KAI_VAD_MIN_SPEECH_RUN_MS"
  fi

  if [[ "$trigger_mode" == "wakeword" ]]; then
    warn "wakeword and VAD runtime behavior is not fully validated by kai-doctor; run live wake + speech tests with the physical mute switch"
  else
    warn "VAD runtime behavior is not fully validated by kai-doctor; run a live speech test with the physical mute switch"
  fi
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

check_raspap_state() {
  local enabled_state ap_ip
  local expected_fallback=0

  if [[ "$INSTALL_RASPAP" != "1" ]]; then
    ok "raspap disabled in bootstrap config"
    return 0
  fi

  if [[ -d /etc/raspap ]]; then
    ok "raspap directory present: /etc/raspap"
  else
    fail "raspap directory missing: /etc/raspap"
  fi

  if ! have_command systemctl; then
    warn "skipping raspap service checks because systemctl is unavailable"
    return 0
  fi

  enabled_state="$(systemctl is-enabled lighttpd 2>/dev/null || true)"
  case "$enabled_state" in
    enabled|enabled-runtime)
      ok "raspap web service enabled (lighttpd)"
      ;;
    *)
      fail "raspap web service not enabled (lighttpd)"
      ;;
  esac

  if systemctl is-active --quiet lighttpd; then
    ok "raspap web service active (lighttpd)"
  else
    fail "raspap web service not active (lighttpd)"
  fi

  if [[ -f /etc/hostapd/hostapd.conf ]]; then
    if grep -Fqx "ssid=${RASPAP_AP_SSID}" /etc/hostapd/hostapd.conf; then
      ok "raspap AP SSID configured: $RASPAP_AP_SSID"
    else
      fail "raspap AP SSID does not match expected value: $RASPAP_AP_SSID"
    fi
  else
    fail "hostapd config missing: /etc/hostapd/hostapd.conf"
  fi

  if [[ -f /etc/dnsmasq.d/090_wlan0.conf ]]; then
    if grep -Fqx "dhcp-range=${RASPAP_AP_DHCP_RANGE}" /etc/dnsmasq.d/090_wlan0.conf; then
      ok "raspap AP DHCP range configured"
    else
      fail "raspap AP DHCP range does not match expected value"
    fi
  else
    fail "dnsmasq config missing: /etc/dnsmasq.d/090_wlan0.conf"
  fi

  if [[ -f /etc/dhcpcd.conf ]]; then
    ap_ip="${RASPAP_AP_SUBNET_CIDR%/*}"
    if grep -Fqx "static ip_address=${RASPAP_AP_SUBNET_CIDR}" /etc/dhcpcd.conf; then
      ok "raspap AP static subnet configured: $RASPAP_AP_SUBNET_CIDR"
    else
      fail "raspap AP static subnet does not match expected value: $RASPAP_AP_SUBNET_CIDR"
    fi

    if [[ -n "$ap_ip" ]] && grep -Fqx "static routers=${ap_ip}" /etc/dhcpcd.conf; then
      fail "raspap dhcpcd config still sets static routers to AP self IP ($ap_ip), which can hijack default routing"
    else
      ok "raspap dhcpcd config does not set AP self IP as static router"
    fi

    if [[ "$RASPAP_ENABLE_FALLBACK_AP" == "1" ]]; then
      expected_fallback=1
      if grep -Fqx "fallback static_wlan0" /etc/dhcpcd.conf; then
        ok "raspap fallback AP behavior enabled for wlan0"
      else
        fail "raspap fallback AP behavior expected but not configured for wlan0"
      fi
    fi

    if [[ "$expected_fallback" != "1" ]] && grep -Fqx "fallback static_wlan0" /etc/dhcpcd.conf; then
      warn "raspap fallback AP behavior is enabled in dhcpcd.conf but disabled in bootstrap config"
    fi
  else
    fail "dhcpcd config missing: /etc/dhcpcd.conf"
  fi

  if [[ -n "${ap_ip:-}" ]] && ip route show default 2>/dev/null | grep -Fq "via ${ap_ip} dev wlan0"; then
    fail "default route currently points to AP self IP on wlan0 ($ap_ip); this breaks upstream connectivity and tailscale stability"
  else
    ok "default route is not pinned to AP self IP on wlan0"
  fi

  if [[ -f /etc/raspap/raspap.auth ]]; then
    if head -n 1 /etc/raspap/raspap.auth | grep -Fqx "$RASPAP_ADMIN_USER"; then
      ok "raspap admin user configured: $RASPAP_ADMIN_USER"
    else
      fail "raspap admin user in /etc/raspap/raspap.auth does not match expected value: $RASPAP_ADMIN_USER"
    fi
  else
    fail "raspap auth file missing: /etc/raspap/raspap.auth"
  fi
}

check_systemd_state() {
  local enabled_state active_state trigger_socket trigger_mode

  if [[ ! -f "$SYSTEMD_UNIT" ]]; then
    fail "kai-edge.service unit file missing: $SYSTEMD_UNIT"
    return 0
  fi

  if grep -Fq "kai-edge-daemon" "$SYSTEMD_UNIT"; then
    ok "kai-edge.service uses the runtime daemon entrypoint"
  else
    fail "kai-edge.service does not reference kai-edge-daemon (placeholder unit or stale config)"
  fi

  if grep -Fq "StandardOutput=journal" "$SYSTEMD_UNIT"; then
    ok "kai-edge.service stdout is routed to journald"
  else
    fail "kai-edge.service stdout is not explicitly routed to journald"
  fi

  if grep -Fq "StandardError=journal" "$SYSTEMD_UNIT"; then
    ok "kai-edge.service stderr is routed to journald"
  else
    fail "kai-edge.service stderr is not explicitly routed to journald"
  fi

  if ! have_command systemctl; then
    ok "kai-edge.service unit present: $SYSTEMD_UNIT"
    warn "skipping kai-edge.service state check because systemctl is unavailable"
    return 0
  fi

  enabled_state="$(systemctl is-enabled kai-edge.service 2>/dev/null || true)"
  active_state="$(systemctl is-active kai-edge.service 2>/dev/null || true)"

  if [[ "$ENABLE_KAI_EDGE_SERVICE" == "1" ]]; then
    case "$enabled_state" in
      enabled|enabled-runtime)
        ok "kai-edge.service enabled as expected"
        ;;
      *)
        fail "kai-edge.service should be enabled (ENABLE_KAI_EDGE_SERVICE=1), but state is: ${enabled_state:-disabled}"
        ;;
    esac

    if [[ "$active_state" == "active" ]]; then
      ok "kai-edge.service active as expected"
    else
      fail "kai-edge.service should be active (ENABLE_KAI_EDGE_SERVICE=1), but state is: ${active_state:-inactive}"
    fi
  else
    if [[ "$active_state" == "active" ]]; then
      ok "kai-edge.service active (manual or prior enable)"
    elif [[ "$enabled_state" == "enabled" || "$enabled_state" == "enabled-runtime" ]]; then
      warn "kai-edge.service is enabled but not active (state: ${active_state:-inactive})"
    else
      ok "kai-edge.service installed and inactive by default (ENABLE_KAI_EDGE_SERVICE=0)"
    fi
  fi

  if [[ "$active_state" != "active" ]]; then
    return 0
  fi

  trigger_mode="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_TRIGGER_MODE:-manual}"
    ) 2>/dev/null || true
  )"

  case "$trigger_mode" in
    manual|vad|wakeword)
      ;;
    *)
      fail "cannot verify runtime mode because KAI_TRIGGER_MODE is invalid: ${trigger_mode:-<blank>}"
      return 0
      ;;
  esac

  trigger_socket="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_TRIGGER_SOCKET_PATH:-/run/kai-edge/trigger.sock}"
    ) 2>/dev/null || true
  )"

  if [[ "$trigger_mode" == "manual" ]]; then
    if [[ -z "$trigger_socket" ]]; then
      fail "cannot verify daemon trigger socket because KAI_TRIGGER_SOCKET_PATH is empty"
      return 0
    fi

    if [[ -S "$trigger_socket" ]]; then
      ok "daemon trigger socket present: $trigger_socket"
    else
      fail "daemon trigger socket missing while service is active: $trigger_socket"
    fi
    return 0
  fi

  if [[ -z "$trigger_socket" ]]; then
    ok "trigger socket check skipped in $trigger_mode mode (KAI_TRIGGER_SOCKET_PATH is blank)"
  elif [[ -S "$trigger_socket" ]]; then
    ok "daemon trigger socket present in $trigger_mode mode: $trigger_socket"
  else
    ok "trigger socket not present in $trigger_mode mode (expected when manual socket loop is disabled)"
  fi
}

check_logging_retention_state() {
  local manage_journald manage_logrotate sample_log

  manage_journald="$(normalize_bool "$MANAGE_JOURNALD_RETENTION")"
  case "$manage_journald" in
    1)
      if [[ -f "$JOURNALD_DROPIN" ]]; then
        ok "managed journald retention config present: $JOURNALD_DROPIN"
      else
        fail "managed journald retention config missing: $JOURNALD_DROPIN"
      fi

      if [[ -f "$JOURNALD_DROPIN" ]] && grep -Fqx "SystemMaxUse=${JOURNALD_SYSTEM_MAX_USE}" "$JOURNALD_DROPIN"; then
        ok "journald SystemMaxUse matches bootstrap config"
      else
        fail "journald SystemMaxUse does not match expected value: $JOURNALD_SYSTEM_MAX_USE"
      fi

      if [[ -f "$JOURNALD_DROPIN" ]] && grep -Fqx "RuntimeMaxUse=${JOURNALD_RUNTIME_MAX_USE}" "$JOURNALD_DROPIN"; then
        ok "journald RuntimeMaxUse matches bootstrap config"
      else
        fail "journald RuntimeMaxUse does not match expected value: $JOURNALD_RUNTIME_MAX_USE"
      fi

      if [[ -f "$JOURNALD_DROPIN" ]] && grep -Fqx "MaxFileSec=${JOURNALD_MAX_FILE_SEC}" "$JOURNALD_DROPIN"; then
        ok "journald MaxFileSec matches bootstrap config"
      else
        fail "journald MaxFileSec does not match expected value: $JOURNALD_MAX_FILE_SEC"
      fi
      ;;
    0)
      if [[ -f "$JOURNALD_DROPIN" ]]; then
        warn "managed journald retention is disabled, but drop-in is still present: $JOURNALD_DROPIN"
      else
        ok "managed journald retention is disabled"
      fi
      ;;
    *)
      fail "invalid MANAGE_JOURNALD_RETENTION value in bootstrap state: $MANAGE_JOURNALD_RETENTION"
      ;;
  esac

  manage_logrotate="$(normalize_bool "$MANAGE_KAI_LOGROTATE")"
  case "$manage_logrotate" in
    1)
      if [[ -f "$KAI_LOGROTATE_CONFIG" ]]; then
        ok "managed logrotate config present: $KAI_LOGROTATE_CONFIG"
      else
        fail "managed logrotate config missing: $KAI_LOGROTATE_CONFIG"
      fi
      ;;
    0)
      if [[ -f "$KAI_LOGROTATE_CONFIG" ]]; then
        warn "managed logrotate is disabled, but config is still present: $KAI_LOGROTATE_CONFIG"
      else
        ok "managed logrotate config is disabled"
      fi
      ;;
    *)
      fail "invalid MANAGE_KAI_LOGROTATE value in bootstrap state: $MANAGE_KAI_LOGROTATE"
      ;;
  esac

  sample_log="$(find "$KAI_LOG_DIR" -maxdepth 1 -type f -name '*.log' -print -quit 2>/dev/null || true)"
  if [[ -n "$sample_log" ]]; then
    if [[ -f "$KAI_LOGROTATE_CONFIG" ]]; then
      ok "file logs detected under $KAI_LOG_DIR and logrotate policy is present"
    else
      fail "file logs detected under $KAI_LOG_DIR without a managed logrotate policy"
    fi
  else
    ok "no explicit file logs detected under $KAI_LOG_DIR"
  fi
}

check_runtime_status_artifact() {
  local status_enabled_raw status_enabled status_path active_state trigger_mode status_mode

  if [[ ! -f "$EDGE_ENV_FILE" ]]; then
    warn "cannot validate runtime status artifact because edge env file is missing: $EDGE_ENV_FILE"
    return 0
  fi

  status_enabled_raw="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_OBS_STATUS_FILE_ENABLED:-1}"
    ) 2>/dev/null || true
  )"
  status_enabled="$(normalize_bool "$status_enabled_raw")"
  if [[ "$status_enabled" == "invalid" ]]; then
    fail "invalid KAI_OBS_STATUS_FILE_ENABLED in $EDGE_ENV_FILE: ${status_enabled_raw:-<blank>}"
    return 0
  fi

  status_path="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_OBS_STATUS_FILE_PATH:-/run/kai-edge/status.json}"
    ) 2>/dev/null || true
  )"
  if [[ "$status_path" != /* ]]; then
    fail "invalid KAI_OBS_STATUS_FILE_PATH in $EDGE_ENV_FILE: ${status_path:-<blank>}"
    return 0
  fi

  if [[ "$status_enabled" != "1" ]]; then
    ok "runtime status artifact disabled by config"
    return 0
  fi

  if ! have_command systemctl; then
    warn "cannot validate runtime status artifact availability without systemctl"
    return 0
  fi

  active_state="$(systemctl is-active kai-edge.service 2>/dev/null || true)"
  if [[ "$active_state" != "active" ]]; then
    warn "kai-edge.service is not active; runtime status artifact presence is not guaranteed"
    return 0
  fi

  if [[ ! -r "$status_path" ]]; then
    fail "runtime status artifact is enabled but not readable: $status_path"
    return 0
  fi
  ok "runtime status artifact readable: $status_path"

  if ! have_command jq; then
    warn "jq unavailable; cannot validate runtime status artifact structure"
    return 0
  fi

  if jq -e '.state and .mode and .counters and (.counters.interactions != null) and (.counters.wake_detections != null)' "$status_path" >/dev/null 2>&1; then
    ok "runtime status artifact contains expected observability fields"
  else
    fail "runtime status artifact is readable but missing expected observability fields"
    return 0
  fi

  trigger_mode="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_TRIGGER_MODE:-manual}"
    ) 2>/dev/null || true
  )"
  status_mode="$(jq -r '.mode // ""' "$status_path" 2>/dev/null || true)"
  if [[ "$status_mode" == "$trigger_mode" ]]; then
    ok "runtime status artifact mode matches configured trigger mode: $trigger_mode"
  else
    fail "runtime status artifact mode mismatch: expected ${trigger_mode:-<blank>}, got ${status_mode:-<blank>}"
  fi

  if [[ "$trigger_mode" == "wakeword" ]]; then
    if jq -e '.wake_backend != null and .wake_backend != "" and .wake_backend != "n/a"' "$status_path" >/dev/null 2>&1; then
      ok "runtime status artifact reports active wake backend"
    else
      fail "runtime status artifact does not report an active wake backend while in wakeword mode"
    fi
  fi

  if [[ "$trigger_mode" == "vad" || "$trigger_mode" == "wakeword" ]]; then
    if jq -e '.vad_backend != null and .vad_backend != "" and .vad_backend != "n/a"' "$status_path" >/dev/null 2>&1; then
      ok "runtime status artifact reports active VAD backend"
    else
      fail "runtime status artifact does not report an active VAD backend in $trigger_mode mode"
    fi
  fi
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

check_webrtcvad_dependency() {
  local trigger_mode

  if [[ "$CREATE_VENV" != "1" ]]; then
    warn "CREATE_VENV=0; runtime python dependency sync is disabled"
    return 0
  fi

  if [[ ! -x "$KAI_VENV_DIR/bin/python" ]]; then
    warn "venv python missing; skipping webrtcvad check"
    return 0
  fi

  trigger_mode="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_TRIGGER_MODE:-manual}"
    ) 2>/dev/null || true
  )"

  if "$KAI_VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
import webrtcvad
PY
  then
    ok "webrtcvad available in managed venv"
  elif [[ "$trigger_mode" == "manual" ]]; then
    warn "webrtcvad missing in managed venv; VAD and wakeword modes will fall back to energy detector"
  else
    warn "webrtcvad missing in managed venv; runtime will fall back to energy VAD in $trigger_mode mode"
  fi
}

check_pvporcupine_dependency() {
  local trigger_mode

  if [[ "$CREATE_VENV" != "1" ]]; then
    warn "CREATE_VENV=0; runtime python dependency sync is disabled"
    return 0
  fi

  if [[ ! -x "$KAI_VENV_DIR/bin/python" ]]; then
    warn "venv python missing; skipping pvporcupine check"
    return 0
  fi

  trigger_mode="$(
    (
      # shellcheck source=/dev/null
      source "$EDGE_ENV_FILE"
      printf '%s' "${KAI_TRIGGER_MODE:-manual}"
    ) 2>/dev/null || true
  )"

  if "$KAI_VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
import pvporcupine
PY
  then
    ok "pvporcupine available in managed venv"
  elif [[ "$trigger_mode" == "wakeword" ]]; then
    fail "pvporcupine missing in managed venv while wakeword mode is configured"
  else
    warn "pvporcupine missing in managed venv; wakeword mode will be unavailable"
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
  check_runtime_user
  check_runtime_files
  check_ssh_state
  check_tailscale_state
  check_avahi_state
  check_raspap_state
  check_systemd_state
  check_logging_retention_state
  check_runtime_status_artifact
  check_python_venv
  check_webrtcvad_dependency
  check_pvporcupine_dependency
  check_audio_visibility
  print_summary

  if [[ "$fail_count" -gt 0 ]]; then
    exit 1
  fi
}

main "$@"
