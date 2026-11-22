'use strict';

const state = { core: store('core') || '', verdict: '', family: '', bucket: '', preview: '', flipped: false, q: '', offset: 0 };
const view = document.getElementById('view');
let summaryCache = null;
let capsCache = null;
let mapObj = null;

function store(k, v) {
  try {
    if (v === undefined) return localStorage.getItem('lm_' + k);
    localStorage.setItem('lm_' + k, v);
  } catch (e) { return null; }
}

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
}

async function api(path, opts) {
  const r = await fetch('/api/' + path, opts);
  if (r.status === 401) throw new Error('Not signed in on this device: open the access link once.');
  const data = await r.json();
  if (data && data.error) throw new Error(data.error);
  return data;
}

function add(...kids) { view.append(...kids.flat().filter(k => k !== null && k !== undefined && k !== false)); }

function go(path) { history.pushState({}, '', path); route(); }
document.addEventListener('click', e => {
  const a = e.target.closest('a[data-link]');
  if (a && a.origin === location.origin) { e.preventDefault(); go(a.pathname + a.search + a.hash); }
});
window.addEventListener('popstate', route);

function verdict(v) { return el('span', { class: 'v ' + v }, v); }
function short(ts) { return ts ? ts.slice(5, 16) : ''; }
function prettyTest(t) { return t.replace(/_/g, ' '); }
function testLink(core, test) { return `/t/${encodeURIComponent(core)}/${encodeURIComponent(test)}`; }

function capsOf(core) { return capsCache && capsCache.cores ? capsCache.cores[core] : null; }
function mapCores() { return Object.keys((capsCache && capsCache.cores) || {}).filter(c => capsOf(c).features.map); }
function testCores() {
  if (capsCache && capsCache.failed) return summaryCache ? summaryCache.cores : [];
  return Object.keys((capsCache && capsCache.cores) || {}).filter(c => capsOf(c).features.tests);
}

function helpDot() {
  for (const c of Object.values((capsCache && capsCache.cores) || {})) {
    const next = c.ladder.find(l => !l.ok);
    if (!next) continue;
    if (c.caps.some(x => x.level === next.level && x.status !== 'ok' && x.fix.some(f => f.action || f.snippet))) return true;
  }
  return false;
}

function topBar(kind) {
  const set = (id, on) => { const n = document.getElementById(id); if (n) n.classList.toggle('on', on); };
  const tests = document.getElementById('navtests');
  if (tests) tests.classList.toggle('hidden', !testCores().length);
  set('navmap', kind === 'map'); set('navtests', kind === 'tests'); set('navhelp', kind === 'help');
  const dot = document.getElementById('navdot');
  if (dot) dot.classList.toggle('hidden', !helpDot());
  coreChips(kind);
}

function coreChips(kind) {
  const nav = document.getElementById('corechips');
  nav.textContent = '';
  if (kind === 'help') return;
  if (kind === 'map') {
    const cores = mapCores();
    if (cores.length < 2) return;
    const current = typeof W !== 'undefined' && W.core ? W.core : store('mapcore');
    for (const c of cores) nav.append(el('span', { class: 'chip' + (current === c ? ' on' : ''), onclick: () => {
      store('mapcore', c); go(`/w/${encodeURIComponent(c)}`);
    } }, c));
    return;
  }
  const cores = testCores();
  for (const c of [''].concat(cores)) {
    nav.append(el('span', { class: 'chip' + (state.core === c ? ' on' : ''), onclick: () => {
      state.core = c; store('core', c); state.offset = 0; summaryCache = null; route();
    } }, c || 'all cores'));
  }
}

function defaultCore() {
  const cores = mapCores();
  const saved = store('mapcore');
  if (saved && cores.includes(saved)) return saved;
  const live = cores.filter(c => capsOf(c).features.live);
  const players = cores.filter(c => capsOf(c).features.players);
  return live[0] || players[0] || cores[0] || null;
}

function showError(err) {
  view.textContent = '';
  add(el('div', { class: 'notice' }, String(err.message || err)));
}

async function route() {
  if (typeof stopWorld === 'function') stopWorld();
  if (location.pathname !== '/help') clearInterval(helpTimer);
  if (mapObj) { mapObj.remove(); mapObj = null; }
  const p = location.pathname;
  try {
    if (!capsCache) {
      try { capsCache = await api('capabilities'); } catch (e) { capsCache = { cores: {}, failed: String(e.message || e) }; }
    }
    if (state.core && capsCache.cores[state.core] && !testCores().includes(state.core)) { state.core = ''; summaryCache = null; }
    let m;
    if (p === '/help') { topBar('help'); return await helpPage(); }
    if (p === '/settings') { topBar('help'); return await settingsPage(); }
    if (p === '/' || p === '/w' || p === '/w/') {
      const core = defaultCore();
      if (!core) { history.replaceState({}, '', '/help'); topBar('help'); return await helpPage(); }
      topBar('map');
      await worldPage(core, null);
      return coreChips('map');
    }
    if ((m = p.match(/^\/w\/([^/]+)(?:\/(\d+|world|atlas))?$/))) {
      topBar('map');
      await worldPage(decodeURIComponent(m[1]), m[2] === undefined || m[2] === 'atlas' ? null : (m[2] === 'world' ? 'world' : +m[2]));
      return coreChips('map');
    }
    if (!summaryCache) summaryCache = await api('summary' + (state.core ? '?core=' + state.core : ''));
    topBar('tests');
    if ((m = p.match(/^\/r\/(\d+)$/))) return await runPage(+m[1]);
    if ((m = p.match(/^\/t\/([^/]+)\/(.+)$/))) return await testPage(decodeURIComponent(m[1]), decodeURIComponent(m[2]));
    if (p === '/clusters') return await clustersPage();
    return await homePage();
  } catch (e) { showError(e); }
}

