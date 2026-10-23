
# AirOpsEnv live: the contracts

What the runner, the live server, the viewer's live mode, the streamer and the sound promise each other. The runbook
is [README.md](README.md).

## current.json (runner → server)
Written atomically (tmp + rename) by `scripts/live_runner.py` on every change and at least every 10 s while a day runs.
Default path `results/runs/<pilot>/current.json` in the plugin checkout; on the on-air box `/home/ubuntu/live/current.json`
(`--current`).
```
{
 "instance_id": str|null,   the running day's task instance (agent-env's id, "<bundle task id>-xxxxxxxx"), set within seconds
                            of the launch, before the env is up; the finished day's while status is done/intermission
 "run_id": "b6-20240803-0400-joint-a1",   <task>-joint-a<attempt>: the key of days/<run_id>.json and transcripts/<run_id>-<desk>.json
 "date": "2024-08-03", "hub": "JFK", "task_id": "b6-20240803-0400", "model": "openai/gpt-6-astra", "mode": "joint",
 "started_utc": "2026-10-23T16:00:04Z",
 "status": "starting" (launched, env not up yet) | "playing" (env deployed: env.base answers data/get)
         | "done" (day ended; result + day_file set; held card_seconds) | "intermission" (waiting for next.starts_utc),
 "next": {"date", "hub", "task_id", "starts_utc"} | null (null: no further day; see final),
 "history": [{"date", "hub", "task_id", "run_id", "instance_id", "outcome": "scored"|"unscored"|"skipped",
              "reward", "reward_L", "cost_usd", "wall_seconds", "desks", "attempts"}],   finished days in play order
 "attempt": 1|2, "day": n (1-based index on the stream),
 "env": {"base": "http://localhost:PORT", "mcp_url": ".../mcp", "gateway_url": ...} | null   data/get at env.base
                            (the verifier's own rule: mcp_url without /mcp); null until deployed. The env is torn
                            down seconds after the grade, so read the final record from day_file.
 "day_file": absolute path of days/<run_id>.json (the env's final data/get, graded) once status is done, else null
 "result": history's entry for this day once done, else null
 "spent_usd": finished attempts' model spend, "live_usd": the running day's estimate (sum of each desk's last
              "request N: … $X spent" log line), "desks": {"crew": $, "customer": $, "ops": $}, "budget_usd": 430
 "pilot": "occ3-live-astra", "policy": "llm"|"script", "seq": n (+1 per write), "updated_utc",
 "final": false|true, "reason": why the runner stopped (budget, window, dates, stopped) once final
}
```
Before the first day: status "intermission", instance_id/date null, next set. The live server can attach as soon
as `env` is non-null (status "playing").

## Live server → viewer (`scripts/live_server.py`)

### Endpoints (one origin, default port 8765 on the box that plays the day, which has current.json and the env; CORS `*`)
| Path | What | Caching |
|---|---|---|
| `GET /live/state.json` | tiny: `{"seq", "live": {...the live object...}}` | `no-store` |
| `GET /live/episode.json` | the live Episode JSON (below). gzip when the request sends `Accept-Encoding: gzip`; `ETag: "<seq>"`, answers `304` to `If-None-Match` | `no-store` |
| `GET /live/<stem>.stage-<n>.json.gz` | the traffic sidecar the episode names in `stage.traffic` (a new `<n>` per rebuild; the last 8 stay servable) | immutable |
| `GET /live/<run_id>.json` (+ its `<stem>.stage.json.gz`) | each finished day's final episode (`live.status` "done"), kept beside the live one so its `../stage`, `../radar` URLs resolve, for the between-days replay | immutable |
| `GET /live/index.json` | the finished days as a viewer index (`export.entry` rows + `"live": true`, `url` relative to it) | `no-store` |
| `GET /stage/…`, `/radar/…`, `/twin/…` | the static assets: the episode's `../stage/JFK/stage.json.gz`, `../radar/<task>/<frame>.png` resolve here from `/live/` | long |
| `GET /web/…` | the viewer folder (`web/` of the branch the server runs from, or `--web DIR`) | short |

Open the viewer at `http://HOST:8765/web/index.html?live=http://HOST:8765` (the viewer polls `<live>/live/state.json`
and `<live>/live/episode.json`; `?live=..` relative to the page works too).

