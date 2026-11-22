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
const DEFAULT_LAYERS = { dungeons: true, bots: true, names: false, zones: true, labels: true, events: true, intents: false, npcs: false, objects: false, nodes: false, tele: false, zonelv: false };
const GO_COLORS = { chest: '#feca57', questgiver: '#ffd200', mailbox: '#c8d6e5', meetingstone: '#a29bfe', fishinghole: '#48dbfb', door: '#576574', generic: '#8395a7' };
const EVENT_STYLE = { death: ['#e6463c', 'died'], levelup: ['#ffd200', 'level up'], quest: ['#3cdc5a', 'quest'], pvp: ['#ff6b6b', 'pvp'],
  trade: ['#1dd1a1', 'trade'], gear: ['#a29bfe', 'gear'], revive: ['#a0f0ff', 'revived'], kill: ['#ff9f43', 'kill'], group: ['#48dbfb', 'group'],
  guild: ['#c8d6e5', 'guild'], travel: ['#feca57', 'hearth'], trainer: ['#48dbfb', 'trainer'], loot: ['#8395a7', 'loot'], other: ['#8395a7', 'other'] };
const COLOR_MODES = [['faction', 'faction'], ['class', 'class'], ['level', 'level'], ['state', 'state'], ['hp', 'health']];
const FILTERS = [['all', 'everyone'], ['players', 'players + their bots'], ['combat', 'in combat'], ['dead', 'dead'], ['group', 'grouped'], ['test', 'running a test'], ['idle', 'inactive']];
const MAX_LEVEL = { vanilla: 60, tbc: 70, wotlk: 80 };
const ADT_YD = 533.33333333, ADT_PX = 1024;
const LM_CRS = Object.assign({}, L.CRS.Simple, {
  scale: z => (ADT_PX / ADT_YD) * Math.pow(2, z - 1),
  zoom: s => Math.log2(s / (ADT_PX / ADT_YD)) + 1,
  infinite: true,
});
const TERRAIN_SHADES = ['soft', 'medium', 'strong', 'flat'];

const W = {
  core: null, map: null, timer: null, slowTimer: null, markers: new Map(), selected: null, follow: false, info: null,
  snap: null, npcColor: store('wnpccolor') || 'role', pathColor: store('wpathcolor') || 'type', color: store('wcolor') || 'faction', filter: store('wfilter') || 'all', layers: null, trail: null, fitted: false,
  place: null, ov: null, inst: [], honest: false, plain: false, top: {}, counts: {}, scene: false, focus: null, cont: {}, base0: [0, 0],
  art: store('wart') === 'minimap' ? 'minimap' : 'painted',
  on: { ...DEFAULT_LAYERS, ...(JSON.parse(store('wlayers') || 'null') || {}) },
};

function worldScene() { return W.scene; }
function allWorld() { return W.map === 'world'; }
function apiMap() { return W.scene ? 'world' : W.map; }
const KIND_COLORS = { dungeon: '#48dbfb', raid: '#ff9f43', battleground: '#ff6b6b', arena: '#a29bfe', other: '#c8d6e5' };
const PLACE_MODES = [['real', 'dungeons at their location'], ['sea', 'dungeons offshore']];
const LOD_PX = 40;

function placeOf(map) {
  return (W.place && W.place[map]) || null;
}

function bpos(map, x, y) {
  if (!worldScene()) return ll(x, y);
  const p = placeOf(map);
  return p ? ll(x + p.off[0], y + p.off[1]) : null;
}

function mapBox(m) {
  const p = placeOf(m.id);
  const e = m.extent;
  return { top: e.top + p.off[0], bottom: e.bottom + p.off[0], left: e.left + p.off[1], right: e.right + p.off[1] };
}

function applyPlace() {
  const place = {};
  const ov = W.ov;
  W.base0 = [0, 0];
  if (ov) {
    const focus = W.focus === null ? null : ov.continents.find(c => c.id === W.focus);
    if (focus) W.base0 = focus.off;
    const rel = o => [o[0] - W.base0[0], o[1] - W.base0[1]];
    for (const c of ov.continents) if (W.focus === null || c.id === W.focus) place[c.id] = { off: rel(c.off), continent: true };
    if (W.on.dungeons && W.art === 'minimap') for (const i of ov.instances) {
      if (W.focus !== null && (i.tray || i.root !== W.focus)) continue;
      const honest = W.honest || i.fan === false;
      place[i.id] = { off: rel(honest ? (i.off_honest || i.off) : i.off), inst: i };
    }
  }
  W.place = place;
}

function offBox(mapId, b) {
  const p = placeOf(mapId);
  const o = p ? p.off : [0, 0];
  return imgBounds({ top: b.top + o[0], bottom: b.bottom + o[0], left: b.left + o[1], right: b.right + o[1] });
}

function paintedContinent(id) {
  const info = W.cont[id];
  return info ?info.basemaps.find(b => b.kind === 'zone' && !b.area) || null : null;
}

function wSaveLayers() { store('wlayers', JSON.stringify(W.on)); }

async function wapi(path, opts) {
  const gen = W.gen;
  const data = await api(path, opts);
  if (gen !== W.gen) return new Promise(() => {});
  return data;
}

