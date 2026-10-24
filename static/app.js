'use strict';

// ── Zustand ───────────────────────────────────────
const PAGE_SIZE = 60;
const MOBILE = window.matchMedia('(max-width: 820px)');
let cfg = null;               // Einstellungen vom Server
let cfgVersion = 0;           // erhöht sich bei lokalen Änderungen (für das Neuzeichnen)
let radiusOptions = [0, 5, 10, 20, 30, 50, 100, 150, 200];
let listings = [];
let unread = new Set();
let rev = null;
let status = {};
let firstPoll = true;
let saveTimer = null;

let view = 'list';            // list | db | settings
let folder = 'all';           // all | new | <Such-ID>
let findText = '';
let sort = 'new';
let showingMore = false;
let selectedId = null;
let detailMode = null;        // null | listing | edit | new
let draft = null;             // Arbeitskopie im Editor
let deleteArmed = false;
let listKey = '';

const $ = id => document.getElementById(id);

const ICON = {
  play: '<path d="M7 4v16l13-8z"/>',
  pause: '<path d="M8 4v16M16 4v16"/>',
  list: '<path d="M4 7h16M4 12h16M4 17h10"/>',
  link: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  warn: '<path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  box: '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
};
const svg = (name, extra = '') => `<svg viewBox="0 0 24 24"${extra}>${ICON[name]}</svg>`;

// ── Start ─────────────────────────────────────────
(async function init() {
  await loadConfig();
  updateNotifBtn();
  try { const v = localStorage.getItem('kr-view'); if (v === 'db' || v === 'settings') showView(v); } catch (e) { /* egal */ }
  renderDetail();
  poll();
  setInterval(poll, 3000);
  setInterval(updateStatus, 1000);
  document.addEventListener('keydown', onKey);
  MOBILE.addEventListener('change', () => document.documentElement.classList.remove('side-open', 'detail-open'));
})();

// ── Server-Kommunikation ─────────────────────────
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

async function loadConfig() {
  try {
    const d = await api('/api/config');
    cfg = d.config;
    radiusOptions = d.radius_options || radiusOptions;
    applyConfigToUI();
  } catch (e) {
    showToast('Einstellungen konnten nicht geladen werden');
  }
}

function saveConfig() {
  cfgVersion++;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      const { running, ...payload } = cfg;
      cfg = (await api('/api/config', payload)).config;
      cfgVersion++;
      renderAll();
    } catch (e) { showToast('Speichern fehlgeschlagen: ' + e.message); }
  }, 0);
}

function applyConfigToUI() {
  const range = cfg.interval_min + '-' + cfg.interval_max;
  const isel = $('interval-sel');
  if (![...isel.options].some(o => o.value === range)) {
    const o = document.createElement('option');
    o.value = range; o.textContent = cfg.interval_min + ' – ' + cfg.interval_max + ' Sekunden';
    isel.appendChild(o);
  }
  isel.value = range;
  $('hide-requests').checked = !!cfg.hide_requests;
  $('tg-enabled').checked = !!cfg.telegram_enabled;
  $('hl-zip').value = (cfg.highlight_zip || []).join(', ');
  renderBlacklist(); renderSearchSets();
  renderAll();
}

function onSettingChange() {
  const [min, max] = $('interval-sel').value.split('-').map(Number);
  cfg.interval_min = min; cfg.interval_max = max;
  cfg.hide_requests = $('hide-requests').checked;
  cfg.telegram_enabled = $('tg-enabled').checked;
  cfg.highlight_zip = $('hl-zip').value.split(/[\s,;]+/).filter(w => /^\d{1,5}$/.test(w));
  saveConfig();
  renderAll();
}

// ── Menü & Ansichten ──────────────────────────────
function toggleSide() {
  const root = document.documentElement;
  if (MOBILE.matches) { root.classList.toggle('side-open'); return; }
  const collapsed = root.classList.toggle('side-collapsed');
  try { localStorage.setItem('kr-side', collapsed ? 'collapsed' : 'open'); } catch (e) { /* egal */ }
}
function closeSideMobile() { document.documentElement.classList.remove('side-open'); }

function showView(v) {
  view = ['db', 'settings'].includes(v) ? v : 'list';
  $('pane-list').hidden = view !== 'list';
  $('pane-detail').hidden = view !== 'list';
  $('view-db').hidden = view !== 'db';
  $('view-settings').hidden = view !== 'settings';
  document.querySelector('.shell').classList.toggle('wide', view !== 'list');
  document.documentElement.classList.remove('detail-open');
  closeSideMobile();
  try { localStorage.setItem('kr-view', view); } catch (e) { /* egal */ }
  renderFolders();
  if (view === 'db') { if (!dbStats) renderDbFilters(); loadDb(); }
}

function setFolder(f) {
  folder = f;
  showingMore = false;
  if (view !== 'list') showView('list');
  closeSideMobile();
  renderAll();
  $('pane-list').scrollTop = 0;
}

function renderAll() {
  if (!cfg) return;
  renderFolders();
  renderList();
  updateStatus();
}

// ── Ordnerleiste ──────────────────────────────────
function findSearch(id) { return cfg && cfg.searches.find(s => s.id === id); }

function folderName(f) {
  if (f === 'all') return 'Alle Treffer';
  if (f === 'new') return 'Neu';
  const s = findSearch(f);
  return s ? s.name : 'Alle Treffer';
}

function searchWhere(s) {
  if (s.type === 'url') return 'Kategorie-URL';
  return s.location ? s.location.label + (s.location.radius ? ' + ' + s.location.radius + ' km' : '') : 'ganz Deutschland';
}

function renderFolders() {
  if (!cfg) return;
  if (folder !== 'all' && folder !== 'new' && !findSearch(folder)) folder = 'all';
  const visible = visibleListings();
  const setN = (id, n) => { $(id).textContent = n ? n : ''; };
  setN('n-all', visible.length);
  setN('n-new', visible.filter(l => unread.has(l.id)).length);
  if (dbStats) setN('n-db', dbStats.totals.listings);

  document.querySelectorAll('.side > .folder[data-folder]').forEach(b => {
    const f = b.dataset.folder;
    b.classList.toggle('on', view === 'list' ? f === folder : f === view);
  });

  const health = status.health || {};
  $('search-folders').innerHTML = cfg.searches.map(s => {
    const h = health[s.id];
    const n = visible.filter(l => (l.search_ids || []).includes(s.id)).length;
    let sub = esc(searchWhere(s)), cls = '';
    if (!s.enabled) sub = 'pausiert';
    else if (h && h.state === 'error') { sub = 'Fehler: ' + esc(h.last_error); cls = ' class="warn"'; }
    else if (h && h.state === 'suspicious') { sub = '0 Anzeigen erkannt'; cls = ' class="warn"'; }
    const on = view === 'list' && folder === s.id;
    return `<div class="folder${on ? ' on' : ''}${s.enabled ? '' : ' off'}" role="button" tabindex="0"
        onclick="setFolder('${s.id}')" onkeydown="if(event.key==='Enter')setFolder('${s.id}')" title="${esc(s.type === 'url' ? s.url : s.query)}">
      ${svg(s.type === 'url' ? 'link' : 'list')}
      <span class="fl">${esc(s.name)}<small${cls}>${sub}</small></span>
      <span class="n has-edit">${n || ''}</span>
      <span class="edit" role="button" title="Suche bearbeiten" onclick="event.stopPropagation(); openEditor('${s.id}')">${svg('edit')}</span>
    </div>`;
  }).join('') || '<div class="folder" style="cursor:default"><span class="fl muted">Noch keine Suchen</span></div>';

  const lastCount = Object.values(health).reduce((a, h) => a + (h.last_count || 0), 0);
  $('side-foot').innerHTML = `Intervall ${fmtInterval()} · Telegram ${cfg.telegram_enabled ? 'an' : 'aus'}<br>`
    + (status.last_check_ts ? `Letzte Suche <span class="num">${fmtClock(status.last_check_ts)}</span> · ${lastCount} Treffer` : 'Noch keine Suche');
}