### Polling and sequence numbers
- Poll `/live/state.json` every 2 s. Fetch `/live/episode.json` only when `state.seq` differs from the episode you hold
  (its `live.seq`). `seq` is one counter for the whole server life: it never goes back, across days and restarts of a
  day (a server restart starts it at the wall-clock second, still higher).
- The day changed when `live.run_id` or `live.instance_id` changes (a retry is a new `run_id`, `...-a2`): drop the
  old timeline.
- The episode is rebuilt (1) cheaply on every poll of the env that brought new calls (the room: `steps`, `occ.turns`,
  `occ.requests`, `occ.decisions`, `occ.notes`, `occ.pages`, `live.desks`, `live.feed`), and (2) with the projection
  (`executed`, `cost`, `passengers`, `occ.crew`, `occ.units`/`reserves`/`hotels`/`deadheads`, the open watch's
  `projected`, a new sidecar `stage.traffic`) at each watch that opens and after each committed decision (at most one
  rebuild per 15 s). The server polls the env's data/get every 3 s.

### The live object (`episode.live`, also all of `/live/state.json`)
```
"live": {
  "status": "starting"   current.json starting: the day's world, no env yet; the episode is the 04:00 projection
          | "playing"    the env answers data/get and the day runs
          | "ending"     the runner says done (or moved on) and the server is still reading the day: behind, or
                       the env gone before its last record was read (it reads the runner's day_file)
          | "done"       the grade is in: reward, cost and the record are final (exactly the recorded episode)
          | "intermission" current.json intermission: the finished day's episode stays, `next` says what comes
          | "stale",     data/get has failed for 30 s; the last good episode stays (the field `error` says why)
  "seq": 1729700412,
  "run_id": "b6-20260715-0400-joint-a1", "task_id": "b6-20260715-0400", "date": "2026-07-15", "hub": "BOS",
  "instance_id": "@local/~/…/b6-20260715-0400-joint-xxxxxxxx", "model": "openai/gpt-6-astra",
  "model_label": "GPT-6 Astra", "mode": "joint", "pilot": "occ3-live-astra",
  "watch": 7, "watches": 37,          open watch index (0-based) and the day's watch slots
  "t": 660,                           the open watch's minute (local minutes since midnight, as task.start)
  "decided_until": 660,               everything strictly before this minute is final; play up to it, never past it.
                                      = t while playing; the day's last movement (max executed dep/arr) once done
  "turn": {"n": 31, "desk": "customer", "kind": "customer", "calls": 4} | null,   the turn in progress
  "desks": [{"role": "crew"|"customer"|"ops"|"one", "state": "turn"|"waiting"|"off", "off": null|"done"|"stand_down"|…,
             "last": {"seq": 412, "tool": "crew_confirm", "text": "assign P-A32X-0391 to B6 1381", "t": 660} | null,
             "at": "2026-10-23T16:41:03Z"}],      at: wall clock the server first saw `last`
  "feed": [{"seq": 412, "at": "2026-10-23T16:41:03Z", "watch": 7, "t": 660, "desk": "crew",
            "kind": "request"|"reply"|"decision"|"note"|"notice"|"off", "text": "…one line, flight ids as numbers…",
            "request": "R14"|null, "flight": 154|null}],   the last 60 room events, oldest first
  "projected": {"total": 550467.2, "delay": …, "cancel": …, "notice": …, "aircraft": …, "lodging": …, "fare": …,
                "deadhead": …, "hotel": …, "end": …},     the projection's cost now (moves with each decision)
  "next_watch_at": "2026-10-23T16:42:30Z" | null,   when the next watch should open: the open watch's wall start plus the
                                                    mean wall seconds of the watches so far (null before the second)
  "watch_wall_s": 92.4,               mean wall seconds per closed watch
  "started_utc": "…", "updated_utc": "…", "wall_seconds": 655.0,
  "spent_usd": 3.12,                  current.json live_usd: the running day's model spend so far (null if none)
  "total_usd": 48.9, "budget_usd": 430,   spent_usd of the finished days plus the running day's; the runner's budget
  "result": null | {"reward", "reward_L", "cost", "best", "bound", "B0", "end_reason", "audit_ok", "cost_usd",
                     "wall_seconds"},   once done (cost_usd and wall_seconds from current.json's result)
  "exact": true, "mismatches": 0,     the server's replay against the env's result digests (false: a bug, the
                                      episode still follows the env's calls)
  "lag": 0,                           watches the server's replay is behind the env (it catches up two at a time,
                                      publishing each; a server started mid-day shows the day from 04:00 on)
  "next": current.json next, "history": current.json history,
  "error": null | "…"
}
```