function stopWorld() {
  W.gen = (W.gen || 0) + 1;
  clearInterval(W.timer); clearInterval(W.slowTimer);
  W.timer = W.slowTimer = null;
  W.markers.clear(); W.selected = null; W.trail = null; W.layers = null; W.fitted = false; W.viewApplied = false;
  W.zoneLabels = null; W.zoneLevels = null; W.place = null; W.ov = null; W.inst = []; W.top = {}; W.counts = {};
  W.sceneGround = null; W.scene = false; W.focus = null; W.map = null; W.cont = {}; W.base0 = [0, 0]; W.mapSig = null;
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
  const first = await wapi('live' + (core ? '?core=' + encodeURIComponent(core) : ''));
  W.core = first.core;
  const artParam = new URLSearchParams(location.search).get('art');
  W.art = artParam === null ? (store('wart') === 'minimap' ? 'minimap' : 'painted') : (artParam === 'minimap' ? 'minimap' : 'painted');
  if (mapId === null || mapId === undefined || (mapId !== 'world' && isNaN(mapId))) mapId = 'world';
  W.map = mapId;
  if (location.pathname !== `/w/${W.core}/${W.map}`) history.replaceState({}, '', `/w/${W.core}/${W.map}` + location.search);
  const q0 = new URLSearchParams(location.search);
  const mode = q0.get('place');
  W.placeMode = mode === 'sea' || mode === 'real' ? mode : (store('wplace') || 'real');
  W.honest = q0.has('honest') ? q0.get('honest') === '1' : store('whonest') === '1';
  W.plain = q0.has('plain') ? q0.get('plain') === '1' : store('wplain') === '1';
  W.ov = await wapi(`overlay?core=${W.core}&mode=${W.placeMode}`);
  W.scene = W.map === 'world' || W.ov.continents.some(c => c.id === W.map);
  W.focus = W.scene && W.map !== 'world' ? W.map : null;
  W.info = W.map === 'world' ? { id: 'world', name: 'World + dungeons', basemaps: [], zones: [], extent: null }
    : await wapi(`mapinfo?core=${W.core}&map=${W.map}`);
  W.cont = {};
  if (W.scene) {
    W.atlas = W.ov.continents.map(c => ({ ...c, continent: true })).concat(W.ov.instances);
    applyPlace();
    await Promise.all(W.ov.continents.filter(c => placeOf(c.id)).map(async c => {
      try { W.cont[c.id] = c.id === W.focus ? W.info : await wapi(`mapinfo?core=${W.core}&map=${c.id}`); } catch (e) { W.cont[c.id] = null; }
    }));
  }

  const mapSelect = el('select', { class: 'wsel wmapsel', onchange: e => go(`/w/${W.core}/${e.target.value}`) });
  const badge = el('span', { class: 'wbadge' }, '0');
  const layersBtn = el('button', { class: 'wbtn', onclick: () => setDrawer(true) }, 'Layers', badge);
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
      const hits = await wapi(`live/find?core=${W.core}&q=${encodeURIComponent(q)}`);
      for (const h of hits) results.append(el('div', { class: 'row', onclick: () => {
        results.textContent = ''; search.value = ''; setDrawer(false);
        if (h.map === W.map || (worldScene() && (placeOf(h.map) || allWorld()))) selectBot(h.guid, true); else go(`/w/${W.core}/${h.map}?bot=${h.guid}`);
      } }, el('span', { class: 'name' }, h.name), el('span', { class: 'meta' }, ` level ${h.level} · ${h.bot ? 'bot' : 'player'} · map ${h.map}`)));
      if (!hits.length) results.append(el('div', { class: 'row meta' }, 'nobody online by that name'));
    }, 300);
  });
  const chips = el('div', { class: 'chips wlayers' });
  const npcSelect = el('select', { class: 'wsel', onchange: e => { W.npcColor = e.target.value; store('wnpccolor', W.npcColor); if (W.on.npcs) loadViewportLayer('npcs'); } },
    NPC_MODES.map(([v, l]) => el('option', { value: v, selected: v === W.npcColor ? 'selected' : null }, l)));
  const pathSelect = el('select', { class: 'wsel', onchange: e => { W.pathColor = e.target.value; store('wpathcolor', W.pathColor); if (W.on.nodes) loadViewportLayer('nodes'); } },
    PATH_MODES.map(([v, l]) => el('option', { value: v, selected: v === W.pathColor ? 'selected' : null }, l)));
  const styleRow = el('div', { class: 'wdrow' }, npcSelect, pathSelect);
  const status = el('div', { class: 'wstatus' });
  const legendBox = el('div', { class: 'legend wlegend' });
  const mapDiv = el('div', { id: 'wmap' });
  const sheet = el('div', { class: 'sheet hidden' });
  const terrainTag = el('div', { class: 'wterrain hidden' });
  const terrainRow = null;
  const styleBox = el('div', {});
  const ground = el('div', {});
  const sceneBlock = el('div', { class: 'wdsec hidden' });
  const drawerBody = el('div', { class: 'wdrawer-body' },
    el('div', { class: 'wdsec' }, el('div', { class: 'wdtitle' }, 'Map'), mapSelect),
    el('div', { class: 'wdsec' }, el('div', { class: 'wdtitle' }, 'Map style'), styleBox, ground),
    sceneBlock,
    el('div', { class: 'wdsec' }, search, results),
    el('div', { class: 'wdrow' }, colorSelect, filterSelect),
    el('div', { class: 'wdsec' }, el('div', { class: 'wdtitle' }, 'Show'), chips),
    el('div', { class: 'wdsec' }, el('div', { class: 'wdtitle' }, 'Style'), styleRow),
    el('details', { class: 'wdlegend' }, el('summary', {}, 'Legend'), legendBox));
  const drawer = el('div', { class: 'wdrawer hidden' },
    el('div', { class: 'wdrawer-grab' }),
    el('div', { class: 'wdrawer-head' }, el('strong', {}, 'Layers and options'),
      el('button', { class: 'ghost wdrawer-done', onclick: () => setDrawer(false) }, 'done')),
    drawerBody);
  const backdrop = el('div', { class: 'wdrawer-backdrop hidden', onclick: () => setDrawer(false) });
  add(el('div', { class: 'wmapwrap' }, mapDiv, status, terrainTag, sheet, backdrop, drawer,
    el('div', { class: 'wfab' }, layersBtn)));
  if (worldScene()) {
    const placeSelect = el('select', { class: 'wsel', onchange: e => {
      store('wplace', e.target.value);
      const u = new URL(location.href); u.searchParams.set('place', e.target.value); history.replaceState({}, '', u.pathname + u.search);
      worldPage(W.core, W.map);
    } }, PLACE_MODES.map(([v, l]) => el('option', { value: v, selected: v === W.placeMode ? 'selected' : null }, l)));
    W.honestChip = el('span', { class: 'chip' + (W.honest ? ' on' : ''), onclick: () => setScene({ honest: !W.honest }) }, 'honest (stack at doors)');
    W.plainChip = el('span', { class: 'chip' + (W.plain ? ' on' : ''), onclick: () => setScene({ plain: !W.plain }) }, 'plain art (no borders)');
    sceneBlock.append(el('div', { class: 'wdtitle' }, 'Dungeons on the map'), placeSelect, el('div', { class: 'chips' }, W.honestChip, W.plainChip));
  }
  W.ui = { mapSelect, chips, status, legendBox, sheet, styleRow, npcSelect, pathSelect, terrainTag, terrainRow,
    drawer, backdrop, badge, ground, styleBox, sceneBlock, layersBtn, fab: document.querySelector('.wfab') };
  styleVisibility();

  mapObj = L.map(mapDiv, { crs: LM_CRS, minZoom: -7, maxZoom: 4, zoomSnap: 0.25, attributionControl: false, preferCanvas: true });
  mapObj.createPane('baseimg');
  mapObj.getPane('baseimg').style.zIndex = 340;
  mapObj.createPane('zoneimg');
  mapObj.getPane('zoneimg').style.zIndex = 342;
  mapObj.createPane('terrain');
  mapObj.getPane('terrain').style.zIndex = 350;
  mapObj.createPane('instback');
  mapObj.getPane('instback').style.zIndex = 355;
  mapObj.createPane('instimg');
  mapObj.getPane('instimg').style.zIndex = 360;
  W.backRenderer = L.svg({ pane: 'instback' });
  W.layers = {
    base: L.layerGroup().addTo(mapObj), zones: L.layerGroup(), labels: L.layerGroup(), bots: L.layerGroup(),
    npcs: L.layerGroup(), objects: L.layerGroup(), nodes: L.layerGroup(), events: L.layerGroup(), tele: L.layerGroup(), intents: L.layerGroup(), zonelv: L.layerGroup(), sel: L.layerGroup().addTo(mapObj),
  };
  W.renderer = L.canvas({ padding: 0.3 });
  drawBase();
  W.terrain = mountTerrain(mapObj, W.core, W.map, terrainInfo(),
    () => (W.base && W.base.bounds ? imgBounds(W.base.bounds) : null), { chips: terrainRow, tag: terrainTag, allow: () => W.art === 'minimap' });
  renderStyle();
  for (const [key, label] of [['bots', 'bots'], ['names', 'names'], ['labels', worldScene() ? 'zone + map names' : 'zone names'], ['zonelv', 'zone levels'], ['events', 'deaths / quests / levels'],
    ['intents', 'travel intent'], ['npcs', 'NPCs'], ['objects', 'objects'], ['nodes', 'travel nodes'], ['tele', 'teleport cache']]) {
    const chip = el('span', { class: 'chip' + (W.on[key] ? ' on' : '') }, label);
    chip.onclick = () => {
      W.on[key] = !W.on[key]; chip.classList.toggle('on', W.on[key]); wSaveLayers();
      applyLayer(key);
      updateLayerBadge();
    };
    chips.append(chip);
  }
  updateLayerBadge();
  for (const key of Object.keys(W.on)) applyLayer(key);
  mapObj.on('moveend', debounce(() => { refreshViewportLayers(); updateZones(); updateNames(); updateZoneLevelLabels(); updateLod(); updateClusterChips(); saveView(); }, 400));
  mapObj.on('zoomend', debounce(() => { updateLod(); updateClusterChips(); if (W.terrain) W.terrain.sync(); syncSceneGround(); }, 150));
  mapObj.on('click', () => { closeSheet(); setDrawer(false); });
  ingestSnapshot(await wapi(`live?core=${W.core}&map=${apiMap()}`));
  const want = new URLSearchParams(location.search).get('bot');
  if (want) selectBot(+want, true);
  W.timer = setInterval(() => { if (document.visibilityState === 'visible') poll(); }, 3000);
  W.slowTimer = setInterval(() => { if (document.visibilityState === 'visible') { if (W.on.events) loadEvents(); if (W.on.intents) loadIntents(); } }, 30000);
  legend();
}