function tiles() {
  const by = {};
  for (const r of summaryCache.summary.verdicts) (by[r.core] = by[r.core] || {})[r.verdict] = r.n;
  const tests = Object.fromEntries(summaryCache.summary.tests.map(t => [t.core, t]));
  return el('div', { class: 'tiles' }, Object.keys(by).filter(c => !state.core || c === state.core).map(c =>
    el('div', { class: 'tile' },
      el('h3', {}, c, el('span', { class: 'badge' }, (tests[c] || {}).n + ' tests')),
      el('div', { class: 'nums' }, ['PASS', 'FAIL', 'ABORT', 'IMPOSSIBLE'].filter(v => by[c][v]).map(v => el('span', {}, verdict(v), ' ', by[c][v]))),
      el('div', { class: 'sub' }, 'last run ' + short((tests[c] || {}).last)))));
}

function transitionList(title, rows, cls) {
  if (!rows.length) return null;
  return el('div', {}, el('h2', {}, title), el('div', { class: 'list' }, rows.map(r =>
    el('a', { class: 'row', href: `/r/${r.id}`, 'data-link': 1 },
      el('div', { class: 'name' }, verdict(r.verdict), ' ', prettyTest(r.test)),
      el('div', { class: 'meta' }, `${r.core} · was ${r.prev} · ${short(r.ts)} · ${r.host}`)))));
}

function chipRow(options, key) {
  return el('div', { class: 'chips' }, options.map(([val, label]) => el('span', {
    class: 'chip' + (state[key] === val ? ' on' : ''),
    onclick: () => { state[key] = state[key] === val && val !== '' ? '' : val; state.offset = 0; homePage(); },
  }, label)));
}

async function homePage() {
  const q = new URLSearchParams();
  for (const k of ['core', 'verdict', 'family', 'bucket', 'preview', 'q']) if (state[k]) q.set(k, state[k]);
  if (state.flipped) q.set('flipped', '1');
  q.set('limit', 60); q.set('offset', state.offset);
  const families = await api('families');
  const famNames = [...new Set(families.filter(f => !state.core || f.core === state.core).map(f => f.family))];
  const rows = await api('tests?' + q.toString());
  view.textContent = '';
  const search = el('input', { type: 'search', placeholder: 'search tests (e.g. 8502 clear, molten core)', value: state.q });
  let timer = null;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); state.offset = 0; homePage(); }, 350); });
  add(
    tiles(),
    transitionList('Flipped to PASS', summaryCache.flips.slice(0, 5)),
    transitionList('Regressions (PASS → FAIL)', summaryCache.regressions.slice(0, 5)),
    el('div', { class: 'tabs' }, el('a', { class: 'chip on', href: '/tests', 'data-link': 1 }, 'Tests'),
      el('a', { class: 'chip', href: '/clusters', 'data-link': 1 }, 'Failure groups')),
    search,
    chipRow([['', 'any verdict'], ['PASS', 'pass'], ['FAIL', 'fail'], ['ABORT', 'abort'], ['IMPOSSIBLE', 'impossible']], 'verdict'),
    chipRow([['', 'all families']].concat(famNames.map(f => [f, f])), 'family'),
    el('div', { class: 'chips' },
      el('span', { class: 'chip' + (state.flipped ? ' on' : ''), onclick: () => { state.flipped = !state.flipped; state.offset = 0; homePage(); } }, 'just flipped'),
      ...['bug', 'fluke', 'gap', 'fixed'].map(b => el('span', { class: 'chip' + (state.bucket === b ? ' on' : ''),
        onclick: () => { state.bucket = state.bucket === b ? '' : b; state.offset = 0; homePage(); } }, 'triage: ' + b)),
      ...[['yes', 'with map'], ['no', 'no map']].map(([v, label]) => el('span', { class: 'chip' + (state.preview === v ? ' on' : ''),
        onclick: () => { state.preview = state.preview === v ? '' : v; state.offset = 0; homePage(); } }, label))),
    el('div', { class: 'list' }, rows.map(testRow)),
    rows.length === 60 ? el('button', { class: 'ghost more', onclick: () => { state.offset += 60; homePage(); } }, 'next page') : null,
    state.offset ? el('button', { class: 'ghost more', onclick: () => { state.offset = Math.max(0, state.offset - 60); homePage(); } }, 'previous page') : null,
  );
  if (document.activeElement !== search && state.q) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); }
}

let mapSectionNode = null;
let mapSectionNotice = null;

