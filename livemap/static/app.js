'use strict';

const state = { core: store('core') || '', verdict: '', family: '', bucket: '', flipped: false, q: '', offset: 0 };
const view = document.getElementById('view');
let summaryCache = null;
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

function go(path) { history.pushState({}, '', path); route(); }
document.addEventListener('click', e => {
  const a = e.target.closest('a[data-link]');
  if (a && a.origin === location.origin) { e.preventDefault(); go(a.pathname + a.search); }
});
window.addEventListener('popstate', route);

function verdict(v) { return el('span', { class: 'v ' + v }, v); }
function short(ts) { return ts ? ts.slice(5, 16) : ''; }
function prettyTest(t) { return t.replace(/_/g, ' '); }
function testLink(core, test) { return `/t/${encodeURIComponent(core)}/${encodeURIComponent(test)}`; }

function coreChips() {
  const nav = document.getElementById('corechips');
  nav.textContent = '';
  const cores = (summaryCache && summaryCache.cores) || [];
  for (const c of [''].concat(cores)) {
    nav.append(el('span', { class: 'chip' + (state.core === c ? ' on' : ''), onclick: () => {
      state.core = c; store('core', c); state.offset = 0; summaryCache = null; route();
    } }, c || 'all cores'));
  }
}

function showError(err) {
  view.textContent = '';
  view.append(el('div', { class: 'notice' }, String(err.message || err)));
}

async function route() {
  if (mapObj) { mapObj.remove(); mapObj = null; }
  const p = location.pathname;
  try {
    if (!summaryCache) summaryCache = await api('summary' + (state.core ? '?core=' + state.core : ''));
    coreChips();
    let m;
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
  for (const k of ['core', 'verdict', 'family', 'bucket', 'q']) if (state[k]) q.set(k, state[k]);
  if (state.flipped) q.set('flipped', '1');
  q.set('limit', 60); q.set('offset', state.offset);
  const families = await api('families');
  const famNames = [...new Set(families.filter(f => !state.core || f.core === state.core).map(f => f.family))];
  const rows = await api('tests?' + q.toString());
  view.textContent = '';
  const search = el('input', { type: 'search', placeholder: 'search tests (e.g. 8502 clear, molten core)', value: state.q });
  let timer = null;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); state.offset = 0; homePage(); }, 350); });
  view.append(
    tiles(),
    transitionList('Flipped to PASS', summaryCache.flips.slice(0, 5)),
    transitionList('Regressions (PASS → FAIL)', summaryCache.regressions.slice(0, 5)),
    el('div', { class: 'tabs' }, el('a', { class: 'chip on', href: '/', 'data-link': 1 }, 'Tests'),
      el('a', { class: 'chip', href: '/clusters', 'data-link': 1 }, 'Failure groups')),
    search,
    chipRow([['', 'any verdict'], ['PASS', 'pass'], ['FAIL', 'fail'], ['ABORT', 'abort'], ['IMPOSSIBLE', 'impossible']], 'verdict'),
    chipRow([['', 'all families']].concat(famNames.map(f => [f, f])), 'family'),
    el('div', { class: 'chips' },
      el('span', { class: 'chip' + (state.flipped ? ' on' : ''), onclick: () => { state.flipped = !state.flipped; state.offset = 0; homePage(); } }, 'just flipped'),
      ...['bug', 'fluke', 'gap', 'fixed'].map(b => el('span', { class: 'chip' + (state.bucket === b ? ' on' : ''),
        onclick: () => { state.bucket = state.bucket === b ? '' : b; state.offset = 0; homePage(); } }, 'triage: ' + b))),
    el('div', { class: 'list' }, rows.map(testRow)),
    rows.length === 60 ? el('button', { class: 'ghost more', onclick: () => { state.offset += 60; homePage(); } }, 'next page') : null,
    state.offset ? el('button', { class: 'ghost more', onclick: () => { state.offset = Math.max(0, state.offset - 60); homePage(); } }, 'previous page') : null,
  );
  if (document.activeElement !== search && state.q) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); }
}

function testRow(r) {
  const flipped = r.verdict === 'PASS' && ['FAIL', 'ABORT', 'IMPOSSIBLE'].includes(r.prev);
  return el('a', { class: 'row', href: testLink(r.core, r.test), 'data-link': 1 },
    el('div', { class: 'name' }, verdict(r.verdict), ' ', prettyTest(r.test),
      flipped ? el('span', { class: 'badge flip' }, 'flipped') : null,
      r.bucket ? el('span', { class: 'badge ' + r.bucket }, r.bucket) : null),
    el('div', { class: 'meta' }, `${r.core} · ${r.runs} runs · ${r.passes} pass · ${short(r.ts)} · ${r.host}`),
    r.verdict !== 'PASS' ? el('div', { class: 'msg' }, r.msg) : null);
}

async function clustersPage() {
  const rows = await api('clusters' + (state.core ? '?core=' + state.core : ''));
  view.textContent = '';
  view.append(el('div', { class: 'tabs' }, el('a', { class: 'chip', href: '/', 'data-link': 1 }, 'Tests'),
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
  view.append(
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
  const d = await api('run/' + id);
  const r = d.run;
  view.textContent = '';
  const mapDiv = el('div', { id: 'map' });
  const toggles = el('div', { class: 'toggles' });
  view.append(
    el('h1', {}, verdict(r.verdict), ' ', prettyTest(r.test)),
    el('div', { class: 'sub' }, `${r.core} · ${r.ts} · host ${r.host}${r.elapsed_s ? ' · ' + r.elapsed_s + ' s' : ''}`),
    el('div', { class: 'msg' }, r.msg),
    el('div', { class: 'sub' }, el('a', { href: testLink(r.core, r.test), 'data-link': 1 }, 'test history'), ' · ',
      el('a', { href: `/snapshot/run/${id}.png`, target: '_blank' }, 'snapshot image')),
    d.quest && !d.quest.error ? el('div', { class: 'sub' }, `quest ${d.quest.id}: ${d.quest.title}`) : null,
    ...(d.notice || []).map(n => el('div', { class: 'notice' }, n)),
    mapDiv, toggles,
    el('div', { class: 'legend' },
      el('span', {}, el('span', { class: 'dot', style: 'background:#fff' }), 'start'),
      el('span', {}, el('span', { class: 'dot', style: 'background:var(--accent)' }), 'track / end'),
      ...Object.entries(ROLE).map(([k, c]) => el('span', {}, el('span', { class: 'dot', style: `background:${c}` }), k)),
      d.basemap ? el('span', {}, 'map: ' + d.basemap.name + (d.basemap.floor ? ' floor ' + d.basemap.floor : '')) : null),
    el('h2', {}, `Timeline (${d.events.length} events, ${d.track.length} positions)`),
    el('div', { class: 'list timeline' }, timeline(d)));
  drawRunMap(mapDiv, toggles, d);
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
  mapObj = L.map(div, { crs: L.CRS.Simple, minZoom: -6, maxZoom: 4, zoomSnap: 0.25, attributionControl: false });
  let bounds = null;
  if (d.basemap) {
    const b = d.basemap.bounds;
    bounds = L.latLngBounds(ll(b.bottom, b.left), ll(b.top, b.right));
    L.imageOverlay(`/basemap/${d.run.core}/${d.basemap.image}`, bounds).addTo(mapObj);
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

route();