### The episode while the day runs (same schema as recorded v3 episodes, `web/episode.schema.json`, plus `live`)
- Present and final from the start: `version`, `task`, `model`, `model_label`, `run` (`"<pilot>-<mode>"`, e.g.
  `occ3-live-astra-joint`), `schedule`, `reference` (`anchor`, `best`, `bound`, `B0`, `gap_pct`; `flat` is the
  projection's), `radar`, `twin`, `sources`, `caps: {}`, `nominal: {}`, `ferries: []`, `frozen: []`.
- `reward: null`, `end_reason: null`, `audit: null`, `occ.grade: null` until done. (The schema has `reward` a number
  and `occ.grade` an object: the viewer's live mode must accept null for both; the tests validate the live episode
  against the schema with those two made nullable, and nothing else changed.)
- `cost`: the projection's cost now, with the recorded keys (`total`, `pax_delay`, `cancel`, `delay`, `notice`,
  `aircraft`, `lodging`, `fare`, `deadhead`, `hotel`, `end`; as recorded, `cancel` is the cancel part alone and
  `notice` the late-notice part). Not the grade: the grade is on the truth. Same numbers as `live.projected`.
- `executed`: every JetBlue flight on the projection now (the plan so far on the truth up to `decided_until`, on the
  day as known after it): rows before `decided_until` are what happened; later ones are the forecast and move.
- `watches`: one per watch opened so far, 0..`live.watch`. Closed ones exactly as recorded (`t`, `news`, `changes`,
  `est_cost`, `projected` as the watch left it). The open one: `t`, `news`, `changes` so far, `projected` = now
  (moves), no `est_cost`.
- `steps`: every call so far (as recorded: `t`, `tool`, `args`, `desk`, `watch`, `turn`).
- `news`: FAA advisories and the hub's METARs through `live.t` (as recorded, cut at the open watch).
- `occ`: as recorded (`mode`, `label`, `desks`, `turns`, `requests` with `shown`/`answered`, `decisions`, `notes`,
  `pages`, `absent`, `refs`) so far, including the open watch's turns (their `shown` minutes are after `t`: held back
  by `decided_until`, and a turn's window may still narrow until its watch closes). `crew`, `units`, `reserves`,
  `hotels`, `deadheads` are the projection's.
