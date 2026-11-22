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
  (`telecache.csv`, coloured by level, binned when zoomed out), **zone levels** (subzone outlines
  from `zones.csv`, coloured and labelled by level). NPCs can be coloured by role, level or reaction
  (friendly to Alliance / Horde / both / hostile, from `FactionTemplate.dbc`); travel paths by type or
  by danger for Alliance or Horde (toughest creature level the bots recorded along the path). Dungeons with painted floor maps (WotLK) get floor buttons; every other
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
- **World + dungeons** (`/w/<core>`, the default view). Continents from the client's terrain minimap
  with every dungeon, raid, battleground and arena drawn on top of its continent: either at its real
  entrance (door from `AreaTrigger.dbc`, landing from `areatrigger_teleport`) or offshore next to it on
  open sea, with door-to-landing lines. Battlegrounds sit between their faction entrances, arenas at
  their real places. Placement does not depend on the boot, so a restart never moves anything. Where
  several instances share one entrance they are fanned out with leader lines; **honest mode** stacks
  them at their doors instead and puts a chip row on the cluster to pick which art is on top.
  **Plain art** drops the dark backdrop and the coloured outline. Zoomed out, each instance collapses
  to a pin with the number of bots inside. Every layer works here too, merged over the visible maps:
  NPCs, game objects, travel nodes and their walking paths, travel intent, teleport cache, zone
  levels, deaths / quests / level-ups. Instances the game data cannot place (no entrance trigger) are
  anchored by hand (`livemap/overlay.py`, `ANCHORS`): Ebon Hold over the Scarlet Enclave, the old
  Alliance PVP Barracks at Stormwind, both without door markers.

  **Continent pages use the same view.** Eastern Kingdoms, Kalimdor, Outland and Northrend each open as
  this scene limited to one continent: the painted continent and zone maps (or the ground), with that
  continent's dungeons on top, the same layers and options, plus the rendered ground when zoomed in.
  A **dungeons** layer chip turns the overlay off (here and in the World view). The map selector lists
  the World view, then every map with bots (most bots first), then all other maps by map id.

  `python -m livemap verify --scene world` reports, per instance, where the entrance came from
  (continent trigger / chained off another instance / hand anchor / override / tray), whether the
  inside landing point falls on the instance's own art, its cluster, and whether any placement in
  offshore mode still touches land. `data/overlay_anchors.toml` overrides anything the game data gets
  wrong: `[<core>.<mapId>]` with optional `door = [map, x, y]`, `landing = [x, y]`, `fan`.

- **Ground instead of the painted map.** In a single map (`/w/<core>/<mapId>`) and on a run page, a
  chip switches the background between the painted world/dungeon map and the game's own terrain and
  WMO minimap art. Outdoor terrain is 256 px per 533 yd (the same tile size the client uses), dungeon
  art is 256 px per 128 yd, so instances read sharper than the ground they sit in.

