# AirOpsEnv live: a model's storm days, streamed

Optional tooling for streaming a live run to X, or to any RTMP target: a model plays AirOpsEnv v3 storm days
(`airops-occ3`, three desks) back to back while the viewer shows each day as it is played, live in 3D, with airport
sound. Normal use of the plugin doesn't need any of it, and none of it ships in the wheel.

The code it drives is in the plugin: `scripts/live_runner.py` (and its settings and day order,
`scripts/live_runner.toml`) plays the days, `scripts/live_server.py` (with `src/agentenv_airops/occ/onair.py`) serves
the day so far, and the viewer's live mode is `web/live.js`, `web/director.js` and `web/audio.js`. This folder sets
them up on a cloud box and streams them. [CONTRACT.md](CONTRACT.md) has what each part promises the next.

## How it works

One GPU box, a g6.4xlarge (NVIDIA L4, 16 vCPU, 64 GB) on the AWS Deep Learning Base OSS NVIDIA driver AMI (Ubuntu
22.04), runs everything as systemd units, reached only through SSM:

```
 airops-live         live_runner.py: the model's days back to back, each an `agent-env run` of its task (the env
                     airops-occ3 and three desk agents on local Docker); spend guard, slots and intermissions;
                     writes /home/ubuntu/live/current.json
 airops-live-server  live_server.py on 127.0.0.1:8765: polls the running env's data/get every 3 s, replays the
                     calls in its own day, serves the day so far as Episode JSON, the static assets and the viewer
 airops-x            Xorg on the L4, a virtual 1920x1080 screen; a slate on the root window
 airops-capture      streamer.py: PulseAudio (a 48 kHz null sink), Chrome 156 in kiosk on
                     http://127.0.0.1:8765/web/index.html?live=http://127.0.0.1:8765&stream, and ffmpeg:
                     x11grab + the sink -> h264_nvenc High 6000k CBR, 2 s GOP, no B-frames, AAC 128k
                     -> rtmp://127.0.0.1:1935/airops
 airops-relay        mediamtx: the local relay; records the stream in one-hour fMP4 segments
 airops-push         streamer.py: copies the relay to the target (X's server and key from SSM), reconnecting
 airops-fresh        fresh.py: each minute the stream's picture against Chrome's screenshot; restarts a stale encoder
```

Why one box:

- **The sound.** `127.0.0.1` is a secure origin, so the page's AudioWorklet (its leveller and limiter) runs without
  Chrome flags; on a second box's `http://` address the page falls back to the built-in compressors and a clipper.
- **No port between boxes.** The security group needs no inbound rule, and the server's port stays on loopback.
- **The GPU.** CPU rendering is unusable: on a c7i.8xlarge the page rendered at 0.4 fps with SwiftShader and 2 fps
  with llvmpipe on 32 threads. The L4 renders it at 60 fps or more at 1920x1080, and NVENC encodes it.

## Requirements

- **AWS:** an account where you can launch g6 instances (16 vCPU of G-instance quota for a g6.4xlarge) as
  SSM-managed instances: an instance profile whose role has `AmazonSSMManagedInstanceCore` (it also reads the
  SecureString parameters below, encrypted with the default `aws/ssm` key), a subnet with a route to the internet
  (apt, PyPI, GitHub, the model endpoint, X's ingest, the viewer's map tiles) and a security group (no inbound rule).
- **An S3 bucket** in the same region. Everything travels through it with presigned URLs, so the box needs no S3
  rights.
- **A LiteLLM-compatible endpoint** and a key that reaches the model (`openai/gpt-6-astra` by default). The scripted
  test needs neither.
