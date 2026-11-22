'use strict';

const DEMO = { files: {}, t0: Date.now() };
const DEMO_RACES = { 1: 'Human', 2: 'Orc', 3: 'Dwarf', 4: 'Night Elf', 5: 'Undead', 6: 'Tauren', 7: 'Gnome', 8: 'Troll', 10: 'Blood Elf', 11: 'Draenei' };
const DEMO_CLASSES = { 1: 'Warrior', 2: 'Paladin', 3: 'Hunter', 4: 'Rogue', 5: 'Priest', 6: 'Death Knight', 7: 'Shaman', 8: 'Mage', 9: 'Warlock', 11: 'Druid' };
const DEMO_HORDE = new Set([2, 5, 6, 8, 10]);
const DEMO_FLAGS = [[1, 'combat'], [2, 'dead'], [4, 'ghost'], [8, 'group'], [32, 'delayed'], [64, 'taxi'], [128, 'test']];

function demoFile(name) {
  if (!DEMO.files[name]) {
    DEMO.files[name] = fetch(LM.base + '/data/' + name).then(r => {
      if (!r.ok) throw new Error('not in the demo');
      return r.json();
    });
    DEMO.files[name].catch(() => { delete DEMO.files[name]; });
  }
  return DEMO.files[name];
}

async function demoFrame(back) {
  const live = await demoFile('live.json');
  const n = live.frames.length;
  const i = Math.floor((Date.now() - DEMO.t0) / (live.frame_s * 1000)) % n;
  return { live, i, frame: live.frames[((i - (back || 0)) % n + n) % n] };
}

function demoSnapshot(live, frame, mapId) {
  const ci = Object.fromEntries(live.cols.map((c, k) => [c, k]));
  const counts = new Map();
  for (const r of frame.bots) {
    const m = r[ci.map];
    const c = counts.get(m) || { id: m, bots: 0, players: 0, name: live.map_names[m] || 'map ' + m };
    c.bots += 1;
    counts.set(m, c);
  }
  const rows = mapId === null ? frame.bots : frame.bots.filter(r => r[ci.map] === mapId);
  return { core: live.core, live: true, newest: frame.ts, epoch: frame.epoch, source: 'demo', cols: live.cols,
    maps: [...counts.values()].sort((a, b) => b.bots - a.bots), bots: rows, known: live.known };
}

async function demoCard(guid) {
  const { live, i } = await demoFrame(0);
  const ci = Object.fromEntries(live.cols.map((c, k) => [c, k]));
  const trail = [];
  let row = null;
  const n = live.frames.length;
  for (let back = Math.min(n - 1, 120); back >= 0; back--) {
    const f = live.frames[((i - back) % n + n) % n];
    const r = f.bots.find(b => b[ci.guid] === guid);
    if (!r) continue;
    trail.push([r[ci.x], r[ci.y], r[ci.flags], r[ci.map]]);
    if (back === 0) row = r;
  }
  if (!row) throw new Error('This bot is not online any more.');
  const get = k => row[ci[k]];
  const race = get('race'), cls = get('cls'), flags = get('flags');
  const events = (await demoFile('events.json').catch(() => [])).filter(e => e.bot === get('name')).slice(-40);
  const runs = (await demoFile('tests.json').catch(() => [])).filter(t => t.host === get('name'))
    .map(t => ({ id: t.id, test: t.test, verdict: t.verdict }));
  return { guid, name: get('name'), bot: !!get('bot'), map: get('map'), x: get('x'), y: get('y'), z: get('z'), level: get('level'),
    hp: get('hp'), power: get('power'), money: get('money'), race, cls, race_name: DEMO_RACES[race] || '', class_name: DEMO_CLASSES[cls] || '',
    faction: race ? (DEMO_HORDE.has(race) ? 'Horde' : 'Alliance') : '', age: get('age'), floor: get('floor'),
    state: DEMO_FLAGS.filter(([b]) => flags & b).map(([, t]) => t).join(' ') || 'idle',
    leader_name: '', map_name: live.map_names[get('map')] || '', zone_name: live.areas[get('zone')] || '', area_name: live.areas[get('area')] || '',
    trail, events, runs };
}

