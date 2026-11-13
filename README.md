# SPP Livemap

A browser map and test explorer for SPP / CMaNGOS + PlayerBots servers (vanilla, TBC, WotLK).
It replaces the old QGIS project (kept in [`legacy/`](legacy/README_QGIS.md) until the new map covers
everything it did).

What works today (phase 1):

- **Test explorer.** Every `[BOTTEST]` result from every boot and every core goes into one small
  database: latest verdict, run history, pass rate, **"flipped to PASS"** and **regression** feeds,
  failures grouped by message, and a triage label per test (bug / fluke / gap / fixed + note).
- **Run page.** Verdict, message, a timeline of the host bot's events and states, and a map with the
  bot's track plus the quest's giver, objective and turn-in spawns (from the world database) and the
  bots' travel network (nodes + stored walking paths).
- **Painted maps with exact placement.** Zone maps and (on WotLK) dungeon floor maps are built from
  your own game client. Placement comes from the client's own tables (`WorldMapArea.dbc`,
  `WorldMapOverlay.dbc`, `DungeonMap.dbc`), so server coordinates land exactly on the art. No hand
  georeferencing.
- **Phone first.** Mobile layout, short links (`/r/<run>`, `/t/<core>/<test>`), and an optional
  Cloudflare quick tunnel with token login, so you can use it away from the PC.
- **Snapshots.** `livemap snapshot` renders a run page to a PNG (also at `/snapshot/run/<id>.png`).

Planned next: exact positions with map id and floor from the bot module, live mode, replay scrubber,
performance graphs, favourite bots, trend movies, and the remaining QGIS layers. See the design plan in
the SPP_Server repo (`.agents/docs/livemap-design.md`).

## Install

Python 3.11+ and three packages:

```
pip install -r requirements.txt
```

Optional: `cloudflared` for remote access (`winget install Cloudflare.cloudflared`).

## Configure

Copy `livemap.example.toml` to `livemap.toml` and set your paths. With `spp_root` pointing at your
`SPP_Server` folder, the logs, DBC files and world database login are found automatically per core;
you only add the path to each game client's `Data` folder (needed for painted maps).

## Run

```
python -m livemap serve            # http://127.0.0.1:8420, no login needed on this PC
python -m livemap serve --tunnel   # also prints a https://....trycloudflare.com/?token=... link
python -m livemap ingest           # index results once and print counts
python -m livemap snapshot --run 1804
python -m livemap snapshot --test "an improper burial"
python -m livemap basemaps         # prebuild every painted map (otherwise built on first use)
```

Open the tunnel link once on your phone; it stores a cookie and drops the token from the address.
Anyone without the token gets 401. A quick tunnel gets a new address every start; the token stays
the same (`data/access_token.txt`; delete it to issue a new one).

## What the data needs

| Feature | Needs |
| --- | --- |
| Test explorer | `bot_test_results*.log` in `Settings/<core>/logs` (the test framework writes these) |
| Quest markers, travel nodes | world database (read from `mangosd.conf`) |
| Exact bot track on the run page | `bot_positions.csv` (every bot, every 5 s, map + world position + floor) and `bot_test_runs.csv` (exact test start/end, last actions, travel target) in `AiPlayerbot.AllowedLogFiles`. Needs a PlayerBots build with the livemap data contract (manifest `contract: 1`) |
| Older / fallback tracks | `bot_heartbeat.csv`, `bot_states.csv`, `bot_events.csv` (old builds: display coordinates only, map guessed) |
| History across restarts | `AiPlayerbot.LogArchiveOnBoot = 1` (old logs are renamed `<name>_<YYYYmmdd_HHMMSS>.csv` instead of wiped; `LogArchiveKeepBoots` limits how many). Toggle it on the Settings page |
| Instance positions (old builds) | `map_offsets.csv`, `livemap_manifest.json` in `AiPlayerbot.AllowedLogFiles` |
| Painted maps | the game client's `Data` folder |

The map art comes from your own client and is built locally; this repository does not ship
Blizzard art.

## Layout

```
livemap/
  __main__.py   command line (serve, ingest, snapshot, basemaps)
  config.py     livemap.toml loading
  results.py    [BOTTEST] line + test name parsing
  catalog.py    SQLite: runs, flips, groups, triage, favourites
  telemetry.py  time-sliced reads of the big CSV logs, display -> world coordinates
  basemap.py    painted zone / dungeon floor maps from the client
  clientdata.py MPQ + DBC + BLP reading
  worlddb.py    quest givers / objectives / turn-ins, travel nodes
  app.py        run reconstruction (map choice, track, markers)
  server.py     HTTP API, token login, static files
  snapshot.py   PNG renderer
  static/       web client (Leaflet, no build step)
legacy/         the old QGIS project
```