- **An RTMP target:** an X Media Studio RTMP source (server URL and stream key), or any `rtmp(s)://` URL.
- **On the laptop:** bash, the AWS CLI v2, python3 (with boto3, or [uv](https://docs.astral.sh/uv/)), git, curl, tar
  and zstd, and this checkout synced with `uv sync --extra dev --extra viewer --extra build` to build the server's data.

## Files

Scripts at the top of each folder run on the laptop, from the checkout's root, and take the box's instance id.
Scripts under a `box/` folder run on the box: the laptop scripts send them through SSM, and
`aws/ssm.sh ID TIMEOUT SCRIPT [ARGS]` sends any of them.

| Path | What |
|---|---|
| `live.env.example` | the settings: copy to `live/live.env` (git-ignored); `LIVE_ENV=FILE` names another |
| `aws/` | `common.sh` (the settings, `presign`, `ssm`), `launch-gpu.sh`, `launch.sh`, `teardown.sh`, `ssm.sh`, `put-secret.sh` |
| `runner/` | `setup.sh`, `start.sh`, `plan.sh`, `stop.sh`, `status.sh`, `fetch.sh`: the run box (`airops-live`) |
| `stream/` | `deploy.sh`, `start.sh`, `stop.sh`, `status.sh`, `save-recordings.sh`; `box/` is the streamer, installed in `/opt/airops-stream` with its settings in `/etc/airops-stream.env` |
| `onair/` | `prep.sh`, `upload-data.sh`, `deploy-server.sh`, `fresh.sh`, `health.sh`, `sync.sh`, `clip.sh`, `grab.sh`; `box/loudness.sh`, `box/metrics.sh`, `box/logs.sh` |
| `CONTRACT.md` | `current.json`, the live server's endpoints and live object, the stream's watchdog, the sound |

## Setup

**1. Settings and secrets.** Fill in the settings, then store the model's key in SSM. `put-secret.sh` reads a value
without echoing it, so a key never goes in a command you type, in a file or in a chat.

```bash
cp live/live.env.example live/live.env       # then edit it
live/aws/put-secret.sh litellm-key
```

**2. The server's data,** once, on the laptop: the raw public inputs in `cache/inputs/` (BTS, FAA and IEM, as
`scripts/build_pack.py` downloads them; the snippet fetches what the days' months still lack), the viewer's static
assets from the dataset, and each day's news, hub traffic and radar frames (`prebuild`: under a minute a day, most of
it the radar of a day the dataset has none for). The server prebuilds a day missing from them on the fly.

```bash
python - <<'PY'
import re
from pathlib import Path
from agentenv_airops.build import pack
days = re.findall(r'"b6-(\d{4})(\d\d)\d\d-0400"', Path("scripts/live_runner.toml").read_text())
pack.fetch_inputs(Path("cache/inputs"), sorted({f"{y}-{m}" for y, m in days}), ["JFK", "BOS"])
PY
hf download earakely-scale/AirOpsEnv-AgentEnv --repo-type dataset --revision v0.5.0 \
    --include "viewer/stage/*" "viewer/radar/*" "viewer/twin/*" --local-dir cache/hub/dataset-v0.5.0
python scripts/live_server.py prebuild $(grep -oE '"b6-[0-9]{8}-0400"' scripts/live_runner.toml | tr -d '"') \
    --live cache/live/prebuilt --inputs cache/inputs --assets cache/hub/dataset-v0.5.0/viewer
live/onair/upload-data.sh cache/live/prebuilt cache/hub/dataset-v0.5.0/viewer
```

**3. The box,** about 20 minutes. `launch-gpu.sh` tries each type and subnet until one comes up (GPU capacity moves
between zones) and schedules a power-off 48 h after boot: prep, the day, and slack. Then: Docker's address pools on
the AMI's own Docker, the runner (a local registry, the plugin at this checkout's branch with the pinned framework,
the env and desk images), the streamer, the live server with the data from step 2, the stream (recording only,
nothing pushed) and the watchdog.

```bash
ID=$(live/aws/launch-gpu.sh airops-live-onair)
live/onair/prep.sh $ID
live/runner/setup.sh $ID              # ends with the schedule's dry run
live/stream/deploy.sh $ID
live/onair/deploy-server.sh $ID
live/stream/start.sh $ID --target none
live/onair/fresh.sh $ID
live/onair/health.sh $ID
```

The stream's page defaults to the live server on the same box (`--url` and `--safe-url` change it); until the runner
starts, it waits for a day.

## Test at $0: scripted desks

The env's scripted desks play real days through the real `agent-env run`, with no model. A day takes 3 to 5 minutes
(the env deploys in about 40 s); with 8-minute slots each is followed by its score card and an intermission, as on
the stream. Its pilot is its own folder.

```bash
live/runner/start.sh $ID --policy script --name live-script-box --set slot_minutes=8 --days 3
live/onair/health.sh $ID              # one JSON line; repeat
live/runner/status.sh $ID
live/onair/grab.sh $ID frames 20:05:00 20:09:30=room   # frames of the recording, UTC
live/onair/clip.sh $ID clip.mp4 20:05:00=20 20:09:30=20
```

