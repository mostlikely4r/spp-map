'use strict';

const CLASS_COLORS = { 1: '#c79c6e', 2: '#f58cba', 3: '#abd473', 4: '#fff569', 5: '#ffffff', 6: '#c41f3b', 7: '#0070de', 8: '#69ccf0', 9: '#9482c9', 11: '#ff7d0a' };
const CLASS_NAMES = { 1: 'Warrior', 2: 'Paladin', 3: 'Hunter', 4: 'Rogue', 5: 'Priest', 6: 'Death Knight', 7: 'Shaman', 8: 'Mage', 9: 'Warlock', 11: 'Druid' };
const HORDE_RACES = new Set([2, 5, 6, 8, 10]);
const REACTION_COLORS = { alliance: '#3c8cff', horde: '#e6463c', both: '#3cdc5a', hostile: '#ff9f43', neutral: '#c8d6e5' };
const NPC_MODES = [['role', 'NPCs by role'], ['level', 'NPCs by level'], ['reaction', 'NPCs by reaction']];
const PATH_MODES = [['type', 'paths by type'], ['alliance', 'path danger: Alliance'], ['horde', 'path danger: Horde']];
const ROLE_COLORS = { flightmaster: '#ffe066', innkeeper: '#ff9f43', auctioneer: '#feca57', banker: '#c8d6e5', battlemaster: '#ff6b6b',
  spirithealer: '#a0f0ff', trainer: '#48dbfb', vendor: '#1dd1a1', questgiver: '#ffd200', elite: '#ff9ff3', boss: '#ff3b3b', mob: '#8395a7' };
const INTENT_COLORS = { quest: '#ffd200', 'trainer class': '#a29bfe', 'trainer trade': '#a29bfe', 'trainer mount': '#a29bfe', Bank: '#c8d6e5', AH: '#c8d6e5', Mail: '#c8d6e5',
  Repair: '#8395a7', Vendor: '#8395a7', GatherFishing: '#48dbfb', GatherSkinning: '#e17055', GatherMining: '#b2bec3', GatherHerbalism: '#55efc4',
  Grind: '#ff6b6b', pvp: '#ff3f34', city: '#fab1a0', GenericRpg: '#fd79a8' };
const DEFAULT_LAYERS = { bots: true, names: false, zones: true, labels: true, events: true, intents: false, npcs: false, objects: false, nodes: false, tele: false, zonelv: false };
const GO_COLORS = { chest: '#feca57', questgiver: '#ffd200', mailbox: '#c8d6e5', meetingstone: '#a29bfe', fishinghole: '#48dbfb', door: '#576574', generic: '#8395a7' };
const EVENT_STYLE = { death: ['#e6463c', 'died'], levelup: ['#ffd200', 'level up'], quest: ['#3cdc5a', 'quest'], pvp: ['#ff6b6b', 'pvp'],
  trade: ['#1dd1a1', 'trade'], gear: ['#a29bfe', 'gear'], revive: ['#a0f0ff', 'revived'], kill: ['#ff9f43', 'kill'], group: ['#48dbfb', 'group'],
  guild: ['#c8d6e5', 'guild'], travel: ['#feca57', 'hearth'], trainer: ['#48dbfb', 'trainer'], loot: ['#8395a7', 'loot'], other: ['#8395a7', 'other'] };
const COLOR_MODES = [['faction', 'faction'], ['class', 'class'], ['level', 'level'], ['state', 'state'], ['hp', 'health']];
const FILTERS = [['all', 'everyone'], ['players', 'players + their bots'], ['combat', 'in combat'], ['dead', 'dead'], ['group', 'grouped'], ['test', 'running a test'], ['idle', 'inactive']];
const MAX_LEVEL = { vanilla: 60, tbc: 70, wotlk: 80 };

const W = {
  core: null, map: null, timer: null, slowTimer: null, markers: new Map(), selected: null, follow: false, info: null,
  snap: null, npcColor: store('wnpccolor') || 'role', pathColor: store('wpathcolor') || 'type', color: store('wcolor') || 'faction', filter: store('wfilter') || 'all', layers: null, trail: null, fitted: false,
  on: { ...DEFAULT_LAYERS, ...(JSON.parse(store('wlayers') || 'null') || {}) },
};

const ATLAS_LAYERS = new Set(['bots', 'names', 'labels']);

function atlasMode() { return W.map === 'atlas' || W.map === 'world'; }
function worldScene() { return W.map === 'world'; }
const KIND_COLORS = { dungeon: '#48dbfb', raid: '#ff9f43', battleground: '#ff6b6b', arena: '#a29bfe', other: '#c8d6e5' };
const PLACE_MODES = [['real', 'dungeons at their location'], ['sea', 'dungeons offshore']];

function bpos(map, x, y) {
  if (!atlasMode()) return ll(x, y);
  const off = W.offsets[map];
  return off ? ll(x + off[0], y + off[1]) : null;
}

function wSaveLayers() { store('wlayers', JSON.stringify(W.on)); }

function stopWorld() {
  clearInterval(W.timer); clearInterval(W.slowTimer);
  W.timer = W.slowTimer = null;
  W.markers.clear(); W.selected = null; W.trail = null; W.layers = null; W.fitted = false; W.viewApplied = false;
  W.zoneLabels = null; W.zoneLevels = null;
  document.body.classList.remove('worldmode');
}

