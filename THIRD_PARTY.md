# Third-party code and data

## Bundled

### Leaflet 1.9.4 (`livemap/static/leaflet.js`, `leaflet.css`) — BSD 2-Clause

```
BSD 2-Clause License

Copyright (c) 2010-2023, Volodymyr Agafonkin
Copyright (c) 2010-2011, CloudMade
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### Dungeon-door tables (`livemap/gamedata/teleports_*.csv`)

Trigger id, target map and target x/y of the `areatrigger_teleport` table of the CMaNGOS world
databases (classic-db, tbc-db, wotlk-db; GPL-2.0). Used only when no world database is configured.

## Installed with pip (not in this repository)

| Package | License |
| --- | --- |
| PyMySQL | MIT |
| mpyq | BSD 2-Clause |
| Pillow | MIT-CMU (HPND) |
| NumPy | BSD 3-Clause |

The Windows release zip also contains the official Python embeddable package (Python Software
Foundation License) and these four packages.

## Game data

World of Warcraft and its art are trademarks and property of Blizzard Entertainment. This project is
not affiliated with or endorsed by Blizzard. The map art is built on your own computer from your own
game client; this repository does not ship it, except a small demo area (`demo/basemap/`: one
continent at low resolution and one dungeon) used by the demo and the GitHub Pages site.