function fmtInterval() {
  const a = cfg.interval_min, b = cfg.interval_max;
  return a % 60 === 0 && b % 60 === 0 ? `${a / 60}–${b / 60} Min.` : `${a}–${b} Sek.`;
}

// ── Filter & Liste ────────────────────────────────
// Muss zu passes_filters in app/monitor.py passen
function passesSearch(l, s) {
  const title = (l.title || '').toLowerCase();
  if (s.exclude.some(w => title.includes(w))) return false;
  if (s.must_contain.length && !s.must_contain.some(w => title.includes(w))) return false;
  const p = l.price_value;
  if (p == null) return s.show_no_price !== false;
  if (s.price_min != null && p < s.price_min) return false;
  if (s.price_max != null && p > s.price_max) return false;
  return true;
}
function passesFilters(l) {
  const title = (l.title || '').toLowerCase();
  if (cfg.blacklist.some(w => title.includes(w))) return false;
  if (cfg.hide_requests && l.is_request) return false;
  const ids = l.search_ids || [];
  if (!ids.length) return true;   // ältere Einträge ohne Zuordnung
  // sichtbar, wenn mindestens eine noch vorhandene Suche die Anzeige durchlässt
  return ids.some(id => { const s = findSearch(id); return s && passesSearch(l, s); });
}
function visibleListings() { return cfg ? listings.filter(passesFilters) : []; }

function currentItems() {
  let items = visibleListings();
  if (folder === 'new') items = items.filter(l => unread.has(l.id));
  else if (folder !== 'all') items = items.filter(l => (l.search_ids || []).includes(folder));
  if (findText) {
    const q = findText.toLowerCase();
    items = items.filter(l => ((l.title || '') + ' ' + (l.location || '') + ' ' + (l.description || '')).toLowerCase().includes(q));
  }
  if (sort === 'price_asc' || sort === 'price_desc') {
    const dir = sort === 'price_asc' ? 1 : -1;
    items = [...items].sort((a, b) => (a.price_value == null) - (b.price_value == null) || dir * ((a.price_value || 0) - (b.price_value || 0)));
  }
  return items;
}

function onFind(value) {
  findText = value.trim();
  document.querySelectorAll('.find-input').forEach(i => { if (i.value !== value) i.value = value; });
  showingMore = false;
  renderList();
}
function setSort(v) { sort = v; showingMore = false; renderList(); }
function toggleMore() { showingMore = !showingMore; renderList(); }

function renderList() {
  if (!cfg) return;
  const items = currentItems();
  const shown = showingMore ? items : items.slice(0, PAGE_SIZE);
  const key = [rev, [...unread].join(','), folder, findText, sort, showingMore, selectedId, cfgVersion, items.length].join('|');
  updateListHead(items);
  renderBanners();
  if (key === listKey) return;
  listKey = key;

  const empty = $('empty');
  if (!items.length) {
    $('rows').innerHTML = '';
    empty.hidden = false;
    if (!cfg.searches.length) empty.innerHTML = '<b>Noch keine Suchen</b>Links auf „Neue Suche“ klicken und einen Begriff oder eine Kategorie-URL anlegen.';
    else if (findText) empty.innerHTML = `<b>Nichts gefunden</b>Keine Anzeige enthält „${esc(findText)}“.`;
    else if (folder === 'new') empty.innerHTML = '<b>Alles gelesen</b>Neue Anzeigen erscheinen hier, sobald eine Suche sie findet.';
    else empty.innerHTML = '<b>Noch keine Anzeigen</b>Beim ersten Durchlauf wird der Bestand eingelesen – gemeldet werden danach nur wirklich neue Anzeigen.';
  } else {
    empty.hidden = true;
    $('rows').innerHTML = shown.map(rowHTML).join('');
  }
  const more = $('more-btn');
  more.hidden = items.length <= PAGE_SIZE;
  more.textContent = showingMore ? 'Weniger anzeigen' : `${items.length - PAGE_SIZE} weitere anzeigen`;
}

function updateListHead(items) {
  $('list-title').textContent = findText ? `Suche: ${findText}` : folderName(folder);
  const n = items.filter(l => unread.has(l.id)).length;
  $('list-count').innerHTML = `<span class="num">${items.length}</span> Anzeigen` + (n ? ` · <span class="num">${n}</span> neu` : '');
  $('btn-read-all').disabled = !unread.size;
  const total = visibleListings().filter(l => unread.has(l.id)).length;
  document.title = (total ? '(' + total + ') ' : '') + 'Kleinanzeigen-Radar';
}

function renderBanners() {
  const warns = status.warnings || [];
  const errs = Object.entries(status.errors || {});
  const html = (warns.length ? `<div class="banner warn">${warns.map(esc).join('<br>')}</div>` : '')
    + (errs.length ? `<div class="banner bad">${errs.map(([k, v]) => '<b>' + esc(k) + ':</b> ' + esc(v)).join('<br>')}</div>` : '');
  if ($('banners').innerHTML !== html) $('banners').innerHTML = html;
}

function isHighlighted(loc) {
  const zip = (/\b\d{5}\b/.exec(loc || '') || [''])[0];
  return !!zip && (cfg.highlight_zip || []).some(p => zip.startsWith(p));
}
function place(loc) { return (loc || '').replace(/^\d{5}\s*/, ''); }
function searchNames(l) {
  const names = (l.search_ids || []).map(findSearch).filter(Boolean).map(s => s.name);
  return names.length ? names.join(', ') : (l.source || '');
}
function shortPosted(l) {
  const p = l.posted || '';
  if (p.startsWith('Heute, ')) return p.slice(7);
  if (p.startsWith('Gestern, ')) return 'gestern';
  const m = p.match(/^(\d\d\.\d\d)\.\d{4}$/);
  return m ? m[1] + '.' : (l.first_seen ? fmtClock(l.first_seen) : '');
}

