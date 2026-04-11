#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${KAI_CONFIG_FILE:-$SCRIPT_DIR/config.env}"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo --preserve-env=KAI_CONFIG_FILE bash "$0" "$@"
fi

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

warn() {
  printf '[%s] warning: %s\n' "$(timestamp)" "$*" >&2
}

die() {
  printf '[%s] error: %s\n' "$(timestamp)" "$*" >&2
  exit 1
}

note_change() {
  CHANGED_ITEMS+=("$*")
}

note_manual() {
  MANUAL_ITEMS+=("$*")
}

note_status() {
  STATUS_ITEMS+=("$*")
}

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\\/&]/\\&/g'
}

TMP_DIR="$(mktemp -d)"
declare -a CHANGED_ITEMS=()
declare -a MANUAL_ITEMS=()
declare -a STATUS_ITEMS=()
SSH_SNIPPET_CHANGED=0
SYSTEMD_UNIT_CHANGED=0
JOURNALD_DROPIN_CHANGED=0

cleanup() {
  rm -rf "$TMP_DIR"
}

on_error() {
  local exit_code=$?
  printf '[%s] error: bootstrap failed at line %s\n' "$(timestamp)" "${BASH_LINENO[0]}" >&2
  exit "$exit_code"
}

trap cleanup EXIT
trap on_error ERR