async function mapSetting(key, value) {
  if (mapSectionNotice) mapSectionNotice.textContent = 'saving ...';
  try {
    const d = await api('settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ section: 'map', key, value }) });
    summaryCache = null;
    if (mapSectionNotice) mapSectionNotice.textContent = '';
    if (d.map) renderMapSection(d.map);
  } catch (e) {
    if (mapSectionNotice) mapSectionNotice.textContent = String(e.message || e);
  }
}

async function deleteCache(core) {
  if (mapSectionNotice) mapSectionNotice.textContent = 'deleting ...';
  try {
    const d = await api('mapcache', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ core }) });
    if (mapSectionNotice) mapSectionNotice.textContent = `freed ${d.freed_mb} MB`;
    if (d.map) renderMapSection(d.map);
  } catch (e) {
    if (mapSectionNotice) mapSectionNotice.textContent = String(e.message || e);
  }
}

function renderMapSection(map) {
  if (!mapSectionNode) return;
  mapSectionNode.textContent = '';
  mapSectionNode.append(...mapSectionBlocks(map));
}

function mapSectionBlocks(map) {
  const v = map.values || {};
  const def = map.defaults || {};
  const isOn = key => v[key] === '1';
  const chip = (label, active, run) => el('span', { class: 'chip' + (active ? ' on' : ''), onclick: run }, label);
  const numberRow = (key, label, hint) => {
    const input = el('input', { type: 'search', inputmode: 'numeric', value: v[key] || '', style: 'width:90px;display:inline-block' });
    return el('div', { class: 'row' }, el('div', { class: 'name' }, label), el('div', { class: 'meta' }, hint),
      el('div', {}, input, ' ', el('button', { class: 'ghost', onclick: () => mapSetting(key, input.value.trim()) }, 'save')));
  };
  const usage = map.usage || { total_mb: 0, cores: {} };
  const cores = Object.keys(map.clients || {});
  const saved = Object.entries(usage.cores || {}).map(([k, n]) => `${k} ${n} MB`).join(', ');
  const orderInput = el('input', { type: 'search', value: v.fallback_order || '', style: 'width:170px;display:inline-block' });
  const blocks = [
    el('h2', {}, 'Map'),
    el('p', { class: 'sub' }, 'Close zoom is drawn from the game client terrain files, on demand. The minimap still covers zoomed-out views.'),
    el('div', { class: 'row' },
      el('div', { class: 'name' }, 'Keep a copy on disk'),
      el('div', { class: 'meta' }, `using ${usage.total_mb} MB of ${v.cache_mb || def.cache_mb} MB` + (saved ? ' · ' + saved : '')),
      el('div', { class: 'chips' },
        chip(isOn('keep_cache') ? 'cache on' : 'cache off', isOn('keep_cache'), () => mapSetting('keep_cache', isOn('keep_cache') ? '0' : '1')),
        ...(usage.total_mb ? [chip('delete saved cache', false, () => deleteCache(null)),
          ...cores.filter(c => usage.cores[c]).map(c => chip('delete ' + c, false, () => deleteCache(c)))] : []))),
    numberRow('cache_mb', 'Cache size cap (MB)', 'oldest entries are dropped when the cache grows past this'),
    el('h2', {}, 'Ground shading'),
    el('div', { class: 'row' }, el('div', { class: 'name' }, 'default for new viewers'),
      el('div', { class: 'meta' }, 'the map itself has a quick switch; every viewer remembers its own choice'),
      el('div', { class: 'chips' }, ...(map.shades || ['soft']).map(name => chip(name, v.shading === name, () => mapSetting('shading', name))))),
    numberRow('render_workers', 'Render workers', 'how many ground tiles are drawn at once (1-8)'),
    el('h2', {}, 'Fallback client'),
    el('div', { class: 'row' }, el('div', { class: 'name' }, 'use another core client'),
      el('div', { class: 'meta' }, 'when the viewed core has no client, or its client has no terrain for that map'),
      el('div', { class: 'chips' }, chip(isOn('fallback') ? 'fallback on' : 'fallback off', isOn('fallback'),
        () => mapSetting('fallback', isOn('fallback') ? '0' : '1'))),
      el('div', { class: 'meta' }, 'order (first that can read the map wins)'),
      el('div', {}, orderInput, ' ', el('button', { class: 'ghost', onclick: () => mapSetting('fallback_order', orderInput.value.trim()) }, 'save order'))),
    el('h2', {}, 'Client data folders'),
    el('p', { class: 'sub' }, 'the Data folder that holds the game MPQ files'),
  ];
  for (const core of cores) {
    const info = map.clients[core];
    const input = el('input', { type: 'search', value: info.path || '', placeholder: 'path to the Data folder' });
    const result = el('div', { class: 'meta' },
      !info.path ? 'no client configured' : (info.exists ? 'folder found' : 'folder missing'));
    const check = async () => {
      result.textContent = 'checking ...';
      try {
        const r = await api('clientcheck', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ core, path: input.value.trim() }) });
        result.textContent = r.ok
          ? `ok · ${r.archives} archives · ${r.locale || 'locale unknown'} · read ${r.sample} in ${r.ms} ms`
          : `failed · ${r.error || 'no terrain file could be read from it'}`;
      } catch (e) { result.textContent = String(e.message || e); }
    };
    blocks.push(el('div', { class: 'row' },
      el('div', { class: 'name' }, core),
      el('div', { class: 'meta' }, info.configured ? 'set here' : 'from livemap.toml'),
      el('div', {}, input),
      el('div', { class: 'chips' },
        chip('check', false, check),
        chip('save', false, () => mapSetting('client.' + core, input.value.trim())),
        chip('no client', false, () => mapSetting('client.' + core, '-')),
        chip('reset', false, () => mapSetting('client.' + core, ''))),
      result));
  }
  blocks.push(el('p', { class: 'sub' },
    `renderer v${map.version} · RAM now: ${(map.ram || {}).adt_mb || 0} MB terrain, ${(map.ram || {}).render_mb || 0} MB tiles`));
  return blocks;
}