function col(row, name) { return row[W.snap.colIndex[name]]; }

function factionOf(race) { return race ? (HORDE_RACES.has(race) ? 'Horde' : 'Alliance') : ''; }

function levelColor(level) {
  const max = MAX_LEVEL[W.core] || 80;
  const t = Math.max(0, Math.min(1, (level - 1) / (max - 1)));
  return `hsl(${Math.round(220 - 220 * t)}, 85%, 55%)`;
}

function botColor(r) {
  const flags = col(r, 'flags');
  switch (W.color) {
    case 'class': return CLASS_COLORS[col(r, 'cls')] || '#888';
    case 'level': return levelColor(col(r, 'level'));
    case 'state':
      if (flags & 6) return '#9a9a9a';
      if (flags & 1) return '#ff3b3b';
      if (flags & 64) return '#ffd200';
      if (flags & 128) return '#ff46c8';
      if (!(flags & 16)) return '#4b5563';
      return '#3cdc5a';
    case 'hp': { const hp = col(r, 'hp'); return `hsl(${Math.round(hp * 1.2)}, 85%, 50%)`; }
    default: { const race = col(r, 'race'); return race ? (HORDE_RACES.has(race) ? '#e6463c' : '#3c8cff') : '#9a9a9a'; }
  }
}

function passesFilter(r) {
  const flags = col(r, 'flags');
  switch (W.filter) {
    case 'players': {
      if (!col(r, 'bot')) return true;
      const leader = col(r, 'leader');
      return leader && W.snap.playerGuids.has(leader);
    }
    case 'combat': return flags & 1;
    case 'dead': return flags & 6;
    case 'group': return flags & 8;
    case 'test': return flags & 128;
    case 'idle': return !(flags & 16);
    default: return true;
  }
}

function boundsParams() {
  const b = mapObj.getBounds();
  return { top: b.getNorth(), bottom: b.getSouth(), left: -b.getWest(), right: -b.getEast() };
}