load_config() {
  [[ -f "$CONFIG_FILE" ]] || die "config file not found: $CONFIG_FILE"

  # shellcheck source=/dev/null
  source "$CONFIG_FILE"

  : "${KAI_ROOT:=/opt/kai}"
  : "${KAI_APP_DIR:=$KAI_ROOT/app}"
  : "${KAI_BIN_DIR:=$KAI_ROOT/bin}"
  : "${KAI_CONFIG_DIR:=/etc/kai}"
  : "${KAI_STATE_DIR:=/var/lib/kai}"
  : "${KAI_LOG_DIR:=/var/log/kai}"
  : "${CREATE_VENV:=1}"
  : "${VENV_DIR:=$KAI_ROOT/venv}"
  : "${INSTALL_AVAHI:=1}"
  : "${INSTALL_RASPAP:=1}"
  : "${RASPAP_INSTALL_URL:=https://install.raspap.com}"
  : "${RASPAP_INSTALL_FLAGS:=--yes --openvpn 0 --wireguard 0 --adblock 0 --rest 0 --provider 0}"
  : "${RASPAP_AP_SSID:=Kai-Setup}"
  : "${RASPAP_AP_PASSPHRASE:=KaiSetup12345}"
  : "${RASPAP_AP_SUBNET_CIDR:=10.42.0.1/24}"
  : "${RASPAP_AP_DHCP_RANGE:=10.42.0.50,10.42.0.150,255.255.255.0,12h}"
  : "${RASPAP_AP_DNS_SERVERS:=1.1.1.1 8.8.8.8}"
  : "${RASPAP_ENABLE_FALLBACK_AP:=1}"
  : "${RASPAP_ADMIN_USER:=admin}"
  : "${RASPAP_ADMIN_PASSWORD:=kai-admin-change-this}"
  : "${GIT_USER_NAME:=}"
  : "${GIT_USER_EMAIL:=}"
  : "${KAI_GIT_ENSURE_KAI_LOCAL_FLOW:=1}"
  : "${KAI_GIT_REMOTE:=origin}"
  : "${KAI_GIT_MAIN_BRANCH:=main}"
  : "${KAI_GIT_LOCAL_BRANCH:=kai-local}"
  : "${KAI_CORE_BASE_URL:=}"
  : "${KAI_RECORD_SECONDS:=5}"
  : "${KAI_AUDIO_SAMPLE_RATE:=16000}"
  : "${KAI_HTTP_TIMEOUT_SECONDS:=60}"
  : "${KAI_TRIGGER_MODE:=manual}"
  : "${KAI_TRIGGER_SOCKET_PATH:=/run/kai-edge/trigger.sock}"
  : "${KAI_WAKEWORD_BACKEND:=openwakeword}"
  : "${KAI_WAKEWORD_ACCESS_KEY:=}"
  : "${KAI_WAKEWORD_BUILTIN_KEYWORD:=porcupine}"
  : "${KAI_WAKEWORD_KEYWORD_PATH:=}"
  : "${KAI_WAKEWORD_MODEL_PATH:=}"
  : "${KAI_WAKEWORD_SENSITIVITY:=0.5}"
  : "${KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL:=}"
  : "${KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS:=}"
  : "${KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD:=0.5}"
  : "${KAI_WAKEWORD_DETECTION_COOLDOWN_MS:=1500}"
  : "${KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS:=3000}"
  : "${KAI_VAD_AGGRESSIVENESS:=3}"
  : "${KAI_VAD_FRAME_MS:=30}"
  : "${KAI_VAD_PRE_ROLL_MS:=250}"
  : "${KAI_VAD_MIN_SPEECH_MS:=1200}"
  : "${KAI_VAD_MIN_SPEECH_RUN_MS:=900}"
  : "${KAI_VAD_TRAILING_SILENCE_MS:=700}"
  : "${KAI_VAD_MAX_UTTERANCE_MS:=10000}"
  : "${KAI_VAD_COOLDOWN_MS:=400}"
  : "${KAI_VAD_ENERGY_THRESHOLD:=260}"
  : "${KAI_OBS_SUMMARY_INTERVAL_SECONDS:=300}"
  : "${KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS:=10}"
  : "${KAI_OBS_STATUS_FILE_ENABLED:=1}"
  : "${KAI_OBS_STATUS_FILE_PATH:=/run/kai-edge/status.json}"
  : "${MANAGE_JOURNALD_RETENTION:=1}"
  : "${JOURNALD_SYSTEM_MAX_USE:=200M}"
  : "${JOURNALD_RUNTIME_MAX_USE:=64M}"
  : "${JOURNALD_MAX_FILE_SEC:=7day}"
  : "${MANAGE_KAI_LOGROTATE:=1}"
  : "${ENABLE_KAI_EDGE_SERVICE:=0}"
  : "${KAI_RECORD_DEVICE:=}"
  : "${KAI_PLAYBACK_DEVICE:=}"
  : "${APT_PACKAGES_EXTRA:=}"

  case "$KAI_TRIGGER_MODE" in
    manual|vad|wakeword)
      ;;
    *)
      die "KAI_TRIGGER_MODE must be one of: manual, vad, wakeword (got: $KAI_TRIGGER_MODE)"
      ;;
  esac

  case "$KAI_WAKEWORD_BACKEND" in
    porcupine|openwakeword)
      ;;
    *)
      die "KAI_WAKEWORD_BACKEND must be one of: porcupine, openwakeword (got: $KAI_WAKEWORD_BACKEND)"
      ;;
  esac

  case "$KAI_GIT_ENSURE_KAI_LOCAL_FLOW" in
    0|1)
      ;;
    *)
      die "KAI_GIT_ENSURE_KAI_LOCAL_FLOW must be 0 or 1"
      ;;
  esac

  [[ -n "$KAI_GIT_REMOTE" ]] || die "KAI_GIT_REMOTE must not be blank"
  [[ -n "$KAI_GIT_MAIN_BRANCH" ]] || die "KAI_GIT_MAIN_BRANCH must not be blank"
  [[ -n "$KAI_GIT_LOCAL_BRANCH" ]] || die "KAI_GIT_LOCAL_BRANCH must not be blank"
  if [[ "$KAI_GIT_MAIN_BRANCH" == "$KAI_GIT_LOCAL_BRANCH" ]]; then
    die "KAI_GIT_MAIN_BRANCH and KAI_GIT_LOCAL_BRANCH must be different branch names"
  fi

  [[ "$KAI_WAKEWORD_DETECTION_COOLDOWN_MS" =~ ^[0-9]+$ ]] || \
    die "KAI_WAKEWORD_DETECTION_COOLDOWN_MS must be a non-negative integer"
  [[ "$KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS" =~ ^[0-9]+$ ]] || \
    die "KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS must be a non-negative integer"

  [[ "$KAI_WAKEWORD_SENSITIVITY" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
    die "KAI_WAKEWORD_SENSITIVITY must be a float between 0 and 1"
  awk -v value="$KAI_WAKEWORD_SENSITIVITY" 'BEGIN { exit !(value >= 0 && value <= 1) }' || \
    die "KAI_WAKEWORD_SENSITIVITY must be between 0 and 1"
  [[ "$KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
    die "KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD must be a float between 0 and 1"
  awk -v value="$KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD" 'BEGIN { exit !(value >= 0 && value <= 1) }' || \
    die "KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD must be between 0 and 1"
  if [[ -n "$KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL" ]]; then
    if [[ "$KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL" == */* ]]; then
      die "KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL must be a filename, not a path"
    fi
    case "$KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL" in
      *.tflite|*.onnx)
        ;;
      *)
        die "KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL must end with .tflite or .onnx"
        ;;
    esac
  fi

  if [[ -n "$KAI_WAKEWORD_KEYWORD_PATH" ]] && [[ "$KAI_WAKEWORD_KEYWORD_PATH" != /* ]]; then
    die "KAI_WAKEWORD_KEYWORD_PATH must be absolute when set"
  fi
  if [[ -n "$KAI_WAKEWORD_MODEL_PATH" ]] && [[ "$KAI_WAKEWORD_MODEL_PATH" != /* ]]; then
    die "KAI_WAKEWORD_MODEL_PATH must be absolute when set"
  fi
  if [[ -n "$KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS" ]]; then
    local -a openwakeword_model_paths=()
    local openwakeword_model_path
    IFS=',' read -r -a openwakeword_model_paths <<< "$KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS"
    for openwakeword_model_path in "${openwakeword_model_paths[@]}"; do
      openwakeword_model_path="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$openwakeword_model_path")"
      [[ -z "$openwakeword_model_path" ]] && continue
      if [[ "$openwakeword_model_path" != /* ]]; then
        die "KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS must contain only absolute paths"
      fi
    done
  fi

  if [[ "$KAI_TRIGGER_MODE" == "wakeword" ]]; then
    if [[ "$KAI_WAKEWORD_BACKEND" == "porcupine" ]]; then
      [[ -n "$KAI_WAKEWORD_ACCESS_KEY" ]] || \
        die "KAI_WAKEWORD_ACCESS_KEY must be set when KAI_TRIGGER_MODE=wakeword and KAI_WAKEWORD_BACKEND=porcupine"
      if [[ -z "$KAI_WAKEWORD_BUILTIN_KEYWORD" ]] && [[ -z "$KAI_WAKEWORD_KEYWORD_PATH" ]]; then
        die "set KAI_WAKEWORD_BUILTIN_KEYWORD or KAI_WAKEWORD_KEYWORD_PATH for porcupine wakeword mode"
      fi
    fi
    [[ "$KAI_AUDIO_SAMPLE_RATE" == "16000" ]] || \
      die "wakeword mode currently requires KAI_AUDIO_SAMPLE_RATE=16000"
  fi

  [[ "$KAI_OBS_SUMMARY_INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || \
    die "KAI_OBS_SUMMARY_INTERVAL_SECONDS must be a non-negative integer"
  [[ "$KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS" =~ ^[0-9]+$ ]] || \
    die "KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS must be a non-negative integer"
  [[ "$KAI_OBS_STATUS_FILE_PATH" = /* ]] || \
    die "KAI_OBS_STATUS_FILE_PATH must be an absolute path"

  case "$KAI_OBS_STATUS_FILE_ENABLED" in
    0|1)
      ;;
    *)
      die "KAI_OBS_STATUS_FILE_ENABLED must be 0 or 1"
      ;;
  esac

  case "$MANAGE_JOURNALD_RETENTION" in
    0|1)
      ;;
    *)
      die "MANAGE_JOURNALD_RETENTION must be 0 or 1"
      ;;
  esac

  case "$MANAGE_KAI_LOGROTATE" in
    0|1)
      ;;
    *)
      die "MANAGE_KAI_LOGROTATE must be 0 or 1"
      ;;
  esac

  if [[ "$MANAGE_JOURNALD_RETENTION" == "1" ]]; then
    [[ -n "$JOURNALD_SYSTEM_MAX_USE" ]] || die "JOURNALD_SYSTEM_MAX_USE must not be blank"
    [[ -n "$JOURNALD_RUNTIME_MAX_USE" ]] || die "JOURNALD_RUNTIME_MAX_USE must not be blank"
    [[ -n "$JOURNALD_MAX_FILE_SEC" ]] || die "JOURNALD_MAX_FILE_SEC must not be blank"
  fi

  if [[ -z "${KAI_USER:-}" ]]; then
    KAI_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
  fi

  id "$KAI_USER" >/dev/null 2>&1 || die "kai user does not exist: $KAI_USER"

  if [[ -z "${KAI_GROUP:-}" ]]; then
    KAI_GROUP="$(id -gn "$KAI_USER")"
  fi

  if [[ "$CREATE_VENV" == "1" ]]; then
    KAI_RUNTIME_PYTHON_BIN="$VENV_DIR/bin/python"
  else
    KAI_RUNTIME_PYTHON_BIN="python3"
  fi

  SSH_SNIPPET_DEST="/etc/ssh/sshd_config.d/60-kai-hardening.conf"
  SYSTEMD_UNIT_DEST="/etc/systemd/system/kai-edge.service"
  AUDIO_HELPER_DEST="$KAI_BIN_DIR/kai-audio-check"
  DOCTOR_HELPER_DEST="$KAI_BIN_DIR/kai-doctor"
  PUSH_TO_TALK_DEST="$KAI_BIN_DIR/kai-push-to-talk"
  EDGE_DAEMON_DEST="$KAI_BIN_DIR/kai-edge-daemon"
  EDGE_TRIGGER_DEST="$KAI_BIN_DIR/kai-edge-trigger"
  EDGE_STATUS_DEST="$KAI_BIN_DIR/kai-edge-status"
  EDGE_PACKAGE_DEST="$KAI_APP_DIR/kai_edge"
  DOCTOR_CONFIG_DEST="/etc/kai/bootstrap.env"
  EDGE_ENV_DEST="$KAI_CONFIG_DIR/edge.env"
  JOURNALD_DROPIN_DEST="/etc/systemd/journald.conf.d/90-kai-edge-retention.conf"
  LOGROTATE_CONFIG_DEST="/etc/logrotate.d/kai-edge"
}

ensure_dir() {
  local path=$1
  local mode=$2
  local owner=$3
  local group=$4

  if [[ ! -d "$path" ]]; then
    install -d -m "$mode" -o "$owner" -g "$group" "$path"
    note_change "created directory $path"
    return 0
  fi

  chmod "$mode" "$path"
  chown "$owner:$group" "$path"
  log "directory already present: $path"
}

install_managed_file() {
  local src=$1
  local dest=$2
  local mode=$3
  local owner=$4
  local group=$5

  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    chmod "$mode" "$dest"
    chown "$owner:$group" "$dest"
    log "managed file already current: $dest"
    return 1
  fi

  install -D -m "$mode" -o "$owner" -g "$group" "$src" "$dest"
  note_change "installed $dest"
  return 0
}

render_systemd_unit() {
  local template=$1
  local output=$2

  sed \
    -e "s|__KAI_USER__|$(escape_sed_replacement "$KAI_USER")|g" \
    -e "s|__KAI_GROUP__|$(escape_sed_replacement "$KAI_GROUP")|g" \
    -e "s|__KAI_APP_DIR__|$(escape_sed_replacement "$KAI_APP_DIR")|g" \
    -e "s|__KAI_BIN_DIR__|$(escape_sed_replacement "$KAI_BIN_DIR")|g" \
    -e "s|__KAI_RUNTIME_PYTHON__|$(escape_sed_replacement "$KAI_RUNTIME_PYTHON_BIN")|g" \
    -e "s|__KAI_CONFIG_DIR__|$(escape_sed_replacement "$KAI_CONFIG_DIR")|g" \
    -e "s|__KAI_STATE_DIR__|$(escape_sed_replacement "$KAI_STATE_DIR")|g" \
    -e "s|__KAI_LOG_DIR__|$(escape_sed_replacement "$KAI_LOG_DIR")|g" \
    "$template" > "$output"
}

render_edge_env() {
  local template=$1
  local output=$2

  sed \
    -e "s|__KAI_CORE_BASE_URL__|$(escape_sed_replacement "$KAI_CORE_BASE_URL")|g" \
    -e "s|__KAI_RECORD_SECONDS__|$(escape_sed_replacement "$KAI_RECORD_SECONDS")|g" \
    -e "s|__KAI_AUDIO_SAMPLE_RATE__|$(escape_sed_replacement "$KAI_AUDIO_SAMPLE_RATE")|g" \
    -e "s|__KAI_HTTP_TIMEOUT_SECONDS__|$(escape_sed_replacement "$KAI_HTTP_TIMEOUT_SECONDS")|g" \
    -e "s|__KAI_TRIGGER_MODE__|$(escape_sed_replacement "$KAI_TRIGGER_MODE")|g" \
    -e "s|__KAI_TRIGGER_SOCKET_PATH__|$(escape_sed_replacement "$KAI_TRIGGER_SOCKET_PATH")|g" \
    -e "s|__KAI_WAKEWORD_BACKEND__|$(escape_sed_replacement "$KAI_WAKEWORD_BACKEND")|g" \
    -e "s|__KAI_WAKEWORD_ACCESS_KEY__|$(escape_sed_replacement "$KAI_WAKEWORD_ACCESS_KEY")|g" \
    -e "s|__KAI_WAKEWORD_BUILTIN_KEYWORD__|$(escape_sed_replacement "$KAI_WAKEWORD_BUILTIN_KEYWORD")|g" \
    -e "s|__KAI_WAKEWORD_KEYWORD_PATH__|$(escape_sed_replacement "$KAI_WAKEWORD_KEYWORD_PATH")|g" \
    -e "s|__KAI_WAKEWORD_MODEL_PATH__|$(escape_sed_replacement "$KAI_WAKEWORD_MODEL_PATH")|g" \
    -e "s|__KAI_WAKEWORD_SENSITIVITY__|$(escape_sed_replacement "$KAI_WAKEWORD_SENSITIVITY")|g" \
    -e "s|__KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS__|$(escape_sed_replacement "$KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS")|g" \
    -e "s|__KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD__|$(escape_sed_replacement "$KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD")|g" \
    -e "s|__KAI_WAKEWORD_DETECTION_COOLDOWN_MS__|$(escape_sed_replacement "$KAI_WAKEWORD_DETECTION_COOLDOWN_MS")|g" \
    -e "s|__KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS__|$(escape_sed_replacement "$KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS")|g" \
    -e "s|__KAI_VAD_AGGRESSIVENESS__|$(escape_sed_replacement "$KAI_VAD_AGGRESSIVENESS")|g" \
    -e "s|__KAI_VAD_FRAME_MS__|$(escape_sed_replacement "$KAI_VAD_FRAME_MS")|g" \
    -e "s|__KAI_VAD_PRE_ROLL_MS__|$(escape_sed_replacement "$KAI_VAD_PRE_ROLL_MS")|g" \
    -e "s|__KAI_VAD_MIN_SPEECH_MS__|$(escape_sed_replacement "$KAI_VAD_MIN_SPEECH_MS")|g" \
    -e "s|__KAI_VAD_MIN_SPEECH_RUN_MS__|$(escape_sed_replacement "$KAI_VAD_MIN_SPEECH_RUN_MS")|g" \
    -e "s|__KAI_VAD_TRAILING_SILENCE_MS__|$(escape_sed_replacement "$KAI_VAD_TRAILING_SILENCE_MS")|g" \
    -e "s|__KAI_VAD_MAX_UTTERANCE_MS__|$(escape_sed_replacement "$KAI_VAD_MAX_UTTERANCE_MS")|g" \
    -e "s|__KAI_VAD_COOLDOWN_MS__|$(escape_sed_replacement "$KAI_VAD_COOLDOWN_MS")|g" \
    -e "s|__KAI_VAD_ENERGY_THRESHOLD__|$(escape_sed_replacement "$KAI_VAD_ENERGY_THRESHOLD")|g" \
    -e "s|__KAI_OBS_SUMMARY_INTERVAL_SECONDS__|$(escape_sed_replacement "$KAI_OBS_SUMMARY_INTERVAL_SECONDS")|g" \
    -e "s|__KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS__|$(escape_sed_replacement "$KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS")|g" \
    -e "s|__KAI_OBS_STATUS_FILE_ENABLED__|$(escape_sed_replacement "$KAI_OBS_STATUS_FILE_ENABLED")|g" \
    -e "s|__KAI_OBS_STATUS_FILE_PATH__|$(escape_sed_replacement "$KAI_OBS_STATUS_FILE_PATH")|g" \
    -e "s|__KAI_RECORD_DEVICE__|$(escape_sed_replacement "$KAI_RECORD_DEVICE")|g" \
    -e "s|__KAI_PLAYBACK_DEVICE__|$(escape_sed_replacement "$KAI_PLAYBACK_DEVICE")|g" \
    "$template" > "$output"
}

render_journald_dropin() {
  local template=$1
  local output=$2

  sed \
    -e "s|__JOURNALD_SYSTEM_MAX_USE__|$(escape_sed_replacement "$JOURNALD_SYSTEM_MAX_USE")|g" \
    -e "s|__JOURNALD_RUNTIME_MAX_USE__|$(escape_sed_replacement "$JOURNALD_RUNTIME_MAX_USE")|g" \
    -e "s|__JOURNALD_MAX_FILE_SEC__|$(escape_sed_replacement "$JOURNALD_MAX_FILE_SEC")|g" \
    "$template" > "$output"
}

render_logrotate_config() {
  local template=$1
  local output=$2

  sed \
    -e "s|__KAI_LOG_DIR__|$(escape_sed_replacement "$KAI_LOG_DIR")|g" \
    -e "s|__KAI_USER__|$(escape_sed_replacement "$KAI_USER")|g" \
    -e "s|__KAI_GROUP__|$(escape_sed_replacement "$KAI_GROUP")|g" \
    "$template" > "$output"
}

ensure_packages() {
  local packages=(
    git
    curl
    vim
    htop
    ripgrep
    jq
    ffmpeg
    python3
    python3-pip
    python3-venv
    alsa-utils
    ca-certificates
    rsync
    openssh-server
    logrotate
  )
  local extra_packages=()

  if [[ "$INSTALL_AVAHI" == "1" ]]; then
    packages+=(avahi-daemon)
  fi

  if [[ -n "$APT_PACKAGES_EXTRA" ]]; then
    read -r -a extra_packages <<< "$APT_PACKAGES_EXTRA"
    packages+=("${extra_packages[@]}")
  fi

  export DEBIAN_FRONTEND=noninteractive

  log "updating apt package index"
  apt-get update

  log "ensuring baseline apt packages are installed"
  apt-get install -y --no-install-recommends "${packages[@]}"
  note_change "ran apt update and ensured baseline packages are installed"
}

ensure_group_membership() {
  local user=$1
  local group=$2

  getent group "$group" >/dev/null 2>&1 || die "required group does not exist: $group"

  if id -nG "$user" | tr ' ' '\n' | grep -Fxq "$group"; then
    log "user already in group $group: $user"
    return 0
  fi

  log "adding $user to group $group"
  usermod -aG "$group" "$user"
  note_change "added $user to group $group"
  note_manual "start a new shell for updated group membership, or run audio commands with: sudo -u $user $PUSH_TO_TALK_DEST"
}

cidr_prefix_to_mask() {
  local prefix=$1
  local octet mask=""
  local i

  [[ "$prefix" =~ ^[0-9]+$ ]] || return 1
  (( prefix >= 0 && prefix <= 32 )) || return 1

  for ((i=0; i<4; i++)); do
    if (( prefix >= 8 )); then
      octet=255
      prefix=$((prefix - 8))
    elif (( prefix > 0 )); then
      octet=$((256 - 2 ** (8 - prefix)))
      prefix=0
    else
      octet=0
    fi
    mask+="$octet"
    if (( i < 3 )); then
      mask+="."
    fi
  done

  printf '%s\n' "$mask"
}

upsert_key_value_line() {
  local file=$1
  local key=$2
  local value=$3
  local escaped_value

  escaped_value="$(escape_sed_replacement "$value")"
  if grep -Eq "^${key}=" "$file"; then
    sed -i -E "s|^${key}=.*|${key}=${escaped_value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

replace_raspap_dhcpcd_wlan0_block() {
  local source_file=$1
  local block_file=$2
  local output_file=$3

  awk -v block_file="$block_file" '
    function emit_block(    line) {
      while ((getline line < block_file) > 0) {
        print line
      }
      close(block_file)
      print ""
      emitted = 1
    }
    BEGIN {
      skip = 0
      emitted = 0
    }
    /^# RaspAP wlan0 configuration$/ {
      emit_block()
      skip = 1
      next
    }
    skip && /^# RaspAP [^ ]+ configuration$/ {
      skip = 0
    }
    !skip {
      print
    }
    END {
      if (!emitted) {
        print ""
        emit_block()
      }
    }
  ' "$source_file" > "$output_file"
}

install_raspap_if_requested() {
  local install_flags=()

  if [[ "$INSTALL_RASPAP" != "1" ]]; then
    note_status "raspap install disabled in config.env"
    return 0
  fi

  if [[ -d /etc/raspap ]]; then
    log "raspap already installed"
    note_status "raspap already installed"
    return 0
  fi

  if [[ -n "$RASPAP_INSTALL_FLAGS" ]]; then
    read -r -a install_flags <<< "$RASPAP_INSTALL_FLAGS"
  fi

  log "installing raspap using the official installer"
  if curl -fsSL "$RASPAP_INSTALL_URL" | bash -s -- "${install_flags[@]}"; then
    note_change "installed raspap using $RASPAP_INSTALL_URL"
    note_status "raspap installed"
    return 0
  fi

  die "raspap installation failed"
}

ensure_raspap_hostapd_ini_if_missing() {
  local hostapd_ini="/etc/raspap/hostapd.ini"
  local rendered_hostapd_ini="$TMP_DIR/raspap-hostapd.ini"

  if [[ "$INSTALL_RASPAP" != "1" ]]; then
    return 0
  fi

  if [[ -f "$hostapd_ini" ]]; then
    return 0
  fi

  {
    printf '%s\n' 'WifiInterface=wlan0'
    printf '%s\n' 'LogEnable=0'
    printf '%s\n' 'WifiAPEnable=0'
    printf '%s\n' 'BridgedEnable=0'
    printf '%s\n' 'RepeaterEnable=0'
    printf '%s\n' 'DualAPEnable=0'
    printf '%s\n' 'WifiManaged=wlan0'
  } > "$rendered_hostapd_ini"

  install -m 0644 -o root -g root "$rendered_hostapd_ini" "$hostapd_ini"
  note_change "created missing raspap hostapd.ini defaults"
}

patch_raspap_wifimanager_zero_netid_bug_if_needed() {
  local wifi_manager_file="/var/www/html/src/RaspAP/Networking/Hotspot/WiFiManager.php"

  if [[ "$INSTALL_RASPAP" != "1" ]]; then
    return 0
  fi

  if [[ ! -f "$wifi_manager_file" ]]; then
    warn "raspap WiFiManager.php not found; skipped network-id validation patch"
    note_manual "if wifi joins fail in raspap with 'Invalid network ID returned: 0', patch $wifi_manager_file manually"
    return 0
  fi

  if grep -Fq 'if ($netid === "" || !is_numeric($netid)) {' "$wifi_manager_file"; then
    return 0
  fi

  if grep -Fq 'if (!$netid || !is_numeric($netid)) {' "$wifi_manager_file"; then
    sed -i 's|if (!$netid || !is_numeric($netid)) {|if ($netid === "" || !is_numeric($netid)) {|' "$wifi_manager_file"
    note_change "patched raspap WiFiManager to allow wpa_cli network id 0"
    return 0
  fi

  warn "could not locate expected network-id validation snippet in raspap WiFiManager.php"
  note_manual "inspect $wifi_manager_file if wifi joins fail from raspap web ui"
  return 0
}

configure_raspap_if_requested() {
  local ap_ip ap_prefix ap_mask
  local dhcpcd_block_file="$TMP_DIR/raspap-wlan0-dhcpcd.block"
  local rendered_dhcpcd="$TMP_DIR/dhcpcd.conf.rendered"
  local rendered_defaults="$TMP_DIR/defaults.json.rendered"
  local auth_file="$TMP_DIR/raspap.auth"
  local auth_hash auth_owner auth_group
  local defaults_owner defaults_group

  if [[ "$INSTALL_RASPAP" != "1" ]]; then
    return 0
  fi

  [[ -f /etc/hostapd/hostapd.conf ]] || die "raspap hostapd config missing: /etc/hostapd/hostapd.conf"
  [[ -f /etc/dnsmasq.d/090_wlan0.conf ]] || die "raspap dnsmasq config missing: /etc/dnsmasq.d/090_wlan0.conf"
  [[ -f /etc/dhcpcd.conf ]] || die "raspap dhcpcd config missing: /etc/dhcpcd.conf"
  [[ -d /etc/raspap ]] || die "raspap config directory missing: /etc/raspap"

  ensure_raspap_hostapd_ini_if_missing
  patch_raspap_wifimanager_zero_netid_bug_if_needed

  [[ ${#RASPAP_AP_SSID} -gt 0 ]] || die "RASPAP_AP_SSID must not be empty"
  [[ ${#RASPAP_AP_PASSPHRASE} -ge 8 ]] || die "RASPAP_AP_PASSPHRASE must be at least 8 characters"
  [[ ${#RASPAP_ADMIN_USER} -gt 0 ]] || die "RASPAP_ADMIN_USER must not be empty"
  [[ ${#RASPAP_ADMIN_PASSWORD} -gt 0 ]] || die "RASPAP_ADMIN_PASSWORD must not be empty"
  [[ "$RASPAP_AP_SUBNET_CIDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]] || \
    die "RASPAP_AP_SUBNET_CIDR must look like 10.42.0.1/24"

  ap_ip="${RASPAP_AP_SUBNET_CIDR%/*}"
  ap_prefix="${RASPAP_AP_SUBNET_CIDR#*/}"
  ap_mask="$(cidr_prefix_to_mask "$ap_prefix")" || die "invalid prefix in RASPAP_AP_SUBNET_CIDR: $RASPAP_AP_SUBNET_CIDR"

  log "configuring raspap access point defaults"
  upsert_key_value_line /etc/hostapd/hostapd.conf ssid "$RASPAP_AP_SSID"
  upsert_key_value_line /etc/hostapd/hostapd.conf wpa_passphrase "$RASPAP_AP_PASSPHRASE"

  upsert_key_value_line /etc/dnsmasq.d/090_wlan0.conf interface "wlan0"
  upsert_key_value_line /etc/dnsmasq.d/090_wlan0.conf dhcp-range "$RASPAP_AP_DHCP_RANGE"

  {
    printf '%s\n' '# RaspAP wlan0 configuration'
    printf '%s\n' 'interface wlan0'
    if [[ "$RASPAP_ENABLE_FALLBACK_AP" == "1" ]]; then
      printf '%s\n' 'fallback static_wlan0'
      printf '\n'
      printf '%s\n' 'profile static_wlan0'
      printf 'static ip_address=%s\n' "$RASPAP_AP_SUBNET_CIDR"
      printf 'static domain_name_servers=%s\n' "$RASPAP_AP_DNS_SERVERS"
      printf '%s\n' 'nogateway'
    fi
  } > "$dhcpcd_block_file"
  replace_raspap_dhcpcd_wlan0_block /etc/dhcpcd.conf "$dhcpcd_block_file" "$rendered_dhcpcd"
  install -m 0644 -o root -g root "$rendered_dhcpcd" /etc/dhcpcd.conf

  if [[ -f /etc/raspap/networking/defaults.json ]]; then
    defaults_owner="$(stat -c '%U' /etc/raspap/networking/defaults.json 2>/dev/null || printf 'root')"
    defaults_group="$(stat -c '%G' /etc/raspap/networking/defaults.json 2>/dev/null || printf 'root')"
    jq \
      --arg ap_ip "$ap_ip" \
      --arg ap_mask "$ap_mask" \
      --arg ap_dns "$RASPAP_AP_DNS_SERVERS" \
      --arg dhcp_range "$RASPAP_AP_DHCP_RANGE" \
      '.dhcp.wlan0["static ip_address"] = [$ap_ip]
       | .dhcp.wlan0["static routers"] = []
       | .dhcp.wlan0["subnetmask"] = [$ap_mask]
       | .dhcp.wlan0["static domain_name_servers"] = [$ap_dns]
       | .dnsmasq.wlan0["dhcp-range"] = [$dhcp_range]' \
      /etc/raspap/networking/defaults.json > "$rendered_defaults"
    install -m 0644 -o "$defaults_owner" -g "$defaults_group" "$rendered_defaults" /etc/raspap/networking/defaults.json
  else
    warn "raspap defaults.json not found; skipped network defaults update"
  fi

  command -v php >/dev/null 2>&1 || die "php is required to generate a raspap bcrypt password hash"
  auth_hash="$(php -r 'echo password_hash($argv[1], PASSWORD_BCRYPT), PHP_EOL;' "$RASPAP_ADMIN_PASSWORD")" || \
    die "failed to generate bcrypt hash for raspap admin password"

  {
    printf '%s\n' "$RASPAP_ADMIN_USER"
    printf '%s\n' "$auth_hash"
  } > "$auth_file"

  if id www-data >/dev/null 2>&1; then
    auth_owner="www-data"
    auth_group="www-data"
  else
    auth_owner="root"
    auth_group="root"
  fi
  install -m 0640 -o "$auth_owner" -g "$auth_group" "$auth_file" /etc/raspap/raspap.auth

  note_change "configured raspap AP SSID to $RASPAP_AP_SSID"
  note_change "configured raspap AP subnet to $RASPAP_AP_SUBNET_CIDR"
  note_change "configured raspap admin credentials for user $RASPAP_ADMIN_USER"
  if [[ "$RASPAP_ENABLE_FALLBACK_AP" == "1" ]]; then
    note_status "raspap fallback AP behavior enabled for wlan0"
  else
    note_status "raspap fallback AP behavior disabled for wlan0"
  fi
}

remove_raspap_self_gateway_route_if_present() {
  local ap_ip

  if [[ "$INSTALL_RASPAP" != "1" ]]; then
    return 0
  fi

  ap_ip="${RASPAP_AP_SUBNET_CIDR%/*}"
  if [[ -z "$ap_ip" ]]; then
    return 0
  fi

  if ! ip route show default 2>/dev/null | grep -Fq "via ${ap_ip} dev wlan0"; then
    return 0
  fi

  log "removing invalid default route via raspap AP self IP on wlan0: $ap_ip"
  if ip route del default via "$ap_ip" dev wlan0 >/dev/null 2>&1; then
    note_change "removed invalid default route via raspap AP self IP on wlan0 ($ap_ip)"
  else
    warn "could not remove invalid default route via raspap AP self IP on wlan0: $ap_ip"
    note_manual "remove invalid AP self gateway route manually with: sudo ip route del default via $ap_ip dev wlan0"
  fi
}

ensure_raspap_service_if_requested() {
  if [[ "$INSTALL_RASPAP" != "1" ]]; then
    return 0
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found; cannot validate raspap web service"
    note_manual "ensure the raspap web service is active manually"
    return 0
  fi

  if systemctl is-enabled --quiet lighttpd && systemctl is-active --quiet lighttpd; then
    log "raspap web service already enabled and active (lighttpd)"
    return 0
  fi

  log "ensuring raspap web service is enabled and active"
  if systemctl enable --now lighttpd; then
    note_change "ensured raspap web service (lighttpd) is enabled and active"
    return 0
  fi

  warn "could not enable raspap web service automatically"
  note_manual "enable the raspap web service manually with: sudo systemctl enable --now lighttpd"
  return 1
}

install_tailscale_if_missing() {
  if command -v tailscale >/dev/null 2>&1 && command -v tailscaled >/dev/null 2>&1; then
    log "tailscale already installed"
    return 0
  fi

  log "installing tailscale using the official linux install script"
  curl -fsSL https://tailscale.com/install.sh | sh
  note_change "installed tailscale using the official linux install script"
}

ensure_tailscaled_service() {
  if systemctl is-enabled --quiet tailscaled && systemctl is-active --quiet tailscaled; then
    log "tailscaled is already enabled and running"
    return 0
  fi

  log "ensuring tailscaled is enabled and running"
  if systemctl enable --now tailscaled; then
    note_change "ensured tailscaled is enabled and running"
    return 0
  fi

  warn "could not enable tailscaled automatically"
  note_status "tailscale is installed, but bootstrap could not confirm that tailscaled is enabled and running"
  note_manual "enable tailscaled manually with: sudo systemctl enable --now tailscaled"
  return 1
}

check_tailscale_state() {
  local status_json backend_state ssh_enabled

  log "checking tailscale state"
  if ! status_json="$(tailscale status --json 2>/dev/null)"; then
    warn "could not read tailscale status"
    note_status "tailscale is installed, but bootstrap could not determine whether the node is logged in"
    note_manual "inspect tailscale manually with: sudo tailscale status"
    return 0
  fi

  backend_state="$(jq -r '.BackendState // ""' <<<"$status_json")"
  ssh_enabled="$(get_tailscale_ssh_enabled)"

  case "$backend_state" in
    Running|Starting)
      case "$ssh_enabled" in
        true)
          note_status "tailscale is healthy and ssh is enabled"
          ;;
        false)
          note_status "tailscale is authenticated and healthy, but tailscale ssh is not enabled"
          note_manual $'tailscale is authenticated, but tailscale ssh is not enabled. enable it manually when ready with:\n  sudo tailscale set --ssh'
          ;;
        *)
          note_status "tailscale is authenticated and healthy, but bootstrap could not determine whether tailscale ssh is enabled"
          note_manual $'inspect tailscale ssh state manually with:\n  sudo tailscale debug prefs | jq \'.RunSSH\''
          ;;
      esac
      ;;
    NeedsLogin)
      note_status "tailscale is installed and tailscaled is running, but this node still needs manual login"
      note_manual $'tailscale login is required. run these commands in order, and only run the second command after login succeeds:\n  sudo tailscale up\n  sudo tailscale set --ssh'
      ;;
    NeedsMachineAuth)
      note_status "tailscale login succeeded, but this node still needs tailnet admin approval"
      note_manual $'approve the node in the tailnet admin console first. after approval, enable tailscale ssh manually with:\n  sudo tailscale set --ssh'
      ;;
    Stopped)
      note_status "tailscale is installed, but the daemon reported state: stopped"
      note_manual "start tailscaled manually with: sudo systemctl enable --now tailscaled"
      ;;
    "")
      note_status "tailscale returned an empty backend state"
      note_manual "inspect tailscale manually with: sudo tailscale status"
      ;;
    *)
      note_status "tailscale reported backend state: $backend_state"
      note_manual "inspect tailscale manually with: sudo tailscale status"
      ;;
  esac
}