const LEVEL_NAMES = { L0: 'Maps', L1: 'Your database', L2: 'Bots', L3: 'Bot exports', L4: 'Live map', L5: 'Bot tests' };
const STATUS_TEXT = { ok: 'works', degraded: 'works, approximate', missing: 'not set up', error: 'problem' };

function copyButton(text) {
  const b = el('button', { class: 'ghost copy' }, 'copy');
  b.onclick = async () => {
    try { await navigator.clipboard.writeText(text); b.textContent = 'copied'; }
    catch (e) { b.textContent = 'select and copy by hand'; }
    setTimeout(() => { b.textContent = 'copy'; }, 2000);
  };
  return b;
}

function fixSteps(core, cap, canWrite) {
  return el('ol', { class: 'fix' }, cap.fix.map(f => el('li', {},
    el('div', {}, f.text),
    f.snippet ? el('div', { class: 'snippet' }, el('code', {}, f.snippet), copyButton(f.snippet)) : null,
    f.file ? el('div', { class: 'meta' }, f.file.endsWith('.toml') ? `in the [[core]] entry named "${core}" of ` : 'in ', el('code', {}, f.file)) : null,
    f.action && f.action.enable && typeof enableButton === 'function' ? enableButton(core, f.action.enable, canWrite) : null,
    f.setting ? el('div', { class: 'meta' }, el('a', { href: '#settings' }, 'change it below under Settings')) : null)));
}

const EXPORT_LABELS = { live: 'live positions + test runs', travel: 'travel layers', events: 'deaths, quests and level-ups', zones: 'zone levels' };
let helpTimer = null;

function post(path, body) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

function capsSignature(c) {
  return JSON.stringify(Object.values((c && c.cores) || {}).map(r => r.caps.map(x => x.status + x.detail)));
}

function watchHelp() {
  clearInterval(helpTimer);
  helpTimer = setInterval(async () => {
    if (location.pathname !== '/help' || document.visibilityState !== 'visible') return;
    try {
      const fresh = await api('capabilities?refresh=1');
      if (capsSignature(fresh) !== capsSignature(capsCache)) { capsCache = fresh; helpPage(); }
    } catch (e) { }
  }, 15000);
}

function enableButton(core, group, canWrite) {
  const out = el('div', { class: 'meta' });
  if (!canWrite) return el('div', { class: 'meta' }, 'One-click enable works from the computer livemap runs on.');
  const btn = el('button', { class: 'ghost enable' }, 'Enable ' + (EXPORT_LABELS[group] || group));
  btn.onclick = async () => {
    btn.disabled = true; out.textContent = 'writing aiplayerbot.conf ...';
    try {
      const r = await post('enable', { core, group });
      out.textContent = (r.added.length ? 'Added ' + r.added.join(', ') + ' (old file kept as aiplayerbot.conf.bak). ' : 'Already listed. ')
        + 'Restart the world server to start logging; this card turns green by itself once the first file appears.';
      watchHelp();
    } catch (e) { out.textContent = String(e.message || e); btn.disabled = false; }
  };
  return el('div', { class: 'enablebox' }, btn,
    group === 'live' ? el('div', { class: 'meta' }, 'Disk use: about 1 GB a day at 3000 bots. Turn on "Keep old logs at boot" below to keep old boots in check.') : null,
    out);
}

