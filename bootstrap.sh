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

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\\/&]/\\&/g'
}

TMP_DIR="$(mktemp -d)"
declare -a CHANGED_ITEMS=()
declare -a MANUAL_ITEMS=()
SSH_SNIPPET_CHANGED=0
SYSTEMD_UNIT_CHANGED=0

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
  : "${APT_PACKAGES_EXTRA:=}"

  if [[ -z "${KAI_USER:-}" ]]; then
    KAI_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
  fi

  id "$KAI_USER" >/dev/null 2>&1 || die "kai user does not exist: $KAI_USER"

  if [[ -z "${KAI_GROUP:-}" ]]; then
    KAI_GROUP="$(id -gn "$KAI_USER")"
  fi

  SSH_SNIPPET_DEST="/etc/ssh/sshd_config.d/60-kai-hardening.conf"
  SYSTEMD_UNIT_DEST="/etc/systemd/system/kai-edge.service"
  AUDIO_HELPER_DEST="$KAI_BIN_DIR/kai-audio-check"
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
    -e "s|__KAI_CONFIG_DIR__|$(escape_sed_replacement "$KAI_CONFIG_DIR")|g" \
    -e "s|__KAI_STATE_DIR__|$(escape_sed_replacement "$KAI_STATE_DIR")|g" \
    -e "s|__KAI_LOG_DIR__|$(escape_sed_replacement "$KAI_LOG_DIR")|g" \
    "$template" > "$output"
}

ensure_packages() {
  local packages=(
    git
    curl
    vim
    htop
    jq
    ffmpeg
    python3
    python3-pip
    python3-venv
    alsa-utils
    ca-certificates
    rsync
    openssh-server
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

prepare_manual_follow_up() {
  note_manual "tailscale is assumed present; any future re-authentication remains a manual step"
  note_manual "replace $KAI_APP_DIR/run-edge-service with the real pi-side launcher, then enable kai-edge.service when ready"
  note_manual "review $SSH_SNIPPET_DEST before making stronger ssh changes that could affect your access path"
  note_manual "run $AUDIO_HELPER_DEST after the target microphone and speaker hardware are attached"

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
}

main() {
  load_config
  prepare_manual_follow_up
  ensure_packages
  ensure_base_directories
  ensure_python_venv
  install_ssh_hardening
  reload_ssh_if_needed
  enable_avahi_if_requested
  install_systemd_unit
  reload_systemd_if_needed
  install_audio_helper
  print_summary
}

main "$@"
