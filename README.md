# kai-edge

minimal repo for provisioning and maintaining the `kai` raspberry pi edge node.

## scope

this repo manages the pi once you can run `sudo ./bootstrap.sh` on the host.

it does not manage:

- first-boot imaging
- initial wifi setup before bootstrap is first run
- initial hostname changes
- initial ssh key injection
- tailscale browser login or auth-key flows
- tailscale acl or policy management

## layout

```text
.
├── bootstrap.sh
├── config.env
├── files
│   ├── env
│   │   └── edge.env.tmpl
│   ├── ssh
│   │   └── 60-kai-hardening.conf
│   └── systemd
│       └── kai-edge.service.tmpl
├── scripts
│   ├── kai-audio-check.sh
│   ├── kai-push-to-talk.py
│   └── kai-doctor.sh
└── README.md
```

## what each file is for

- `bootstrap.sh`: main idempotent-ish provisioning entrypoint for the pi
- `config.env`: small set of operator-tunable defaults
- `files/env/edge.env.tmpl`: managed runtime env template rendered to `/etc/kai/edge.env`
- `files/ssh/60-kai-hardening.conf`: managed openssh hardening snippet
- `files/systemd/kai-edge.service.tmpl`: placeholder systemd unit template for the future pi-side edge service
- `scripts/kai-audio-check.sh`: simple alsa validation helper that gets installed to `/opt/kai/bin/kai-audio-check`
- `scripts/kai-push-to-talk.py`: one-shot runtime that records, POSTs to `kai-core`, and plays the reply
- `scripts/kai-doctor.sh`: post-bootstrap readiness helper that gets installed to `/opt/kai/bin/kai-doctor`
- `README.md`: operator notes, scope, and expected workflow

## managed paths on the pi

- `/opt/kai`: app root, helper scripts, and optional venv
- `/etc/kai`: future service config
- `/var/lib/kai`: service state
- `/var/log/kai`: optional log location for pi-side components
- `/etc/ssh/sshd_config.d/60-kai-hardening.conf`: conservative ssh hardening
- `/etc/systemd/system/kai-edge.service`: placeholder unit, installed but not enabled
- `/etc/kai/edge.env`: managed runtime env for the manual push-to-talk helper
- `/etc/kai/bootstrap.env`: managed bootstrap state used by `kai-doctor`
- `/opt/kai/bin/kai-doctor`: post-bootstrap validation helper
- `/opt/kai/bin/kai-push-to-talk`: manual one-shot record -> `/audio` -> playback helper

## bootstrap behavior

`bootstrap.sh` is intentionally simple:

- runs `apt-get update`
- installs baseline packages
- installs tailscale with the official linux install script when it is missing
- ensures `tailscaled` is enabled and running
- checks tailscale state and prints the next manual command(s) when login or `tailscale ssh` still needs operator action
- installs and manages RaspAP by default (configurable via `INSTALL_RASPAP`)
- creates the base directories
- optionally creates a python venv
- ensures the configured runtime user is in the `audio` group for manual ALSA access
- installs the managed ssh snippet and validates `sshd -t`
- optionally enables `avahi-daemon` for `kai.local`
- installs a placeholder `kai-edge.service`
- installs the audio helper
- installs the one-shot `kai-push-to-talk` helper
- renders `/etc/kai/edge.env` from `config.env`
- writes `/etc/kai/bootstrap.env` so `kai-doctor` can validate the configured node shape
- installs the `kai-doctor` validation helper
- prints a short summary with manual follow-up

safe re-runs are handled by comparing managed files before replacing them and by using `mkdir`-style directory setup instead of destructive resets.

## tailscale behavior

bootstrap now handles the practical parts of tailscale setup:

- if `tailscale` is missing, it installs it with `curl -fsSL https://tailscale.com/install.sh | sh`
- it ensures `tailscaled` is enabled and running
- it inspects `tailscale status --json` for login state and `tailscale debug prefs` for `RunSSH`

bootstrap still does not automate login or silently enable tailscale ssh.

the expected tailscale follow-up states are:

- not logged in yet: run `sudo tailscale up`, then run `sudo tailscale set --ssh` after login succeeds
- logged in but tailscale ssh is not enabled: run `sudo tailscale set --ssh`
- logged in and tailscale ssh is already enabled: no tailscale follow-up is needed

this keeps tailscale ssh explicit because changing it can affect active ssh access paths.

## raspap behavior

bootstrap can install RaspAP with its official installer:

- enabled by default with `INSTALL_RASPAP="1"`
- uses `RASPAP_INSTALL_URL` (default `https://install.raspap.com`)
- uses non-interactive default flags in `RASPAP_INSTALL_FLAGS`
- preconfigures AP SSID and passphrase from `RASPAP_AP_SSID` and `RASPAP_AP_PASSPHRASE`
- preconfigures a static AP subnet with `RASPAP_AP_SUBNET_CIDR`, `RASPAP_AP_DHCP_RANGE`, and `RASPAP_AP_DNS_SERVERS`
- enables fallback AP behavior by default with `RASPAP_ENABLE_FALLBACK_AP="1"` (writes `profile static_wlan0` and `fallback static_wlan0` in `dhcpcd.conf`)
- preconfigures web UI credentials from `RASPAP_ADMIN_USER` and `RASPAP_ADMIN_PASSWORD`
- ensures `lighttpd` is enabled and active after install