async function worldPage(core, mapId) {
  stopWorld();
  const lp = new URLSearchParams(location.search).get('layers');
  if (lp !== null) { const want = new Set(lp.split(',')); for (const k of Object.keys(W.on)) W.on[k] = want.has(k); }
  document.body.classList.add('worldmode');
  view.textContent = '';
  const first = await api('live' + (core ? '?core=' + encodeURIComponent(core) : ''));
  W.core = first.core;
  if (mapId === null || mapId === undefined || (mapId !== 'atlas' && mapId !== 'world' && isNaN(mapId))) mapId = first.maps.length ? first.maps[0].id : 0;
  W.map = mapId;
  if (location.pathname !== `/w/${W.core}/${W.map}`) history.replaceState({}, '', `/w/${W.core}/${W.map}` + location.search);
  if (worldScene()) {
    const qp = new URLSearchParams(location.search).get('place');
    W.place = qp === 'sea' || qp === 'real' ? qp : (store('wplace') || 'real');
    const ov = await api(`overlay?core=${W.core}&mode=${W.place}`);
    W.atlas = ov.continents.map(c => ({ ...c, continent: true })).concat(ov.instances);
    W.offsets = Object.fromEntries(W.atlas.map(m => [m.id, m.off]));
    W.info = { id: 'world', name: 'World + dungeons', basemaps: [], zones: [], extent: null };
  } else if (atlasMode()) {
    const atlas = await api(`atlas?core=${W.core}`);
    W.atlas = atlas.maps;
    W.offsets = Object.fromEntries(atlas.maps.map(m => [m.id, m.off]));
    W.info = { id: 'atlas', name: 'All maps (atlas)', basemaps: [], zones: [], extent: null };
  } else W.info = await api(`mapinfo?core=${W.core}&map=${W.map}`);

  const mapSelect = el('select', { class: 'wsel', onchange: e => go(`/w/${W.core}/${e.target.value}`) });
  const colorSelect = el('select', { class: 'wsel', onchange: e => { W.color = e.target.value; store('wcolor', W.color); redrawBots(); legend(); } },
    COLOR_MODES.map(([v, l]) => el('option', { value: v, selected: v === W.color ? 'selected' : null }, 'colour: ' + l)));
  const filterSelect = el('select', { class: 'wsel', onchange: e => { W.filter = e.target.value; store('wfilter', W.filter); redrawBots(); } },
    FILTERS.map(([v, l]) => el('option', { value: v, selected: v === W.filter ? 'selected' : null }, 'show: ' + l)));
  const search = el('input', { type: 'search', class: 'wsearch', placeholder: 'find a bot or player' });
  const results = el('div', { class: 'wresults' });
  let timer = null;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      results.textContent = '';
      const q = search.value.trim();
      if (q.length < 2) return;
      const hits = await api(`live/find?core=${W.core}&q=${encodeURIComponent(q)}`);
      for (const h of hits) results.append(el('div', { class: 'row', onclick: () => {
        results.textContent = ''; search.value = '';
        if (h.map === W.map || atlasMode()) selectBot(h.guid, true); else go(`/w/${W.core}/${h.map}?bot=${h.guid}`);
      } }, el('span', { class: 'name' }, h.name), el('span', { class: 'meta' }, ` level ${h.level} · ${h.bot ? 'bot' : 'player'} · map ${h.map}`)));
      if (!hits.length) results.append(el('div', { class: 'row meta' }, 'nobody online by that name'));
    }, 300);
  });
  const chips = el('div', { class: 'chips wlayers' });
  const npcSelect = el('select', { class: 'wsel', onchange: e => { W.npcColor = e.target.value; store('wnpccolor', W.npcColor); if (W.on.npcs) loadViewportLayer('npcs'); } },
    NPC_MODES.map(([v, l]) => el('option', { value: v, selected: v === W.npcColor ? 'selected' : null }, l)));
  const pathSelect = el('select', { class: 'wsel', onchange: e => { W.pathColor = e.target.value; store('wpathcolor', W.pathColor); if (W.on.nodes) loadViewportLayer('nodes'); } },
    PATH_MODES.map(([v, l]) => el('option', { value: v, selected: v === W.pathColor ? 'selected' : null }, l)));
  const styleRow = el('div', { class: 'wrow' }, npcSelect, pathSelect);
  const status = el('div', { class: 'wstatus' });
  const legendBox = el('div', { class: 'legend wlegend' });
  const mapDiv = el('div', { id: 'wmap' });
  const sheet = el('div', { class: 'sheet hidden' });
  add(el('div', { class: 'wbar' }, el('div', { class: 'wrow' }, mapSelect, search), results,
      el('div', { class: 'wrow' }, colorSelect, filterSelect), chips, styleRow),
    el('div', { class: 'wmapwrap' }, mapDiv, sheet, status), legendBox);
  if (worldScene()) {
    const placeSelect = el('select', { class: 'wsel', onchange: e => { store('wplace', e.target.value); const u = new URL(location.href); u.searchParams.set('place', e.target.value); history.replaceState({}, '', u.pathname + u.search); worldPage(W.core, 'world'); } },
      PLACE_MODES.map(([v, l]) => el('option', { value: v, selected: v === W.place ? 'selected' : null }, l)));
    chips.before(el('div', { class: 'wrow' }, placeSelect));
  }
  W.ui = { mapSelect, chips, status, legendBox, sheet, styleRow, npcSelect, pathSelect };
  styleVisibility();

  mapObj = L.map(mapDiv, { crs: L.CRS.Simple, minZoom: -7, maxZoom: 4, zoomSnap: 0.25, attributionControl: false, preferCanvas: true });
  W.layers = {
    base: L.layerGroup().addTo(mapObj), zones: L.layerGroup(), labels: L.layerGroup(), bots: L.layerGroup(),
    npcs: L.layerGroup(), objects: L.layerGroup(), nodes: L.layerGroup(), events: L.layerGroup(), tele: L.layerGroup(), intents: L.layerGroup(), zonelv: L.layerGroup(), sel: L.layerGroup().addTo(mapObj),
  };
  W.renderer = L.canvas({ padding: 0.3 });
  drawBase();
  for (const [key, label] of [['bots', 'bots'], ['names', 'names'], ['zones', 'zone maps'], ['labels', atlasMode() ? 'map names' : 'zone names'], ['zonelv', 'zone levels'], ['events', 'deaths / quests / levels'],
    ['intents', 'travel intent'], ['npcs', 'NPCs'], ['objects', 'objects'], ['nodes', 'travel nodes'], ['tele', 'teleport cache']]) {
    if (atlasMode() && !ATLAS_LAYERS.has(key)) continue;
    const chip = el('span', { class: 'chip' + (W.on[key] ? ' on' : '') }, label);
    chip.onclick = () => { W.on[key] = !W.on[key]; chip.classList.toggle('on', W.on[key]); wSaveLayers(); applyLayer(key); };
    chips.append(chip);
  }
  for (const key of Object.keys(W.on)) applyLayer(key);
  mapObj.on('moveend', debounce(() => { refreshViewportLayers(); updateZones(); updateNames(); updateZoneLevelLabels(); saveView(); }, 400));
  mapObj.on('click', () => closeSheet());
  ingestSnapshot(await api(`live?core=${W.core}&map=${W.map}`));
  const want = new URLSearchParams(location.search).get('bot');
  if (want) selectBot(+want, true);
  W.timer = setInterval(() => { if (document.visibilityState === 'visible') poll(); }, 3000);
  W.slowTimer = setInterval(() => { if (document.visibilityState === 'visible') { if (W.on.events) loadEvents(); if (W.on.intents) loadIntents(); } }, 30000);
  legend();
}

function saveView() {
  if (!mapObj) return;
  const c = mapObj.getCenter();
  const u = new URL(location.href);
  u.searchParams.set('x', Math.round(c.lat)); u.searchParams.set('y', Math.round(-c.lng)); u.searchParams.set('z', mapObj.getZoom());
  history.replaceState({}, '', u.pathname + u.search);
}

