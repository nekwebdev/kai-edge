# kai-edge

minimal repo for provisioning and maintaining the `kai` raspberry pi edge node.

## scope

this repo manages the pi once you can run `sudo ./bootstrap.sh` on the host.

it does not manage:

- first-boot imaging
- wifi setup
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
│   ├── ssh
│   │   └── 60-kai-hardening.conf
│   └── systemd
│       └── kai-edge.service.tmpl
├── scripts
│   └── kai-audio-check.sh
└── README.md
```

## what each file is for

- `bootstrap.sh`: main idempotent-ish provisioning entrypoint for the pi
- `config.env`: small set of operator-tunable defaults
- `files/ssh/60-kai-hardening.conf`: managed openssh hardening snippet
- `files/systemd/kai-edge.service.tmpl`: placeholder systemd unit template for the future pi-side edge service
- `scripts/kai-audio-check.sh`: simple alsa validation helper that gets installed to `/opt/kai/bin/kai-audio-check`
- `README.md`: operator notes, scope, and expected workflow

## managed paths on the pi

- `/opt/kai`: app root, helper scripts, and optional venv
- `/etc/kai`: future service config
- `/var/lib/kai`: service state
- `/var/log/kai`: optional log location for pi-side components
- `/etc/ssh/sshd_config.d/60-kai-hardening.conf`: conservative ssh hardening
- `/etc/systemd/system/kai-edge.service`: placeholder unit, installed but not enabled

## bootstrap behavior

`bootstrap.sh` is intentionally simple:

- runs `apt-get update`
- installs baseline packages
- installs tailscale with the official linux install script when it is missing
- ensures `tailscaled` is enabled and running
- checks tailscale state and prints the next manual command(s) when login or `tailscale ssh` still needs operator action
- creates the base directories
- optionally creates a python venv
- installs the managed ssh snippet and validates `sshd -t`
- optionally enables `avahi-daemon` for `kai.local`
- installs a placeholder `kai-edge.service`
- installs the audio helper
- prints a short summary with manual follow-up

safe re-runs are handled by comparing managed files before replacing them and by using `mkdir`-style directory setup instead of destructive resets.

## tailscale behavior

bootstrap now handles the practical parts of tailscale setup:

- if `tailscale` is missing, it installs it with `curl -fsSL https://tailscale.com/install.sh | sh`
- it ensures `tailscaled` is enabled and running
- it inspects `tailscale status --json` to decide what to print in the final summary

bootstrap still does not automate login or silently enable tailscale ssh.

the expected tailscale follow-up states are:

- not logged in yet: run `sudo tailscale up`, then run `sudo tailscale set --ssh` after login succeeds
- logged in but tailscale ssh is not enabled: run `sudo tailscale set --ssh`
- logged in and tailscale ssh is already enabled: no tailscale follow-up is needed

this keeps tailscale ssh explicit because changing it can affect active ssh access paths.

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

## usage

review `config.env`, then run:

```bash
sudo ./bootstrap.sh
```

the script provisions the host it is executed on. it is fine to run it over ssh, but verify you still have another working ssh path before closing your current session after ssh-related changes.

## next steps after v1

- replace the placeholder `ExecStart` target with the actual pi-side service launcher
- add a real `EnvironmentFile` under `/etc/kai/edge.env` when the service shape settles
- enable and start `kai-edge.service` only after the service exists
- use `/opt/kai/bin/kai-audio-check --smoke-test` once the target microphone and speaker hardware are attached