if you do not want RaspAP managed by bootstrap, set `INSTALL_RASPAP="0"` in `config.env`.

## defaults

the default package set is:

- `git`
- `curl`
- `vim`
- `htop`
- `jq`
- `ffmpeg`
- `python3`
- `python3-pip`
- `python3-venv`
- `alsa-utils`
- `ca-certificates`
- `rsync`
- `openssh-server`
- `avahi-daemon` when `INSTALL_AVAHI="1"`

`avahi-daemon` is enabled by default because `kai.local` is useful on the local lan, but it is still optional and can be turned off in `config.env`.

the default one-shot runtime settings written to `/etc/kai/edge.env` are:

- `KAI_CORE_BASE_URL=""`
- `KAI_RECORD_SECONDS="5"`
- `KAI_AUDIO_SAMPLE_RATE="16000"`
- `KAI_HTTP_TIMEOUT_SECONDS="60"`
- empty optional `KAI_RECORD_DEVICE` and `KAI_PLAYBACK_DEVICE`

the default RaspAP settings are:

- `INSTALL_RASPAP="1"`
- `RASPAP_INSTALL_URL="https://install.raspap.com"`
- `RASPAP_INSTALL_FLAGS="--yes --openvpn 0 --wireguard 0 --adblock 0 --rest 0 --provider 0"`
- `RASPAP_AP_SSID="Kai-Setup"`
- `RASPAP_AP_PASSPHRASE="KaiSetup12345"`
- `RASPAP_AP_SUBNET_CIDR="10.42.0.1/24"`
- `RASPAP_AP_DHCP_RANGE="10.42.0.50,10.42.0.150,255.255.255.0,12h"`
- `RASPAP_AP_DNS_SERVERS="1.1.1.1 8.8.8.8"`
- `RASPAP_ENABLE_FALLBACK_AP="1"`
- `RASPAP_ADMIN_USER="admin"`
- `RASPAP_ADMIN_PASSWORD="kai-admin-change-this"`

## usage

review `config.env`, set `KAI_CORE_BASE_URL` for your `kai-core` host, then run:

```bash
sudo ./bootstrap.sh
```

the script provisions the host it is executed on. it is fine to run it over ssh, but verify you still have another working ssh path before closing your current session after ssh-related changes.

## manual push-to-talk

after bootstrap, the minimal edge runtime is a manual one-shot command:

```bash
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk
```

the helper:

- records a fixed-duration mono WAV clip from the default ALSA capture device
- `POST`s it to `${KAI_CORE_BASE_URL}/audio` as multipart form-data with the `file` field
- prints the transcribed text and assistant response text
- decodes and plays returned audio locally when `audio` is present
- exits cleanly after the single request

use CLI flags to override the managed defaults for a specific run:

```bash
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk --record-seconds 6
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk --backend-url http://kai-core.tailnet:8000
```

if you need to target non-default ALSA devices, set `KAI_RECORD_DEVICE` or `KAI_PLAYBACK_DEVICE` in `config.env` and rerun bootstrap, or pass `--record-device` / `--playback-device` for a one-off test.

## post-bootstrap validation

run this after bootstrap finishes:

```bash
sudo /opt/kai/bin/kai-doctor
```

`kai-doctor` is a lightweight readiness check for the pi. it confirms that the expected directories and commands exist, validates the runtime user and audio group membership, checks that `/etc/kai/edge.env` and `/opt/kai/bin/kai-push-to-talk` are installed, validates `sshd -t`, reports tailscale daemon and auth state, verifies tailscale ssh from `tailscale debug prefs`, checks `avahi-daemon` when it is enabled, validates RaspAP and `lighttpd` when `INSTALL_RASPAP=1`, confirms the placeholder `kai-edge.service` unit is present, verifies the python venv, and reports whether alsa playback and capture devices are currently visible.

the output is intentionally simple:

- `ok` for checks that match the expected bootstrap state
- `warn` for operator follow-up or optional hardware visibility issues
- `fail` for important readiness gaps such as missing directories, missing commands, invalid ssh config, missing unit files, or inactive required services

the script exits `0` when there are no `fail` lines and exits `1` when one or more important checks fail.

## next steps after v1

- replace the placeholder `ExecStart` target with the actual pi-side service launcher
- reuse `/etc/kai/edge.env` as the service `EnvironmentFile` when the service shape settles
- enable and start `kai-edge.service` only after the service exists
- run `/opt/kai/bin/kai-doctor` after bootstrap or after any readiness-related node changes
- use `/opt/kai/bin/kai-audio-check --smoke-test` once the target microphone and speaker hardware are attached