function debounce(fn, ms) { let t = null; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

function pickBase() {
  const bms = W.info.basemaps;
  const continent = bms.find(b => b.kind === 'zone' && !b.area);
  const floors = bms.filter(b => b.kind === 'floor').sort((a, b) => a.floor - b.floor);
  const zones = bms.filter(b => b.kind === 'zone' && b.area);
  const wmo = bms.find(b => b.kind === 'wmo');
  return { continent, floors, zones, wmo };
}

function imgBounds(b) { return L.latLngBounds(ll(b.bottom, b.left), ll(b.top, b.right)); }

function drawAtlas() {
  const all = [];
  const scene = worldScene();
  for (const m of W.atlas) {
    const [ox, oy] = m.off;
    const e = m.extent;
    const b = { top: e.top + ox, bottom: e.bottom + ox, left: e.left + oy, right: e.right + oy };
    const box = imgBounds(b);
    all.push(box);
    if (scene && !m.continent) {
      const pad = Math.max(40, Math.abs(e.top - e.bottom) * 0.03);
      const back = imgBounds({ top: b.top + pad, bottom: b.bottom - pad, left: b.left + pad, right: b.right - pad });
      L.rectangle(back, { color: KIND_COLORS[m.kind] || KIND_COLORS.other, weight: 1.5, fillColor: '#0b0f14', fillOpacity: 0.72 })
        .on('click', ev => { L.DomEvent.stop(ev); go(`/w/${W.core}/${m.id}`); }).addTo(W.layers.base);
    }
    if (m.base) L.imageOverlay(`/basemap/${W.core}/${m.base.key}.webp`, box).addTo(W.layers.base);
    else L.rectangle(box, { color: '#576574', weight: 1, fill: false, interactive: false }).addTo(W.layers.base);
  }
  if (scene) {
    for (const m of W.atlas) {
      if (m.continent || !m.links) continue;
      const c = KIND_COLORS[m.kind] || KIND_COLORS.other;
      for (const [door, land] of m.links) {
        if (m.moved || Math.hypot(door[0] - land[0], door[1] - land[1]) > 30)
          L.polyline([ll(door[0], door[1]), ll(land[0], land[1])], { color: c, weight: 2, opacity: 0.85, dashArray: '6 6', interactive: false }).addTo(W.layers.base);
        L.circleMarker(ll(door[0], door[1]), { radius: 4, color: '#000', weight: 1, fillColor: c, fillOpacity: 1, interactive: false }).addTo(W.layers.base);
        L.circleMarker(ll(land[0], land[1]), { radius: 3, color: c, weight: 1, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(W.layers.base);
      }
    }
  }
  if (!W.fitted && all.length) { mapObj.fitBounds(all.reduce((a, b) => a.extend(b), L.latLngBounds(all[0].getSouthWest(), all[0].getNorthEast()))); W.fitted = true; }
  const v = new URLSearchParams(location.search);
  if (!W.viewApplied && v.has('z')) { mapObj.setView(ll(+v.get('x'), +v.get('y')), +v.get('z')); W.fitted = true; }
  W.viewApplied = true;
}

function drawBase() {
  W.layers.base.clearLayers();
  if (atlasMode()) return drawAtlas();
  const { continent, floors, zones, wmo } = pickBase();
  W.floor = W.floor && floors.some(f => f.floor === W.floor) ? W.floor : (floors[0] && floors[0].floor);
  let base = continent;
  if (!base && floors.length) base = floors.find(f => f.floor === W.floor);
  if (!base && zones.length) base = zones.slice().sort((a, b) => area(b.bounds) - area(a.bounds))[0];
  if (!base && wmo) base = wmo;
  W.base = base;
  if (base) {
    L.imageOverlay(`/basemap/${W.core}/${base.key}.webp`, imgBounds(base.bounds)).addTo(W.layers.base);
    if (!W.fitted) { mapObj.fitBounds(imgBounds(base.bounds)); W.fitted = true; }
  } else if (!W.fitted && W.info.extent) { mapObj.fitBounds(imgBounds(W.info.extent)); W.fitted = true; }
  else if (!W.fitted) mapObj.setView([0, 0], -3);
  const v = new URLSearchParams(location.search);
  if (!W.viewApplied && v.has('z')) { mapObj.setView(ll(+v.get('x'), +v.get('y')), +v.get('z')); W.fitted = true; }
  W.viewApplied = true;
  const old = document.querySelector('.wfloors');
  if (old) old.remove();
  if (floors.length > 1) {
    const row = el('div', { class: 'chips wfloors' }, floors.map(f => el('span', { class: 'chip' + (f.floor === W.floor ? ' on' : ''),
      onclick: () => { W.floor = f.floor; drawBase(); redrawBots(); } }, 'floor ' + f.floor)));
    W.ui.chips.after(row);
  }
}

function area(b) { return Math.abs(b.top - b.bottom) * Math.abs(b.left - b.right); }

function updateAtlasLabels() {
  W.layers.labels.clearLayers();
  if (!W.on.labels) return;
  const view = mapObj.getBounds();
  const px = Math.pow(2, mapObj.getZoom());
  for (const m of W.atlas) {
    const [ox, oy] = m.off;
    const e = m.extent;
    const w = Math.abs(e.left - e.right);
    const at = ll(e.top + ox - Math.abs(e.top - e.bottom) * 0.04, (e.left + e.right) / 2 + oy);
    if (w * px < 110 || !view.contains(at)) continue;
    L.marker(at, { icon: L.divIcon({ className: 'zlabel', html: '<span>' + escapeHtml(m.name) + '</span>', iconSize: null }), interactive: false }).addTo(W.layers.labels);
  }
}

function updateZones() {
  if (!W.layers) return;
  if (atlasMode()) return updateAtlasLabels();
  const { continent, zones } = pickBase();
  W.layers.zones.clearLayers();
  W.layers.labels.clearLayers();
  if (!continent) return;
  const view = mapObj.getBounds();
  const zoom = mapObj.getZoom();
  const cw = Math.abs(continent.bounds.left - continent.bounds.right);
  const pxPerYard = Math.pow(2, zoom);
  if (W.on.zones && pxPerYard * cw > 3600) {
    for (const z of zones.slice().sort((a, b) => area(b.bounds) - area(a.bounds))) {
      const zb = imgBounds(z.bounds);
      if (view.intersects(zb)) L.imageOverlay(`/basemap/${W.core}/${z.key}.webp`, zb, { opacity: 1 }).addTo(W.layers.zones);
    }
  }
  if (W.on.labels) {
    const sizes = Object.fromEntries(zones.map(z => [z.key, Math.abs(z.bounds.left - z.bounds.right)]));
    for (const z of W.info.zones) {
      if (!view.contains(ll(z.x, z.y))) continue;
      if ((sizes[z.key] || 0) * pxPerYard < 90) continue;
      L.marker(ll(z.x, z.y), { icon: L.divIcon({ className: 'zlabel', html: '<span>' + escapeHtml(z.name) + '</span>', iconSize: null }), interactive: false }).addTo(W.layers.labels);
    }
  }
}

function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function applyLayer(key) {
  if (!W.layers) return;
  const layer = { bots: W.layers.bots, zones: W.layers.zones, labels: W.layers.labels, npcs: W.layers.npcs, objects: W.layers.objects,
    nodes: W.layers.nodes, events: W.layers.events, tele: W.layers.tele, intents: W.layers.intents, zonelv: W.layers.zonelv }[key];
  styleVisibility();
  if (key === 'names') return updateNames();
  if (!layer || (atlasMode() && !ATLAS_LAYERS.has(key))) return;
  if (W.on[key]) {
    layer.addTo(mapObj);
    if (key === 'zones' || key === 'labels') updateZones();
    if (key === 'npcs' || key === 'objects' || key === 'nodes' || key === 'tele') loadViewportLayer(key);
    if (key === 'events') loadEvents();
    if (key === 'intents') loadIntents();
    if (key === 'zonelv') loadZoneLevels();
  } else mapObj.removeLayer(layer);
}

async function poll() {
  try {
    ingestSnapshot(await api(`live?core=${W.core}&map=${W.map}`));
  } catch (e) { W.ui.status.textContent = String(e.message || e); }
}

function ingestSnapshot(snap) {
  snap.colIndex = Object.fromEntries(snap.cols.map((c, i) => [c, i]));
  snap.playerGuids = new Set(snap.bots.filter(r => !r[snap.colIndex.bot]).map(r => r[snap.colIndex.guid]));
  W.snap = snap;
  const sel = W.ui.mapSelect;
  const known = new Map(snap.maps.map(m => [m.id, m]));
  if (!known.has(W.map) && !atlasMode()) known.set(W.map, { id: W.map, name: W.info.name, bots: 0, players: 0 });
  sel.textContent = '';
  const total = snap.maps.reduce((n, m) => n + m.bots, 0);
  sel.append(el('option', { value: 'world', selected: worldScene() ? 'selected' : null }, `World + dungeons (${total})`));
  sel.append(el('option', { value: 'atlas', selected: W.map === 'atlas' ? 'selected' : null }, `All maps, atlas (${total})`));
  for (const m of [...known.values()].sort((a, b) => b.bots - a.bots))
    sel.append(el('option', { value: m.id, selected: m.id === W.map ? 'selected' : null }, `${m.name} (${m.bots}${m.players ? ', ' + m.players + ' players' : ''})`));
  redrawBots();
  const age = snap.live ? 'live' : 'not live (server stopped or positions log off)';
  W.ui.status.textContent = `${snap.core} · ${age} · ${snap.bots.length} here · ${snap.newest ? snap.newest.slice(11) : 'no data yet'}`;
  if (W.selected) refreshSelected();
}

function redrawBots() {
  if (!W.snap) return;
  const seen = new Set();
  const multiFloor = !atlasMode() && pickBase().floors.length > 1;
  for (const r of W.snap.bots) {
    const guid = col(r, 'guid');
    if (!passesFilter(r)) continue;
    seen.add(guid);
    const player = !col(r, 'bot');
    const offFloor = multiFloor && col(r, 'floor') !== null && col(r, 'floor') !== W.floor;
    const style = { radius: player ? 6 : 4, color: player ? '#fff' : '#000', weight: player ? 2 : 1, fillColor: botColor(r),
      fillOpacity: offFloor ? 0.25 : 0.95, opacity: offFloor ? 0.3 : 1, renderer: W.renderer };
    let m = W.markers.get(guid);
    const pos = bpos(col(r, 'map'), col(r, 'x'), col(r, 'y'));
    if (!pos) { seen.delete(guid); continue; }
    if (!m) {
      m = L.circleMarker(pos, style);
      m.on('click', e => { L.DomEvent.stopPropagation(e); selectBot(guid, false); });
      m.addTo(W.layers.bots);
      W.markers.set(guid, m);
    } else { m.setLatLng(pos); m.setStyle(style); }
    m.row = r;
    const text = `${col(r, 'name')} (${col(r, 'level')})`;
    if (!m.getTooltip()) m.bindTooltip(text, { permanent: false, direction: 'top' });
    else if (m.getTooltip().getContent() !== text) m.setTooltipContent(text);
  }
  for (const [guid, m] of W.markers) if (!seen.has(guid)) { W.layers.bots.removeLayer(m); W.markers.delete(guid); }
  updateNames();
}

function updateNames() {
  if (!W.layers) return;
  const view = mapObj.getBounds();
  const visible = [...W.markers.values()].filter(m => view.contains(m.getLatLng()));
  const show = W.on.names && visible.length <= 250;
  for (const m of W.markers.values()) {
    const want = show && view.contains(m.getLatLng());
    const tip = m.getTooltip();
    if (!tip) continue;
    if (want !== !!tip.options.permanent) {
      const text = tip.getContent();
      m.unbindTooltip();
      m.bindTooltip(text, { permanent: want, direction: 'top', className: want ? 'nlabel' : '' });
    }
  }
}

function legend() {
  const box = W.ui.legendBox;
  box.textContent = '';
  const item = (c, t) => el('span', {}, el('span', { class: 'dot', style: `background:${c}` }), t);
  if (W.color === 'faction') box.append(item('#3c8cff', 'Alliance'), item('#e6463c', 'Horde'));
  if (W.color === 'class') for (const [k, n] of Object.entries(CLASS_NAMES)) if (W.core === 'wotlk' || k !== '6') box.append(item(CLASS_COLORS[k], n));
  if (W.color === 'level') box.append(item(levelColor(1), 'level 1'), item(levelColor((MAX_LEVEL[W.core] || 80) / 2), 'mid'), item(levelColor(MAX_LEVEL[W.core] || 80), 'max'));
  if (W.color === 'state') box.append(item('#3cdc5a', 'active'), item('#ff3b3b', 'combat'), item('#9a9a9a', 'dead'), item('#ffd200', 'flying'), item('#ff46c8', 'test'), item('#4b5563', 'inactive'));
  if (W.color === 'hp') box.append(item('hsl(0,85%,50%)', '0%'), item('hsl(60,85%,50%)', '50%'), item('hsl(120,85%,50%)', '100%'));
  box.append(el('span', {}, el('span', { class: 'dot', style: 'background:#888;border:2px solid #fff' }), 'real player'));
  if (W.on.events) for (const k of ['death', 'levelup', 'quest']) box.append(el('span', {}, el('span', { class: 'dot sq', style: `background:${EVENT_STYLE[k][0]}` }), EVENT_STYLE[k][1]));
}

async function loadEvents() {
  if (atlasMode()) return;
  if (!W.on.events) return;
  const rows = await api(`live/events?core=${W.core}&map=${W.map}&seconds=1800&kinds=death,levelup,quest,pvp`);
  W.layers.events.clearLayers();
  for (const e of rows) {
    const [c, label] = EVENT_STYLE[e.kind] || EVENT_STYLE.other;
    L.circleMarker(ll(e.x, e.y), { radius: e.kind === 'death' ? 5 : 4, color: '#000', weight: 1, fillColor: c, fillOpacity: 0.8, renderer: W.renderer })
      .bindTooltip(`${e.t.slice(11)} ${e.bot} ${label}${e.kind === 'levelup' ? ' ' + e.level : ''}${e.detail ? ' · ' + e.detail : ''}`).addTo(W.layers.events);
  }
}

function styleVisibility() {
  if (!W.ui || !W.ui.styleRow) return;
  W.ui.npcSelect.style.display = W.on.npcs ? '' : 'none';
  W.ui.pathSelect.style.display = W.on.nodes ? '' : 'none';
  W.ui.styleRow.style.display = !atlasMode() && (W.on.npcs || W.on.nodes) ? '' : 'none';
}

async function loadZoneLevels() {
  if (atlasMode()) return;
  const rows = await api(`layer?core=${W.core}&map=${W.map}&kind=zonelevels&left=0&right=0&top=0&bottom=0`);
  const layer = W.layers.zonelv;
  layer.clearLayers();
  W.zoneLabels = null;
  W.zoneLevels = rows;
  for (const z of rows) {
    const c = z.level ? levelColor(z.level) : '#8395a7';
    const tip = `${z.name}${z.level ? ' · level ' + z.level : ''}`;
    if (z.hull.length >= 3) L.polygon(z.hull.map(p => ll(p[0], p[1])), { color: c, weight: 1, fillColor: c, fillOpacity: 0.22, renderer: W.renderer }).bindTooltip(tip, { sticky: true }).addTo(layer);
  }
  updateZoneLevelLabels();
}

function updateZoneLevelLabels() {
  if (!W.layers || !W.on.zonelv || !W.zoneLevels) return;
  if (!W.zoneLabels) W.zoneLabels = L.layerGroup().addTo(W.layers.zonelv);
  W.zoneLabels.clearLayers();
  const view = mapObj.getBounds();
  if (mapObj.getZoom() < -3) return;
  for (const z of W.zoneLevels) {
    if (!z.level || !z.center || !view.contains(ll(z.center[0], z.center[1]))) continue;
    L.marker(ll(z.center[0], z.center[1]), { icon: L.divIcon({ className: 'zlabel', html: '<span>' + z.level + '</span>', iconSize: null }), interactive: false }).addTo(W.zoneLabels);
  }
}

function refreshViewportLayers() { for (const k of ['npcs', 'objects', 'nodes', 'tele']) if (W.on[k]) loadViewportLayer(k); }

async function loadIntents() {
  if (atlasMode()) return;
  const rows = await api(`live/intents?core=${W.core}&map=${W.map}&seconds=900`);
  const layer = W.layers.intents;
  layer.clearLayers();
  for (const r of rows) {
    const c = INTENT_COLORS[r.type] || '#dfe6e9';
    const tip = `${r.t.slice(11)} ${r.bot} (${r.level}) · ${r.purpose}${r.to ? '' : ' · ' + (r.to_name ? 'on ' + r.to_name : 'elsewhere')} · ${r.dist}y`;
    if (r.to) {
      L.polyline([ll(r.from[0], r.from[1]), ll(r.to[0], r.to[1])], { color: c, weight: 2, opacity: 0.75, dashArray: '6 4', renderer: W.renderer }).bindTooltip(tip).addTo(layer);
      L.circleMarker(ll(r.to[0], r.to[1]), { radius: 4, color: '#000', weight: 1, fillColor: c, fillOpacity: 1, renderer: W.renderer }).bindTooltip(tip).addTo(layer);
    } else {
      L.circleMarker(ll(r.from[0], r.from[1]), { radius: 6, color: c, weight: 2, fill: false, renderer: W.renderer }).bindTooltip(tip).addTo(layer);
    }
  }
}

async function loadViewportLayer(key) {
  if (atlasMode()) return;
  const b = boundsParams();
  const kind = { npcs: 'creatures', objects: 'objects', nodes: 'nodes', tele: 'telecache' }[key];
  const q = new URLSearchParams({ core: W.core, map: W.map, kind, ...b });
  const d = await api('layer?' + q.toString());
  const layer = W.layers[key];
  layer.clearLayers();
  if (key === 'nodes') {
    for (const link of d.links) {
      let color = LINK_COLORS[link.type] || '#fff';
      let tip = null;
      if (W.pathColor !== 'type' && link.type === 1) {
        const dz = link.danger || [0, 0, 0];
        const lvl = Math.max(dz[0], W.pathColor === 'alliance' ? dz[2] : dz[1]);
        color = lvl ? levelColor(lvl) : '#576574';
        tip = lvl ? `toughest creature for ${W.pathColor === 'alliance' ? 'Alliance' : 'Horde'}: level ${lvl}` : 'no hostile creatures recorded';
      }
      const line = L.polyline(link.points.map(p => ll(p[0], p[1])), { color, weight: link.type === 1 ? (W.pathColor === 'type' ? 2 : 3) : 1, opacity: 0.8, renderer: W.renderer });
      if (tip) line.bindTooltip(tip, { sticky: true });
      line.addTo(layer);
    }
    for (const n of d.nodes) L.circleMarker(ll(n.x, n.y), { radius: 3, color: '#000', weight: 1, fillColor: '#fff', fillOpacity: 1, renderer: W.renderer }).bindTooltip(n.name).addTo(layer);
    return;
  }
  if (key === 'tele') {
    if (d.binned) {
      const max = Math.max(...d.bins.map(b => b[2]));
      for (const [x, y, n, lvl] of d.bins) L.circleMarker(ll(x, y), { radius: 3 + 8 * Math.sqrt(n / max), color: levelColor(lvl), weight: 1, fillColor: levelColor(lvl), fillOpacity: 0.3, renderer: W.renderer })
        .bindTooltip(`${n} teleport spots · avg level ${lvl} (zoom in for detail)`).addTo(layer);
    } else {
      for (const [x, y, lvl, kind] of d.features) L.circleMarker(ll(x, y), { radius: kind === 'rpg' ? 3 : 2, color: '#000', weight: kind === 'rpg' ? 1 : 0, fillColor: levelColor(lvl), fillOpacity: 0.8, renderer: W.renderer })
        .bindTooltip(`teleport spot · level ${lvl} · ${kind === 'rpg' ? 'roleplay' : 'levelling'}`).addTo(layer);
    }
    return;
  }
  if (d.binned) {
    const max = Math.max(...d.bins.map(b => b[2]));
    for (const [x, y, n] of d.bins) L.circleMarker(ll(x, y), { radius: 3 + 9 * Math.sqrt(n / max), color: key === 'npcs' ? '#ff9ff3' : '#feca57', weight: 1, fillOpacity: 0.25, renderer: W.renderer })
      .bindTooltip(`${n} ${key === 'npcs' ? 'NPCs' : 'objects'} (zoom in for detail)`).addTo(layer);
    return;
  }
  for (const f of d.features) {
    if (key === 'npcs') {
      const [, entry, x, y, name, level, role, reaction, maxLevel] = f;
      const fill = W.npcColor === 'level' ? levelColor(maxLevel) : W.npcColor === 'reaction' ? (REACTION_COLORS[reaction] || '#8395a7') : (ROLE_COLORS[role] || '#8395a7');
      const plain = W.npcColor === 'role' && role === 'mob';
      L.circleMarker(ll(x, y), { radius: plain ? 2 : (role === 'mob' ? 3 : 4), color: '#000', weight: plain ? 0 : 1, fillColor: fill, fillOpacity: plain ? 0.45 : 0.9, renderer: W.renderer })
        .bindTooltip(`${name} (${level})${role !== 'mob' ? ' · ' + role : ''} · ${reaction} · ${entry}`).addTo(layer);
    } else {
      const [, entry, x, y, name, type] = f;
      L.circleMarker(ll(x, y), { radius: 3, color: '#000', weight: 1, fillColor: GO_COLORS[type] || '#8395a7', fillOpacity: 0.9, renderer: W.renderer })
        .bindTooltip(`${name} · ${type} · ${entry}`).addTo(layer);
    }
  }
}

function closeSheet() {
  W.selected = null; W.follow = false;
  W.layers && W.layers.sel.clearLayers();
  W.ui.sheet.classList.add('hidden');
  const u = new URL(location.href); u.searchParams.delete('bot'); history.replaceState({}, '', u.pathname + u.search);
}

async function selectBot(guid, center) {
  W.selected = guid;
  const u = new URL(location.href); u.searchParams.set('bot', guid); history.replaceState({}, '', u.pathname + u.search);
  await refreshSelected(center);
}

async function refreshSelected(center) {
  const guid = W.selected;
  let card;
  try { card = await api(`live/bot?core=${W.core}&guid=${guid}`); } catch (e) { W.ui.sheet.classList.remove('hidden'); W.ui.sheet.textContent = 'This bot is not online any more.'; return; }
  if (W.selected !== guid) return;
  if (card.map !== W.map && !atlasMode()) { go(`/w/${W.core}/${card.map}?bot=${guid}`); return; }
  W.layers.sel.clearLayers();
  const here = bpos(card.map, card.x, card.y);
  if (card.trail.length > 1 && here) L.polyline(card.trail.map(p => bpos(card.map, p[0], p[1])), { color: '#28c8ff', weight: 3, opacity: 0.85 }).addTo(W.layers.sel);
  if (here) L.circleMarker(here, { radius: 11, color: '#28c8ff', weight: 3, fill: false }).addTo(W.layers.sel);
  if (here && (center || W.follow)) mapObj.setView(here, Math.max(mapObj.getZoom(), center ? (atlasMode() ? -3 : -1) : mapObj.getZoom()));
  renderCard(card);
}

function money(c) { return `${Math.floor(c / 10000)}g ${Math.floor(c / 100) % 100}s ${c % 100}c`; }

function renderCard(c) {
  const s = W.ui.sheet;
  const open = s.querySelector('details') ? s.querySelector('details').open : false;
  s.textContent = '';
  s.classList.remove('hidden');
  const followBtn = el('button', { class: 'ghost', onclick: () => { W.follow = !W.follow; followBtn.textContent = W.follow ? 'following' : 'follow'; followBtn.classList.toggle('on', W.follow); } }, W.follow ? 'following' : 'follow');
  if (W.follow) followBtn.classList.add('on');
  const where = [c.zone_name, c.area_name && c.area_name !== c.zone_name ? c.area_name : null].filter(Boolean).join(' › ');
  s.append(...[
    el('div', { class: 'sheethead' },
      el('div', {}, el('div', { class: 'name' }, c.name, ' ', el('span', { class: 'badge' }, c.bot ? 'bot' : 'player')),
        el('div', { class: 'meta' }, `level ${c.level} ${c.race_name} ${c.class_name}${c.faction ? ' · ' + c.faction : ''}`)),
      el('div', { class: 'sheetbtns' }, followBtn, el('button', { class: 'ghost', onclick: closeSheet }, '✕'))),
    el('div', { class: 'meta' }, `${c.map_name}${where ? ' · ' + where : ''}${c.floor ? ' · floor ' + c.floor : ''}`),
    el('div', { class: 'meta' }, `${c.state} · hp ${c.hp}% · power ${c.power}% · ${money(c.money)}${c.leader_name ? ' · group leader ' + c.leader_name : ''} · seen ${c.age}s ago`),
    c.runs.length ? el('div', { class: 'meta' }, 'tests: ', ...c.runs.slice(0, 4).map(r => el('a', { href: `/r/${r.id}`, 'data-link': 1, style: 'margin-right:8px' }, verdict(r.verdict), ' ', prettyTest(r.test).slice(0, 40)))) : null,
    el('details', { open: open ? 'open' : null }, el('summary', { class: 'meta' }, `last hour: ${c.events.length} events`),
      el('div', { class: 'list' }, c.events.slice().reverse().map(e => el('div', { class: 'meta' }, `${e.t.slice(11)} ${e.event.replace(/Action$/, '')}${e.detail ? ' · ' + e.detail : ''}`))))].filter(Boolean));
}