function rowHTML(l) {
  const thumb = l.image ? `<img src="${esc(l.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : svg('box');
  const loc = place(l.location);
  return `<div class="row${unread.has(l.id) ? ' unread' : ''}${selectedId === l.id ? ' sel' : ''}" data-id="${esc(l.id)}" onclick="selectListing('${esc(l.id)}')">
    <span class="u"></span>
    <div class="thumb">${thumb}</div>
    <div class="t"><b>${esc(l.title)}</b>
      <span>${l.is_request ? '<span class="tag">Gesuch</span>' : ''}${folder === 'all' || folder === 'new' ? esc(searchNames(l)) + ' · ' : ''}<span class="${isHighlighted(l.location) ? 'hl' : ''}">${esc(loc)}</span></span></div>
    <div class="p"><b>${esc(l.price || 'VB')}</b><span>${esc(shortPosted(l))}</span></div>
  </div>`;
}

// ── Vorschau ──────────────────────────────────────
function selectListing(id) {
  selectedId = id;
  detailMode = 'listing';
  deleteArmed = false;
  document.querySelectorAll('.row.sel').forEach(r => r.classList.remove('sel'));
  const row = document.querySelector(`.row[data-id="${CSS.escape(id)}"]`);
  if (row) { row.classList.add('sel'); row.classList.remove('unread'); }
  renderDetail();
  openDetailMobile();
  if (unread.has(id)) {
    unread.delete(id);
    listKey = '';
    renderFolders(); renderList();
    api('/api/seen', { ids: [id] }).catch(() => {});
  }
}

function openDetailMobile() {
  if (MOBILE.matches) { document.documentElement.classList.add('detail-open'); $('pane-detail').scrollTop = 0; }
}
function closeDetail() {
  document.documentElement.classList.remove('detail-open');
  if (detailMode === 'edit' || detailMode === 'new') { detailMode = selectedId ? 'listing' : null; draft = null; renderDetail(); }
}

function renderDetail() {
  const box = $('detail');
  if (detailMode === 'edit' || detailMode === 'new') { box.innerHTML = editorHTML(); return; }
  const l = detailMode === 'listing' && listings.find(x => x.id === selectedId);
  if (!l) {
    box.innerHTML = `<div class="placeholder">Anzeige in der Liste anklicken, um sie hier anzusehen.<br><span class="muted">Mit ↑ und ↓ springst du durch die Liste.</span></div>`;
    return;
  }
  const img = l.image_large || l.image;
  box.innerHTML = `
    ${img ? `<a href="${esc(l.url)}" target="_blank" rel="noopener"><img class="pv-img" src="${esc(img)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()"></a>` : ''}
    <h1 class="pv-title">${esc(l.title)}</h1>
    <div class="pv-price">${esc(l.price || 'VB')}</div>
    ${l.description ? `<p class="pv-desc">${esc(l.description)}${l.description.length >= 299 ? ' …' : ''}</p>` : ''}
    <div class="kv">
      <span>Ort</span><div class="${isHighlighted(l.location) ? 'hl' : ''}">${esc(l.location || '—')}</div>
      <span>Inseriert</span><div class="num">${esc(l.posted || '—')}</div>
      <span>Gefunden</span><div class="num">${esc(fmtDateTime(l.first_seen))}</div>
      <span>Gefunden von</span><div>${esc(searchNames(l))}</div>
      ${(l.badges || []).length ? `<span>Merkmale</span><div>${esc(l.badges.join(', '))}</div>` : ''}
      <span>Preisverlauf</span><div id="pv-history" class="muted">…</div>
    </div>
    <div class="pv-actions">
      <a class="btn primary" href="${esc(l.url)}" target="_blank" rel="noopener">${svg('link')}Auf Kleinanzeigen öffnen</a>
    </div>`;
  loadPriceHistory(l.id);
}

async function loadPriceHistory(id) {
  let d;
  try { d = await api('/api/history/listing?id=' + encodeURIComponent(id)); }
  catch (e) { const el = $('pv-history'); if (el) el.textContent = 'nicht verfügbar'; return; }
  const el = $('pv-history');
  if (!el || selectedId !== id) return;
  const changes = (d.changes || []).filter(c => c.field === 'price');
  el.classList.remove('muted');
  el.innerHTML = changes.length
    ? changes.map(c => `<div>${esc(fmtDateTime(c.ts))}: ${changeHTML(c)}</div>`).join('')
    : `unverändert seit ${esc(fmtDateTime(d.first_seen))} <span class="muted">(${d.seen_count}× gesehen)</span>`;
}

// ── Such-Editor ───────────────────────────────────
function newSearch(fields) {
  return Object.assign({
    id: 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    type: 'keyword', query: '', url: '', name: '', enabled: true, location: null,
    price_min: null, price_max: null, show_no_price: true, must_contain: [], exclude: [], telegram: true,
  }, fields);
}
function openNewSearch() {
  const last = [...cfg.searches].reverse().find(s => s.type === 'keyword');
  draft = newSearch({ location: last ? last.location : null });
  detailMode = 'new';
  deleteArmed = false;
  if (view !== 'list') showView('list');
  closeSideMobile();
  renderDetail();
  openDetailMobile();
  setTimeout(() => { const i = $('ed-query'); if (i && !MOBILE.matches) i.focus(); }, 50);
}
function openEditor(id) {
  const s = findSearch(id);
  if (!s) return;
  draft = JSON.parse(JSON.stringify(s));
  detailMode = 'edit';
  deleteArmed = false;
  if (view !== 'list') showView('list');
  closeSideMobile();
  renderDetail();
  openDetailMobile();
}
function setDraftType(t) { draft.type = t; renderDetail(); }

function editorHTML() {
  const d = draft, isNew = detailMode === 'new';
  const h = !isNew && (status.health || {})[d.id];
  let health = '';
  if (h && h.state === 'error') health = `<div class="health bad">Letzter Abruf fehlgeschlagen: ${esc(h.last_error)}</div>`;
  else if (h && h.state === 'suspicious') health = '<div class="health warn">Letzter Abruf lieferte 0 Anzeigen, obwohl Kleinanzeigen Treffer zeigt.</div>';
  else if (h && h.last_count != null) health = `<div class="health ok">Letzter Abruf: ${h.last_count} Anzeigen</div>`;

  const loc = d.location;
  const radius = radiusOptions.map(r =>
    `<option value="${r}"${loc && loc.radius === r ? ' selected' : ''}>${r ? '+ ' + r + ' km' : 'nur dieser Ort'}</option>`).join('');
  const what = d.type === 'keyword' ? `
    <div class="field"><label for="ed-query">Suchbegriff</label>
      <input type="text" id="ed-query" value="${esc(d.query)}" maxlength="100" oninput="draft.query=this.value" onkeydown="if(event.key==='Enter')saveEditor()"></div>
    <div class="field"><span class="lbl-row">Ort &amp; Umkreis</span>
      <div class="loc-now"><b>${loc ? esc(loc.label) : 'Ganz Deutschland'}</b>${loc ? '<button class="link" onclick="setLocation(null)">ganz Deutschland</button>' : ''}</div>
      ${loc ? `<select onchange="draft.location.radius=Number(this.value)">${radius}</select>` : ''}
      <input type="search" id="loc-input" placeholder="${loc ? 'Anderen Ort suchen …' : 'PLZ oder Ort suchen …'}" oninput="searchLocations(this.value)" autocomplete="off" style="margin-top:.45rem">
      <div class="loc-results" id="loc-results"></div></div>` : `
    <div class="field"><label for="ed-name">Name</label>
      <input type="text" id="ed-name" value="${esc(d.name)}" maxlength="100" placeholder="z. B. Fahrräder Hamburg" oninput="draft.name=this.value"></div>
    <div class="field"><label for="ed-url">Kleinanzeigen-URL</label>
      <input type="text" id="ed-url" value="${esc(d.url)}" placeholder="https://www.kleinanzeigen.de/s-…" oninput="draft.url=this.value.trim()">
      <div class="hint">Auf kleinanzeigen.de Kategorie und Filter wählen und die Adresse aus dem Browser einfügen.</div></div>`;

  return `<div class="form">
    <h3>${isNew ? 'Neue Suche' : esc(d.name)}</h3>
    <div class="sub">${isNew ? 'Der erste Durchlauf liest den Bestand ein, gemeldet werden danach nur neue Anzeigen.' : esc(searchWhere(d))}</div>
    ${health}
    ${isNew ? `<div class="seg"><button class="${d.type === 'keyword' ? 'on' : ''}" onclick="setDraftType('keyword')">Suchbegriff</button><button class="${d.type === 'url' ? 'on' : ''}" onclick="setDraftType('url')">Kategorie-URL</button></div>` : ''}
    ${what}
    <div class="field"><span class="lbl-row">Preis (€)</span>
      <div class="pair"><input type="number" min="0" placeholder="von" value="${d.price_min ?? ''}" oninput="draft.price_min=numOrNull(this.value)"><span>–</span>
      <input type="number" min="0" placeholder="bis" value="${d.price_max ?? ''}" oninput="draft.price_max=numOrNull(this.value)"></div>
      <label class="check"><input type="checkbox" ${d.show_no_price ? 'checked' : ''} onchange="draft.show_no_price=this.checked"> Auch Anzeigen ohne Preis (VB, k. A.)</label></div>
    <div class="field"><label>Titel muss eines enthalten</label>
      <input type="text" value="${esc(d.must_contain.join(', '))}" placeholder="z. B. rennrad, gravel" oninput="draft.must_contain=splitWords(this.value)"></div>
    <div class="field"><label>Ausschließen</label>
      <input type="text" value="${esc(d.exclude.join(', '))}" placeholder="z. B. defekt, ersatzteile" oninput="draft.exclude=splitWords(this.value)"></div>
    <label class="check"><input type="checkbox" ${d.telegram ? 'checked' : ''} onchange="draft.telegram=this.checked"> Neue Treffer per Telegram melden</label>
    ${isNew ? '' : `<label class="check"><input type="checkbox" ${d.enabled ? 'checked' : ''} onchange="draft.enabled=this.checked"> Suche aktiv</label>`}
    <div class="form-actions">
      <button class="btn primary" onclick="saveEditor()">${isNew ? 'Suche anlegen' : 'Speichern'}</button>
      <button class="btn" onclick="closeDetail()">Abbrechen</button>
      ${isNew ? '' : `<button class="btn danger" onclick="deleteSearch()">${deleteArmed ? 'Wirklich löschen?' : 'Löschen'}</button>`}
    </div>
  </div>`;
}

let locResults = [], locTimer = null;
function searchLocations(q) {
  clearTimeout(locTimer);
  const box = $('loc-results');
  if (q.trim().length < 2) { box.innerHTML = ''; return; }
  locTimer = setTimeout(async () => {
    try {
      const d = await api('/api/locations?q=' + encodeURIComponent(q.trim()));
      locResults = d.locations || [];
      box.innerHTML = locResults.length
        ? locResults.map((l, i) => `<button onclick="setLocation(locResults[${i}])">${esc(l.label)}</button>`).join('')
        : '<button disabled>Kein Ort gefunden</button>';
    } catch (e) { box.innerHTML = '<button disabled>' + esc(e.message) + '</button>'; }
  }, 350);
}
function setLocation(loc) {
  draft.location = loc ? { id: loc.id, label: loc.label, radius: draft.location ? draft.location.radius : 30 } : null;
  renderDetail();
}

function saveEditor() {
  const d = draft, isNew = detailMode === 'new';
  if (d.type === 'keyword') {
    d.query = d.query.trim();
    if (!d.query) { showToast('Suchbegriff fehlt'); return; }
    d.name = d.query;
    d.url = '';
  } else {
    if (!isKaUrl(d.url)) { showToast('Bitte eine kleinanzeigen.de-Adresse angeben'); return; }
    d.name = d.name.trim() || 'Kategorie';
    d.location = null;
  }
  const dup = cfg.searches.some(s => s.id !== d.id && (d.type === 'keyword'
    ? s.type === 'keyword' && s.query.toLowerCase() === d.query.toLowerCase() : s.url === d.url));
  if (dup) { showToast('Diese Suche gibt es schon'); return; }
  if (isNew) cfg.searches.push(d);
  else {
    const i = cfg.searches.findIndex(s => s.id === d.id);
    if (i < 0) { closeDetail(); return; }
    cfg.searches[i] = d;
  }
  draft = null;
  detailMode = selectedId ? 'listing' : null;
  saveConfig();
  renderDetail();
  document.documentElement.classList.remove('detail-open');
  if (isNew) setFolder(d.id); else renderAll();
  showToast(isNew ? 'Suche angelegt' : 'Suche gespeichert');
}

function deleteSearch() {
  if (!deleteArmed) { deleteArmed = true; renderDetail(); return; }
  const id = draft.id;
  cfg.searches = cfg.searches.filter(s => s.id !== id);
  draft = null; deleteArmed = false;
  detailMode = selectedId ? 'listing' : null;
  if (folder === id) folder = 'all';
  saveConfig();
  renderDetail();
  document.documentElement.classList.remove('detail-open');
  renderAll();
  showToast('Suche gelöscht');
}

function isKaUrl(url) {
  try {
    const u = new URL(url);
    return /^https?:$/.test(u.protocol) && /(^|\.)kleinanzeigen\.de$/.test(u.hostname);
  } catch (e) { return false; }
}
function numOrNull(v) { const n = parseFloat(v); return isNaN(n) || n < 0 ? null : n; }
function splitWords(v) { return v.split(',').map(w => w.trim().toLowerCase()).filter(Boolean); }

// ── Einstellungen: Blacklist & Such-Sets ──────────
function addBlackword() {
  const inp = $('bl-input');
  const w = inp.value.trim().toLowerCase();
  inp.value = '';
  if (!w || cfg.blacklist.includes(w)) return;
  cfg.blacklist.push(w);
  renderBlacklist(); saveConfig(); renderAll();
}
function removeBlackword(i) { cfg.blacklist.splice(i, 1); renderBlacklist(); saveConfig(); renderAll(); }
function renderBlacklist() {
  $('bl-tags').innerHTML = cfg.blacklist.length
    ? cfg.blacklist.map((w, i) => `<button onclick="removeBlackword(${i})" title="Entfernen">${esc(w)}</button>`).join('')
    : '<span class="muted">Keine Wörter.</span>';
}

function saveSearchSet() {
  const active = cfg.searches.filter(s => s.enabled);
  if (!active.length) { showToast('Mindestens eine Suche aktivieren'); return; }
  const label = active.map(s => s.name).join(', ');
  if (cfg.search_sets.some(f => f.label === label)) { showToast('Dieses Set gibt es schon'); return; }
  cfg.search_sets.push({ label, searches: JSON.parse(JSON.stringify(active)) });
  renderSearchSets(); saveConfig();
  showToast('Such-Set gespeichert');
}
function loadSearchSet(i) {
  const f = cfg.search_sets[i];
  if (!f) return;
  cfg.searches = JSON.parse(JSON.stringify(f.searches)).map(s => Object.assign(s, { enabled: true }));
  draft = null; detailMode = selectedId ? 'listing' : null;
  saveConfig(); setFolder('all');
  showToast('Such-Set geladen');
}
function deleteSearchSet(i) { cfg.search_sets.splice(i, 1); renderSearchSets(); saveConfig(); }
function renderSearchSets() {
  $('set-list').innerHTML = cfg.search_sets.map((f, i) => `<div class="set-item">
      <span class="nm" title="${esc(f.label)}">${esc(f.label)}</span>
      <span class="muted">${f.searches.length} Suche${f.searches.length === 1 ? '' : 'n'}</span>
      <button class="btn sm" onclick="loadSearchSet(${i})">Laden</button>
      <button class="btn sm danger" onclick="deleteSearchSet(${i})">Löschen</button>
    </div>`).join('');
}

// ── Steuerung ─────────────────────────────────────
async function toggleRunning() {
  try {
    if (status.running) { await api('/api/stop', {}); showToast('Überwachung pausiert'); }
    else {
      await api('/api/start', {});
      showToast('Überwachung gestartet');
      if ('Notification' in window && Notification.permission === 'default') handleNotifBtn();
    }
    poll();
  } catch (e) { showToast(e.message); }
}
async function scanNow() {
  try { await api('/api/scan', {}); showToast('Suche wird gestartet …'); setTimeout(poll, 800); }
  catch (e) { showToast(e.message); }
}
async function clearListings() {
  try { await api('/api/clear', {}); showingMore = false; selectedId = null; detailMode = null; renderDetail(); showToast('Liste geleert'); poll(); }
  catch (e) { showToast(e.message); }
}
async function markAllSeen() {
  try { await api('/api/seen', {}); unread.clear(); listKey = ''; renderAll(); }
  catch (e) { showToast(e.message); }
}
async function testTelegram() {
  try { await api('/api/telegram/test', {}); showToast('Testnachricht gesendet'); }
  catch (e) { showToast(e.message); }
}

// ── Abfrage des Server-Zustands ───────────────────
async function poll() {
  if (!cfg) { await loadConfig(); if (!cfg) return; }
  let d;
  try { d = await api('/api/state?rev=' + (rev ?? '')); }
  catch (e) { setStatus('error', 'Server nicht erreichbar'); return; }

  const before = unread;
  status = d;
  unread = new Set(d.unread || []);
  if (d.listings) { listings = d.listings; rev = d.rev; }

  const fresh = [...unread].filter(id => !before.has(id));
  if (!firstPoll && fresh.length) {
    triggerFlash();
    sendBrowserNotifications(fresh);
  }
  firstPoll = false;

  renderAll();
  refreshDbAfterScan();
}

function setStatus(state, text, title) {
  $('status-dot').className = 'dot ' + state;
  $('status-text').textContent = text;
  $('state').title = title || text;
}

function updateStatus() {
  if (!cfg) return;
  const running = !!status.running;
  $('run-icon').innerHTML = running ? ICON.pause : ICON.play;
  $('run-label').textContent = running ? 'Pausieren' : 'Starten';
  $('btn-run').classList.toggle('primary', !running);
  $('btn-run').title = running ? 'Überwachung pausieren' : 'Überwachung starten';
  $('btn-scan').disabled = !running || status.scanning;
  $('tg-hint').textContent = status.telegram_configured
    ? 'Bot ist eingerichtet. Höchstens 10 Meldungen pro Suchlauf.'
    : 'Nicht eingerichtet: Umgebungsvariablen TELEGRAM_TOKEN und TELEGRAM_CHAT_ID setzen (siehe README).';

  const active = cfg.searches.filter(s => s.enabled).map(s => s.name).join(', ');
  if (!running) setStatus('idle', 'Pausiert');
  else if (status.status === 'blocked') setStatus('blocked', 'Kleinanzeigen blockiert – Pause', 'Kleinanzeigen blockiert gerade, neuer Versuch in einigen Minuten.');
  else if (status.scanning) setStatus('running', 'Suche läuft …', 'Überwache: ' + active);
  else {
    const rem = Math.max(0, Math.round((status.next_check_ts || 0) - Date.now() / 1000));
    const next = status.next_check_ts ? ' · nächste Suche in ' + Math.floor(rem / 60) + ':' + String(rem % 60).padStart(2, '0') : '';
    const state = status.status === 'warning' ? 'warning' : status.status === 'error' ? 'error' : 'running';
    setStatus(state, (state === 'running' ? 'Läuft' : 'Warnung') + next, 'Überwache: ' + active);
  }
}

// ── Tastatur ──────────────────────────────────────
function onKey(e) {
  if (view !== 'list' || e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (['input', 'select', 'textarea'].includes(tag)) return;
  if (e.key === 'Escape') { closeDetail(); closeSideMobile(); return; }
  if (!['ArrowDown', 'ArrowUp', 'j', 'k'].includes(e.key)) return;
  const rows = [...document.querySelectorAll('#rows .row')];
  if (!rows.length) return;
  e.preventDefault();
  let i = rows.findIndex(r => r.dataset.id === selectedId);
  i = e.key === 'ArrowDown' || e.key === 'j' ? Math.min(rows.length - 1, i + 1) : Math.max(0, i - 1);
  selectListing(rows[i].dataset.id);
  rows[i].scrollIntoView({ block: 'nearest' });
}

// ── Datenbank (Suchverlauf) ───────────────────────
const FIELD_LABELS = {
  title: 'Titel', price: 'Preis', location: 'Ort', posted: 'Datum', description: 'Beschreibung',
  image: 'Bild', badges: 'Merkmale', is_request: 'Gesuch', price_value: 'Preis (Zahl)',
};
let dbStats = null;
let dbSub = 'listings';
let dbFilter = { search: '', q: '', status: '', sort: 'new', field: '', errors: false };
let dbPage = 1;
let dbOpenId = null;          // aufgeklappte Anzeige
let dbLastCheck = null;       // für automatisches Nachladen nach einem Suchlauf
let dbTextTimer = null;

async function loadDb() {
  try {
    dbStats = await api('/api/history/stats');
    renderDbStats();
    fillDbSearchSelect();
    renderFolders();
  } catch (e) {
    $('db-stats').innerHTML = `<div class="banner bad">${esc(e.message)}</div>`;
    return;
  }
  loadDbTable();
}

function dbSearchName(id) {
  const s = dbStats && dbStats.searches.find(x => x.id === id);
  return s ? s.name : (findSearch(id) || {}).name || id;
}

function renderDbStats() {
  const t = dbStats.totals;
  const tiles = [
    ['Anzeigen', fmtNum(t.listings)],
    ['Suchen', fmtNum(t.searches)],
    ['Abrufe', fmtNum(t.runs) + (t.run_errors ? `<small>${fmtNum(t.run_errors)} Fehler</small>` : '')],
    ['Preisänderungen', fmtNum(t.price_changes)],
    ['Gesammelt seit', t.since ? fmtDate(t.since) : '—'],
    ['Größe', fmtBytes(t.size_bytes)],
  ];
  $('db-stats').innerHTML = tiles.map(([k, v]) =>
    `<div class="db-stat"><div class="db-stat-val">${v}</div><div class="db-stat-label">${k}</div></div>`).join('');

  $('db-searches').innerHTML = dbStats.searches.map(s => {
    const configured = !!findSearch(s.id);
    const where = s.type === 'url' ? 'Kategorie-URL' : (s.location ? esc(s.location) + (s.radius_km ? ' + ' + s.radius_km + ' km' : '') : 'ganz Deutschland');
    const prices = s.price_avg != null
      ? `${fmtEuro(Math.round(s.price_min_seen))} – ${fmtEuro(Math.round(s.price_max_seen))} · Ø ${fmtEuro(Math.round(s.price_avg))}` : 'keine Preise';
    const last = s.last_run ? (s.last_run_ok ? 'letzter Abruf ' : 'letzter Abruf fehlgeschlagen, ') + relTime(s.last_run) : 'noch kein Abruf';
    return `<button class="db-search-card${dbFilter.search === s.id ? ' active' : ''}${configured ? '' : ' removed'}"
        onclick="filterDbSearch('${esc(s.id)}')" title="Nur diese Suche anzeigen">
      <div class="dsc-name">${esc(s.name)}${configured ? '' : ' <small>(entfernt)</small>'}</div>
      <div class="dsc-meta">${where}</div>
      <div class="dsc-nums"><b>${fmtNum(s.listings)}</b> Anzeigen · <b>${fmtNum(s.new_24h || 0)}</b> neu (24 h) · <b>${fmtNum(s.current || 0)}</b> aktuell</div>
      <div class="dsc-meta">${prices}</div>
      <div class="dsc-meta">${fmtNum(s.runs)} Abrufe · ${last}</div>
    </button>`;
  }).join('') || '<span class="muted">Noch keine Daten – nach dem ersten Suchlauf erscheinen hier die Suchen.</span>';
}

function filterDbSearch(id) {
  dbFilter.search = dbFilter.search === id ? '' : id;
  dbPage = 1; dbOpenId = null;
  renderDbStats();
  fillDbSearchSelect();
  loadDbTable();
}

function showDbSub(sub) {
  dbSub = sub; dbPage = 1; dbOpenId = null;
  document.querySelectorAll('#db-subtabs button').forEach(b => b.classList.toggle('on', b.dataset.sub === sub));
  renderDbFilters();
  fillDbSearchSelect();
  loadDbTable();
}

// Filterleiste nur beim Wechsel neu zeichnen, damit Eingaben den Fokus behalten
function renderDbFilters() {
  const f = dbFilter;
  let html = `<select id="dbf-search" onchange="setDbFilter('search', this.value)"></select>`;
  if (dbSub === 'listings') {
    html += `<input type="search" placeholder="Titel, Beschreibung, Ort …" value="${esc(f.q)}" oninput="setDbText(this.value)">
      <select onchange="setDbFilter('status', this.value)">
        <option value="">alle</option>
        <option value="current"${f.status === 'current' ? ' selected' : ''}>noch in den Treffern</option>
        <option value="gone"${f.status === 'gone' ? ' selected' : ''}>nicht mehr in den Treffern</option>
      </select>
      <select onchange="setDbFilter('sort', this.value)">
        ${[['new', 'Neueste zuerst'], ['old', 'Älteste zuerst'], ['seen', 'Zuletzt gesehen'], ['price_asc', 'Preis aufsteigend'],
           ['price_desc', 'Preis absteigend'], ['duration', 'Längste Laufzeit'], ['changes', 'Meiste Änderungen']]
          .map(([v, l]) => `<option value="${v}"${f.sort === v ? ' selected' : ''}>${l}</option>`).join('')}
      </select>`;
  } else if (dbSub === 'changes') {
    html += `<select onchange="setDbFilter('field', this.value)">
        <option value="">alle Felder</option>
        ${Object.entries(FIELD_LABELS).filter(([k]) => k !== 'price_value')
          .map(([k, l]) => `<option value="${k}"${f.field === k ? ' selected' : ''}>${l}</option>`).join('')}
      </select>`;
  } else {
    html += `<label class="check"><input type="checkbox" ${f.errors ? 'checked' : ''} onchange="setDbFilter('errors', this.checked)"> nur Fehler</label>`;
  }
  $('db-filters').innerHTML = html;
}

function fillDbSearchSelect() {
  const sel = $('dbf-search');
  if (!sel) return;
  const opts = (dbStats ? dbStats.searches : []).map(s =>
    `<option value="${esc(s.id)}"${dbFilter.search === s.id ? ' selected' : ''}>${esc(s.name)}</option>`).join('');
  sel.innerHTML = `<option value="">alle Suchen</option>` + opts;
}

function setDbFilter(key, value) {
  dbFilter[key] = value;
  dbPage = 1; dbOpenId = null;
  if (key === 'search') renderDbStats();
  loadDbTable();
}
function setDbText(value) {
  clearTimeout(dbTextTimer);
  dbTextTimer = setTimeout(() => setDbFilter('q', value.trim()), 350);
}
function setDbPage(p) { dbPage = p; dbOpenId = null; loadDbTable(); $('view-db').scrollTop = 0; }

async function loadDbTable() {
  const f = dbFilter;
  const params = new URLSearchParams({ page: dbPage });
  if (f.search) params.set('search', f.search);
  let path;
  if (dbSub === 'listings') {
    path = 'listings';
    if (f.q) params.set('q', f.q);
    if (f.status) params.set('status', f.status);
    params.set('sort', f.sort);
  } else if (dbSub === 'changes') {
    path = 'changes';
    if (f.field) params.set('field', f.field);
  } else {
    path = 'runs';
    if (f.errors) params.set('errors', '1');
  }
  let d;
  try { d = await api('/api/history/' + path + '?' + params); }
  catch (e) { $('db-table').innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; $('db-pager').innerHTML = ''; return; }
  if (dbSub === 'listings') renderDbListings(d.items);
  else if (dbSub === 'changes') renderDbChanges(d.items);
  else renderDbRuns(d.items);
  renderDbPager(d);
  if (dbOpenId) openDbListing(dbOpenId, true);
}

function renderDbPager(d) {
  const pages = Math.max(1, Math.ceil(d.total / d.per_page));
  $('db-pager').innerHTML = `
    <button class="btn sm" ${d.page <= 1 ? 'disabled' : ''} onclick="setDbPage(${d.page - 1})">‹ Zurück</button>
    <span>Seite ${d.page} von ${pages} · ${fmtNum(d.total)} Einträge</span>
    <button class="btn sm" ${d.page >= pages ? 'disabled' : ''} onclick="setDbPage(${d.page + 1})">Weiter ›</button>`;
}

function renderDbListings(items) {
  if (!items.length) { $('db-table').innerHTML = '<div class="db-empty">Keine Anzeigen gefunden.</div>'; return; }
  $('db-table').innerHTML = `<table class="db-table"><thead><tr>
      <th></th><th>Anzeige</th><th class="num">Preis</th><th>Ort</th><th>Gefunden</th><th>Zuletzt gesehen</th><th>Status</th>
    </tr></thead><tbody>${items.map(l => {
      const thumb = l.image ? `<img src="${esc(l.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : svg('box');
      const extra = [l.search_ids.map(dbSearchName).join(', ')];
      if (l.is_request) extra.push('Gesuch');
      if (l.price_changes) extra.push(`Preis ${l.price_changes}× geändert`);
      if (l.other_changes) extra.push(`${l.other_changes} weitere Änderungen`);
      return `<tr class="db-row${dbOpenId === l.id ? ' open' : ''}" id="dbr-${esc(l.id)}" onclick="toggleDbListing('${esc(l.id)}')">
        <td class="db-thumb"><div class="thumb">${thumb}</div></td>
        <td class="db-title"><a href="${esc(l.url || '#')}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(l.title)}</a>
          <div class="db-tags">${esc(extra.join(' · '))}</div></td>
        <td class="num mono">${esc(l.price || '—')}</td>
        <td class="${isHighlighted(l.location) ? 'hl' : ''}">${esc(l.location || '')}</td>
        <td class="nowrap"><span class="num">${esc(fmtDateTime(l.first_seen))}</span><div class="db-sub">${esc(l.posted || '')}</div></td>
        <td class="nowrap" title="${esc(fmtDateTime(l.last_seen))}">${esc(relTime(l.last_seen))}<div class="db-sub">${fmtNum(l.seen_count)}× gesehen</div></td>
        <td class="nowrap">${l.is_current ? '<span class="db-status on">aktuell</span>'
          : `<span class="db-status" title="Nicht mehr auf der ersten Ergebnisseite – verkauft, gelöscht oder von neueren Anzeigen nach hinten geschoben">raus</span><div class="db-sub">nach ${esc(fmtDuration(l.last_seen - l.first_seen))}</div>`}</td>
      </tr>`;
    }).join('')}</tbody></table>`;
}

function toggleDbListing(id) {
  if (dbOpenId === id) { dbOpenId = null; $('dbd-' + id)?.remove(); $('dbr-' + id)?.classList.remove('open'); return; }
  if (dbOpenId) { $('dbd-' + dbOpenId)?.remove(); $('dbr-' + dbOpenId)?.classList.remove('open'); }
  dbOpenId = id;
  openDbListing(id);
}

async function openDbListing(id, silent) {
  const row = $('dbr-' + id);
  if (!row) return;
  row.classList.add('open');
  let l;
  try { l = await api('/api/history/listing?id=' + encodeURIComponent(id)); }
  catch (e) { if (!silent) showToast(e.message); return; }
  if (dbOpenId !== id) return;
  $('dbd-' + id)?.remove();
  const changes = l.changes.filter(c => c.field !== 'price_value');
  const tr = document.createElement('tr');
  tr.className = 'db-detail';
  tr.id = 'dbd-' + id;
  tr.innerHTML = `<td colspan="7"><div class="db-detail-body">
      ${l.image_large ? `<a href="${esc(l.image_large)}" target="_blank" rel="noopener"><img class="db-detail-img" src="${esc(l.image_large)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()"></a>` : ''}
      <div class="db-detail-info">
        ${l.description ? `<p class="pv-desc" style="margin-top:0">${esc(l.description)}</p>` : ''}
        <div class="kv"><span>Anzeigen-ID</span><div class="num">${esc(l.id)}</div>
          <span>Inseriert</span><div>${esc(l.posted || '—')}</div>
          <span>Erstmals gefunden</span><div class="num">${esc(fmtDateTime(l.first_seen))}</div>
          <span>Zuletzt gesehen</span><div><span class="num">${esc(fmtDateTime(l.last_seen))}</span> (${fmtNum(l.seen_count)}×)</div>
          ${l.badges.length ? `<span>Merkmale</span><div>${esc(l.badges.join(', '))}</div>` : ''}</div>
        <h4>Gefunden von</h4>
        <ul class="db-list">${l.searches.map(s => `<li><b>${esc(s.name || s.search_id)}</b> – ${esc(fmtDateTime(s.first_seen))} bis ${esc(fmtDateTime(s.last_seen))}, ${fmtNum(s.seen_count)}× gesehen${s.first_rank ? ', zuerst auf Platz ' + s.first_rank : ''}</li>`).join('')}</ul>
        <h4>Änderungen</h4>
        ${changes.length ? `<ul class="db-list">${changes.map(c => `<li>${esc(fmtDateTime(c.ts))} · <b>${esc(FIELD_LABELS[c.field] || c.field)}</b>: ${changeHTML(c)}</li>`).join('')}</ul>`
          : '<p class="db-sub">Keine Änderungen seit dem ersten Fund.</p>'}
        <p style="margin-top:.9rem"><a class="btn sm" href="${esc(l.url)}" target="_blank" rel="noopener">${svg('link')}Auf Kleinanzeigen öffnen</a></p>
      </div></div></td>`;
  row.after(tr);
}

function changeHTML(c) {
  const oldV = c.field === 'image' ? 'altes Bild' : (c.old_value ?? '—');
  const newV = c.field === 'image' ? 'neues Bild' : (c.new_value ?? '—');
  let cls = '';
  if (c.field === 'price') {
    const a = parsePrice(c.old_value), b = parsePrice(c.new_value);
    if (a != null && b != null && a !== b) cls = b < a ? 'down' : 'up';
  }
  return `<span class="old">${esc(shorten(oldV))}</span> → <b class="${cls}">${esc(shorten(newV))}</b>`;
}

function renderDbChanges(items) {
  if (!items.length) { $('db-table').innerHTML = '<div class="db-empty">Noch keine Änderungen erfasst.</div>'; return; }
  $('db-table').innerHTML = `<table class="db-table"><thead><tr><th>Zeit</th><th>Anzeige</th><th>Feld</th><th>Änderung</th></tr></thead>
    <tbody>${items.map(c => `<tr>
      <td class="nowrap num">${esc(fmtDateTime(c.ts))}</td>
      <td class="db-title"><a href="${esc(c.url || '#')}" target="_blank" rel="noopener">${esc(c.title)}</a></td>
      <td>${esc(FIELD_LABELS[c.field] || c.field)}</td>
      <td>${changeHTML(c)}</td></tr>`).join('')}</tbody></table>`;
}

function renderDbRuns(items) {
  if (!items.length) { $('db-table').innerHTML = '<div class="db-empty">Keine Abrufe.</div>'; return; }
  $('db-table').innerHTML = `<table class="db-table"><thead><tr><th>Zeit</th><th>Suche</th><th>Ergebnis</th><th class="num">Anzeigen</th><th class="num">TOP</th><th>Abruf</th></tr></thead>
    <tbody>${items.map(r => `<tr>
      <td class="nowrap num">${esc(fmtDateTime(r.ts))}</td>
      <td>${esc(r.name || r.search_id)}</td>
      <td>${r.ok ? (r.no_results ? '<span class="db-status">keine Treffer</span>' : '<span class="db-status on">ok</span>')
        : `<span class="db-status bad">${esc(r.error || 'Fehler')}</span>`}</td>
      <td class="num mono">${r.listings ?? '—'}</td>
      <td class="num mono">${r.top_ads ?? '—'}</td>
      <td>${r.request_url ? `<a class="link" href="${esc(r.request_url)}" target="_blank" rel="noopener">Suche öffnen</a>` : ''}</td>
    </tr>`).join('')}</tbody></table>`;
}

// Nach jedem Suchlauf Kennzahlen und (auf Seite 1, ohne offene Details) die Tabelle auffrischen
function refreshDbAfterScan() {
  if (view !== 'db' || !status.last_check_ts || status.last_check_ts === dbLastCheck) return;
  const first = dbLastCheck === null;
  dbLastCheck = status.last_check_ts;
  if (first) return;
  api('/api/history/stats').then(d => { dbStats = d; renderDbStats(); renderFolders(); }).catch(() => {});
  if (dbPage === 1 && !dbOpenId) loadDbTable();
}

// ── Browser-Benachrichtigungen ────────────────────
function updateNotifBtn() {
  const btn = $('notif-btn');
  if (!('Notification' in window)) { btn.textContent = 'In diesem Browser nicht verfügbar'; btn.disabled = true; return; }
  const p = Notification.permission;
  if (p === 'granted') { btn.textContent = 'Aktiv'; btn.disabled = true; btn.classList.add('on'); }
  else if (p === 'denied') { btn.textContent = 'Im Browser blockiert'; btn.disabled = true; }
  else { btn.textContent = 'Aktivieren'; btn.disabled = false; }
}
async function handleNotifBtn() {
  if (!('Notification' in window) || Notification.permission !== 'default') return;
  await Notification.requestPermission();
  updateNotifBtn();
}
function sendBrowserNotifications(ids) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const idSet = new Set(ids);
  const items = listings.filter(l => idSet.has(l.id) && passesFilters(l));
  const bySource = {};
  items.forEach(l => { const k = searchNames(l) || 'Suche'; bySource[k] = (bySource[k] || 0) + 1; });
  Object.entries(bySource).forEach(([src, n]) => {
    const body = n === 1 ? `Neue Anzeige: „${src}“` : `${n} neue Anzeigen: „${src}“`;
    const note = new Notification('Kleinanzeigen-Radar', { body, silent: true, tag: 'kr-' + src });
    setTimeout(() => note.close(), 6000);
  });
}

// ── Theme & Hilfsfunktionen ───────────────────────
function toggleTheme() {
  const dark = document.documentElement.classList.toggle('dark');
  try { localStorage.setItem('kr-theme', dark ? 'dark' : 'light'); } catch (e) { /* egal */ }
  document.querySelector('meta[name=theme-color]').content = dark ? '#161616' : '#ffffff';
}
function triggerFlash() {
  document.body.classList.remove('flash');
  void document.body.offsetWidth;
  document.body.classList.add('flash');
  setTimeout(() => document.body.classList.remove('flash'), 1500);
}
let toastTimer = null;
function showToast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}
function fmtEuro(v) { return Number(v).toLocaleString('de-DE') + ' €'; }
function fmtNum(v) { return Number(v || 0).toLocaleString('de-DE'); }
function fmtBytes(b) {
  if (!b) return '0 KB';
  return b >= 1048576 ? (b / 1048576).toLocaleString('de-DE', { maximumFractionDigits: 1 }) + ' MB'
    : Math.ceil(b / 1024).toLocaleString('de-DE') + ' KB';
}
function fmtDate(ts) { return new Date(ts * 1000).toLocaleDateString('de-DE'); }
function fmtClock(ts) { return new Date(ts * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }); }
function fmtDateTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: sameYear ? undefined : 'numeric' })
    + ' ' + fmtClock(ts);
}
function fmtDuration(sec) {
  sec = Math.max(0, sec || 0);
  if (sec < 3600) return Math.max(1, Math.round(sec / 60)) + ' Min.';
  if (sec < 86400) return Math.round(sec / 3600) + ' Std.';
  const d = Math.floor(sec / 86400), h = Math.round((sec % 86400) / 3600);
  return d + ' T' + (h ? ' ' + h + ' Std.' : '');
}
function relTime(ts) {
  if (!ts) return '—';
  const sec = Date.now() / 1000 - ts;
  return sec < 60 ? 'gerade eben' : 'vor ' + fmtDuration(sec);
}
function parsePrice(text) {
  if (!text) return null;
  if (/verschenk/i.test(text)) return 0;
  const m = String(text).match(/\d[\d.]*(?:,\d+)?/);
  return m ? parseFloat(m[0].replace(/\./g, '').replace(',', '.')) : null;
}
function shorten(s, n = 120) { s = String(s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
