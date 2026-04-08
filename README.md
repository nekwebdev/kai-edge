# kai-edge

minimal repo for provisioning and operating the `kai` raspberry pi edge node.

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
├── kai_edge
│   ├── audio.py
│   ├── cli
│   │   ├── daemon.py
│   │   ├── push_to_talk.py
│   │   └── trigger.py
│   ├── config.py
│   ├── core_client.py
│   ├── daemon.py
│   ├── interaction.py
│   ├── state.py
│   └── trigger_client.py
├── scripts
│   ├── kai-audio-check.sh
│   ├── kai-doctor.sh
│   ├── kai-edge-daemon.py
│   ├── kai-edge-trigger.py
│   └── kai-push-to-talk.py
├── tests
│   ├── test_config.py
│   └── test_core_client.py
└── README.md
```

## runtime architecture

`kai-edge` now runs as an explicit pi-side daemon for manual push-to-talk.

the daemon state machine is:

- `idle`
- `recording`
- `sending`
- `speaking`
- `error`

runtime flow for one interaction:

1. wait in `idle` for a local trigger command.
2. transition to `recording` and capture one wav clip with `arecord`.
3. transition to `sending` and `POST` that wav to `${KAI_CORE_BASE_URL}/audio`.
4. parse `text`, `response`, and optional `audio` from the json response.
5. when audio is present, transition to `speaking` and play it with `aplay`.
6. on success or failure, return to `idle`.

shared logic is in `kai_edge/` so both daemon mode and the one-shot helper use the same config/audio/http path.

## trigger model

the trigger model is a local unix socket command:

- daemon listens on `KAI_TRIGGER_SOCKET_PATH` (default `/run/kai-edge/trigger.sock`)
- operator runs `/opt/kai/bin/kai-edge-trigger`
- trigger command sends one request and waits for result (`ok`, `busy`, or `error: ...`)

this works cleanly over ssh, does not require extra hardware, and keeps push-to-talk explicit.

## managed paths on the pi

- `/opt/kai`: app root, helper scripts, and optional venv
- `/opt/kai/app/kai_edge`: installed runtime python package
- `/opt/kai/bin/kai-edge-daemon`: daemon entrypoint
- `/opt/kai/bin/kai-edge-trigger`: daemon trigger helper
- `/opt/kai/bin/kai-push-to-talk`: one-shot fallback helper
- `/opt/kai/bin/kai-doctor`: readiness helper
- `/etc/kai/edge.env`: runtime config for daemon and one-shot helper
- `/etc/kai/bootstrap.env`: bootstrap state used by `kai-doctor`
- `/etc/systemd/system/kai-edge.service`: managed runtime service unit
- `/var/lib/kai`: service state
- `/var/log/kai`: optional log location for edge components
- `/etc/ssh/sshd_config.d/60-kai-hardening.conf`: conservative ssh hardening

## bootstrap behavior

`bootstrap.sh` is intentionally simple and reproducible:

- runs `apt-get update`
- installs baseline packages
- installs tailscale with the official linux install script when it is missing
- ensures `tailscaled` is enabled and running
- checks tailscale auth/ssh state and prints manual follow-up when needed
- installs and manages raspap by default (configurable via `INSTALL_RASPAP`)
- creates base directories
- optionally creates a python venv
- ensures runtime user is in the `audio` group
- installs managed ssh hardening and validates `sshd -t`
- optionally enables `avahi-daemon` for `kai.local`
- installs runtime package and helper commands
- renders `/etc/kai/edge.env`
- installs real `kai-edge.service` and reloads systemd
- optionally enables/starts `kai-edge.service` when `ENABLE_KAI_EDGE_SERVICE="1"`
- writes `/etc/kai/bootstrap.env` for `kai-doctor`
- installs `kai-doctor`

safe re-runs are handled with managed file comparisons and non-destructive directory setup.

## service enable default

bootstrap installs the real service unit by default but **does not enable/start it** unless explicitly configured.

default:

- `ENABLE_KAI_EDGE_SERVICE="0"`

reason:

- safer rollout for current project maturity
- keeps runtime activation explicit for operators
- avoids surprising service starts on nodes still being tuned

opt-in autostart:

```bash
# set in config.env, then rerun bootstrap
ENABLE_KAI_EDGE_SERVICE="1"
```

## runtime config (`/etc/kai/edge.env`)

managed keys:

- `KAI_CORE_BASE_URL`
- `KAI_RECORD_SECONDS`
- `KAI_AUDIO_SAMPLE_RATE`
- `KAI_HTTP_TIMEOUT_SECONDS`
- `KAI_RECORD_DEVICE`
- `KAI_PLAYBACK_DEVICE`
- `KAI_TRIGGER_SOCKET_PATH`

## service operations

start/stop/status:

```bash
sudo systemctl start kai-edge.service
sudo systemctl stop kai-edge.service
sudo systemctl status kai-edge.service
```

enable at boot:

```bash
sudo systemctl enable kai-edge.service
```

journal logs:

```bash
sudo journalctl -u kai-edge.service -f
```

## push-to-talk usage

### daemon path (recommended)

1. ensure daemon is running (`systemctl start kai-edge.service`).
2. trigger one interaction:

```bash
sudo -u <kai-user> /opt/kai/bin/kai-edge-trigger
```

### one-shot fallback path

`kai-push-to-talk` is still available for direct one-off runs without the daemon:

```bash
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk
```

optional one-off overrides still work:

```bash
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk --record-seconds 6
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk --backend-url http://kai-core.tailnet:8000
```

## daemon path vs one-shot helper

daemon path:

- long-running process under systemd
- explicit trigger per interaction
- stable journal logs and restart policy
- state-machine visibility (`idle`, `recording`, `sending`, `speaking`, `error`)

one-shot helper:

- starts, runs one interaction, exits
- useful for direct troubleshooting and fallback
- uses the same shared runtime modules

## post-bootstrap validation

run:

```bash
sudo /opt/kai/bin/kai-doctor
```

`kai-doctor` validates:

- expected directories, commands, runtime user, and audio group membership
- installed runtime files (`kai_edge` package, daemon/trigger/one-shot helpers, env file)
- ssh config validity
- tailscale state and tailscale ssh state
- avahi state when enabled
- raspap state when enabled
- real `kai-edge.service` unit shape (not placeholder)
- service state expectations based on `ENABLE_KAI_EDGE_SERVICE`
- trigger socket presence when service is active
- python venv and alsa device visibility

exit status:

- `0` when no `fail` checks are present
- `1` when one or more required checks fail

## intentionally not implemented yet

this runtime intentionally does **not** include:

- wake word
- vad
- streaming stt/tts
- continuous open-mic listening
- conversation memory
- multi-turn dialogue orchestration on the edge

those are later phases; this repo currently targets explicit manual one-shot push-to-talk.

## usage

review `config.env`, set `KAI_CORE_BASE_URL`, then run:

```bash
sudo ./bootstrap.sh
```

the script provisions the host it is executed on.