- `passengers`: the passengers on the projection now (`flights`, `connections`, `events`; a flight's `hold` against
  the projection without the customer desk's holds).
- `stage`: `{"hub", "url": "../stage/<hub>/stage.json.gz", "traffic": "<stem>.stage-<n>.json.gz"}`: the static part
  (span start, the other airlines, runways from the METARs so far) stays; JetBlue's aircraft, crews and passengers
  follow the projection. `<stem>` = `<run>.<model slug>.<task_id>`.
- `record`: `{"folder": pilot, "run": "<task>-<mode>", "attempt", "cost_usd", "wall_seconds", "desks"}` from
  current.json (`result`; null until the runner has them).

### Once done (`live.status` "done", then "intermission")
The episode is the recorded one, built by the same code as `occ_viewer.py` from the day the env graded (our replay,
checked against the env's digest), plus `live`: `reward`, `occ.grade` (with `reward_L`), `cost` (the grade's),
`executed` (the truth), `end_reason`, `audit`, `passengers`, `stage.traffic` = `<stem>.stage.json.gz`, the final
`decided_until`, and `live.result`. It is also kept at `/live/<run_id>.json` and listed in `/live/index.json`.

### Where data/get comes from (for anyone attaching by hand)
The task instance store has the running day's env once `deploy` is done: `with namespace_routing():
task_instances("@local/~/<bundle path>/<task>-joint", limit=1)[0].context["deployed_envs"]` → the entry with
`env_id == "airops-occ3"`, its `mcp_url` minus `/mcp` is the base; `POST <base>/agentenv`
`{"jsonrpc":"2.0","id":1,"method":"data/get","params":{}}` → `result.parts[0].data` (the verifier's call; the gateway
forwards it to the env, whose handler runs on its event loop, so a snapshot is consistent). `live_server.py attach
BUNDLE TASK --current FILE` does this for a plain `agent-env run`. The server polls every 3 s (60 s timeout); a call
takes ~1-2 s on a loaded laptop and never changes the day.

## Stream → viewer and server (`live/stream/`)
- **The page:** Google Chrome 156, kiosk, 1920x1080 at device pixel ratio 1, on an NVIDIA L4 (WebGL renderer
  `ANGLE (NVIDIA Corporation, NVIDIA L4/PCIe/SSE2, OpenGL 4.5.0)`), one URL for the whole stream (`AIROPS_URL`), e.g.
  `http://127.0.0.1:8765/web/index.html?live=http://127.0.0.1:8765&stream`. Flags that matter to the page:
  `--autoplay-policy=no-user-gesture-required` (an AudioContext starts without a click),
  `--disable-background-timer-throttling --disable-renderer-backgrounding`. The page's own reloads are fine.
- **Sound:** everything the page plays goes to PulseAudio's default sink and is encoded as is: AAC-LC 128k, 44.1 kHz,
  stereo, over a quiet airport bed that only shows while the page is silent. No normalization or limiter after the page:
  level the mix in the page.
- **Watchdog (what the page must do):** keep calling requestAnimationFrame (a page with no rAF callback for 20 s is
  reloaded, and Chrome restarted if a reload doesn't help); answer DevTools within 45 s; a `.error` element makes it
  reload (at most every 30 s); a lost WebGL context for 10 s makes it reload. The supervisor injects a rAF counter and a
  `webglcontextlost` listener (`window.__airopsFrames`, `window.__airopsLost`) through DevTools; nothing else.
- **Frame rate:** the page renders at the display's 60 Hz unless `AIROPS_PAGE_FPS=30` (a rAF wrapper injected through
  DevTools caps every rAF user at 30 fps; used on a 4-vCPU box). Capture is 30 fps.
- **Safe restarts:** with `AIROPS_SAFE_URL=http://127.0.0.1:8765/live/state.json`, Chrome is restarted for memory only
  while `live.status` is `done` or `intermission` (PSS over 6 GB), or at once over 9 GB.
- **Network:** Chrome opens the live server on `127.0.0.1:8765`, and the viewer's map tiles from openfreemap.org and
  the USGS basemap over the internet.

## Sound (`web/audio.js`)
- **Loading:** `app.js` calls `mountSound(window.airops, timelineEl, {on, stream})` once `window.airops` is set. It isn't
  called with `record=1` or `?sound=0`. Afterwards `window.airops.sound` is the `AirportSound`.
- **On and off:** `&sound=1` or `&stream` turns the sound on; otherwise a speaker button at the end of the timeline does.
  With `&stream` there's no button.
- **Per frame (optional):** `window.airops.sound.update(state, camera)`.
  - `state` needs `{main: "stage" | "room" | "map", view}`, as in `window.airops.state()`.
  - `camera` is the THREE camera on screen.
  - Without these calls, the sound runs its own rAF loop, reading `airops.state()` at 4 Hz.
  - What it reads from the viewer: `stage` (its `fleet.live`, `T`, `rate`, `rig`, `env.weatherAt`, `lightning`, `gate()`) and `room.state()` (`turn`, `cards`).
- **Quiet bed:** `update({main: "map", view: "network"})` gives the quiet map bed, for example under a card.
  `sound.setVolume(0..1)` trims the output.
- **Levels:** about -20 LUFS integrated; the limiter sits at -4.5 dBFS, because the stream's AAC encode adds about
  2 dB of true peak (-2.5 dBFS measured -0.6 dBTP on the stream); meters are in `sound.state().meter`.
  - The leveller and limiter need an AudioWorklet, which needs a secure origin: https, localhost, or Chromium's
    `--unsafely-treat-insecure-origin-as-secure=http://HOST:8765` (on one box the page is on 127.0.0.1, already secure).
  - Elsewhere it falls back to the built-in compressors and a clipper at -2 dBFS.
  - The AudioContext runs at 48 kHz with `latencyHint: "playback"` and starts without a click only with
    `--autoplay-policy=no-user-gesture-required`.