function setupForm(first) {
  const box = el('div', { class: 'tile setup' });
  const field = (label, hint, value) => {
    const input = el('input', { type: 'search', value: value || '', placeholder: hint });
    return [input, el('label', { class: 'setuprow' }, el('div', { class: 'name' }, label), input)];
  };
  const [name, nameRow] = field('Name', 'a short label, e.g. wotlk or myserver');
  const [client, clientRow] = field('Game client Data folder', 'the folder with the .MPQ files, e.g. C:/Games/WoW/Data');
  const [conf, confRow] = field('mangosd.conf (optional)', 'your world server config; gives players, NPCs and bots');
  const [logs, logsRow] = field('Logs folder (optional)', 'only if it is not the LogsDir from mangosd.conf');
  const clientChips = el('div', { class: 'chips' });
  const confChips = el('div', { class: 'chips' });
  const result = el('div', {});
  const body = () => ({ name: name.value.trim(), client: client.value.trim(), mangosd_conf: conf.value.trim(), logs: logs.value.trim() });
  const fill = d => {
    if (!name.value) name.value = d.suggest || '';
    clientChips.textContent = '';
    for (const c of d.clients) clientChips.append(el('span', { class: 'chip', title: c, onclick: () => { client.value = c; } }, c));
    if (d.scanning) clientChips.append(el('span', { class: 'meta' }, ' looking for game clients on your drives ...'));
    else if (!d.clients.length) clientChips.append(el('span', { class: 'meta' }, 'no game client found automatically; type the path'));
    confChips.textContent = '';
    for (const c of d.confs) confChips.append(el('span', { class: 'chip', title: c, onclick: () => { conf.value = c; } }, c));
    return d.scanning;
  };
  const load = async () => {
    try { if (fill(await api('setup/detect'))) setTimeout(() => { if (box.isConnected) load(); }, 4000); } catch (e) { }
  };
  const test = el('button', { class: 'ghost' }, 'test');
  const save = el('button', {}, first ? 'save and open the map' : 'add this server');
  test.onclick = async () => {
    result.textContent = 'checking ...';
    try {
      const r = await post('setup/check', body());
      result.textContent = '';
      result.append(el('div', { class: 'ladder' }, r.ladder.map(l => el('span', { class: 'step' + (l.ok ? ' ok' : ''), title: l.text }, l.level + ' ' + LEVEL_NAMES[l.level]))),
        el('div', { class: 'sub' }, r.level ? `This setup reaches ${r.level}: ${r.ladder.find(l => l.level === r.level).text}.` : 'Nothing works with these paths yet.'),
        el('div', { class: 'list' }, r.caps.filter(c => ['client', 'dbc', 'world_db', 'char_db', 'bot_module'].includes(c.id)).map(c =>
          el('div', { class: 'row' }, el('div', { class: 'name' }, c.label, el('span', { class: 'badge st-' + c.status }, STATUS_TEXT[c.status])), el('div', { class: 'meta' }, c.detail)))),
        r.derived.logs ? el('div', { class: 'meta' }, 'logs: ' + r.derived.logs) : null);
    } catch (e) { result.textContent = String(e.message || e); }
  };
  save.onclick = async () => {
    result.textContent = 'saving ...';
    try {
      await post('setup/save', body());
      capsCache = null;
      go('/');
    } catch (e) { result.textContent = String(e.message || e); }
  };
  box.append(
    el('div', { class: 'sub' }, first
      ? 'Tell livemap where your game client and server are. Only the client is needed to browse the maps; mangosd.conf adds players, NPCs and bots.'
      : 'Add another server (or another expansion) to this map.'),
    nameRow, clientRow, clientChips, confRow, confChips,
    el('details', {}, el('summary', { class: 'meta' }, 'more options'), logsRow),
    el('div', { class: 'chips' }, test, save), result);
  load();
  return box;
}

function unlocks(report, capId) {
  return Object.entries(report.layers).filter(([, l]) => l.status !== 'ok' && l.needs.includes(capId)).map(([k, l]) => [k, l.label]);
}

async function helpPage(refresh) {
  if (refresh || !capsCache) capsCache = await api('capabilities' + (refresh ? '?refresh=1' : ''));
  view.textContent = '';
  const cores = Object.entries(capsCache.cores || {});
  const canWrite = capsCache.can_write;
  add(el('h1', {}, 'Help and setup'),
    el('p', { class: 'sub' }, 'What this map can show for each server, where each part comes from, and how to unlock the rest. ',
      el('button', { class: 'ghost', onclick: () => helpPage(true) }, 're-check')));
  if (capsCache.failed) add(el('div', { class: 'notice' }, 'Could not check the servers: ' + capsCache.failed));
  if (!cores.length && !capsCache.failed) {
    add(el('h2', {}, 'Set up'), canWrite ? setupForm(true)
      : el('div', { class: 'notice' }, 'No server is set up yet. Open this page on the computer livemap runs on to set it up.'));
    return;
  }
  add(el('h2', {}, 'Your server at a glance'));
  for (const [name, r] of cores) {
    const ladder = el('div', { class: 'ladder' }, r.ladder.map(l => el('span', { class: 'step' + (l.ok ? ' ok' : ''), title: l.text },
      l.level + ' ' + LEVEL_NAMES[l.level])));
    const working = Object.entries(r.layers).filter(([, l]) => l.status !== 'missing');
    add(el('div', { class: 'tile helpcore' },
      el('h3', {}, name, el('span', { class: 'badge' }, [r.flavor, r.expansion].filter(Boolean).join(' · ') || 'unknown server')),
      ladder,
      el('div', { class: 'sub' }, r.level ? `Reached ${r.level}: ${r.ladder.find(l => l.level === r.level).text}.` : 'Nothing works yet: start with the game client below.'),
      working.length ? el('details', {}, el('summary', {}, `${working.length} map layers work`),
        el('div', { class: 'list' }, working.map(([k, l]) => el('div', { class: 'row' },
          el('div', { class: 'name' }, l.label, l.status === 'degraded' ? el('span', { class: 'badge approxb' }, 'approx.') : null),
          el('div', { class: 'meta' }, 'from ' + (l.source || '?')))))) : null));
  }
  add(el('h2', {}, 'Get more'));
  let cards = 0;
  for (const [name, r] of cores) {
    const order = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5'];
    const todo = r.caps.filter(c => c.status !== 'ok' && c.fix.length && c.id !== 'archive')
      .sort((a, b) => order.indexOf(a.level) - order.indexOf(b.level));
    if (todo.length && cores.length > 1) add(el('h3', {}, name));
    for (const c of todo) {
      cards += 1;
      const gains = unlocks(r, c.id);
      const body = [
        gains.length ? el('div', { class: 'meta' }, 'unlocks: ' + gains.map(g => g[1]).join(', ')) : null,
        el('div', { class: 'msg' }, c.detail),
        fixSteps(name, c, canWrite),
      ];
      const head = [el('span', { class: 'badge lvl' }, c.level), ' ', c.label, el('span', { class: 'badge st-' + c.status }, STATUS_TEXT[c.status]),
];
      const anchors = gains.map(g => el('span', { id: `layer-${name}-${g[0]}`, class: 'anchor' }));
      const card = c.level === 'L5'
        ? el('details', { class: 'row card', id: `cap-${name}-${c.id}` }, el('summary', {}, ...head), ...anchors, ...body)
        : el('div', { class: 'row card', id: `cap-${name}-${c.id}` }, el('div', { class: 'name' }, ...head), ...anchors, ...body);
      add(card);
    }
  }
  if (!cards) add(el('p', { class: 'sub' }, 'Everything is set up. Nothing more to unlock.'));
  if (canWrite) add(el('details', { class: 'addserver' }, el('summary', {}, 'Add another server'), setupForm(false)));
  if (cores.some(([, r]) => r.caps.some(c => c.status !== 'ok' && c.detail.includes('listed but not written')))) watchHelp();
  add(el('h2', { id: 'settings' }, 'Settings'));
  await settingsPage(true);
  if (location.hash) {
    const target = document.getElementById(location.hash.slice(1));
    if (target) {
      const card = target.closest('.card') || target;
      if (card.tagName === 'DETAILS') card.open = true;
      card.classList.add('flash');
      card.scrollIntoView({ block: 'start' });
    }
  }
}

