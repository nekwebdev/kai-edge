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
├── requirements-runtime.txt
├── files
│   ├── env
│   │   └── edge.env.tmpl
│   ├── journald
│   │   └── kai-edge-retention.conf.tmpl
│   ├── logrotate
│   │   └── kai-edge.tmpl
│   ├── wakeword
│   │   └── openwakeword
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
│   ├── observability.py
│   ├── state.py
│   ├── vad.py
│   ├── vad_session.py
│   ├── wakeword.py
│   ├── wakeword_runtime.py
│   └── trigger_client.py
├── scripts
│   ├── kai-audio-check.sh
│   ├── kai-doctor.sh
│   ├── kai-edge-daemon.py
│   ├── kai-edge-status.sh
│   ├── kai-edge-trigger.py
│   └── kai-push-to-talk.py
├── tests
│   ├── test_config.py
│   ├── test_core_client.py
│   ├── test_daemon.py
│   ├── test_interaction.py
│   ├── test_observability.py
│   ├── test_vad_session.py
│   ├── test_wakeword.py
│   └── test_wakeword_runtime.py
└── README.md
```

## runtime architecture

`kai-edge` runs as an explicit pi-side daemon with three trigger modes:

- `manual`: socket-triggered push-to-talk (existing behavior)
- `vad`: armed listening with speech start/end detection
- `wakeword`: passive wake detection that hands off into VAD-bounded utterance capture

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
3. daemon sends it to `${KAI_CORE_BASE_URL}/audio` (or `${KAI_CORE_BASE_URL}/audio/stream` when edge streaming is enabled).
4. daemon plays returned audio when present (streaming playback starts on first audio chunk when enabled).
5. daemon returns to `idle`.

### vad mode

vad mode arms the microphone and loops:

1. stay in `listening` while reading short audio frames.
2. transition to `recording` when speech is detected.
3. stop on trailing silence or max utterance duration.
4. reject short/noise bursts below minimum speech duration or speech-run gate.
5. send accepted wav to `${KAI_CORE_BASE_URL}/audio` (or `${KAI_CORE_BASE_URL}/audio/stream` when edge streaming is enabled).
6. play returned audio when present (streaming playback starts on first audio chunk when enabled).
7. return to `idle`, apply cooldown, and re-arm.

### wakeword mode

wakeword mode is layered on top of the same VAD/session logic:

1. stay in `listening` while passively scanning microphone frames for the wake keyword.
2. when wakeword is detected, start one post-wake utterance capture using the existing VAD collector.
3. reuse the same VAD gates for speech start, pre-roll, trailing silence stop, max duration stop, and accept/reject thresholds.
4. if speech does not start before timeout, discard and re-arm wake listening.
5. send accepted wav to `${KAI_CORE_BASE_URL}/audio` (or `${KAI_CORE_BASE_URL}/audio/stream` when edge streaming is enabled).
6. play returned audio when present (streaming playback starts on first audio chunk when enabled).
7. return to `idle`, apply cooldown, and re-arm passive wake listening.

logs include mode selection, arm state, speech start/end, accept/reject reason, sending/speaking/idle transitions, and errors.
the daemon also emits a periodic observability summary with counters (interactions, accepted/rejected, wake detections, wake timeouts, retrigger suppressions, stop/rejection reasons, error count, and utterance duration stats).

when enabled, the daemon writes a runtime status artifact to `/run/kai-edge/status.json` so operators and `kai-doctor` can inspect current state and counters without scraping logs.

## backend choices

the daemon prefers the lightweight `webrtcvad` python module when available.
if it is missing or the sample rate is unsupported, it falls back to an internal energy-threshold detector.

for wakeword detection, the daemon supports two backends:

- `openwakeword` (default):
  - no access-key signup required
  - local/offline inference at runtime
  - supports repo-staged models under `files/wakeword/openwakeword/`
  - bootstrap selects repo-staged models first (prefers `.tflite`), then falls back to `hey_jarvis` prefetch when no model is selected
  - can use custom model paths
  - usually higher cpu usage than porcupine on raspberry pi
- `porcupine` (`pvporcupine`):
  - very low runtime cpu overhead on raspberry pi
  - local/offline inference at runtime
  - stable python integration
  - requires picovoice access key and keyword configuration

operator guidance:

- choose `openwakeword` if you want a zero-signup setup and can afford higher cpu.
- choose `porcupine` if you want lower cpu and have a valid `KAI_WAKEWORD_ACCESS_KEY`.
- for both backends, quality still depends on mic placement, gain, and room noise.

this keeps deployment simple and avoids heavyweight speech stacks while still giving a practical daily-use wake + utterance loop for pi testing.

bootstrap now syncs runtime python dependencies from `requirements-runtime.txt` into the managed venv (when `CREATE_VENV=1`), and the systemd unit runs the daemon with that venv python.

## managed paths on the pi

- `/opt/kai`: app root, helper scripts, and optional venv
- `/opt/kai/app/kai_edge`: installed runtime python package
- `/opt/kai/bin/kai-edge-daemon`: daemon entrypoint
- `/opt/kai/bin/kai-edge-status`: runtime status helper
- `/opt/kai/bin/kai-edge-trigger`: daemon trigger helper
- `/opt/kai/bin/kai-push-to-talk`: one-shot fallback helper
- `/opt/kai/bin/kai-doctor`: readiness helper
- `/etc/kai/edge.env`: runtime config for daemon and one-shot helper
- `/etc/kai/bootstrap.env`: bootstrap state used by `kai-doctor`
- `/etc/systemd/system/kai-edge.service`: managed runtime service unit
- `/etc/systemd/journald.conf.d/90-kai-edge-retention.conf`: managed journald retention policy (optional, bootstrap-managed)
- `/etc/logrotate.d/kai-edge`: managed rotation policy for optional `/var/log/kai/*.log` files
- `/run/kai-edge/status.json`: daemon runtime status artifact (when enabled)
- `/var/lib/kai`: service state
- `/var/lib/kai/wakeword/openwakeword`: bootstrap-managed openwakeword model cache
- `/var/lib/kai/wakeword/openwakeword/custom`: repo-staged openwakeword model files synced by bootstrap
- `/var/log/kai`: optional file-log location for edge components
- `/etc/ssh/sshd_config.d/60-kai-hardening.conf`: conservative ssh hardening

## bootstrap behavior

`bootstrap.sh` is intentionally simple and reproducible:

- runs `apt-get update`
- installs baseline packages (including `ripgrep` / `rg`)
- installs tailscale with the official linux install script when it is missing
- ensures `tailscaled` is enabled and running
- enforces tailscale DNS acceptance (`--accept-dns=true`) when `KAI_TAILSCALE_ACCEPT_DNS="1"` so tailnet hostnames (for example `lotus`) resolve reliably
- checks tailscale auth/ssh state and prints manual follow-up when needed
- installs and manages raspap by default (configurable via `INSTALL_RASPAP`)
- ensures `/etc/raspap/hostapd.ini` exists so raspap networking pages parse cleanly
- applies a compatibility patch for older raspap builds that reject `wpa_cli` network id `0` on wifi add/connect
- creates base directories
- optionally creates a python venv
- syncs runtime python dependencies into the managed venv (when `CREATE_VENV=1`)
- configures repo-local git identity in the bootstrap checkout from `GIT_USER_NAME` and `GIT_USER_EMAIL`
- enforces kai rollout git flow (configurable): keeps local `main` fast-forwarded to `origin/main`, ensures a `kai-local` branch exists, commits local `config.env` overrides on `kai-local`, rebases `kai-local` on local `main`, and leaves the checkout on `kai-local`
- syncs repo-staged openwakeword model files from `files/wakeword/openwakeword/` into `/var/lib/kai/wakeword/openwakeword/custom/`
- selects an openwakeword repo model when available (prefers `.tflite` unless `KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL` is set)
- prefetches and pins an openwakeword `hey_jarvis` model only when no openwakeword model path is set
- ensures runtime user is in the `audio` group
- installs managed ssh hardening and validates `sshd -t`
- optionally enables `avahi-daemon` for `kai.local`
- installs runtime package and helper commands
- renders `/etc/kai/edge.env` including trigger mode, wakeword settings, VAD settings, and observability settings
- installs `kai-edge-status` for quick service/runtime inspection
- installs a managed logrotate policy for optional `/var/log/kai/*.log` files
- optionally installs a managed journald retention drop-in with conservative size/time bounds
- installs real `kai-edge.service` and reloads systemd
- optionally enables/starts `kai-edge.service` when `ENABLE_KAI_EDGE_SERVICE="1"`
- writes `/etc/kai/bootstrap.env` for `kai-doctor`
- installs `kai-doctor`
- applies a safety guard that removes invalid fallback AP self-gateway routes on `wlan0`

re-runs are idempotent for managed files and non-destructive directory setup; git-flow guardrails now stop bootstrap when branch cleanliness assumptions are violated.

git rollout flow keys in `config.env`:

- `GIT_USER_NAME`
- `GIT_USER_EMAIL`
- `KAI_GIT_ENSURE_KAI_LOCAL_FLOW` (`1` default, set `0` to disable bootstrap branch automation)
- `KAI_GIT_REMOTE` (default `origin`)
- `KAI_GIT_MAIN_BRANCH` (default `main`)
- `KAI_GIT_LOCAL_BRANCH` (default `kai-local`)
- when enabled, bootstrap only allows one dirty scenario: first run on `main` with `config.env` edits and no existing `kai-local`; bootstrap migrates those edits into a new `kai-local` commit, resets `main`, then returns to `kai-local`
- on reruns, bootstrap allows one dirty case: `kai-local` has local `config.env` edits; branch sync is skipped for that run so config changes can be iterated without committing yet
- bootstrap still exits with an error for dirty non-`config.env` paths
- update flow is `main` fast-forward from `origin/main`, then `kai-local` rebase onto local `main`

tailscale key in `config.env`:

- `KAI_TAILSCALE_ACCEPT_DNS` (`1` default): when enabled, bootstrap sets `tailscale --accept-dns=true` so tailnet hostnames resolve on the node

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
- `KAI_AUDIO_STREAM_ENABLED` (`1` enables `/audio/stream`; default `0`)
- `KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM` (`1` retries `/audio` only if stream fails before playback starts)
- `KAI_RECORD_DEVICE`
- `KAI_PLAYBACK_DEVICE`

trigger keys:

- `KAI_TRIGGER_MODE` (`manual`, `vad`, or `wakeword`)
- `KAI_TRIGGER_SOCKET_PATH` (manual trigger socket path)

wakeword keys:

- `KAI_WAKEWORD_BACKEND` (`openwakeword` default, or `porcupine`)
- `KAI_WAKEWORD_ACCESS_KEY` (required for `porcupine`, ignored by `openwakeword`)
- `KAI_WAKEWORD_BUILTIN_KEYWORD` (default `porcupine`)
- `KAI_WAKEWORD_KEYWORD_PATH` (optional absolute path to `.ppn`)
- `KAI_WAKEWORD_MODEL_PATH` (optional absolute path to `.pv`)
- `KAI_WAKEWORD_SENSITIVITY` (`0.0` to `1.0`)
- `KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL` (optional filename from `files/wakeword/openwakeword/`, `.tflite` or `.onnx`)
- `KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS` (optional comma-separated absolute model paths)
- if blank, bootstrap first tries repo-staged models under `/var/lib/kai/wakeword/openwakeword/custom/`, then tries `hey_jarvis` prefetch under `/var/lib/kai/wakeword/openwakeword/`
- `KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD` (`0.0` to `1.0`)
- `KAI_WAKEWORD_DETECTION_COOLDOWN_MS`
- `KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS`

vad tuning keys (used for `vad` mode and post-wake utterance capture in `wakeword` mode):

- `KAI_VAD_AGGRESSIVENESS`
- `KAI_VAD_FRAME_MS`
- `KAI_VAD_PRE_ROLL_MS`
- `KAI_VAD_MIN_SPEECH_MS`
- `KAI_VAD_MIN_SPEECH_RUN_MS`
- `KAI_VAD_TRAILING_SILENCE_MS`
- `KAI_VAD_MAX_UTTERANCE_MS`
- `KAI_VAD_COOLDOWN_MS`
- `KAI_VAD_ENERGY_THRESHOLD` (fallback detector threshold)

observability keys:

- `KAI_OBS_SUMMARY_INTERVAL_SECONDS` (periodic summary cadence; `0` disables time-based summaries)
- `KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS` (summary cadence by interaction count; `0` disables count-based summaries)
- `KAI_OBS_STATUS_FILE_ENABLED` (`1` or `0`)
- `KAI_OBS_STATUS_FILE_PATH` (default `/run/kai-edge/status.json`)

## switching modes

1. set `KAI_TRIGGER_MODE` in `config.env`:
   - `manual` for explicit socket trigger
   - `vad` for armed listening
   - `wakeword` for passive wake detection + VAD post-wake capture
2. if using `wakeword`:
   - set `KAI_AUDIO_SAMPLE_RATE="16000"`
   - choose `KAI_WAKEWORD_BACKEND`
   - if backend is `porcupine`, set `KAI_WAKEWORD_ACCESS_KEY` and keyword source (`KAI_WAKEWORD_BUILTIN_KEYWORD` or `KAI_WAKEWORD_KEYWORD_PATH`)
   - if backend is `openwakeword`, either set `KAI_WAKEWORD_OPENWAKEWORD_REPO_MODEL` to a filename in `files/wakeword/openwakeword/`, or set `KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS` to absolute path(s)
   - if both are blank, bootstrap auto-selects a repo-staged model (prefers `.tflite`), then falls back to `hey_jarvis` prefetch
   - tune `KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD` as needed
3. rerun bootstrap:

```bash
sudo ./bootstrap.sh
```

4. restart service:

```bash
sudo systemctl restart kai-edge.service
```

## tts streaming toggle

if your `kai-core` has `POST /audio/stream` enabled, you can reduce first-audio latency on edge:

1. set in `config.env`:

```bash
KAI_AUDIO_STREAM_ENABLED="1"
KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM="1"
```

2. rerun bootstrap and restart:

```bash
sudo ./bootstrap.sh
sudo systemctl restart kai-edge.service
```

when fallback is enabled, edge retries classic `/audio` only if stream setup fails before playback starts.

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

status helper:

```bash
/opt/kai/bin/kai-edge-status
```

## logging retention

daemon/service logs are intentionally journald-first (`StandardOutput=journal`, `StandardError=journal`).

bootstrap can manage host-level journald retention with:

- `MANAGE_JOURNALD_RETENTION`
- `JOURNALD_SYSTEM_MAX_USE`
- `JOURNALD_RUNTIME_MAX_USE`
- `JOURNALD_MAX_FILE_SEC`

default policy is conservative for pi storage and is applied through `/etc/systemd/journald.conf.d/90-kai-edge-retention.conf`.

`/var/log/kai` is optional. if explicit file logs are used there, `/etc/logrotate.d/kai-edge` keeps `*.log` files bounded.

periodic summary lines look like:

```text
summary trigger=periodic mode=wakeword vad_backend=webrtcvad wake_backend=porcupine state=listening interactions=17 accepted=12 rejected=5 errors=0 wake_detections=9 wake_post_accepted=7 wake_post_timeouts=2 wake_retrigger_suppressions=9 avg_utterance_ms=2140 last_accepted_ms=2010 last_rejection=speech_too_short last_error=-
summary rejection_reasons={'speech_too_short': 4, 'speech_run_too_short': 1}
summary stop_reasons={'trailing_silence': 16, 'max_duration': 1}
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

### wakeword daemon path (first daily-use phase)

recommended workflow while validating wakeword mode:

1. keep the physical mute switch enabled until you are ready to test.
2. unmute, say the wake keyword, then speak one utterance.
3. confirm logs show `wakeword detected`, then `post-wake utterance accepted` (or an explicit timeout/rejection reason).
4. confirm reply audio plays, then daemon returns to `listening`.
5. inspect counters and backend status:

```bash
/opt/kai/bin/kai-edge-status
sudo journalctl -u kai-edge.service -f
```

first-phase limitations are expected; tune wake sensitivity and VAD thresholds conservatively for your room and mic.

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
- installed runtime files (`kai_edge` package, daemon/trigger/status/one-shot helpers, env file)
- ssh config validity
- tailscale state and tailscale ssh state
- tailscale DNS acceptance state (`CorpDNS`) against bootstrap policy
- avahi state when enabled
- raspap state when enabled
- real `kai-edge.service` unit shape (not placeholder)
- journald routing for service stdout/stderr
- service state expectations based on `ENABLE_KAI_EDGE_SERVICE`
- mode-aware runtime checks for `manual`, `vad`, and `wakeword`
- streaming tts env shape checks (`KAI_AUDIO_STREAM_ENABLED`, `KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM`)
- trigger socket expectations when service is active
- VAD config shape checks when `KAI_TRIGGER_MODE=vad` or `wakeword`
- wakeword config and model/keyword path checks when `KAI_TRIGGER_MODE=wakeword`
- raspap routing safety checks (no AP self-gateway default route on `wlan0`)
- observability env shape and status artifact path config
- runtime status artifact readability, mode match, and backend shape checks when service is active
- managed journald retention config shape when enabled
- managed logrotate policy presence and file-log coverage under `/var/log/kai`
- python venv and alsa device visibility
- webrtcvad, pvporcupine, and openwakeword availability in the managed venv when relevant

in VAD or wakeword mode, doctor explicitly does not claim end-to-end acoustic quality; it only validates safe/static runtime shape.

exit status:

- `0` when no `fail` checks are present
- `1` when one or more required checks fail

## limitations in this phase

this runtime intentionally does **not** include:

- streaming stt
- conversation memory on the edge
- multi-turn dialogue orchestration on the edge
- interruption/barge-in during playback
- external telemetry stacks (prometheus/opentelemetry/etc.)

wakeword mode limitations in this phase:

- no acoustic echo cancellation is applied, so room echo can still cause false detections
- cooldown-based retrigger suppression is time-based, not speaker-ID aware
- if post-wake speech never starts, the daemon times out and re-arms without interaction

## quick start

review `config.env`, set `KAI_CORE_BASE_URL`, choose `KAI_TRIGGER_MODE`, then run:

```bash
sudo ./bootstrap.sh
```

the script provisions the host it is executed on.
