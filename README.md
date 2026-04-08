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
│   ├── audio_stream.py
│   ├── cli
│   │   ├── daemon.py
│   │   ├── push_to_talk.py
│   │   └── trigger.py
│   ├── config.py
│   ├── core_client.py
│   ├── daemon.py
│   ├── interaction.py
│   ├── state.py
│   ├── vad.py
│   ├── vad_session.py
│   └── trigger_client.py
├── scripts
│   ├── kai-audio-check.sh
│   ├── kai-doctor.sh
│   ├── kai-edge-daemon.py
│   ├── kai-edge-trigger.py
│   └── kai-push-to-talk.py
├── tests
│   ├── test_config.py
│   ├── test_core_client.py
│   ├── test_daemon.py
│   └── test_vad_session.py
└── README.md
```

## runtime architecture

`kai-edge` runs as an explicit pi-side daemon with two trigger modes:

- `manual`: socket-triggered push-to-talk (existing behavior)
- `vad`: armed listening with speech start/end detection

the daemon state model is:

- `idle`
- `listening`
- `recording`
- `sending`
- `speaking`
- `error`

shared logic lives in `kai_edge/`, so daemon mode and the one-shot helper still use the same config/audio/http/playback path.

## trigger modes

### manual mode

manual mode is unchanged:

1. daemon waits in `idle` for a local unix socket trigger.
2. on trigger, daemon records one fixed-duration wav clip.
3. daemon sends it to `${KAI_CORE_BASE_URL}/audio`.
4. daemon plays returned audio when present.
5. daemon returns to `idle`.

### vad mode

vad mode arms the microphone and loops:

1. stay in `listening` while reading short audio frames.
2. transition to `recording` when speech is detected.
3. stop on trailing silence or max utterance duration.
4. reject short/noise bursts below minimum speech duration.
5. send accepted wav to `${KAI_CORE_BASE_URL}/audio`.
6. play returned audio when present.
7. return to `idle`, apply cooldown, and re-arm.

logs include mode selection, arm state, speech start/end, accept/reject reason, sending/speaking/idle transitions, and errors.

## vad implementation choice

the daemon prefers the lightweight `webrtcvad` python module when available.
if it is missing or the sample rate is unsupported, it falls back to an internal energy-threshold detector.

this keeps deployment simple and avoids heavyweight speech stacks while still giving a practical VAD loop for pi testing.

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
- renders `/etc/kai/edge.env` including trigger mode and VAD settings
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

core/runtime keys:

- `KAI_CORE_BASE_URL`
- `KAI_RECORD_SECONDS`
- `KAI_AUDIO_SAMPLE_RATE`
- `KAI_HTTP_TIMEOUT_SECONDS`
- `KAI_RECORD_DEVICE`
- `KAI_PLAYBACK_DEVICE`

trigger keys:

- `KAI_TRIGGER_MODE` (`manual` or `vad`)
- `KAI_TRIGGER_SOCKET_PATH` (manual trigger socket path)

vad tuning keys:

- `KAI_VAD_AGGRESSIVENESS`
- `KAI_VAD_FRAME_MS`
- `KAI_VAD_PRE_ROLL_MS`
- `KAI_VAD_MIN_SPEECH_MS`
- `KAI_VAD_TRAILING_SILENCE_MS`
- `KAI_VAD_MAX_UTTERANCE_MS`
- `KAI_VAD_COOLDOWN_MS`
- `KAI_VAD_ENERGY_THRESHOLD` (fallback detector threshold)

## switching modes

1. set `KAI_TRIGGER_MODE` in `config.env`:
   - `manual` for explicit socket trigger
   - `vad` for armed listening
2. rerun bootstrap:

```bash
sudo ./bootstrap.sh
```

3. restart service:

```bash
sudo systemctl restart kai-edge.service
```

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

## usage

### manual daemon path

1. ensure daemon is running:

```bash
sudo systemctl start kai-edge.service
```

2. trigger one interaction:

```bash
sudo -u <kai-user> /opt/kai/bin/kai-edge-trigger
```

### vad daemon path (development workflow)

recommended workflow while testing VAD on the conference device:

1. keep the physical mute switch enabled by default.
2. unmute only when ready to test one utterance.
3. speak naturally; VAD captures one utterance and sends it.
4. re-mute after playback.
5. inspect logs:

```bash
sudo journalctl -u kai-edge.service -f
```

this phase relies on hardware mute for privacy/safety control during development.

### one-shot fallback path

`kai-push-to-talk` remains available for direct one-off runs without the daemon:

```bash
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk
```

optional one-off overrides still work:

```bash
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk --record-seconds 6
sudo -u <kai-user> /opt/kai/bin/kai-push-to-talk --backend-url http://kai-core.tailnet:8000
```

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
- mode-aware runtime checks for `manual` vs `vad`
- trigger socket expectations when service is active
- VAD config shape checks when `KAI_TRIGGER_MODE=vad`
- python venv and alsa device visibility

in VAD mode, doctor explicitly does not claim end-to-end speech quality; it only validates safe/static runtime shape.

exit status:

- `0` when no `fail` checks are present
- `1` when one or more required checks fail

## limitations in this phase

this runtime intentionally does **not** include:

- wake word
- streaming stt/tts
- conversation memory on the edge
- multi-turn dialogue orchestration on the edge

wake word is intentionally deferred so VAD behavior, daemon stability, and operator controls can be validated first.

## quick start

review `config.env`, set `KAI_CORE_BASE_URL`, choose `KAI_TRIGGER_MODE`, then run:

```bash
sudo ./bootstrap.sh
```

the script provisions the host it is executed on.