function setDrawer(open) {
  if (!W.ui || !W.ui.drawer) return;
  if (open && W.selected) closeSheet();
  W.ui.drawer.classList.toggle('hidden', !open);
  W.ui.backdrop.classList.toggle('hidden', !open);
  if (open) updateLayerBadge();
}

function updateLayerBadge() {
  if (!W.ui || !W.ui.badge) return;
  const active = Object.keys(W.on).filter(k => W.on[k] && optionApplies(k)).length;
  W.ui.badge.textContent = String(active);
}

function optionApplies(k) {
  if (k === 'zones') return W.art !== 'minimap';
  if (k === 'dungeons') return W.art === 'minimap' && worldScene();
  return true;
}

function renderStyle() {
  const box = W.ui.styleBox;
  box.textContent = '';
  const chip = (label, on, run, hint) => el('span', { class: 'chip' + (on ? ' on' : ''), title: hint || null, onclick: run }, label);
  const mm = W.art === 'minimap';
  box.append(el('div', { class: 'chips' },
    chip('painted', !mm, () => setArt('painted'), 'the painted world and zone maps'),
    chip('minimap', mm, () => setArt('minimap'), 'the game minimap, with dungeons, real terrain and interiors')));
  const opts = el('div', { class: 'chips' });
  const p = terrainPrefs(terrainInfo());
  if (!mm) opts.append(chip('zone maps', W.on.zones, () => toggleOption('zones'), 'painted zone maps over the continent when zoomed in'));
  else {
    if (worldScene()) opts.append(chip('dungeons', W.on.dungeons, () => toggleOption('dungeons'), 'dungeons, raids and battlegrounds drawn at their entrances'));
    opts.append(chip('real terrain (on zoom)', p.detailed, () => { store('wdetail', p.detailed ? '0' : '1'); groundChanged(); },
      'draw the ground from the client terrain files when zoomed in'));
    opts.append(chip('interiors', p.buildings, () => { store('wbld', p.buildings ? '0' : '1'); groundChanged(); },
      'buildings and caves on the real terrain; off shows the bare ground'));
  }
  box.append(opts);
  if (mm && p.detailed)
    box.append(el('div', { class: 'chips' }, TERRAIN_SHADES.map(n => chip(n, p.shade === n, () => { store('wshade', n); groundChanged(); }, 'terrain shading'))));
  if (W.ui.sceneBlock) W.ui.sceneBlock.classList.toggle('hidden', !(worldScene() && mm && W.on.dungeons));
}

function toggleOption(key) {
  W.on[key] = !W.on[key];
  wSaveLayers();
  if (key === 'dungeons') rebuildScene(); else applyLayer(key);
  renderStyle();
  updateLayerBadge();
}

function groundChanged() {
  renderStyle();
  if (W.terrain) W.terrain.sync();
  syncSceneGround(true);
}

const SceneGround = L.GridLayer.extend({
  createTile(coords, done) {
    const tile = document.createElement('canvas');
    tile.width = tile.height = 256;
    const ctx = tile.getContext('2d');
    const s = LM_CRS.scale(coords.z);
    const x0 = coords.x * 256, y0 = coords.y * 256;
    const prefs = this.options.prefs;
    const jobs = [];
    for (const src of this.options.sources) {
      const b = src.box;
      if (-b.right * s < x0 || -b.left * s > x0 + 256 || -b.bottom * s < y0 || -b.top * s > y0 + 256) continue;
      const sx = x0 + src.off[1] * s, sy = y0 + src.off[0] * s;
      for (let ty = Math.floor(sy / 256); ty <= Math.floor((sy + 255) / 256); ty++)
        for (let tx = Math.floor(sx / 256); tx <= Math.floor((sx + 255) / 256); tx++) {
          const url = `/tiles/${W.core}/adt/${src.id}/${coords.z}/${tx}/${ty}.webp?v=${src.info.version}&shade=${prefs.shade}${prefs.buildings ? '' : '&bld=0'}`;
          jobs.push(new Promise(res => {
            const img = new Image();
            img.onload = () => { ctx.drawImage(img, tx * 256 - sx, ty * 256 - sy); res(); };
            img.onerror = res;
            img.src = url;
          }));
        }
    }
    Promise.all(jobs).then(() => done(null, tile));
    return tile;
  },
});

function sceneGroundSources() {
  const out = [];
  for (const m of W.atlas || []) {
    if (!m.continent || !placeOf(m.id)) continue;
    const info = W.cont[m.id] && W.cont[m.id].terrain_render;
    if (info && info.available) out.push({ id: m.id, off: placeOf(m.id).off, info, box: mapBox(m) });
  }
  return out;
}

function syncSceneGround(force) {
  if (!mapObj || !allWorld()) return;
  const prefs = terrainPrefs();
  const sources = sceneGroundSources();
  const info = sources.length ? sources[0].info : null;
  const want = W.art === 'minimap' && prefs.detailed && info && mapObj.getZoom() >= info.min_zoom;
  const key = terrainKey(prefs);
  if (W.sceneGround && (!want || force || W.sceneGround.key !== key)) {
    mapObj.removeLayer(W.sceneGround.layer);
    W.sceneGround = null;
  }
  if (want && !W.sceneGround) {
    const layer = new SceneGround({ pane: 'terrain', tileSize: 256, minNativeZoom: info.min_zoom, maxNativeZoom: info.max_zoom,
      minZoom: mapObj.getMinZoom(), maxZoom: mapObj.getMaxZoom(), noWrap: true, keepBuffer: 2, updateWhenIdle: false, sources, prefs });
    layer.addTo(mapObj);
    W.sceneGround = { layer, key };
  }
}

