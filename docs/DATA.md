# What each feature reads


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
| Rendered ground | the same client `Data` folder (vanilla, TBC or WotLK client) |

The map art comes from your own client and is built locally; this repository does not ship
Blizzard art.

## Capability levels

`python -m livemap doctor` (and the Help page) reports these per server:

| Level | You have | You get |
| --- | --- | --- |
| L0 | the game client `Data` folder | world, continent and dungeon maps, zone names, dungeons at their entrances |
| L1 | + world / characters database (from `mangosd.conf`) | online players, NPC / object / quest layers |
| L2 | + PlayerBots (bots in the characters DB) | bots on the map, positions as of their last save |
| L3 | + bot log exports in `AiPlayerbot.AllowedLogFiles` | travel intent, teleport cache, zone levels, deaths / quests / level-ups |
| L4 | + live-map data contract (`livemap_manifest.json`, `bot_positions.csv`) | the real live map: 5 s positions, trails, follow, bot state |
| L5 | + test results (`GenerateBotTests` builds) | test explorer, tests on the map, run pages |

The characters-DB feed polls `characters where online = 1` every `db_poll_s` seconds (default 20) and
is never used while `bot_positions.csv` is fresh. Bots are told apart from players by the
`AiPlayerbot.RandomBotAccountPrefix` (default `rndbot`) through the login database.

## vmangos

The world-DB queries go through a small per-flavor table in `worlddb.py`: vmangos keeps several rows
per template (one per content patch), so each join picks the newest row at or below `WowPatch`
(mangosd.conf), and spawns / quest relations are filtered by `patch_min` / `patch_max`. The fork does
not write the live-map files yet (tracked upstream as BL-V11), so bots come from the characters DB.