get_tailscale_ssh_enabled() {
  local prefs_json run_ssh

  if ! prefs_json="$(tailscale debug prefs 2>/dev/null)"; then
    warn "could not read tailscale debug prefs"
    printf 'unknown\n'
    return 0
  fi

  run_ssh="$(jq -r '.RunSSH // "unknown"' <<<"$prefs_json")"
  case "$run_ssh" in
    true|false)
      printf '%s\n' "$run_ssh"
      ;;
    *)
      warn "tailscale debug prefs did not return a usable RunSSH value"
      printf 'unknown\n'
      ;;
  esac
}

ensure_base_directories() {
  log "ensuring base directories exist"
  ensure_dir "$KAI_ROOT" 0755 "$KAI_USER" "$KAI_GROUP"
  ensure_dir "$KAI_APP_DIR" 0755 "$KAI_USER" "$KAI_GROUP"
  ensure_dir "$KAI_BIN_DIR" 0755 "$KAI_USER" "$KAI_GROUP"
  ensure_dir "$KAI_CONFIG_DIR" 0755 root root
  ensure_dir "$KAI_STATE_DIR" 0755 "$KAI_USER" "$KAI_GROUP"
  ensure_dir "$KAI_LOG_DIR" 0755 "$KAI_USER" "$KAI_GROUP"

  if [[ "$CREATE_VENV" == "1" ]]; then
    ensure_dir "$VENV_DIR" 0755 "$KAI_USER" "$KAI_GROUP"
  fi
}

