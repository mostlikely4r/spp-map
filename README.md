# SPP Livemap

A browser map and test explorer for SPP / CMaNGOS + PlayerBots servers (vanilla, TBC, WotLK).
It replaces the old QGIS project (kept in [`legacy/`](legacy/README_QGIS.md) until the new map covers
everything it did).

What works today:

- **Live world map** (`/w`). Every online bot and player on the painted continent, zone and dungeon
  floor maps, refreshed every 3 seconds. Colour by faction, class, level, state or health; filter to
  players and their party bots, fights, deaths, groups, bots running a test or inactive bots. Tap a
  bot for its card: level, race/class, zone, health, money, group leader, its trail over the last
  15 minutes, the last hour of events and its recent test runs. Follow a bot, search by name, share
  the exact view as a link. Layers: zone maps and names, recent deaths / quests / level-ups, NPCs
  (coloured by role: flight master, inn, vendor, trainer, quest giver, elite...), game objects and
  the bots' travel network, **travel intent** (dashed line from each bot to where it is heading, coloured
  by purpose: quest, trainer, bank, gathering..., from `travel_map.csv`) and the **teleport cache**
  (`telecache.csv`, coloured by level, binned when zoomed out). Dungeons with painted floor maps (WotLK) get floor buttons; every other
  dungeon, raid and battleground shows its indoor minimap art (see below).

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
- **Indoor minimap art for every instance.** Dungeons without painted floor maps (all classic and TBC
  instances, on every core) are drawn from the client's own minimap tiles: the dungeon building
  (WMO) tiles placed through the map's `MODF` entry and each building section's bounding box, with
  the outdoor terrain minimap underneath (blank filler tiles dropped, clipped to where the map has
  spawns). `python -m livemap verify` checks every instance map: the share of world-database
  spawns that land on art.
- **Phone first.** Mobile layout, short links (`/r/<run>`, `/t/<core>/<test>`), and an optional
  Cloudflare quick tunnel with token login, so you can use it away from the PC.
- **Snapshots.** `livemap snapshot` renders a run page to a PNG (also at `/snapshot/run/<id>.png`).

Planned next: the all-continents atlas view, replay
scrubber, performance graphs, favourite bots, trend movies, and the remaining QGIS layers. See the design plan in
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
python -m livemap basemaps         # prebuild every map image (otherwise built on first use)
python -m livemap verify           # spawns-on-art check per instance map (--core, --map, --threshold)
```

Open the tunnel link once on your phone; it stores a cookie and drops the token from the address.
Anyone without the token gets 401. A quick tunnel gets a new address every start; the token stays
the same (`data/access_token.txt`; delete it to issue a new one).

## What the data needs

| Feature | Needs |
| --- | --- |
| Test explorer | `bot_test_results*.log` in `Settings/<core>/logs` (the test framework writes these) |
| Quest markers, travel nodes | world database (read from `mangosd.conf`) |
| Live world map | `bot_positions.csv` (below); race/class from the characters database (read from `mangosd.conf`) |
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
  __main__.py   command line (serve, ingest, snapshot, basemaps, verify)
  config.py     livemap.toml loading
  results.py    [BOTTEST] line + test name parsing
  catalog.py    SQLite: runs, flips, groups, triage, favourites
  telemetry.py  time-sliced reads of the big CSV logs, display -> world coordinates
  basemap.py    painted zone / dungeon floor maps from the client
  wmomap.py     indoor (WMO) + terrain minimap art for instance maps
  clientdata.py MPQ + DBC + BLP reading
  worlddb.py    quest givers / objectives / turn-ins, travel nodes, NPC / object layers
  live.py       tails bot_positions.csv for the live map, bot cards, recent events
  app.py        run reconstruction (map choice, track, markers)
  server.py     HTTP API, token login, static files
  snapshot.py   PNG renderer
  static/       web client (Leaflet, no build step; world.js = live map)
legacy/         the old QGIS project
```