function demoFilterTests(rows, q) {
  let out = rows;
  if (q.core) out = out.filter(r => r.core === q.core);
  if (q.verdict) out = out.filter(r => r.verdict === q.verdict);
  if (q.family) out = out.filter(r => r.family === q.family);
  if (q.q) out = out.filter(r => r.test.toLowerCase().includes(q.q.toLowerCase().replace(/ /g, '_')));
  return out;
}

async function demoApi(path, opts) {
  if (opts && opts.method && opts.method !== 'GET') throw new Error('The demo is read-only. Run livemap on your own server to change things.');
  const [name, qs] = path.split('?');
  const q = Object.fromEntries(new URLSearchParams(qs || ''));
  const mapId = q.map === undefined || q.map === '' || q.map === 'world' ? null : +q.map;
  switch (name) {
    case 'capabilities': {
      const c = await demoFile('capabilities.json');
      return q.core ? c.cores[q.core] : c;
    }
    case 'live': {
      const { live, frame } = await demoFrame(0);
      return demoSnapshot(live, frame, mapId);
    }
    case 'live/bot': return demoCard(+q.guid);
    case 'live/find': {
      const { live, frame } = await demoFrame(0);
      const ci = Object.fromEntries(live.cols.map((c, k) => [c, k]));
      const t = (q.q || '').toLowerCase();
      return frame.bots.filter(r => r[ci.name].toLowerCase().includes(t)).slice(0, 20)
        .map(r => ({ guid: r[ci.guid], name: r[ci.name], map: r[ci.map], level: r[ci.level], bot: !!r[ci.bot] }));
    }
    case 'live/events': {
      const rows = await demoFile('events.json');
      const kinds = q.kinds ? new Set(q.kinds.split(',')) : null;
      return rows.filter(e => (mapId === null || e.map === mapId) && (!kinds || kinds.has(e.kind)));
    }
    case 'live/intents': return (await demoFile('intents.json')).filter(r => mapId === null || r.map === mapId);
    case 'tests/where': return (await demoFile('tests_where.json')).filter(r => mapId === null || r.map === mapId);
    case 'overlay': return demoFile('overlay.json');
    case 'mapinfo': return demoFile(`mapinfo_${q.map}.json`).catch(() => ({ id: +q.map, name: 'map ' + q.map, basemaps: [], zones: [],
      extent: null, terrain: null, terrain_render: { available: false } }));
    case 'terrain': return { available: false };
    case 'layer': {
      if (q.kind === 'zonelevels') return mapId === null ? demoFile('layer_zonelevels.json').catch(() => []) : [];
      const empty = { scene: true, maps: [], features: [], bins: [], links: [], nodes: [], total: 0, binned: false };
      return mapId === null ? demoFile(`layer_${q.kind}.json`).catch(() => empty) : empty;
    }
    case 'nodes': return { nodes: [], links: [] };
    case 'summary': return demoFile('summary.json');
    case 'families': return demoFile('families.json');
    case 'tests': return demoFilterTests(await demoFile('tests.json'), q);
    case 'transitions': return [];
    case 'clusters': return [];
    case 'test': {
      const all = await demoFile('test_pages.json');
      const page = all[q.core + '/' + q.test];
      if (!page) throw new Error('This test is not part of the demo.');
      return page;
    }
    case 'settings': return { remote: true, can_write: false, cores: {}, map: null };
    default:
      if (name.startsWith('run/')) return demoFile(`run_${name.slice(4)}.json`).catch(() => { throw new Error('This run is not part of the demo.'); });
      throw new Error('Not available in the demo.');
  }
}