async function settingsPage(embedded) {
  const d = await api('settings');
  if (!embedded) view.textContent = '';
  add(el(embedded ? 'h3' : 'h1', {}, 'Bot settings'),
    el('p', { class: 'sub' }, "Written to each core's live aiplayerbot.conf; they take effect on the next server boot."),
    ...(d.can_write ? [] : [el('div', { class: 'notice' }, 'Read-only from a remote session. Set allow_remote_write = true in livemap.toml to change these from here.')]));
  for (const [core, values] of Object.entries(d.cores)) {
    if (!values) { add(el('h3', {}, core), el('div', { class: 'meta' }, 'no aiplayerbot.conf (PlayerBots not set up for this core)')); continue; }
    add(el('h2', {}, core));
    for (const [key, v] of Object.entries(values)) {
      const current = v.value === null ? v.default : v.value;
      const isBool = key.endsWith('OnBoot');
      const save = async val => {
        await api('settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ core, key, value: val }) });
        settingsPage();
      };
      let control;
      if (isBool) {
        control = el('span', { class: 'chip' + (current === '1' ? ' on' : ''), onclick: () => d.can_write && save(current === '1' ? '0' : '1') }, current === '1' ? 'on' : 'off');
      } else {
        const input = el('input', { type: 'search', inputmode: 'numeric', value: current, style: 'width:90px;display:inline-block' });
        control = el('span', {}, input, ' ', d.can_write ? el('button', { class: 'ghost', onclick: () => save(input.value.trim()) }, 'save') : null);
      }
      add(el('div', { class: 'row' }, el('div', { class: 'name' }, v.label), el('div', { class: 'meta' }, key + (v.value === null ? ' (not set, default ' + v.default + ')' : '')), el('div', {}, control)));
    }
  }
  if (d.map) {
    mapSectionNode = el('div', {});
    mapSectionNotice = el('div', { class: 'sub' });
    add(mapSectionNode, mapSectionNotice);
    if (!d.can_write) add(el('div', { class: 'notice' }, 'Client folders stay read-only from a remote session (allow_remote_write in livemap.toml turns that on); the other map settings can be changed.'));
    renderMapSection(d.map);
  }
}

function testRow(r) {
  const flipped = r.verdict === 'PASS' && ['FAIL', 'ABORT', 'IMPOSSIBLE'].includes(r.prev);
  return el('a', { class: 'row', href: testLink(r.core, r.test), 'data-link': 1 },
    el('div', { class: 'name' }, verdict(r.verdict), ' ', prettyTest(r.test),
      flipped ? el('span', { class: 'badge flip' }, 'flipped') : null,
      r.bucket ? el('span', { class: 'badge ' + r.bucket }, r.bucket) : null,
      r.preview === null || r.preview === undefined ? null
        : el('span', { class: 'badge ' + (r.preview > 0 ? 'map' : 'nomap'), title: r.preview > 0 ? 'the latest run has a track to show on the map' : 'no position data for the latest run' }, r.preview > 0 ? 'map' : 'no map')),
    el('div', { class: 'meta' }, `${r.core} · ${r.runs} runs · ${r.passes} pass · ${short(r.ts)} · ${r.host}`),
    r.verdict !== 'PASS' ? el('div', { class: 'msg' }, r.msg) : null);
}

async function clustersPage() {
  const rows = await api('clusters' + (state.core ? '?core=' + state.core : ''));
  view.textContent = '';
  add(el('div', { class: 'tabs' }, el('a', { class: 'chip', href: '/tests', 'data-link': 1 }, 'Tests'),
    el('a', { class: 'chip on', href: '/clusters', 'data-link': 1 }, 'Failure groups')),
    el('p', { class: 'sub' }, 'Failures grouped by message (numbers ignored). Tap a group to open its newest run.'),
    el('div', { class: 'list' }, rows.map(r => el('a', { class: 'row', href: `/r/${r.example}`, 'data-link': 1 },
      el('div', { class: 'name' }, r.cluster),
      el('div', { class: 'meta' }, `${r.core} · ${r.family} · ${r.n} failures over ${r.tests} tests · last ${short(r.last)}`)))));
}

async function testPage(core, test) {
  const d = await api(`test?core=${encodeURIComponent(core)}&test=${encodeURIComponent(test)}`);
  view.textContent = '';
  const passes = d.history.filter(h => h.verdict === 'PASS').length;
  const quest = d.quest && !d.quest.error ? d.quest : null;
  const note = el('textarea', { placeholder: 'note (why it fails, backlog id, ...)' }, (d.triage && d.triage.note) || '');
  const current = d.triage && d.triage.bucket;
  const saveTriage = async bucket => {
    await api('triage', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ core, test, bucket, note: note.value }) });
    testPage(core, test);
  };
  add(
    el('h1', {}, prettyTest(test)),
    el('div', { class: 'sub' }, `${core} · ${d.history.length} runs · ${passes} pass`),
    quest ? el('div', { class: 'sub' }, `quest ${quest.id}: ${quest.title} (level ${quest.level})`) : null,
    el('h2', {}, 'Triage'),
    el('div', { class: 'triage' }, ['bug', 'fluke', 'gap', 'fixed'].map(b =>
      el('span', { class: 'chip' + (current === b ? ' on' : ''), onclick: () => saveTriage(current === b ? '' : b) }, b))),
    note, el('div', {}, el('button', { class: 'ghost', onclick: () => saveTriage(current) }, 'save note')),
    el('h2', {}, 'History'),
    el('div', { class: 'list' }, d.history.map(h => el('a', { class: 'row', href: `/r/${h.id}`, 'data-link': 1 },
      el('div', { class: 'name' }, verdict(h.verdict), ' ', h.ts, ' · ', h.host),
      el('div', { class: 'msg' }, h.msg)))));
}