function maskToLand(overlay, outer, land) {
  const img = overlay.getElement();
  if (!img || !land) return;
  const h = Math.abs(outer.top - outer.bottom), w = Math.abs(outer.left - outer.right);
  const fw = Math.abs(land.bounds.left - land.bounds.right) / w, fh = Math.abs(land.bounds.top - land.bounds.bottom) / h;
  const ox = (outer.left - land.bounds.left) / w, oy = (outer.top - land.bounds.top) / h;
  const pos = (o, f) => (Math.abs(1 - f) < 1e-6 ? 0 : o / (1 - f)) * 100;
  for (const pre of ['mask', 'webkitMask']) {
    img.style[pre + 'Image'] = `url(/basemap/${W.core}/${land.key}.webp)`;
    img.style[pre + 'Size'] = `${fw * 100}% ${fh * 100}%`;
    img.style[pre + 'Position'] = `${pos(ox, fw)}% ${pos(oy, fh)}%`;
    img.style[pre + 'Repeat'] = 'no-repeat';
  }
}

function terrainInfo() {
  return (W.info && W.info.terrain_render) || { available: false };
}

function terrainPrefs(info) {
  const spec = info || terrainInfo();
  let shade = store('wshade');
  if (!TERRAIN_SHADES.includes(shade)) shade = TERRAIN_SHADES.includes(spec.shade) ? spec.shade : 'soft';
  const detailed = store('wdetail');
  return { shade, detailed: detailed === null || detailed === undefined ? true : detailed === '1', buildings: store('wbld') !== '0' };
}

function terrainKey(prefs) { return prefs.shade + (prefs.buildings ? '' : '-nobld'); }

function terrainUrl(core, mapId, info, prefs) {
  return `/tiles/${core}/adt/${mapId}/{z}/{x}/{y}.webp?v=${info.version}&shade=${prefs.shade}${prefs.buildings ? '' : '&bld=0'}`;
}

function terrainLayerFor(info, core, mapId, prefs, bounds, map) {
  const layer = L.tileLayer(terrainUrl(core, mapId, info, prefs), {
    tileSize: 256, minNativeZoom: info.min_zoom, maxNativeZoom: info.max_zoom,
    minZoom: map.getMinZoom() - 1, maxZoom: map.getMaxZoom(),
    noWrap: true, keepBuffer: 2, pane: 'terrain', updateWhenIdle: false,
    bounds: bounds || undefined,
  });
  layer.lmKey = terrainKey(prefs);
  return layer;
}

function fillTerrainChips(row, info, sync) {
  if (!row) return;
  row.textContent = '';
  if (!info || !info.available) {
    if (info && info.pending) row.append(el('span', { class: 'chip' }, 'checking the client ...'));
    return;
  }
  const prefs = terrainPrefs(info);
  const chip = (label, on, run, hint) => {
    const node = el('span', { class: 'chip' + (on ? ' on' : ''), title: hint || null }, label);
    node.onclick = () => { run(); fillTerrainChips(row, info, sync); sync(); };
    return node;
  };
  row.append(chip('detailed ground', prefs.detailed, () => store('wdetail', prefs.detailed ? '0' : '1'),
    'draw the ground from the client terrain files instead of the minimap'));
  row.append(chip('buildings + caves', prefs.buildings, () => store('wbld', prefs.buildings ? '0' : '1'),
    'draw buildings and caves on the rendered ground; off shows the bare terrain'));
  for (const name of TERRAIN_SHADES)
    row.append(chip(name, prefs.shade === name, () => store('wshade', name), 'ground shading preset'));
}

function mountTerrain(map, core, mapId, info, boundsOf, ui) {
  if (!map.getPane('terrain')) {
    map.createPane('terrain');
    map.getPane('terrain').style.zIndex = 350;
  }
  const state = { layer: null, info, bounds: null };
  let polled = 0;
  const poll = () => {
    if (state.info && (state.info.available || !state.info.pending) || polled++ > 6) return;
    setTimeout(async () => {
      try {
        const fresh = await wapi(`terrain?core=${encodeURIComponent(core)}&map=${mapId}`);
        if (fresh && !fresh.pending) {
          state.info = fresh;
          fillTerrainChips(ui.chips, fresh, sync);
          sync();
          return;
        }
      } catch (e) { return; }
      poll();
    }, 1500);
  };
  const sync = () => {
    if (!state.info || !state.info.available || mapId === null || mapId === undefined) {
      if (state.layer && map.hasLayer(state.layer)) map.removeLayer(state.layer);
      if (ui.tag) { ui.tag.textContent = ''; ui.tag.classList.add('hidden'); }
      return;
    }
    const info = state.info;
    const prefs = terrainPrefs(info);
    const bounds = boundsOf ? boundsOf() : null;
    if (state.layer && bounds && state.bounds && !state.bounds.equals(bounds)) {
      map.removeLayer(state.layer);
      state.layer = null;
    }
    if (state.layer && state.layer.lmKey !== terrainKey(prefs)) {
      state.layer.setUrl(terrainUrl(core, mapId, info, prefs));
      state.layer.lmKey = terrainKey(prefs);
    }
    const want = prefs.detailed && (!ui.allow || ui.allow()) && map.getZoom() >= info.min_zoom;
    if (want && !state.layer) {
      state.bounds = bounds;
      state.layer = terrainLayerFor(info, core, mapId, prefs, bounds, map).addTo(map);
    } else if (want && !map.hasLayer(state.layer)) map.addLayer(state.layer);
    else if (!want && state.layer && map.hasLayer(state.layer)) map.removeLayer(state.layer);
    if (ui.tag) {
      const on = state.layer && map.hasLayer(state.layer);
      const text = on && info.fallback ? `terrain: rendered from the ${info.source} client` : '';
      ui.tag.textContent = text;
      ui.tag.classList.toggle('hidden', !text);
    }
  };
  map.on('zoomend', sync);
  fillTerrainChips(ui.chips, info, sync);
  sync();
  poll();
  return { sync, fill: () => fillTerrainChips(ui.chips, state.info, sync), state };
}

function saveView() {
  if (!mapObj) return;
  const c = mapObj.getCenter();
  const u = new URL(location.href);
  u.searchParams.set('x', Math.round(c.lat)); u.searchParams.set('y', Math.round(-c.lng)); u.searchParams.set('z', mapObj.getZoom());
  history.replaceState({}, '', u.pathname + u.search);
}

function setScene(patch) {
  Object.assign(W, patch);
  if ('honest' in patch) store('whonest', W.honest ? '1' : '0');
  if ('plain' in patch) store('wplain', W.plain ? '1' : '0');
  const u = new URL(location.href);
  u.searchParams.set('honest', W.honest ? '1' : '0');
  u.searchParams.set('plain', W.plain ? '1' : '0');
  history.replaceState({}, '', u.pathname + u.search);
  if (W.honestChip) W.honestChip.classList.toggle('on', W.honest);
  if (W.plainChip) W.plainChip.classList.toggle('on', W.plain);
  rebuildScene();
}

function rebuildScene() {
  applyPlace();
  drawBase();
  redrawBots();
  updateZones();
  refreshViewportLayers();
  if (W.on.events) loadEvents();
  if (W.on.intents) loadIntents();
  if (W.on.zonelv) loadZoneLevels();
  if (W.selected) refreshSelected();
}