- **Rendered ground, zoomed in.** Close up, the background is drawn from the client's terrain files
  instead of the minimap art: one ADT per request, splatted ground textures, hillshade relief from the
  height field and real liquid layers, at half a yard per pixel (four times sharper than the minimap).
  It switches on by zoom (minimap below ~2 yd/px, rendered ground at and above), has a shading switch
  (soft / medium / strong / flat) and a "detailed ground" on/off chip in the map's layer drawer, and a
  Settings section for the cache, the shading default, worker count and client folders. Buildings
  (world models placed in the terrain) are drawn on top of the ground, so towns read as towns and the
  terrain holes are filled by the building instead of the minimap doodle. A **buildings + caves** chip in the ground controls turns them off to show the bare terrain (tiles
  are requested with `bld=0` and cached separately). See
  [Ground (terrain) layer](#ground-terrain-layer).

- **One button for the controls.** The live map keeps the map itself full-screen: a single floating
  "Layers" pill (with a count of the active layers) opens a drawer holding the map selector, search,
  colour/filter, every layer toggle, the ground controls, the scene options and the (collapsible)
  legend. On a wide window the drawer becomes a floating panel on the right.
- **Phone first.** Mobile layout, short links (`/r/<run>`, `/t/<core>/<test>`), and an optional
  Cloudflare quick tunnel with token login, so you can use it away from the PC.
- **Snapshots.** `livemap snapshot` renders a run page to a PNG (also at `/snapshot/run/<id>.png`).

Planned next: replay
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

## Ground (terrain) layer

`livemap/adtrender.py` renders the ground on demand, per 533 yd ADT tile, from the client's
`World\Maps\<map>\<map>_<x>_<y>.adt` (vanilla and TBC formats; WotLK needs the R3 work).

- **Tile endpoint:** `GET /tiles/<core>/adt/<map>/<z>/<x>/<y>.webp?shade=<soft|medium|strong|flat>`.
  The grid follows the viewer's CRS exactly: at `z=1` one ADT is 4x4 tiles of 256 px (0.52 yd/px),
  `z=0` is 2x2, `z=-1` is one ADT per tile (2.08 yd/px, the minimap resolution). Above `z=1` the
  browser scales the native tiles up; below `z=-1` the viewer uses the minimap image instead. Tiles
  for ocean or missing ADTs return `204`.
- **Caches:** parsed ADTs (80 MB), rendered ADT images (80 MB), decoded ground textures (24 MB), WMO
  roots (128 files), WMO group geometry (40 MB) and WMO texture tiles (8 MB) live in RAM as LRUs
  shared by every client; each render also warms its eight neighbour ADTs in the background so the
  next pan is instant. With "keep a copy on disk" on, rendered ADTs go to
  `data/adtcache/<core>/<source core>/<map>/v<renderer>/<x>/<y>_<shade>.webp`, capped and evicted
  oldest-first. The cache key carries the renderer version, so a bump never serves stale tiles and the
  browser may cache tiles for a week.
- **Workers:** `ProcessPoolExecutor` is replaced by a `ThreadPoolExecutor` (setting `render_workers`,
  default 2): the render is numpy/Pillow work that releases the GIL, and threads let every worker share
  the RAM caches and the MPQ archives instead of paying the ~5 s archive open per process. Duplicate
  requests for the same `(client, map, ADT, shade)` share one future.
- **Fallback (per map):** when the viewed core has no client, or its client has no terrain for that
  map, another core's client renders it (order in Settings, default `tbc, vanilla`). The map shows
  "terrain: rendered from the <core> client" and the disk cache keeps those tiles apart. A core whose
  client DBC has map 571 (Northrend) is treated as WotLK and is not used for terrain until R3.
- **Buildings (R2):** each ADT's `MODF` placements are read (and taken from the eight neighbouring
  ADTs too, so a building that crosses a tile edge is complete; the client repeats the same record in
  every ADT it touches, so they are de-duplicated). For each placed WMO the root (materials, group
  boxes) and its group files (`MOVT`/`MOVI`/`MOPY`/`MOTV`) are parsed once and cached, then the
  triangles are projected top-down: bounding-box culling per placement and group, up-facing and
  area filters per triangle, big triangles filled in depth order, small ones splatted and dilated,
  colour sampled from the material texture at the triangle's UV. Roofs get a slope shade and the
  silhouette a dark outline so a town reads at a glance. Trees, doodads and M2 props are still R4;
  WotLK formats are still R3.
- **Settings** (`/settings`, section "Map", stored in `data/livemap.sqlite`): keep cache on/off, cache
  size cap with current usage, delete the saved cache (all cores or one), default shading, render
  workers, fallback on/off and order, and the client `Data` folder per core with a check button
  (reports the archive count, locale and whether a sample ADT can be read). Client folders are a
  filesystem path, so they are editable locally only unless `allow_remote_write = true`.
- **Cost:** first tile after start ~5 s (MPQ tables are parsed by `mpyq`), then ~0.25 s per new ADT on
  a warm zone and instant for cached ones. A town ADT pays for its buildings once: measured 0.8 s
  (Northshire abbey), 1.8 s (Stormwind) to 3.2 s (Goldshire's ADT, which also contains a corner of
  Stormwind) on the first render, and 0.3-0.8 s per additional shading preset, because the WMO
  geometry, its textures and the rendered image are all cached. Server RSS settles around 330 MB with
  those caches full (the caps add up to ~232 MB; the budget is 400 MB).

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
| Rendered ground | the same client `Data` folder (vanilla or TBC client; WotLK from R3) |

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
  adtrender.py  zoomed-in ground rendered from the client's ADT terrain files
  mapstore.py   map settings (cache, shading, workers, client folders) in livemap.sqlite
  clientdata.py MPQ + DBC + BLP reading
  worlddb.py    quest givers / objectives / turn-ins, travel nodes, NPC / object layers
  live.py       tails bot_positions.csv for the live map, bot cards, recent events
  app.py        run reconstruction (map choice, track, markers)
  server.py     HTTP API, token login, static files
  snapshot.py   PNG renderer
  static/       web client (Leaflet, no build step; world.js = live map)
legacy/         the old QGIS project
```