ensure_python_venv() {
  if [[ "$CREATE_VENV" != "1" ]]; then
    note_manual "python venv creation is disabled in config.env"
    return 0
  fi

  if [[ -x "$VENV_DIR/bin/python" ]]; then
    log "python venv already present: $VENV_DIR"
    return 0
  fi

  command -v runuser >/dev/null 2>&1 || die "runuser is required to create the venv as $KAI_USER"

  log "creating python venv at $VENV_DIR"
  runuser -u "$KAI_USER" -- python3 -m venv "$VENV_DIR"
  note_change "created python venv at $VENV_DIR"
}

ensure_runtime_python_dependencies() {
  local requirements_file="$SCRIPT_DIR/requirements-runtime.txt"
  local venv_python="$VENV_DIR/bin/python"
  local pip_install_log="$TMP_DIR/runtime-pip-install.log"
  local pip_attempt=1
  local pip_max_attempts=3
  local pip_sync_ok=0
  local log_line=""

  if [[ "$CREATE_VENV" != "1" ]]; then
    warn "cannot install runtime python dependencies automatically because CREATE_VENV=0"
    note_status "runtime python dependency sync skipped (CREATE_VENV=0)"
    note_manual "set CREATE_VENV=1 to sync runtime python dependencies during bootstrap"
    return 0
  fi

  if [[ ! -f "$requirements_file" ]]; then
    die "missing runtime python requirements file: $requirements_file"
  fi

  if [[ ! -x "$venv_python" ]]; then
    warn "cannot install runtime python dependencies because venv python is missing: $venv_python"
    note_status "runtime python dependency sync skipped"
    return 0
  fi

  log "syncing runtime python dependencies from $requirements_file"
  : > "$pip_install_log"
  while (( pip_attempt <= pip_max_attempts )); do
    if runuser -u "$KAI_USER" -- "$venv_python" -m pip install --disable-pip-version-check --no-input -r "$requirements_file" >"$pip_install_log" 2>&1; then
      pip_sync_ok=1
      break
    fi
    if (( pip_attempt < pip_max_attempts )); then
      warn "runtime python dependency sync attempt $pip_attempt failed; retrying"
      sleep 2
    fi
    pip_attempt=$((pip_attempt + 1))
  done

  if [[ "$pip_sync_ok" == "1" ]]; then
    note_change "synced runtime python dependencies into $VENV_DIR"
    if (( pip_attempt > 1 )); then
      note_status "runtime python dependency sync succeeded on attempt $pip_attempt of $pip_max_attempts"
    fi
  else
    warn "could not sync runtime python dependencies from $requirements_file"
    if [[ -s "$pip_install_log" ]]; then
      warn "runtime python dependency sync error tail:"
      while IFS= read -r log_line; do
        warn "$log_line"
      done < <(tail -n 20 "$pip_install_log")
    fi
    note_status "runtime python dependency sync failed; daemon may fall back where supported"
    note_manual "inspect dependency install manually with: sudo -u $KAI_USER $venv_python -m pip install -r $requirements_file"
  fi

  if runuser -u "$KAI_USER" -- "$venv_python" -c 'import webrtcvad' >/dev/null 2>&1; then
    note_status "webrtcvad is available for VAD and post-wake capture"
  elif [[ "$KAI_TRIGGER_MODE" == "manual" ]]; then
    note_status "webrtcvad is not installed; energy fallback will be used if VAD or wakeword mode is enabled"
  else
    warn "webrtcvad missing after dependency sync; trying source fallback package"
    if runuser -u "$KAI_USER" -- "$venv_python" -m pip install --disable-pip-version-check --no-input webrtcvad >/dev/null 2>&1; then
      note_change "installed webrtcvad source fallback into $VENV_DIR"
      note_status "webrtcvad is available for VAD and post-wake capture"
    else
      warn "could not install webrtcvad source fallback into $VENV_DIR"
      note_status "webrtcvad install failed; daemon will use energy fallback VAD"
      note_manual "inspect VAD package install manually with: sudo -u $KAI_USER $venv_python -m pip install webrtcvad"
    fi
  fi

  if runuser -u "$KAI_USER" -- "$venv_python" -c 'import pvporcupine' >/dev/null 2>&1; then
    note_status "pvporcupine is available for wakeword backend porcupine"
  elif [[ "$KAI_TRIGGER_MODE" == "wakeword" && "$KAI_WAKEWORD_BACKEND" == "porcupine" ]]; then
    warn "pvporcupine missing after dependency sync; trying direct install"
    if runuser -u "$KAI_USER" -- "$venv_python" -m pip install --disable-pip-version-check --no-input pvporcupine >/dev/null 2>&1; then
      note_change "installed pvporcupine into $VENV_DIR"
      note_status "pvporcupine is available for wakeword backend porcupine"
    else
      warn "could not install pvporcupine into $VENV_DIR"
      note_status "pvporcupine install failed; wakeword backend porcupine will not start"
      note_manual "inspect porcupine dependency install manually with: sudo -u $KAI_USER $venv_python -m pip install pvporcupine"
    fi
  else
    note_status "pvporcupine is not installed; wakeword backend porcupine will be unavailable until dependencies are synced"
  fi

  if runuser -u "$KAI_USER" -- "$venv_python" -c 'import openwakeword' >/dev/null 2>&1; then
    note_status "openwakeword is available for wakeword backend openwakeword"
    return 0
  fi

  if [[ "$KAI_TRIGGER_MODE" != "wakeword" || "$KAI_WAKEWORD_BACKEND" != "openwakeword" ]]; then
    note_status "openwakeword is not installed; wakeword backend openwakeword will be unavailable until dependencies are synced"
    return 0
  fi

  warn "openwakeword missing after dependency sync; trying direct install"
  if runuser -u "$KAI_USER" -- "$venv_python" -m pip install --disable-pip-version-check --no-input openwakeword >/dev/null 2>&1; then
    note_change "installed openwakeword into $VENV_DIR"
    note_status "openwakeword is available for wakeword backend openwakeword"
    return 0
  fi

  warn "could not install openwakeword into $VENV_DIR"
  note_status "openwakeword install failed; wakeword backend openwakeword will not start"
  note_manual "inspect openwakeword dependency install manually with: sudo -u $KAI_USER $venv_python -m pip install openwakeword"
  return 0
}