function debounce(fn, ms) { let t = null; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

function pickBase() {
  const bms = W.info.basemaps;
  const continent = bms.find(b => b.kind === 'zone' && !b.area);
  const floors = bms.filter(b => b.kind === 'floor').sort((a, b) => a.floor - b.floor);
  const zones = bms.filter(b => b.kind === 'zone' && b.area);
  const wmo = bms.find(b => b.kind === 'wmo');
  return { continent, floors, zones, wmo, terrain: W.info.terrain || null };
}

function imgBounds(b) { return L.latLngBounds(ll(b.bottom, b.left), ll(b.top, b.right)); }

function drawScene() {
  const all = [];
  W.inst = [];
  for (const m of drawOrder()) {
    const b = mapBox(m);
    const box = imgBounds(b);
    if (!m.tray) all.push(box);
    if (m.continent || !m.extent) {
      const painted = m.continent && W.art !== 'minimap' ? paintedContinent(m.id) : null;
      if (painted) {
        const art = L.imageOverlay(`/basemap/${W.core}/${painted.key}.webp`, offBox(m.id, painted.bounds), { pane: 'baseimg' });
        if (allWorld()) art.on('add', () => maskToLand(art, painted.bounds, m.base));
        art.addTo(W.layers.base);
      }
      else if (m.base) L.imageOverlay(`/basemap/${W.core}/${m.base.key}.webp`, box, { pane: 'baseimg' }).addTo(W.layers.base);
      continue;
    }
    const inst = { m, b, box, pin: null };
    const pad = Math.max(40, Math.abs(b.top - b.bottom) * 0.03);
    const color = KIND_COLORS[m.kind] || KIND_COLORS.other;
    if (!W.plain) {
      const back = imgBounds({ top: b.top + pad, bottom: b.bottom - pad, left: b.left + pad, right: b.right - pad });
      inst.back = L.rectangle(back, { color, weight: 1.5, dashArray: m.tray ? '6 4' : null, fillColor: '#0b0f14', fillOpacity: 0.72, pane: 'instback', renderer: W.backRenderer }).addTo(W.layers.base);
    }
    const open = ev => { L.DomEvent.stop(ev); go(`/w/${W.core}/${m.id}`); };
    if (m.base) inst.img = L.imageOverlay(`/basemap/${W.core}/${m.base.key}.webp`, box, { pane: 'instimg', interactive: true }).on('click', open).addTo(W.layers.base);
    else inst.plain = L.rectangle(box, { color: '#576574', weight: 1, fill: false, interactive: true }).on('click', open).addTo(W.layers.base);
    W.inst.push(inst);
  }
  const [bx, by] = W.base0;
  for (const m of W.atlas) {
    if (m.continent || !m.links || !placeOf(m.id)) continue;
    const c = KIND_COLORS[m.kind] || KIND_COLORS.other;
    for (const [door, land] of m.links) {
      const d = ll(door[0] - bx, door[1] - by), l = ll(land[0] - bx, land[1] - by);
      if (m.moved || Math.hypot(door[0] - land[0], door[1] - land[1]) > 30)
        L.polyline([d, l], { color: c, weight: 2, opacity: 0.85, dashArray: '6 6', interactive: false }).addTo(W.layers.base);
      L.circleMarker(d, { radius: 4, color: '#000', weight: 1, fillColor: c, fillOpacity: 1, interactive: false }).addTo(W.layers.base);
      L.circleMarker(l, { radius: 3, color: c, weight: 1, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(W.layers.base);
    }
  }
  W.base = W.focus !== null && placeOf(W.focus) ? { bounds: mapBox(W.atlas.find(m => m.id === W.focus)) } : null;
  const old = document.querySelector('.wfloors');
  if (old) old.remove();
  if (W.terrain) W.terrain.sync();
  syncSceneGround(true);
  if (!W.fitted && all.length) { mapObj.fitBounds(all.reduce((a, b) => a.extend(b), L.latLngBounds(all[0].getSouthWest(), all[0].getNorthEast()))); W.fitted = true; }
  const v = new URLSearchParams(location.search);
  if (!W.viewApplied && v.has('z')) { mapObj.setView(ll(+v.get('x'), +v.get('y')), +v.get('z')); W.fitted = true; }
  W.viewApplied = true;
  updateLod();
  updateClusterChips();
}

function drawOrder() {
  const members = new Map();
  const atlas = W.atlas.filter(m => placeOf(m.id));
  for (const m of atlas) if (m.cluster) {
    if (!members.has(m.cluster)) members.set(m.cluster, []);
    members.get(m.cluster).push(m);
  }
  const moved = new Set();
  const rank = new Map();
  for (const [, group] of members) {
    const chosen = W.top[group[0].cluster] && group.find(m => m.id === W.top[group[0].cluster]);
    const sorted = group.slice().sort((a, b) => area(a.extent) - area(b.extent));
    const push = chosen && sorted.splice(sorted.indexOf(chosen), 1);
    for (const m of sorted) { moved.add(m); rank.set(m.id, 0); }
    if (push) { moved.add(chosen); rank.set(chosen.id, 1); }
  }
  const first = atlas.filter(m => !moved.has(m));
  const grouped = atlas.filter(m => moved.has(m)).sort((a, b) => rank.get(a.id) - rank.get(b.id));
  return first.concat(grouped);
}

function artWidth(inst) {
  const px = LM_CRS.scale(mapObj.getZoom());
  return Math.abs(inst.box.getEast() - inst.box.getWest()) * px;
}

function updateLod() {
  if (!worldScene() || !mapObj) return;
  const counts = W.counts || {};
  for (const inst of W.inst) {
    const small = artWidth(inst) < LOD_PX;
    for (const layer of [inst.img, inst.plain]) {
      if (!layer) continue;
      const on = mapObj.hasLayer(layer);
      if (small === on) { if (small) mapObj.removeLayer(layer); else mapObj.addLayer(layer); }
    }
    if (inst.back) {
      if (small && mapObj.hasLayer(inst.back)) mapObj.removeLayer(inst.back);
      if (!small && !mapObj.hasLayer(inst.back)) mapObj.addLayer(inst.back);
    }
    const n = counts[inst.m.id] || 0;
    if (small) {
      const at = ll((inst.b.top + inst.b.bottom) / 2, (inst.b.left + inst.b.right) / 2);
      const html = pinHtml(inst.m, n);
      if (!inst.pin) {
        inst.pin = L.marker(at, { icon: L.divIcon({ className: 'wpinwrap', html, iconSize: [0, 0] }), keyboard: false })
          .bindTooltip(`${inst.m.name}${n ? ' \u00b7 ' + n + ' inside' : ''}`)
          .on('click', () => go(`/w/${W.core}/${inst.m.id}`)).addTo(W.layers.base);
      } else {
        inst.pin.setLatLng(at);
        inst.pin.setIcon(L.divIcon({ className: 'wpinwrap', html, iconSize: [0, 0] }));
      }
    } else if (inst.pin) {
      W.layers.base.removeLayer(inst.pin);
      inst.pin = null;
    }
  }
}

function pinHtml(m, n) {
  const color = KIND_COLORS[m.kind] || KIND_COLORS.other;
  return `<span class="wpin${n ? '' : ' empty'}" style="border-color:${color}" title="${escapeHtml(m.name)}">${n || ''}</span>`;
}

function updateClusterChips() {
  if (!W.layers) return;
  if (!W.layers.chips) W.layers.chips = L.layerGroup().addTo(mapObj);
  W.layers.chips.clearLayers();
  if (!worldScene() || !W.honest) return;
  const groups = new Map();
  for (const m of W.atlas) if (m.cluster && placeOf(m.id)) {
    if (!groups.has(m.cluster)) groups.set(m.cluster, []);
    groups.get(m.cluster).push(m);
  }
  const view = mapObj.getBounds();
  const px = LM_CRS.scale(mapObj.getZoom());
  for (const [group, members] of groups) {
    const boxes = members.map(m => mapBox(m));
    const top = Math.max(...boxes.map(b => b.top)), bottom = Math.min(...boxes.map(b => b.bottom));
    const left = Math.max(...boxes.map(b => b.left)), right = Math.min(...boxes.map(b => b.right));
    if (Math.abs(left - right) * px < LOD_PX) continue;
    const at = ll((top + bottom) / 2, (left + right) / 2);
    if (!view.contains(at)) continue;
    const row = el('div', { class: 'wcluster' }, ...members.slice().sort((a, b) => area(b.extent) - area(a.extent))
      .map(m => el('span', { class: 'chip' + (W.top[group] === m.id ? ' on' : ''), onclick: ev => {
        L.DomEvent.stop(ev);
        W.top[group] = m.id;
        drawBase();
      } }, shortName(m.name))));
    L.marker(at, { icon: L.divIcon({ className: 'wclusterwrap', html: row, iconSize: null }), keyboard: false })
      .addTo(W.layers.chips);
  }
}

function shortName(name) {
  const n = name.replace(/^The\s+/i, '');
  return n.length > 22 ? n.slice(0, 21) + '\u2026' : n;
}

function drawBase() {
  W.layers.base.clearLayers();
  if (worldScene()) return drawScene();
  const { continent, floors, zones, wmo, terrain } = pickBase();
  W.floor = W.floor && floors.some(f => f.floor === W.floor) ? W.floor : (floors[0] && floors[0].floor);
  let base = null;
  if (W.art === 'minimap') base = wmo || terrain;
  if (!base && W.art !== 'minimap') base = continent;
  if (!base && floors.length) base = floors.find(f => f.floor === W.floor);
  if (!base && zones.length) base = zones.slice().sort((a, b) => area(b.bounds) - area(a.bounds))[0];
  if (!base) base = wmo || terrain;
  W.base = base;
  if (base) {
    L.imageOverlay(`/basemap/${W.core}/${base.key}.webp`, imgBounds(base.bounds), { pane: 'baseimg' }).addTo(W.layers.base);
    if (!W.fitted) { mapObj.fitBounds(imgBounds(base.bounds)); W.fitted = true; }
  } else if (!W.fitted && W.info.extent) { mapObj.fitBounds(imgBounds(W.info.extent)); W.fitted = true; }
  else if (!W.fitted) mapObj.setView([0, 0], -3);
  const v = new URLSearchParams(location.search);
  if (!W.viewApplied && v.has('z')) { mapObj.setView(ll(+v.get('x'), +v.get('y')), +v.get('z')); W.fitted = true; }
  W.viewApplied = true;
  const old = document.querySelector('.wfloors');
  if (old) old.remove();
  const row = el('div', { class: 'chips wfloors' });
  if (floors.length > 1 && W.art !== 'minimap')
    row.append(...floors.map(f => el('span', { class: 'chip' + (f.floor === W.floor ? ' on' : ''),
      onclick: () => { W.floor = f.floor; drawBase(); redrawBots(); } }, 'floor ' + f.floor)));
  if (row.children.length) W.ui.ground.append(row);
  if (W.terrain) W.terrain.sync();
}

function setArt(art) {
  W.art = art === 'minimap' ? 'minimap' : 'painted';
  store('wart', W.art);
  const u = new URL(location.href);
  if (W.art === 'minimap') u.searchParams.set('art', 'minimap'); else u.searchParams.delete('art');
  history.replaceState({}, '', u.pathname + u.search);
  if (worldScene()) rebuildScene();
  else { drawBase(); redrawBots(); updateZones(); }
  renderStyle();
  updateLayerBadge();
}

function area(b) { return Math.abs(b.top - b.bottom) * Math.abs(b.left - b.right); }

function updateMapLabels() {
  W.layers.labels.clearLayers();
  if (!W.on.labels) return;
  const view = mapObj.getBounds();
  const px = LM_CRS.scale(mapObj.getZoom());
  for (const m of W.atlas) {
    const p = placeOf(m.id);
    if (!p) continue;
    const b = mapBox(m);
    const w = Math.abs(b.left - b.right);
    const at = ll(b.top - Math.abs(b.top - b.bottom) * 0.04, (b.left + b.right) / 2);
    if (w * px < 110 || !view.contains(at)) continue;
    const name = escapeHtml(m.name) + (m.tray ? ' (no entrance)' : '');
    L.marker(at, { icon: L.divIcon({ className: 'zlabel', html: '<span>' + name + '</span>', iconSize: null }), interactive: false }).addTo(W.layers.labels);
  }
  for (const [id, info] of Object.entries(W.cont)) {
    const p = placeOf(+id);
    if (!p || !info) continue;
    const sizes = Object.fromEntries(info.basemaps.filter(b => b.kind === 'zone' && b.area).map(z => [z.key, Math.abs(z.bounds.left - z.bounds.right)]));
    for (const z of info.zones) {
      const at = ll(z.x + p.off[0], z.y + p.off[1]);
      if (!view.contains(at) || (sizes[z.key] || 0) * px < 90) continue;
      L.marker(at, { icon: L.divIcon({ className: 'zlabel', html: '<span>' + escapeHtml(z.name) + '</span>', iconSize: null }), interactive: false }).addTo(W.layers.labels);
    }
  }
}

function updateSceneZones() {
  W.layers.zones.clearLayers();
  if (!W.on.zones || W.art === 'minimap') return;
  const view = mapObj.getBounds();
  const px = LM_CRS.scale(mapObj.getZoom());
  for (const [id, info] of Object.entries(W.cont)) {
    const continent = paintedContinent(+id);
    if (!continent || !placeOf(+id) || px * Math.abs(continent.bounds.left - continent.bounds.right) <= 3600) continue;
    for (const z of info.basemaps.filter(b => b.kind === 'zone' && b.area).sort((a, b) => area(b.bounds) - area(a.bounds))) {
      const zb = offBox(+id, z.bounds);
      if (view.intersects(zb)) L.imageOverlay(`/basemap/${W.core}/${z.key}.webp`, zb, { pane: 'zoneimg' }).addTo(W.layers.zones);
    }
  }
}

function updateZones() {
  if (!W.layers) return;
  if (worldScene()) { updateSceneZones(); return updateMapLabels(); }
  const { continent, zones } = pickBase();
  W.layers.zones.clearLayers();
  W.layers.labels.clearLayers();
  if (!continent) return;
  const view = mapObj.getBounds();
  const zoom = mapObj.getZoom();
  const cw = Math.abs(continent.bounds.left - continent.bounds.right);
  const pxPerYard = LM_CRS.scale(zoom);
  if (W.on.zones && W.art !== 'minimap' && pxPerYard * cw > 3600) {
    for (const z of zones.slice().sort((a, b) => area(b.bounds) - area(a.bounds))) {
      const zb = imgBounds(z.bounds);
      if (view.intersects(zb)) L.imageOverlay(`/basemap/${W.core}/${z.key}.webp`, zb, { opacity: 1, pane: 'zoneimg' }).addTo(W.layers.zones);
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
  if (key === 'dungeons') return;
  if (!layer) return;
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
    ingestSnapshot(await wapi(`live?core=${W.core}&map=${apiMap()}`));
  } catch (e) { W.ui.status.textContent = String(e.message || e); }
}

function ingestSnapshot(snap) {
  snap.colIndex = Object.fromEntries(snap.cols.map((c, i) => [c, i]));
  snap.playerGuids = new Set(snap.bots.filter(r => !r[snap.colIndex.bot]).map(r => r[snap.colIndex.guid]));
  W.snap = snap;
  W.counts = {};
  for (const r of snap.bots) W.counts[r[snap.colIndex.map]] = (W.counts[r[snap.colIndex.map]] || 0) + 1;
  fillMapSelect(snap);
  redrawBots();
  if (worldScene()) updateLod();
  const lost = allWorld() && W.on.dungeons && optionApplies('dungeons') && W.notPlaced ? ' / not placed: ' + W.notPlaced : '';
  const age = snap.live ? 'live' : 'not live (server stopped or positions log off)';
  const where = W.info && W.info.name ? W.info.name : '';
  W.ui.status.textContent = `${where}${where ? ' · ' : ''}${snap.core} · ${age} · ${worldScene() ? W.markers.size : snap.bots.length} here · ${snap.newest ? snap.newest.slice(11) : 'no data yet'}${lost}`;
  if (W.selected) refreshSelected();
}

function fillMapSelect(snap) {
  const sel = W.ui.mapSelect;
  const live = snap.maps.filter(m => m.bots || m.players);
  const liveIds = new Set(live.map(m => m.id));
  const names = new Map((snap.known || []).map(m => [m.id, m.name]));
  for (const m of snap.maps) names.set(m.id, m.name);
  if (!allWorld() && !names.has(W.map)) names.set(W.map, W.info.name);
  const idle = [...names.keys()].filter(id => !liveIds.has(id)).sort((a, b) => a - b);
  const total = snap.maps.reduce((n, m) => n + m.bots, 0);
  const sig = JSON.stringify([W.map, total, live.map(m => [m.id, m.bots, m.players]), idle]);
  if (sig === W.mapSig) return;
  W.mapSig = sig;
  sel.textContent = '';
  const opt = (value, text) => el('option', { value, selected: value === W.map ? 'selected' : null }, text);
  sel.append(opt('world', `World + dungeons (${total})`));
  if (live.length) sel.append(el('optgroup', { label: 'Maps with bots' }, live.slice().sort((a, b) => b.bots - a.bots || a.id - b.id)
    .map(m => opt(m.id, `${m.name} (${m.bots}${m.players ? ', ' + m.players + ' players' : ''})`))));
  if (idle.length) sel.append(el('optgroup', { label: 'No bots (by map id)' }, idle.map(id => opt(id, `${id} · ${names.get(id)}`))));
}

function redrawBots() {
  if (!W.snap) return;
  const seen = new Set();
  const multiFloor = !worldScene() && pickBase().floors.length > 1;
  W.notPlaced = 0;
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
    if (!pos) { seen.delete(guid); W.notPlaced += 1; continue; }
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
  if (!W.on.events) return;
  const rows = await wapi(`live/events?core=${W.core}&map=${apiMap()}&seconds=1800&kinds=death,levelup,quest,pvp`);
  W.layers.events.clearLayers();
  for (const e of rows) {
    const [c, label] = EVENT_STYLE[e.kind] || EVENT_STYLE.other;
    const pos = bpos(e.map === undefined ? W.map : e.map, e.x, e.y);
    if (!pos) continue;
    L.circleMarker(pos, { radius: e.kind === 'death' ? 5 : 4, color: '#000', weight: 1, fillColor: c, fillOpacity: 0.8, renderer: W.renderer })
      .bindTooltip(`${e.t.slice(11)} ${e.bot} ${label}${e.kind === 'levelup' ? ' ' + e.level : ''}${e.detail ? ' · ' + e.detail : ''}`).addTo(W.layers.events);
  }
}

function styleVisibility() {
  if (!W.ui || !W.ui.styleRow) return;
  W.ui.npcSelect.style.display = W.on.npcs ? '' : 'none';
  W.ui.pathSelect.style.display = W.on.nodes ? '' : 'none';
  W.ui.styleRow.style.display = W.on.npcs || W.on.nodes ? '' : 'none';
}

async function loadZoneLevels() {
  const rows = await wapi(`layer?core=${W.core}&map=${apiMap()}&kind=zonelevels&left=0&right=0&top=0&bottom=0`);
  const layer = W.layers.zonelv;
  layer.clearLayers();
  W.zoneLabels = null;
  W.zoneLevels = rows;
  for (const z of rows) {
    const c = z.level ? levelColor(z.level) : '#8395a7';
    const tip = `${z.name}${z.level ? ' · level ' + z.level : ''}`;
    const pts = z.hull.map(p => bpos(z.map === undefined ? W.map : z.map, p[0], p[1])).filter(Boolean);
    if (pts.length >= 3) L.polygon(pts, { color: c, weight: 1, fillColor: c, fillOpacity: 0.22, renderer: W.renderer }).bindTooltip(tip, { sticky: true }).addTo(layer);
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
    if (!z.level || !z.center) continue;
    const pos = bpos(z.map === undefined ? W.map : z.map, z.center[0], z.center[1]);
    if (!pos || !view.contains(pos)) continue;
    L.marker(pos, { icon: L.divIcon({ className: 'zlabel', html: '<span>' + z.level + '</span>', iconSize: null }), interactive: false }).addTo(W.zoneLabels);
  }
}

function refreshViewportLayers() { for (const k of ['npcs', 'objects', 'nodes', 'tele']) if (W.on[k]) loadViewportLayer(k); }

async function loadIntents() {
  const rows = await wapi(`live/intents?core=${W.core}&map=${apiMap()}&seconds=900`);
  const layer = W.layers.intents;
  layer.clearLayers();
  for (const r of rows) {
    const c = INTENT_COLORS[r.type] || '#dfe6e9';
    const tip = `${r.t.slice(11)} ${r.bot} (${r.level}) · ${r.purpose}${r.to ? '' : ' · ' + (r.to_name ? 'on ' + r.to_name : 'elsewhere')} · ${r.dist}y`;
    const map = r.map === undefined ? W.map : r.map;
    const from = bpos(map, r.from[0], r.from[1]);
    if (!from) continue;
    if (r.to) {
      const to = bpos(map, r.to[0], r.to[1]);
      L.polyline([from, to], { color: c, weight: 2, opacity: 0.75, dashArray: '6 4', renderer: W.renderer }).bindTooltip(tip).addTo(layer);
      L.circleMarker(to, { radius: 4, color: '#000', weight: 1, fillColor: c, fillOpacity: 1, renderer: W.renderer }).bindTooltip(tip).addTo(layer);
    } else {
      L.circleMarker(from, { radius: 6, color: c, weight: 2, fill: false, renderer: W.renderer }).bindTooltip(tip).addTo(layer);
    }
  }
}

async function loadViewportLayer(key) {
  const b = boundsParams();
  const kind = { npcs: 'creatures', objects: 'objects', nodes: 'nodes', tele: 'telecache' }[key];
  const q = new URLSearchParams({ core: W.core, map: apiMap(), kind, ...b });
  if (worldScene()) {
    q.set('mode', W.placeMode); q.set('honest', W.honest ? '1' : '0'); q.set('dungeons', W.on.dungeons ? '1' : '0');
    if (W.focus !== null) q.set('focus', W.focus);
  }
  const d = await wapi('layer?' + q.toString());
  const scene = !!d.scene;
  const at = (map, x, y) => bpos(scene ? map : W.map, x, y);
  const rows = list => (list || []).map(r => (Array.isArray(r)
    ? { map: scene ? r[0] : W.map, v: scene ? r.slice(1) : r }
    : { map: r.map === undefined || r.map === null ? W.map : r.map, v: r }));
  const bins = rows(d.bins);
  const max = Math.max(1, ...bins.map(o => o.v[2]));
  const layer = W.layers[key];
  layer.clearLayers();
  if (key === 'nodes') {
    for (const o of rows(d.links)) {
      const link = o.v;
      let color = LINK_COLORS[link.type] || '#fff';
      let tip = null;
      if (W.pathColor !== 'type' && link.type === 1) {
        const dz = link.danger || [0, 0, 0];
        const lvl = Math.max(dz[0], W.pathColor === 'alliance' ? dz[2] : dz[1]);
        color = lvl ? levelColor(lvl) : '#576574';
        tip = lvl ? `toughest creature for ${W.pathColor === 'alliance' ? 'Alliance' : 'Horde'}: level ${lvl}` : 'no hostile creatures recorded';
      }
      const pts = link.points.map(p => at(o.map, p[0], p[1])).filter(Boolean);
      if (pts.length < 2) continue;
      const line = L.polyline(pts, { color, weight: link.type === 1 ? (W.pathColor === 'type' ? 2 : 3) : 1, opacity: 0.8, renderer: W.renderer });
      if (tip) line.bindTooltip(tip, { sticky: true });
      line.addTo(layer);
    }
    for (const o of rows(d.nodes)) {
      const n = o.v;
      const pos = at(o.map, n.x, n.y);
      if (pos) L.circleMarker(pos, { radius: 3, color: '#000', weight: 1, fillColor: '#fff', fillOpacity: 1, renderer: W.renderer }).bindTooltip(n.name).addTo(layer);
    }
    return;
  }
  if (key === 'tele') {
    for (const o of bins) {
      const [x, y, n, lvl] = o.v;
      const pos = at(o.map, x, y);
      if (pos) L.circleMarker(pos, { radius: 3 + 8 * Math.sqrt(n / max), color: levelColor(lvl), weight: 1, fillColor: levelColor(lvl), fillOpacity: 0.3, renderer: W.renderer })
        .bindTooltip(`${n} teleport spots · avg level ${lvl} (zoom in for detail)`).addTo(layer);
    }
    for (const o of rows(d.features)) {
      const [x, y, lvl, k] = o.v;
      const pos = at(o.map, x, y);
      if (pos) L.circleMarker(pos, { radius: k === 'rpg' ? 3 : 2, color: '#000', weight: k === 'rpg' ? 1 : 0, fillColor: levelColor(lvl), fillOpacity: 0.8, renderer: W.renderer })
        .bindTooltip(`teleport spot · level ${lvl} · ${k === 'rpg' ? 'roleplay' : 'levelling'}`).addTo(layer);
    }
    return;
  }
  for (const o of bins) {
    const [x, y, n] = o.v;
    const pos = at(o.map, x, y);
    if (pos) L.circleMarker(pos, { radius: 3 + 9 * Math.sqrt(n / max), color: key === 'npcs' ? '#ff9ff3' : '#feca57', weight: 1, fillOpacity: 0.25, renderer: W.renderer })
      .bindTooltip(`${n} ${key === 'npcs' ? 'NPCs' : 'objects'} (zoom in for detail)`).addTo(layer);
  }
  for (const o of rows(d.features)) {
    const f = o.v;
    const pos = at(o.map, f[2], f[3]);
    if (!pos) continue;
    if (key === 'npcs') {
      const [, entry, , , name, level, role, reaction, maxLevel] = f;
      const fill = W.npcColor === 'level' ? levelColor(maxLevel) : W.npcColor === 'reaction' ? (REACTION_COLORS[reaction] || '#8395a7') : (ROLE_COLORS[role] || '#8395a7');
      const plain = W.npcColor === 'role' && role === 'mob';
      L.circleMarker(pos, { radius: plain ? 2 : (role === 'mob' ? 3 : 4), color: '#000', weight: plain ? 0 : 1, fillColor: fill, fillOpacity: plain ? 0.45 : 0.9, renderer: W.renderer })
        .bindTooltip(`${name} (${level})${role !== 'mob' ? ' · ' + role : ''} · ${reaction} · ${entry}`).addTo(layer);
    } else {
      const [, entry, , , name, type] = f;
      L.circleMarker(pos, { radius: 3, color: '#000', weight: 1, fillColor: GO_COLORS[type] || '#8395a7', fillOpacity: 0.9, renderer: W.renderer })
        .bindTooltip(`${name} · ${type} · ${entry}`).addTo(layer);
    }
  }
}

function closeSheet() {
  W.selected = null; W.follow = false;
  if (W.ui && W.ui.fab) W.ui.fab.classList.remove('hidden');
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
  try { card = await wapi(`live/bot?core=${W.core}&guid=${guid}`); } catch (e) { W.ui.sheet.classList.remove('hidden'); W.ui.sheet.textContent = 'This bot is not online any more.'; return; }
  if (W.selected !== guid) return;
  if (worldScene() ? (W.focus !== null && !placeOf(card.map)) : card.map !== W.map) { go(`/w/${W.core}/${card.map}?bot=${guid}`); return; }
  W.layers.sel.clearLayers();
  const here = bpos(card.map, card.x, card.y);
  drawTrail(card);
  if (here) L.circleMarker(here, { radius: 11, color: '#28c8ff', weight: 3, fill: false }).addTo(W.layers.sel);
  if (here && (center || W.follow)) mapObj.setView(here, Math.max(mapObj.getZoom(), center ? (allWorld() ? -3 : -1) : mapObj.getZoom()));
  if (W.ui.fab) W.ui.fab.classList.add('hidden');
  renderCard(card);
}

function drawTrail(card) {
  if (!card.trail || card.trail.length < 2) return;
  const pts = [];
  for (const p of card.trail) {
    const map = p.length > 3 ? p[3] : card.map;
    if (!worldScene() && map !== card.map) continue;
    const pos = bpos(map, p[0], p[1]);
    if (pos) pts.push({ pos, map });
  }
  const solid = pts => L.polyline(pts, { color: '#28c8ff', weight: 3, opacity: 0.85 }).addTo(W.layers.sel);
  let run = [];
  for (let i = 0; i < pts.length; i++) {
    if (i && pts[i].map !== pts[i - 1].map) {
      if (worldScene()) L.polyline([pts[i - 1].pos, pts[i].pos], { color: '#28c8ff', weight: 2, opacity: 0.6, dashArray: '3 5' }).addTo(W.layers.sel);
      if (run.length > 1) solid(run.map(p => p.pos));
      run = [];
    }
    run.push(pts[i]);
  }
  if (run.length > 1) solid(run.map(p => p.pos));
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