const ROLE = { giver: 'var(--giver)', taker: 'var(--taker)', objective: 'var(--objective)' };
const ROLE_HEX = { giver: '#ffd200', taker: '#3cdc5a', objective: '#ff46c8' };
const LINK_COLORS = { 1: '#28c8ff', 2: '#ff9600', 3: '#c800ff', 4: '#ffff00', 5: '#ff5050', 6: '#ffffff' };
const ll = (x, y) => [x, -y];

async function runPage(id) {
  const art = store('wrunart') === 'minimap' ? 'minimap' : 'painted';
  const d = await api('run/' + id + (art === 'minimap' ? '?art=minimap' : ''));
  const r = d.run;
  view.textContent = '';
  const mapDiv = el('div', { id: 'map' });
  const toggles = el('div', { class: 'toggles' });
  add(
    el('h1', {}, verdict(r.verdict), ' ', prettyTest(r.test)),
    el('div', { class: 'sub' }, `${r.core} · ${r.ts} · host ${r.host}${r.elapsed_s ? ' · ' + r.elapsed_s + ' s' : ''}`),
    el('div', { class: 'msg' }, r.msg),
    el('div', { class: 'sub' }, el('a', { href: testLink(r.core, r.test), 'data-link': 1 }, 'test history'), ' · ',
      el('a', { href: `/snapshot/run/${id}.png`, target: '_blank' }, 'snapshot image')),
    d.quest && !d.quest.error ? el('div', { class: 'sub' }, `quest ${d.quest.id}: ${d.quest.title}`) : null,
    ...(d.notice || []).map(n => el('div', { class: 'notice' }, n)),
    d.record ? recordBlock(d.record) : null,
    mapDiv, toggles,
    el('div', { class: 'legend' },
      el('span', {}, el('span', { class: 'dot', style: 'background:#fff' }), 'start'),
      el('span', {}, el('span', { class: 'dot', style: 'background:var(--accent)' }), 'track / end'),
      ...Object.entries(ROLE).map(([k, c]) => el('span', {}, el('span', { class: 'dot', style: `background:${c}` }), k)),
      d.basemap ? el('span', {}, 'map: ' + d.basemap.name + (d.basemap.floor ? ' floor ' + d.basemap.floor : '')) : null),
    el('h2', {}, `Timeline (${d.events.length} events, ${d.track.length} positions)`),
    el('div', { class: 'list timeline' }, timeline(d)));
  drawRunMap(mapDiv, toggles, d);
  toggles.append(el('span', { class: 'chip' + (art === 'minimap' ? ' on' : ''), onclick: () => {
    store('wrunart', art === 'minimap' ? 'painted' : 'minimap');
    if (mapObj) { mapObj.remove(); mapObj = null; }
    runPage(id);
  } }, art === 'minimap' ? 'real ground' : 'ground instead of painted map'));
}

function recordBlock(r) {
  const actions = (r.last_action || '').split('|').map(s => s.trim()).filter(Boolean);
  return el('details', { class: 'row' },
    el('summary', {}, `How it ended · ${(r.elapsed_ms / 1000).toFixed(0)} s from ${r.start ? r.start.slice(11) : '?'} to ${r.end.slice(11)}`),
    r.travel_target ? el('div', { class: 'meta' }, 'travel target: ' + r.travel_target) : null,
    el('div', { class: 'meta' }, 'last actions:'),
    el('div', { class: 'msg' }, actions.map(a => el('div', {}, a))));
}