ensure_repo_git_identity() {
  local repo_dir="$SCRIPT_DIR"
  local current_name current_email

  if [[ ! -d "$repo_dir/.git" ]]; then
    note_status "repo git identity setup skipped (no .git directory at $repo_dir)"
    return 0
  fi

  if [[ -z "$GIT_USER_NAME" ]] || [[ -z "$GIT_USER_EMAIL" ]]; then
    warn "GIT_USER_NAME or GIT_USER_EMAIL is blank; skipping repo git identity setup"
    note_manual "set GIT_USER_NAME and GIT_USER_EMAIL in $CONFIG_FILE to configure repo git identity on this node"
    return 0
  fi

  current_name="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" config --get user.name 2>/dev/null || true)"
  current_email="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" config --get user.email 2>/dev/null || true)"

  if [[ "$current_name" == "$GIT_USER_NAME" ]] && [[ "$current_email" == "$GIT_USER_EMAIL" ]]; then
    log "repo git identity already configured for $repo_dir"
    note_status "repo git identity configured for $KAI_USER: $GIT_USER_NAME <$GIT_USER_EMAIL>"
    return 0
  fi

  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" config user.name "$GIT_USER_NAME"; then
    warn "could not set repo git user.name in $repo_dir"
    note_manual "set git user.name manually with: cd $repo_dir && git config user.name \"$GIT_USER_NAME\""
    return 0
  fi
  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" config user.email "$GIT_USER_EMAIL"; then
    warn "could not set repo git user.email in $repo_dir"
    note_manual "set git user.email manually with: cd $repo_dir && git config user.email \"$GIT_USER_EMAIL\""
    return 0
  fi
  note_change "configured repo git identity for $repo_dir as $GIT_USER_NAME <$GIT_USER_EMAIL>"
  note_status "repo git identity configured for $KAI_USER: $GIT_USER_NAME <$GIT_USER_EMAIL>"
}