`health.sh` prints `picture_vs_page` (the stream's frame against Chrome's screenshot, 96x54 grey: about 1 while the
encoder sees the page, tens on a stale picture), the runner's status and spend, the server's `status`, `watch`, `lag`,
`exact` and `mismatches` (`exact` true and 0 mismatches: the server's replay agrees with the env), and the last capture
and push metrics (`ff_fps` 30, `ff_drop` flat after the first minute, `chrome_restarts`).

## Rehearse: one model day

One real day in its own pilot, about $22 and an hour with GPT-6 Astra, to see a model day on the stream end to end:

```bash
live/runner/start.sh $ID --name occ3-live-rehearsal --days 1
```

Its spend isn't counted by the stream's pilot: lower the stream's `budget_usd` by it. To watch it privately, push to
a private RTMP server with `live/stream/start.sh $ID --target rtmp://HOST/APP/KEY`, or cut a clip from the recording.

## Arm for a start time

The runner holds an intermission (the page's "next storm" card) until `start_utc`, then plays the days of
`scripts/live_runner.toml` in order, in slots: a day starts only if the spend so far plus a worst day
(`worst_day_usd`, $42) stays within `budget_usd`, and only if its slot ends inside the `window_hours` (24) from
`start_utc`; a day that ends early is followed by an intermission until its slot ends. At $22.68 a typical Astra day,
18 days fit $430; 84-minute slots spread 17 over the 24 hours. The config is read again before every day, and `--set`
wins over it. Plan with the same arguments first; `plan.sh` plays nothing:

```bash
live/runner/plan.sh $ID --set start_utc=2026-10-23T16:00:00Z --set budget_usd=405 --set slot_minutes=84
live/runner/start.sh $ID --set start_utc=2026-10-23T16:00:00Z --set budget_usd=405 --set slot_minutes=84
```

For another model, set `LIVE_MODEL` and, with `--set` or in the config, its `cap_joint_usd` (each desk's cost cap;
Astra's is $23.50), `worst_day_usd` and `typical_day_usd`.

## Go live on X

1. In X Media Studio (studio.x.com), **Producer**, **Sources**, **Create source**: type RTMP, the nearest region.
   Copy the **RTMP URL** and the **Stream key** into SSM:
   ```bash
   live/aws/put-secret.sh x-server
   live/aws/put-secret.sh x-key
   ```
2. About 30 minutes before the start, push the relay to X (this restarts only the push; capture and recording go
   on): `live/stream/start.sh $ID --target ssm`.
3. In Producer, **Broadcasts**, **Create broadcast**: pick the source, set the title, wait for the preview (the
   page, with sound, about 10 s after step 2), then **Go Live**.

The key is read from SSM when the push starts and never printed (the logs show the parameter names). An RTMP drop to
X restarts only the push (1 s, doubling to 30 s); the encoder and the recording keep going. A new key in SSM takes
effect at the next `start.sh --target ssm`.

## Monitor

```bash
live/onair/health.sh $ID                                   # every half hour or so
live/runner/status.sh $ID                                  # the runner: day, spend, next, the last days, its log
live/stream/status.sh $ID                                  # units, relay, metrics, restarts and warnings, GPU, disk
live/aws/ssm.sh $ID 60 live/onair/box/logs.sh 10           # the server's and runner's logs, the fresh watchdog's
live/aws/ssm.sh $ID 300 live/onair/box/metrics.sh 16:00:00 18:00:00   # encoder runs and drops, fps, CPU, GPU, memory
live/aws/ssm.sh $ID 600 live/onair/box/loudness.sh "2026-10-23 16:00" "2026-10-23 16:30"   # LUFS, true peak, silences
```

The fresh watchdog logs a line a minute to `/var/log/airops-stream/fresh.jsonl` (`diff` about 1 while fresh, an
`action` when it restarted the encoder). To change code during the stream, commit on the branch and
`live/onair/sync.sh $ID`: `web/` changes are served at once (the page reloads between days), a restarted
`airops-live-server` resumes from its checkpoint, and the runner takes its changes between days.

## Stop, save, tear down

The runner stops by itself at the end of the window or the budget or the days (`current.json` `final`); the page then
holds its last card.

```bash
live/runner/stop.sh $ID               # finish the day in progress, then exit; `cancel` takes it back
live/runner/stop.sh $ID now           # stop the day now (agent-env tears it down; the attempt is recorded)
```

End the broadcast in Producer, then `live/stream/stop.sh $ID --push-only` (recording goes on) or
`live/stream/stop.sh $ID` (everything).

```bash
live/stream/save-recordings.sh $ID 2026-10-23_1   # finished segments matching a pattern, to s3://BUCKET/PREFIX/rec/
live/runner/fetch.sh $ID                          # current.json, the logs and the pilot folder, into live/runner/fetched/
live/aws/teardown.sh $ID airops-live-onair
```

Recordings are `/var/lib/airops-stream/rec/airops/<UTC start>.mp4`, one-hour fMP4 segments of about 2.7 GB (24 h is
about 65 GB); below 8 GB free the oldest is deleted, and every encoder restart starts a new one. Save a few hours at a
time, so that each SSM command stays small. The pilot folder is a board's: `agent-env airops-occ3 report <pilot>`
replays and audits it (about 5 minutes a day; not on the box while it streams). Afterwards, reset the stream key in
Media Studio, revoke the model key, delete the SSM parameters and empty `s3://BUCKET/PREFIX/`.

## What restarts what

| Fault | Detected by | Action, and the recovery measured |
|---|---|---|
| Chrome exits or crashes | the process | restart (2 s, doubling to 60 s), the slate meanwhile: back in about 20 s |
| the page hangs | no frame for 20 s (a frame counter injected through DevTools) | reload; if still none, restart Chrome: 75 s |
| the viewer shows its error | `.error` in the page | reload, at most every 30 s |
| a lost WebGL context | `webglcontextlost` | reload after 5 s, then the page's load: about 20 s |
| Chrome's memory | PSS every 30 s | over 6 GB: restart between days (the safe URL says done or intermission); over 9 GB: now |
| the encoder exits or stalls | the process, or no new frame for 20 s | restart (1 s, doubling to 30 s): 12 s |
| the X server restarts | the pid in `/tmp/.X0-lock` | restart the encoder |
| a stale picture at full rate | fresh.py, 3 minutes past a difference of 12 | kill the encoder; the supervisor starts it again |
| PulseAudio exits | the process | restart it and the encoder: 17 s |
| the push drops or stalls | the process, or no progress for 30 s | reconnect (1 s, doubling to 30 s): 11 s |
| the relay, the display, a supervisor | systemd | `Restart=always`: the relay in 14 s |
| the live server | systemd | `Restart=always`: answering in 3 s, back on the day from its checkpoint in about 6 s |
| the runner | systemd | restarted after 30 s; it picks the running day up (`KillMode=process` keeps `agent-env run` alive) |
| a full disk | under 8 GB free | delete the oldest recording segment |

## Measured

- **Picture:** 1920x1080 at 30 fps, NVENC High CBR 6000k, with 0 dropped frames after warm-up (each encoder start drops
  about 120 frames in its first minute while Chrome loads): 48 minutes on a g6.xlarge with 0 drops, and on the
  g6.4xlarge 29.97 fps at 1.00x speed with no new drops under 16 busy loops on every core for 2 minutes.
- **Restarts:** the live server checkpoints its replay at each watch, so a restarted server is back on the day in about
  6 s; without the checkpoint it replays the day from 04:00, single-threaded (about 80 s of CPU per Astra watch).
- **A GPT-6 Astra day** costs about $22 and takes about an hour of wall time here: the rehearsal day, 2024-06-26 at
  JFK, cost $21.82 in 58 minutes (the board's Astra days: $22.68 on average, $41.46 at most).
- **Sound:** about -20 LUFS integrated, with the limiter at -4.5 dBFS (below).
- **Memory:** Chrome's PSS grew about 300 MB in 48 minutes; the page's heap after GC stayed flat across days.

## Traps

- **x11grab after an X restart encodes a stale screen.** ffmpeg keeps reading the old X server's shared memory at full
  frame rate, so a frame-count watchdog stays green while the stream shows a frozen loop. Fix: the capture supervisor
  restarts the encoder when the X server's pid changes, and the fresh watchdog compares the stream with Chrome's
  screenshot each minute and restarts the encoder after three stale minutes. Check `picture_vs_page` in `health.sh`.
- **The framework is pinned on the box:** agentenv-framework 0.9.1339 and agentenv-framework-protocol 0.1.354
  (`LIVE_PINS`). 0.9.1343 breaks deploys from the repo directory and 0.9.1344 fixes it: check a newer pin with the $0
  test before you use it.
- **`--autoplay-policy=no-user-gesture-required`.** Without it Chrome won't start the page's AudioContext without a
  click, and the stream carries only the quiet airport bed under the page. `streamer.py` passes it; keep it if you
  change Chrome's flags.
- **AAC adds true peak.** The page's limiter sits at -4.5 dBFS because at -2.5 dBFS the stream measured -0.6 dBTP
  after the AAC encode. Measure with `loudness.sh` before you raise it.
- **Keys belong in SSM.** The X stream key and the LiteLLM key go in with `put-secret.sh` and are read on the box when
  they're needed (`with-model.sh` at every runner start, the push at every start). Never put one on a command line, in
  `live.env` or in a chat.
- **The box powers itself off** MAX_HOURS after boot (48 by default for `launch-gpu.sh`); `runner/status.sh` and
  `stream/status.sh` print when, and `shutdown -c` on the box cancels it.
- **The Deep Learning AMI ships Docker,** so the runner's setup leaves its `daemon.json` alone: run `onair/prep.sh`
  first, or the env's Docker networks run out of addresses.
- **Leave the X output at the driver's 1024x768.** Set to 1920x1080, Chrome syncs to 60 Hz and the 3D falls to 34 to
  52 fps; the virtual screen is 1920x1080 and the slate fills the whole root window.
- **A rehearsal in its own pilot isn't counted** in the stream's spend: lower `budget_usd` by it.