function timeline(d) {
  const items = d.events.map(e => ({ t: e.t, text: e.event, sub: e.detail }));
  const states = [];
  let last = null;
  for (const p of d.track) {
    const s = p.state + (p.target ? ' → ' + p.target : '');
    if (s !== last) states.push({ t: p.t, text: s, sub: p.src });
    last = s;
  }
  return items.concat(states).sort((a, b) => a.t.localeCompare(b.t)).slice(0, 400).map(i =>
    el('div', { class: 'row' }, el('div', { class: 'meta' }, i.t.slice(11)), el('div', {}, i.text), i.sub ? el('div', { class: 'meta' }, i.sub) : null));
}

function drawRunMap(div, toggles, d) {
  mapObj = L.map(div, { crs: LM_CRS, minZoom: -6, maxZoom: 4, zoomSnap: 0.25, attributionControl: false });
  mapObj.createPane('baseimg');
  mapObj.getPane('baseimg').style.zIndex = 340;
  let bounds = null;
  if (d.basemap) {
    const b = d.basemap.bounds;
    bounds = L.latLngBounds(ll(b.bottom, b.left), ll(b.top, b.right));
    L.imageOverlay(`/basemap/${d.run.core}/${d.basemap.image}`, bounds, { pane: 'baseimg' }).addTo(mapObj);
  }
  const markers = L.layerGroup().addTo(mapObj);
  for (const m of d.markers) {
    L.circleMarker(ll(m.x, m.y), { radius: m.focus ? 7 : 5, color: '#000', weight: 1, fillColor: ROLE_HEX[m.role], fillOpacity: 0.9 })
      .bindTooltip(`${m.role}: ${m.name}`).addTo(markers);
  }
  const track = L.layerGroup().addTo(mapObj);
  const pts = d.track.map(p => ll(p.x, p.y));
  if (pts.length > 1) L.polyline(pts, { color: '#28c8ff', weight: 3 }).addTo(track);
  d.track.forEach((p, i) => {
    if (i === 0 || i === d.track.length - 1 || i % 10 === 0)
      L.circleMarker(ll(p.x, p.y), { radius: i === 0 ? 6 : 3, color: '#000', weight: 1, fillColor: i === 0 ? '#fff' : '#28c8ff', fillOpacity: 1 })
        .bindTooltip(`${p.t.slice(11)} ${p.state || ''}`).addTo(track);
  });
  for (const e of d.events) {
    const bad = /die|dead|death/i.test(e.event);
    L.circleMarker(ll(e.x, e.y), { radius: 4, color: '#000', weight: 1, fillColor: bad ? '#e6463c' : '#ddd', fillOpacity: 0.95 })
      .bindTooltip(`${e.t.slice(11)} ${e.event}`).addTo(track);
  }
  const focus = pts.concat(d.markers.filter(m => m.focus || m.role !== 'objective').map(m => ll(m.x, m.y)));
  if (focus.length) mapObj.fitBounds(L.latLngBounds(focus).pad(0.4), { maxZoom: 1 });
  else if (bounds) mapObj.fitBounds(bounds);
  else mapObj.setView([0, 0], -4);
  const nodes = L.layerGroup();
  let nodesLoaded = false;
  const addToggle = (label, layer, on, onFirst) => {
    const chip = el('span', { class: 'chip' + (on ? ' on' : '') }, label);
    chip.onclick = async () => {
      if (mapObj.hasLayer(layer)) { mapObj.removeLayer(layer); chip.classList.remove('on'); return; }
      if (onFirst) await onFirst();
      layer.addTo(mapObj); chip.classList.add('on');
    };
    toggles.append(chip);
  };
  addToggle('track', track, true);
  addToggle('quest markers', markers, true);
  if (d.terrain_render && d.terrain_render.available) {
    const tag = el('div', { class: 'wterrain hidden' });
    div.append(tag);
    const row = el('div', { class: 'chips wterrainrow' });
    toggles.after(row);
    mountTerrain(mapObj, d.run.core, d.map && d.map.id, d.terrain_render, () => bounds, { chips: row, tag });
  }
  if (d.map && d.map.id !== null) addToggle('travel nodes', nodes, false, async () => {
    if (nodesLoaded) return;
    nodesLoaded = true;
    const b = d.basemap ? d.basemap.bounds : boundsOf(d);
    if (!b) return;
    const q = new URLSearchParams({ core: d.run.core, map: d.map.id, left: b.left, right: b.right, top: b.top, bottom: b.bottom });
    const n = await api('nodes?' + q.toString());
    for (const link of n.links) L.polyline(link.points.map(p => ll(p[0], p[1])), { color: LINK_COLORS[link.type] || '#fff', weight: link.type === 1 ? 2 : 1, opacity: 0.8 }).addTo(nodes);
    for (const node of n.nodes) L.circleMarker(ll(node.x, node.y), { radius: 3, color: '#000', weight: 1, fillColor: '#fff', fillOpacity: 1 }).bindTooltip(node.name).addTo(nodes);
  });
}

function boundsOf(d) {
  const pts = d.track.concat(d.markers);
  if (!pts.length) return null;
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  return { top: Math.max(...xs) + 200, bottom: Math.min(...xs) - 200, left: Math.max(...ys) + 200, right: Math.min(...ys) - 200 };
}

window.addEventListener('DOMContentLoaded', route);