ensure_kai_local_git_flow() {
  local repo_dir="$SCRIPT_DIR"
  local config_rel_path="config.env"
  local config_path="$repo_dir/$config_rel_path"
  local config_backup_path="$TMP_DIR/config.env.kai-local.backup"
  local main_remote_ref="refs/remotes/$KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH"
  local main_local_ref="refs/heads/$KAI_GIT_MAIN_BRANCH"
  local local_branch_ref="refs/heads/$KAI_GIT_LOCAL_BRANCH"
  local path current_pull_rebase current_rebase_autostash current_branch status_output
  local main_before="" main_after="" local_before="" local_after="" remote_head=""
  local local_branch_exists=0
  local first_run_config_migration=0
  local config_dirty=0
  local -a non_config_changes=() dirty_paths=()
  local -A changed_paths=()

  if [[ "$KAI_GIT_ENSURE_KAI_LOCAL_FLOW" != "1" ]]; then
    note_status "kai-local git flow automation disabled (KAI_GIT_ENSURE_KAI_LOCAL_FLOW=0)"
    return 0
  fi

  if [[ ! -d "$repo_dir/.git" ]]; then
    note_status "kai-local git flow setup skipped (no .git directory at $repo_dir)"
    return 0
  fi

  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "could not validate git repository at $repo_dir for kai-local branch setup"
  fi

  current_pull_rebase="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" config --get pull.rebase 2>/dev/null || true)"
  if [[ "$current_pull_rebase" != "true" ]]; then
    if runuser -u "$KAI_USER" -- git -C "$repo_dir" config pull.rebase true; then
      note_change "configured repo git pull.rebase=true for $repo_dir"
    else
      die "could not set pull.rebase=true in $repo_dir"
    fi
  fi

  current_rebase_autostash="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" config --get rebase.autoStash 2>/dev/null || true)"
  if [[ "$current_rebase_autostash" != "true" ]]; then
    if runuser -u "$KAI_USER" -- git -C "$repo_dir" config rebase.autoStash true; then
      note_change "configured repo git rebase.autoStash=true for $repo_dir"
    else
      die "could not set rebase.autoStash=true in $repo_dir"
    fi
  fi

  current_branch="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  [[ -n "$current_branch" ]] || die "git checkout is detached in $repo_dir; checkout $KAI_GIT_MAIN_BRANCH or $KAI_GIT_LOCAL_BRANCH before running bootstrap"

  if runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse --verify --quiet "$local_branch_ref" >/dev/null 2>&1; then
    local_branch_exists=1
  fi

  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" remote get-url "$KAI_GIT_REMOTE" >/dev/null 2>&1; then
    die "git remote \"$KAI_GIT_REMOTE\" is not configured in $repo_dir"
  fi

  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" fetch "$KAI_GIT_REMOTE" "$KAI_GIT_MAIN_BRANCH" >/dev/null 2>&1; then
    die "could not fetch $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH for kai-local branch sync"
  fi

  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse --verify --quiet "$main_remote_ref" >/dev/null 2>&1; then
    die "remote ref $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH is not available in $repo_dir"
  fi

  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse --verify --quiet "$main_local_ref" >/dev/null 2>&1; then
    if runuser -u "$KAI_USER" -- git -C "$repo_dir" branch "$KAI_GIT_MAIN_BRANCH" "$KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH" >/dev/null 2>&1; then
      note_change "created local $KAI_GIT_MAIN_BRANCH branch from $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH"
    else
      die "could not create local $KAI_GIT_MAIN_BRANCH branch in $repo_dir"
    fi
  fi

  status_output="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" status --porcelain --untracked-files=all 2>/dev/null || true)"
  if [[ -n "$status_output" ]]; then
    while IFS= read -r path; do
      [[ -n "$path" ]] && changed_paths["$path"]=1
    done < <(runuser -u "$KAI_USER" -- git -C "$repo_dir" diff --name-only 2>/dev/null || true)
    while IFS= read -r path; do
      [[ -n "$path" ]] && changed_paths["$path"]=1
    done < <(runuser -u "$KAI_USER" -- git -C "$repo_dir" diff --cached --name-only 2>/dev/null || true)
    while IFS= read -r path; do
      [[ -n "$path" ]] && changed_paths["$path"]=1
    done < <(runuser -u "$KAI_USER" -- git -C "$repo_dir" ls-files --others --exclude-standard 2>/dev/null || true)

    for path in "${!changed_paths[@]}"; do
      if [[ "$path" == "$config_rel_path" ]]; then
        config_dirty=1
        continue
      fi
      non_config_changes+=("$path")
      dirty_paths+=("$path")
    done

    if [[ ${#non_config_changes[@]} -eq 0 && "$config_dirty" == "1" && "$local_branch_exists" == "0" && "$current_branch" == "$KAI_GIT_MAIN_BRANCH" ]]; then
      if [[ ! -f "$config_path" ]]; then
        die "cannot preserve local config overrides because $config_path is missing"
      fi
      cp "$config_path" "$config_backup_path"
      first_run_config_migration=1
      note_status "detected first-run config.env override on $KAI_GIT_MAIN_BRANCH; migrating to $KAI_GIT_LOCAL_BRANCH"
    elif [[ ${#non_config_changes[@]} -eq 0 && "$config_dirty" == "1" && "$local_branch_exists" == "1" && "$current_branch" == "$KAI_GIT_LOCAL_BRANCH" ]]; then
      note_status "detected local config.env edits on $KAI_GIT_LOCAL_BRANCH; skipping git branch sync for this bootstrap run"
      note_manual "commit local config.env changes on $KAI_GIT_LOCAL_BRANCH when you are ready to persist operator settings"
      return 0
    else
      die "git working tree is dirty on $current_branch (${dirty_paths[*]:-unknown}); commit or stash changes and rerun bootstrap"
    fi
  fi

  if [[ "$first_run_config_migration" == "1" ]]; then
    if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" checkout -b "$KAI_GIT_LOCAL_BRANCH" >/dev/null 2>&1; then
      die "could not create and checkout $KAI_GIT_LOCAL_BRANCH from dirty $KAI_GIT_MAIN_BRANCH"
    fi
    if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" add "$config_rel_path" >/dev/null 2>&1; then
      die "could not stage $config_rel_path on $KAI_GIT_LOCAL_BRANCH"
    fi
    if runuser -u "$KAI_USER" -- git -C "$repo_dir" diff --cached --quiet -- "$config_rel_path"; then
      die "no staged $config_rel_path change found for first-run kai-local migration"
    fi
    if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" commit -m "chore(kai): sync local config.env overrides" >/dev/null 2>&1; then
      die "could not commit first-run $config_rel_path override on $KAI_GIT_LOCAL_BRANCH"
    fi
    note_change "committed local $config_rel_path overrides on $KAI_GIT_LOCAL_BRANCH"
    local_branch_exists=1

    if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" checkout "$KAI_GIT_MAIN_BRANCH" >/dev/null 2>&1; then
      die "could not checkout $KAI_GIT_MAIN_BRANCH after first-run kai-local migration"
    fi

    if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" checkout HEAD -- "$config_rel_path" >/dev/null 2>&1; then
      die "could not reset $config_rel_path on $KAI_GIT_MAIN_BRANCH after first-run migration"
    fi
  elif ! runuser -u "$KAI_USER" -- git -C "$repo_dir" checkout "$KAI_GIT_MAIN_BRANCH" >/dev/null 2>&1; then
    die "could not checkout $KAI_GIT_MAIN_BRANCH in $repo_dir"
  fi

  status_output="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" status --porcelain --untracked-files=all 2>/dev/null || true)"
  if [[ -n "$status_output" ]]; then
    die "$KAI_GIT_MAIN_BRANCH must be clean before sync; commit or stash changes and rerun bootstrap"
  fi

  main_before="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse "$main_local_ref" 2>/dev/null || true)"
  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" merge --ff-only "$KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH" >/dev/null 2>&1; then
    die "could not fast-forward $KAI_GIT_MAIN_BRANCH to $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH"
  fi
  main_after="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse "$main_local_ref" 2>/dev/null || true)"
  if [[ -n "$main_before" ]] && [[ "$main_before" != "$main_after" ]]; then
    note_change "fast-forwarded $KAI_GIT_MAIN_BRANCH to $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH"
  else
    note_status "$KAI_GIT_MAIN_BRANCH already up to date with $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH"
  fi

  if [[ "$local_branch_exists" != "1" ]]; then
    if runuser -u "$KAI_USER" -- git -C "$repo_dir" branch "$KAI_GIT_LOCAL_BRANCH" "$KAI_GIT_MAIN_BRANCH" >/dev/null 2>&1; then
      note_change "created local rollout branch $KAI_GIT_LOCAL_BRANCH from $KAI_GIT_MAIN_BRANCH"
      local_branch_exists=1
    else
      die "could not create local rollout branch $KAI_GIT_LOCAL_BRANCH"
    fi
  fi

  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" branch --set-upstream-to="$KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH" "$KAI_GIT_LOCAL_BRANCH" >/dev/null 2>&1; then
    die "could not set upstream for $KAI_GIT_LOCAL_BRANCH to $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH"
  fi

  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" checkout "$KAI_GIT_LOCAL_BRANCH" >/dev/null 2>&1; then
    die "could not checkout rollout branch $KAI_GIT_LOCAL_BRANCH"
  fi

  status_output="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" status --porcelain --untracked-files=all 2>/dev/null || true)"
  if [[ -n "$status_output" ]]; then
    die "$KAI_GIT_LOCAL_BRANCH must be clean before rebase; commit or stash changes and rerun bootstrap"
  fi

  local_before="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse "$local_branch_ref" 2>/dev/null || true)"
  if ! runuser -u "$KAI_USER" -- git -C "$repo_dir" rebase "$KAI_GIT_MAIN_BRANCH" >/dev/null 2>&1; then
    runuser -u "$KAI_USER" -- git -C "$repo_dir" rebase --abort >/dev/null 2>&1 || true
    die "could not rebase $KAI_GIT_LOCAL_BRANCH onto local $KAI_GIT_MAIN_BRANCH"
  fi
  local_after="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse "$local_branch_ref" 2>/dev/null || true)"
  if [[ -n "$local_before" ]] && [[ "$local_before" != "$local_after" ]]; then
    note_change "rebased $KAI_GIT_LOCAL_BRANCH onto local $KAI_GIT_MAIN_BRANCH"
  else
    note_status "$KAI_GIT_LOCAL_BRANCH already rebased on local $KAI_GIT_MAIN_BRANCH"
  fi

  remote_head="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse "$main_remote_ref" 2>/dev/null || true)"
  main_after="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" rev-parse "$main_local_ref" 2>/dev/null || true)"
  if [[ -n "$main_after" ]] && [[ -n "$remote_head" ]] && [[ "$main_after" == "$remote_head" ]]; then
    note_status "$KAI_GIT_MAIN_BRANCH is clean and up to date with $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH"
  else
    die "$KAI_GIT_MAIN_BRANCH is not aligned with $KAI_GIT_REMOTE/$KAI_GIT_MAIN_BRANCH after bootstrap sync"
  fi

  if runuser -u "$KAI_USER" -- git -C "$repo_dir" merge-base --is-ancestor "$KAI_GIT_MAIN_BRANCH" "$KAI_GIT_LOCAL_BRANCH" >/dev/null 2>&1; then
    note_status "$KAI_GIT_LOCAL_BRANCH is rebased on local $KAI_GIT_MAIN_BRANCH"
  else
    die "$KAI_GIT_LOCAL_BRANCH is not rebased on local $KAI_GIT_MAIN_BRANCH"
  fi

  if runuser -u "$KAI_USER" -- git -C "$repo_dir" diff --quiet "$KAI_GIT_MAIN_BRANCH..$KAI_GIT_LOCAL_BRANCH" -- "$config_rel_path"; then
    note_status "$KAI_GIT_LOCAL_BRANCH has no committed $config_rel_path delta against $KAI_GIT_MAIN_BRANCH"
  else
    note_status "$KAI_GIT_LOCAL_BRANCH has committed $config_rel_path overrides"
  fi

  status_output="$(runuser -u "$KAI_USER" -- git -C "$repo_dir" status --porcelain --untracked-files=all 2>/dev/null || true)"
  if [[ -n "$status_output" ]]; then
    die "working tree is not clean on $KAI_GIT_LOCAL_BRANCH after bootstrap sync"
  fi
  note_status "working tree clean on $KAI_GIT_LOCAL_BRANCH"

  if [[ "$first_run_config_migration" == "1" && -f "$config_backup_path" ]]; then
    if cmp -s "$config_backup_path" "$config_path"; then
      note_status "first-run config.env migration completed on $KAI_GIT_LOCAL_BRANCH"
    else
      die "first-run config.env migration verification failed on $KAI_GIT_LOCAL_BRANCH"
    fi
  fi
}

ensure_openwakeword_default_model() {
  local venv_python="$VENV_DIR/bin/python"
  local model_dir="$KAI_STATE_DIR/wakeword/openwakeword"
  local custom_model_dir="$model_dir/custom"
  local repo_model_dir="$SCRIPT_DIR/files/wakeword/openwakeword"
  local model_path=""
  local selected_repo_model_path=""
  local src_model_path=""
  local dest_model_path=""
  local candidate_model_path=""
  local -a synced_model_paths=()

  ensure_dir "$model_dir" 0755 "$KAI_USER" "$KAI_GROUP"
  ensure_dir "$custom_model_dir" 0755 "$KAI_USER" "$KAI_GROUP"

  if [[ -d "$repo_model_dir" ]]; then
    while IFS= read -r -d '' src_model_path; do
      case "$src_model_path" in
        *.tflite|*.onnx)
          dest_model_path="$custom_model_dir/$(basename "$src_model_path")"
          if [[ -f "$dest_model_path" ]] && cmp -s "$src_model_path" "$dest_model_path"; then
            :
          else
            install -m 0644 -o "$KAI_USER" -g "$KAI_GROUP" "$src_model_path" "$dest_model_path"
            note_change "synced openwakeword repo model: $dest_model_path"
          fi
          synced_model_paths+=("$dest_model_path")
          ;;
        *)
          note_status "ignoring unsupported openwakeword repo artifact: $(basename "$src_model_path")"
          ;;
      esac
    done < <(find "$repo_model_dir" -maxdepth 1 -type f -print0 | sort -z)
  fi

  if [[ -n "$KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL" ]]; then
    selected_repo_model_path="$custom_model_dir/$KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL"
    if [[ ! -f "$selected_repo_model_path" ]]; then
      die "KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL not found in $repo_model_dir: $KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL"
    fi
    if [[ -z "$KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS" ]]; then
      KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS="$selected_repo_model_path"
      note_change "selected openwakeword repo model: $selected_repo_model_path"
    else
      note_status "KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS is set; ignoring KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL"
    fi
  fi

  if [[ -z "$KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS" ]] && [[ ${#synced_model_paths[@]} -gt 0 ]]; then
    for candidate_model_path in "${synced_model_paths[@]}"; do
      if [[ "$candidate_model_path" == *.tflite ]]; then
        selected_repo_model_path="$candidate_model_path"
        break
      fi
    done
    if [[ -z "$selected_repo_model_path" ]]; then
      selected_repo_model_path="${synced_model_paths[0]}"
    fi

    KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS="$selected_repo_model_path"
    note_change "selected openwakeword repo model: $selected_repo_model_path"
    note_status "openwakeword repo model auto-selected (preferring .tflite)"
  fi

  if [[ -n "$KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS" ]]; then
    note_status "openwakeword model path(s): $KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS"
    return 0
  fi

  if [[ "$CREATE_VENV" != "1" ]]; then
    note_status "openwakeword model prefetch skipped (CREATE_VENV=0)"
    return 0
  fi

  if [[ ! -x "$venv_python" ]]; then
    warn "cannot prefetch openwakeword model because venv python is missing: $venv_python"
    return 0
  fi

  if ! runuser -u "$KAI_USER" -- "$venv_python" -c 'import openwakeword' >/dev/null 2>&1; then
    note_status "openwakeword model prefetch skipped (openwakeword dependency unavailable)"
    return 0
  fi

  if ! model_path="$(
    runuser -u "$KAI_USER" -- env KAI_OPENWAKEWORD_MODEL_DIR="$model_dir" "$venv_python" - <<'PY'
import os
import shutil
from pathlib import Path

import openwakeword
from openwakeword import utils

target_dir = Path(os.environ["KAI_OPENWAKEWORD_MODEL_DIR"]).resolve()
target_dir.mkdir(parents=True, exist_ok=True)

# try a one-time model sync via the official utility.
try:
    utils.download_models()
except Exception:
    pass

candidate_roots = [
    target_dir,
    Path(openwakeword.__file__).resolve().parent,
    Path.home() / ".cache" / "openwakeword",
    Path.home() / ".local" / "share" / "openwakeword",
]
patterns = ("*hey_jarvis*.tflite", "*hey_jarvis*.onnx")

candidates = []
seen = set()
for root in candidate_roots:
    if not root.exists():
        continue
    for pattern in patterns:
        for path in root.rglob(pattern):
            resolved = path.resolve()
            key = str(resolved)
            if key in seen or not resolved.is_file():
                continue
            seen.add(key)
            candidates.append(resolved)

if not candidates:
    raise SystemExit(2)

def score(path):
    # prefer tflite over onnx on linux for lower runtime cost.
    suffix_rank = 0 if path.suffix.lower() == ".tflite" else 1
    return (suffix_rank, len(path.name), path.name)

best = sorted(candidates, key=score)[0]
destination = target_dir / best.name
if best != destination:
    shutil.copy2(best, destination)

print(destination)
PY
  )"; then
    warn "could not prefetch openwakeword hey_jarvis model"
    note_status "openwakeword dependency is installed, but hey_jarvis model prefetch failed"
    note_manual "set KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS manually to a local hey_jarvis model path if wake detection is unstable"
    return 0
  fi

  if [[ -z "$model_path" ]]; then
    warn "openwakeword model prefetch returned an empty model path"
    return 0
  fi

  if [[ -z "$KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS" ]]; then
    KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS="$model_path"
    note_change "prefetched openwakeword hey_jarvis model: $model_path"
    note_status "openwakeword default wake model pinned to hey_jarvis"
    return 0
  fi

  note_status "openwakeword model path(s): $KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS"
}

ensure_runtime_user_access() {
  ensure_group_membership "$KAI_USER" audio
}

validate_sshd_config() {
  local sshd_bin
  sshd_bin="$(command -v sshd || true)"
  [[ -n "$sshd_bin" ]] || die "sshd binary not found"
  "$sshd_bin" -t
}

install_ssh_hardening() {
  local src="$SCRIPT_DIR/files/ssh/60-kai-hardening.conf"
  local backup=""

  [[ -f "$src" ]] || die "missing ssh hardening snippet: $src"

  if [[ -f "$SSH_SNIPPET_DEST" ]]; then
    backup="$TMP_DIR/$(basename "$SSH_SNIPPET_DEST").bak"
    cp "$SSH_SNIPPET_DEST" "$backup"
  fi

  if install_managed_file "$src" "$SSH_SNIPPET_DEST" 0644 root root; then
    log "validating sshd configuration"
    if ! validate_sshd_config; then
      warn "restoring previous sshd snippet after validation failure"
      if [[ -n "$backup" ]]; then
        install -D -m 0644 -o root -g root "$backup" "$SSH_SNIPPET_DEST"
      else
        rm -f "$SSH_SNIPPET_DEST"
      fi
      die "sshd config validation failed"
    fi
    SSH_SNIPPET_CHANGED=1
  fi
}

reload_ssh_if_needed() {
  if [[ "$SSH_SNIPPET_CHANGED" != "1" ]]; then
    return 0
  fi

  log "reloading ssh to apply the managed snippet"
  if systemctl reload ssh; then
    note_change "reloaded ssh"
    return 0
  fi

  warn "could not reload ssh automatically"
  note_manual "reload ssh manually with: sudo systemctl reload ssh"
}

enable_avahi_if_requested() {
  if [[ "$INSTALL_AVAHI" != "1" ]]; then
    note_manual "avahi is disabled in config.env, so kai.local will not be available from the local lan"
    return 0
  fi

  log "ensuring avahi-daemon is enabled"
  if systemctl enable --now avahi-daemon; then
    note_change "ensured avahi-daemon is enabled"
    return 0
  fi

  warn "could not enable avahi-daemon automatically"
  note_manual "enable avahi-daemon manually with: sudo systemctl enable --now avahi-daemon"
}

install_systemd_unit() {
  local template="$SCRIPT_DIR/files/systemd/kai-edge.service.tmpl"
  local rendered="$TMP_DIR/kai-edge.service"

  [[ -f "$template" ]] || die "missing systemd unit template: $template"

  render_systemd_unit "$template" "$rendered"
  if install_managed_file "$rendered" "$SYSTEMD_UNIT_DEST" 0644 root root; then
    SYSTEMD_UNIT_CHANGED=1
  fi
}

reload_systemd_if_needed() {
  if [[ "$SYSTEMD_UNIT_CHANGED" != "1" ]]; then
    return 0
  fi

  log "reloading systemd manager configuration"
  systemctl daemon-reload
  note_change "reloaded systemd manager configuration"
}

install_audio_helper() {
  local src="$SCRIPT_DIR/scripts/kai-audio-check.sh"
  [[ -f "$src" ]] || die "missing audio helper: $src"
  if install_managed_file "$src" "$AUDIO_HELPER_DEST" 0755 "$KAI_USER" "$KAI_GROUP"; then
    :
  fi
}

install_runtime_python_package() {
  local src_dir="$SCRIPT_DIR/kai_edge"
  local output

  [[ -d "$src_dir" ]] || die "missing runtime package directory: $src_dir"
  install -d -m 0755 -o "$KAI_USER" -g "$KAI_GROUP" "$EDGE_PACKAGE_DEST"

  output="$(rsync -rlpt --delete --checksum --itemize-changes "$src_dir/" "$EDGE_PACKAGE_DEST/")"
  chown -R "$KAI_USER:$KAI_GROUP" "$EDGE_PACKAGE_DEST"

  if [[ -n "$output" ]]; then
    note_change "synced runtime package to $EDGE_PACKAGE_DEST"
  else
    log "runtime package already current: $EDGE_PACKAGE_DEST"
  fi
}

install_push_to_talk_helper() {
  local src="$SCRIPT_DIR/scripts/kai-push-to-talk.py"
  [[ -f "$src" ]] || die "missing push-to-talk helper: $src"
  if install_managed_file "$src" "$PUSH_TO_TALK_DEST" 0755 "$KAI_USER" "$KAI_GROUP"; then
    :
  fi
}

install_edge_daemon_helper() {
  local src="$SCRIPT_DIR/scripts/kai-edge-daemon.py"
  [[ -f "$src" ]] || die "missing edge daemon helper: $src"
  if install_managed_file "$src" "$EDGE_DAEMON_DEST" 0755 "$KAI_USER" "$KAI_GROUP"; then
    :
  fi
}

install_edge_trigger_helper() {
  local src="$SCRIPT_DIR/scripts/kai-edge-trigger.py"
  [[ -f "$src" ]] || die "missing edge trigger helper: $src"
  if install_managed_file "$src" "$EDGE_TRIGGER_DEST" 0755 "$KAI_USER" "$KAI_GROUP"; then
    :
  fi
}

install_edge_status_helper() {
  local src="$SCRIPT_DIR/scripts/kai-edge-status.sh"
  [[ -f "$src" ]] || die "missing edge status helper: $src"
  if install_managed_file "$src" "$EDGE_STATUS_DEST" 0755 "$KAI_USER" "$KAI_GROUP"; then
    :
  fi
}

install_journald_retention_config() {
  local template="$SCRIPT_DIR/files/journald/kai-edge-retention.conf.tmpl"
  local rendered="$TMP_DIR/kai-edge-retention.conf"

  if [[ "$MANAGE_JOURNALD_RETENTION" != "1" ]]; then
    if [[ -f "$JOURNALD_DROPIN_DEST" ]]; then
      rm -f "$JOURNALD_DROPIN_DEST"
      note_change "removed managed journald retention config at $JOURNALD_DROPIN_DEST"
      JOURNALD_DROPIN_CHANGED=1
    else
      log "journald retention management disabled in config.env"
    fi
    return 0
  fi

  [[ -f "$template" ]] || die "missing journald retention template: $template"
  render_journald_dropin "$template" "$rendered"
  if install_managed_file "$rendered" "$JOURNALD_DROPIN_DEST" 0644 root root; then
    JOURNALD_DROPIN_CHANGED=1
    note_status "managed journald retention policy installed at $JOURNALD_DROPIN_DEST"
  fi
}

reload_journald_if_needed() {
  if [[ "$JOURNALD_DROPIN_CHANGED" != "1" ]]; then
    return 0
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found; cannot reload systemd-journald automatically"
    note_manual "reload journald manually with: sudo systemctl restart systemd-journald"
    return 0
  fi

  log "restarting systemd-journald to apply retention changes"
  if systemctl restart systemd-journald; then
    note_change "restarted systemd-journald for updated retention settings"
    return 0
  fi

  warn "could not restart systemd-journald automatically"
  note_manual "restart journald manually with: sudo systemctl restart systemd-journald"
  return 0
}

install_logrotate_config() {
  local template="$SCRIPT_DIR/files/logrotate/kai-edge.tmpl"
  local rendered="$TMP_DIR/kai-edge.logrotate"

  if [[ "$MANAGE_KAI_LOGROTATE" != "1" ]]; then
    if [[ -f "$LOGROTATE_CONFIG_DEST" ]]; then
      rm -f "$LOGROTATE_CONFIG_DEST"
      note_change "removed managed logrotate config at $LOGROTATE_CONFIG_DEST"
    fi
    return 0
  fi

  [[ -f "$template" ]] || die "missing logrotate template: $template"
  render_logrotate_config "$template" "$rendered"
  if install_managed_file "$rendered" "$LOGROTATE_CONFIG_DEST" 0644 root root; then
    note_status "managed logrotate policy installed at $LOGROTATE_CONFIG_DEST"
  fi
}

ensure_kai_edge_service_if_requested() {
  local enabled_state active_state

  if [[ ! -f "$SYSTEMD_UNIT_DEST" ]]; then
    die "cannot manage kai-edge.service because the unit file is missing: $SYSTEMD_UNIT_DEST"
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found; cannot manage kai-edge.service runtime state"
    note_manual "manage kai-edge.service manually because systemctl is unavailable"
    return 0
  fi

  if [[ "$ENABLE_KAI_EDGE_SERVICE" == "1" ]]; then
    log "ensuring kai-edge.service is enabled and active"
    if systemctl enable --now kai-edge.service; then
      note_change "ensured kai-edge.service is enabled and active"
      return 0
    fi

    warn "could not enable kai-edge.service automatically"
    note_manual "enable and start kai-edge.service manually with: sudo systemctl enable --now kai-edge.service"
    return 1
  fi

  enabled_state="$(systemctl is-enabled kai-edge.service 2>/dev/null || true)"
  active_state="$(systemctl is-active kai-edge.service 2>/dev/null || true)"
  note_status "kai-edge.service install complete (enabled: ${enabled_state:-disabled}, active: ${active_state:-inactive})"
  note_manual "service autostart is disabled by ENABLE_KAI_EDGE_SERVICE=0; start it manually with: sudo systemctl start kai-edge.service"
  return 0
}

install_edge_env() {
  local template="$SCRIPT_DIR/files/env/edge.env.tmpl"
  local rendered="$TMP_DIR/edge.env"

  [[ -f "$template" ]] || die "missing edge env template: $template"

  render_edge_env "$template" "$rendered"
  if install_managed_file "$rendered" "$EDGE_ENV_DEST" 0644 root root; then
    :
  fi
}

render_doctor_config() {
  local output=$1

  {
    printf '%s\n' '# managed by kai-edge bootstrap'
    printf 'KAI_USER=%q\n' "$KAI_USER"
    printf 'KAI_GROUP=%q\n' "$KAI_GROUP"
    printf 'KAI_ROOT=%q\n' "$KAI_ROOT"
    printf 'KAI_APP_DIR=%q\n' "$KAI_APP_DIR"
    printf 'KAI_BIN_DIR=%q\n' "$KAI_BIN_DIR"
    printf 'KAI_CONFIG_DIR=%q\n' "$KAI_CONFIG_DIR"
    printf 'KAI_STATE_DIR=%q\n' "$KAI_STATE_DIR"
    printf 'KAI_LOG_DIR=%q\n' "$KAI_LOG_DIR"
    printf 'KAI_VENV_DIR=%q\n' "$VENV_DIR"
    printf 'CREATE_VENV=%q\n' "$CREATE_VENV"
    printf 'INSTALL_AVAHI=%q\n' "$INSTALL_AVAHI"
    printf 'INSTALL_RASPAP=%q\n' "$INSTALL_RASPAP"
    printf 'RASPAP_INSTALL_URL=%q\n' "$RASPAP_INSTALL_URL"
    printf 'RASPAP_AP_SSID=%q\n' "$RASPAP_AP_SSID"
    printf 'RASPAP_AP_SUBNET_CIDR=%q\n' "$RASPAP_AP_SUBNET_CIDR"
    printf 'RASPAP_AP_DHCP_RANGE=%q\n' "$RASPAP_AP_DHCP_RANGE"
    printf 'RASPAP_ENABLE_FALLBACK_AP=%q\n' "$RASPAP_ENABLE_FALLBACK_AP"
    printf 'RASPAP_ADMIN_USER=%q\n' "$RASPAP_ADMIN_USER"
    printf 'ENABLE_KAI_EDGE_SERVICE=%q\n' "$ENABLE_KAI_EDGE_SERVICE"
    printf 'SSH_SNIPPET=%q\n' "$SSH_SNIPPET_DEST"
    printf 'SYSTEMD_UNIT=%q\n' "$SYSTEMD_UNIT_DEST"
    printf 'EDGE_ENV_FILE=%q\n' "$EDGE_ENV_DEST"
    printf 'EDGE_DAEMON_HELPER=%q\n' "$EDGE_DAEMON_DEST"
    printf 'EDGE_TRIGGER_HELPER=%q\n' "$EDGE_TRIGGER_DEST"
    printf 'EDGE_STATUS_HELPER=%q\n' "$EDGE_STATUS_DEST"
    printf 'EDGE_RUNTIME_PACKAGE_DIR=%q\n' "$EDGE_PACKAGE_DEST"
    printf 'PUSH_TO_TALK_HELPER=%q\n' "$PUSH_TO_TALK_DEST"
    printf 'MANAGE_JOURNALD_RETENTION=%q\n' "$MANAGE_JOURNALD_RETENTION"
    printf 'JOURNALD_DROPIN=%q\n' "$JOURNALD_DROPIN_DEST"
    printf 'JOURNALD_SYSTEM_MAX_USE=%q\n' "$JOURNALD_SYSTEM_MAX_USE"
    printf 'JOURNALD_RUNTIME_MAX_USE=%q\n' "$JOURNALD_RUNTIME_MAX_USE"
    printf 'JOURNALD_MAX_FILE_SEC=%q\n' "$JOURNALD_MAX_FILE_SEC"
    printf 'MANAGE_KAI_LOGROTATE=%q\n' "$MANAGE_KAI_LOGROTATE"
    printf 'KAI_LOGROTATE_CONFIG=%q\n' "$LOGROTATE_CONFIG_DEST"
  } > "$output"
}

install_doctor_config() {
  local rendered="$TMP_DIR/bootstrap.env"
  render_doctor_config "$rendered"
  if install_managed_file "$rendered" "$DOCTOR_CONFIG_DEST" 0644 root root; then
    :
  fi
}

install_doctor_helper() {
  local src="$SCRIPT_DIR/scripts/kai-doctor.sh"
  [[ -f "$src" ]] || die "missing doctor helper: $src"
  if install_managed_file "$src" "$DOCTOR_HELPER_DEST" 0755 "$KAI_USER" "$KAI_GROUP"; then
    :
  fi
}

prepare_manual_follow_up() {
  note_manual "review $SSH_SNIPPET_DEST before making stronger ssh changes that could affect your access path"
  note_manual "run sudo $DOCTOR_HELPER_DEST after bootstrap to validate node readiness"
  note_manual "run $AUDIO_HELPER_DEST after the target microphone and speaker hardware are attached"
  note_manual "run sudo -u $KAI_USER $PUSH_TO_TALK_DEST for a manual one-shot record -> /audio -> playback test"
  note_manual "run sudo -u $KAI_USER $EDGE_TRIGGER_DEST to trigger one daemon-managed push-to-talk interaction"
  note_manual "run $EDGE_STATUS_DEST to inspect live daemon state and counters"
  note_manual "inspect service logs with: sudo journalctl -u kai-edge.service -f"

  if [[ "$INSTALL_RASPAP" == "1" ]]; then
    note_manual "join the fallback AP SSID \"$RASPAP_AP_SSID\" to reach the setup network if upstream wifi is unavailable"
    note_manual "open the raspap web ui from the same lan and verify login with the configured admin user: $RASPAP_ADMIN_USER"
    note_manual "restart the node (or restart hostapd, dnsmasq, dhcpcd) after bootstrap if AP settings do not apply immediately"
  fi

  if [[ -z "$KAI_CORE_BASE_URL" ]]; then
    note_status "kai-core base URL is not configured yet"
    note_manual "set KAI_CORE_BASE_URL in $CONFIG_FILE and rerun bootstrap before using $EDGE_TRIGGER_DEST or $PUSH_TO_TALK_DEST"
  else
    note_status "kai-core base URL configured for push-to-talk: $KAI_CORE_BASE_URL"
  fi

  note_status "kai-edge trigger mode configured: $KAI_TRIGGER_MODE"
  if [[ "$KAI_TRIGGER_MODE" == "vad" ]]; then
    note_manual "VAD mode is armed listening; use the physical mute switch during development testing"
  elif [[ "$KAI_TRIGGER_MODE" == "wakeword" ]]; then
    note_status "wakeword backend configured: $KAI_WAKEWORD_BACKEND"
    if [[ "$KAI_WAKEWORD_BACKEND" == "openwakeword" ]]; then
      if [[ -n "$KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL" ]]; then
        note_status "openwakeword repo model selector: $KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL"
      fi
      if [[ -n "$KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS" ]]; then
        note_status "openwakeword model path(s): $KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS"
      else
        note_status "openwakeword using backend defaults (no explicit model paths configured)"
      fi
      note_status "openwakeword wake phrase target is defined by the selected model file(s)"
    fi
    note_manual "wakeword mode keeps passive listening armed; use the physical mute switch when not actively testing"
    note_manual "validate wakeword flow end-to-end with: sudo journalctl -u kai-edge.service -f and $EDGE_STATUS_DEST"
  fi

  if [[ "$ENABLE_KAI_EDGE_SERVICE" == "1" ]]; then
    note_status "kai-edge.service will be enabled and started by bootstrap"
  else
    note_status "kai-edge.service will be installed but left disabled (ENABLE_KAI_EDGE_SERVICE=0)"
    note_manual "enable and start kai-edge.service manually with: sudo systemctl enable --now kai-edge.service"
  fi

  if [[ "$MANAGE_JOURNALD_RETENTION" == "1" ]]; then
    note_status "managed journald retention policy enabled"
  else
    note_status "managed journald retention policy disabled"
  fi

  if [[ "$MANAGE_KAI_LOGROTATE" == "1" ]]; then
    note_status "managed logrotate policy enabled for $KAI_LOG_DIR/*.log"
  fi

  if [[ "$INSTALL_AVAHI" == "1" ]]; then
    note_manual "verify mdns reachability from the same lan with: ping kai.local or ssh $KAI_USER@kai.local"
  fi
}

print_summary() {
  local item

  printf '\n'
  log "bootstrap complete"
  printf '\nchanges\n'

  if [[ ${#CHANGED_ITEMS[@]} -eq 0 ]]; then
    printf -- '- no managed changes were required\n'
  else
    for item in "${CHANGED_ITEMS[@]}"; do
      printf -- '- %s\n' "$item"
    done
  fi

  printf '\nstatus\n'
  if [[ ${#STATUS_ITEMS[@]} -eq 0 ]]; then
    printf -- '- no additional status notes\n'
  else
    for item in "${STATUS_ITEMS[@]}"; do
      printf -- '- %s\n' "$item"
    done
  fi

  printf '\nmanual follow-up\n'
  for item in "${MANUAL_ITEMS[@]}"; do
    printf -- '- %s\n' "$item"
  done

  printf '\nmanaged paths\n'
  printf -- '- %s\n' "$KAI_ROOT"
  printf -- '- %s\n' "$KAI_CONFIG_DIR"
  printf -- '- %s\n' "$KAI_STATE_DIR"
  printf -- '- %s\n' "$KAI_LOG_DIR"
  printf -- '- %s\n' "$SSH_SNIPPET_DEST"
  printf -- '- %s\n' "$SYSTEMD_UNIT_DEST"
  printf -- '- %s\n' "$EDGE_ENV_DEST"
  printf -- '- %s\n' "$EDGE_PACKAGE_DEST"
  printf -- '- %s\n' "$EDGE_DAEMON_DEST"
  printf -- '- %s\n' "$EDGE_TRIGGER_DEST"
  printf -- '- %s\n' "$EDGE_STATUS_DEST"
  printf -- '- %s\n' "$PUSH_TO_TALK_DEST"
  printf -- '- %s\n' "$LOGROTATE_CONFIG_DEST"
  printf -- '- %s\n' "$JOURNALD_DROPIN_DEST"
}

main() {
  load_config
  prepare_manual_follow_up
  ensure_packages
  ensure_repo_git_identity
  ensure_kai_local_git_flow
  ensure_runtime_user_access
  install_raspap_if_requested
  configure_raspap_if_requested
  remove_raspap_self_gateway_route_if_present
  ensure_raspap_service_if_requested || true
  install_tailscale_if_missing
  if ensure_tailscaled_service; then
    check_tailscale_state
  fi
  ensure_base_directories
  ensure_python_venv
  ensure_runtime_python_dependencies
  ensure_openwakeword_default_model
  install_ssh_hardening
  reload_ssh_if_needed
  enable_avahi_if_requested
  install_audio_helper
  install_runtime_python_package
  install_push_to_talk_helper
  install_edge_daemon_helper
  install_edge_trigger_helper
  install_edge_status_helper
  install_edge_env
  install_logrotate_config
  install_journald_retention_config
  reload_journald_if_needed
  install_systemd_unit
  reload_systemd_if_needed
  ensure_kai_edge_service_if_requested || true
  install_doctor_config
  install_doctor_helper
  print_summary
}

main "$@"
