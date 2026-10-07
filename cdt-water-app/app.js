(() => {
'use strict';
const V = window.APP_V, $ = s => document.querySelector(s);
const LS = { get(k, d) { try { const v = localStorage.getItem('cdtw.' + k); return v == null ? d : JSON.parse(v) } catch (e) { return d } },
             set(k, v) { try { localStorage.setItem('cdtw.' + k, JSON.stringify(v)) } catch (e) {} } };
const LEVELS = ['Dry', 'Stagnant', 'Trickle', 'Flowing', 'Abundant'];
const DCOL = ['#c8603e', '#e8a27c', '#e8c9a8', '#8fb09c', '#2d4a3e'];
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

let D = null, T = null, S = [], PG = [];
const st = { doy: 0, date: '', nobo: LS.get('nobo', true), good: LS.get('good', false), thr: LS.get('thr', 75),
             view: 'map', gps: null, plan: LS.get('plan', false), manual: LS.get('manual', 0), sel: null, open: new Set() };

/* ---------- date / probabilities ---------- */
function todayStr() { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0') }
function doyOfStr(s) { const [y, m, d] = s.split('-').map(Number); return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(y, 0, 0)) / 864e5) }
function interp(arr, doy, grid) {
  const g0 = grid[0], step = grid[1] - grid[0], x = clamp((doy - g0) / step, 0, grid.length - 1), i = Math.min(grid.length - 2, Math.floor(x)), f = x - i;
  return arr[i] + (arr[i + 1] - arr[i]) * f;
}
function cumAt(s, doy) { return [0, 1, 2, 3].map(k => interp(s.c[k], doy, D.grid)) }    // P(q>=1..4) %
function distAt(s, doy) { const c = cumAt(s, doy); return [100 - c[0], c[0] - c[1], c[1] - c[2], c[2] - c[3], c[3]].map(v => Math.max(0, v)) }
function pgood(s) { return interp(s.c[2], st.doy, D.grid) }                               // P(q>=3) %
function recompute() { PG = S.map(pgood) }

/* ---------- colors ---------- */
function lerp(a, b, t) { return Math.round(a + (b - a) * t) }
const STOPS = [[0, [200, 96, 62]], [0.5, [232, 201, 168]], [0.78, [143, 176, 156]], [1, [45, 74, 62]]];
function colorFor(p) {
  p = clamp(p / 100, 0, 1);
  for (let i = 1; i < STOPS.length; i++) if (p <= STOPS[i][0]) {
    const [p0, c0] = STOPS[i - 1], [p1, c1] = STOPS[i], t = (p - p0) / (p1 - p0);
    return `rgb(${lerp(c0[0], c1[0], t)},${lerp(c0[1], c1[1], t)},${lerp(c0[2], c1[2], t)})`;
  }
  return 'rgb(45,74,62)';
}

/* ---------- track ---------- */
const R2D = Math.PI / 180;
const mercY = lat => Math.log(Math.tan(Math.PI / 4 + lat * R2D / 2));
function hav(la1, lo1, la2, lo2) { const a = Math.sin((la2 - la1) * R2D / 2) ** 2 + Math.cos(la1 * R2D) * Math.cos(la2 * R2D) * Math.sin((lo2 - lo1) * R2D / 2) ** 2; return 2 * 6371008.8 * Math.asin(Math.sqrt(a)) }
function buildTrack(t, lm) {
  const n = t.dlat.length + 1, lat = new Float64Array(n), lon = new Float64Array(n), x = new Float64Array(n), y = new Float64Array(n), mi = new Float64Array(n);
  let la = t.lat0, lo = t.lon0; lat[0] = la / 1e5; lon[0] = lo / 1e5;
  for (let i = 1; i < n; i++) { la += t.dlat[i - 1]; lo += t.dlon[i - 1]; lat[i] = la / 1e5; lon[i] = lo / 1e5; mi[i] = mi[i - 1] + hav(lat[i - 1], lon[i - 1], lat[i], lon[i]) / 1609.344 }
  if (lm) { let k = 1; for (let i = 0; i < n; i++) { const m = mi[i]; while (k < lm.thin.length - 1 && m > lm.thin[k]) k++; const a0 = lm.thin[k - 1], b0 = lm.thin[k]; mi[i] = lm.true[k - 1] + (lm.true[k] - lm.true[k - 1]) * ((m - a0) / ((b0 - a0) || 1)) } }   // thinned-track miles -> true route miles
  for (let i = 0; i < n; i++) { x[i] = lon[i] * R2D; y[i] = mercY(lat[i]) }
  return { n, lat, lon, x, y, mi, total: mi[n - 1] };
}
function snap(lat, lon) {
  const cl = Math.cos(lat * R2D); let best = 1e18, bi = 0;
  for (let i = 0; i < T.n; i++) { const dx = (T.lon[i] - lon) * cl, dy = T.lat[i] - lat, d = dx * dx + dy * dy; if (d < best) { best = d; bi = i } }
  let bd = Math.sqrt(best) * 111320, bm = T.mi[bi];
  for (const [a, b] of [[bi - 1, bi], [bi, bi + 1]]) {
    if (a < 0 || b >= T.n) continue;
    const ax = (T.lon[a] - lon) * cl * 111320, ay = (T.lat[a] - lat) * 110574, bx = (T.lon[b] - lon) * cl * 111320, by = (T.lat[b] - lat) * 110574;
    const vx = bx - ax, vy = by - ay, L2 = vx * vx + vy * vy, t = L2 ? clamp(-(ax * vx + ay * vy) / L2, 0, 1) : 0, d = Math.hypot(ax + t * vx, ay + t * vy);
    if (d < bd) { bd = d; bm = T.mi[a] + t * (T.mi[b] - T.mi[a]) }
  }
  return { mile: bm, off: bd / 1609.344, idx: bi };
}
function idxAtMile(m) { let lo = 0, hi = T.n - 1; while (lo < hi) { const mid = (lo + hi) >> 1; if (T.mi[mid] < m) lo = mid + 1; else hi = mid } return lo }
function xyAtMile(m) { m = clamp(m, 0, T.total); const i = Math.max(1, idxAtMile(m)), a = i - 1, f = (m - T.mi[a]) / ((T.mi[i] - T.mi[a]) || 1); return { x: T.x[a] + (T.x[i] - T.x[a]) * f, y: T.y[a] + (T.y[i] - T.y[a]) * f, lat: T.lat[a] + (T.lat[i] - T.lat[a]) * f, lon: T.lon[a] + (T.lon[i] - T.lon[a]) * f } }

/* ---------- position ---------- */
function effPos() {
  if (!st.plan && st.gps) return { mile: st.gps.mile, off: st.gps.off, src: 'gps', acc: st.gps.acc, x: st.gps.x, y: st.gps.y, lat: st.gps.lat, lon: st.gps.lon };
  if (st.plan || !st.gps) { const m = clamp(st.manual, 0, T.total), p = xyAtMile(m); return { mile: m, off: 0, src: 'plan', x: p.x, y: p.y, lat: p.lat, lon: p.lon } }
}
function onGps(p) {
  const { latitude: lat, longitude: lon, accuracy: acc } = p.coords; const s = snap(lat, lon);
  st.gps = { lat, lon, acc, mile: s.mile, off: s.off, x: lon * R2D, y: mercY(lat), t: Date.now() };
  if (!st.plan) st.manual = s.mile;
  status(); if (st.view === 'map') draw(); else if (st.view === 'list') renderList();
}
let watchId = null;
function startGps(centre) {
  if (!('geolocation' in navigator)) { toast('GPS not available in this browser'); return }
  if (watchId == null) watchId = navigator.geolocation.watchPosition(onGps, e => { st.gpsErr = e.code === 1 ? 'GPS permission denied' : 'no GPS fix yet'; status() }, { enableHighAccuracy: true, maximumAge: 15000, timeout: 30000 });
  if (centre) navigator.geolocation.getCurrentPosition(p => { onGps(p); centerOnMe() }, () => toast('No GPS fix yet'), { enableHighAccuracy: true, timeout: 20000 });
}
function status(msg) {
  const el = $('#status'); if (!T) return;
  const p = effPos(); let t = msg || '';
  if (!msg) { t = p.src === 'gps' ? (st.gpsErr = '', `Mile ${p.mile.toFixed(1)} · GPS ±${Math.round(p.acc)} m${p.off > 1 ? ` · ${p.off.toFixed(1)} mi off route` : ''}`) : `Plan: mile ${p.mile.toFixed(1)}${st.gpsErr ? ' \u00b7 ' + st.gpsErr : ''}`; if (st.doy < 91 || st.doy > 301) t += ' · off-season' }
  el.textContent = t;
}

/* ---------- map ---------- */
const cv = $('#cv'), ctx = cv.getContext('2d'); let W = 0, H = 0, DPR = 1;
const view = { cx: 0, cy: 0, s: 1 };
const px = x => (x - view.cx) * view.s + W / 2, py = y => -(y - view.cy) * view.s + H / 2;
const ux = X => (X - W / 2) / view.s + view.cx, uy = Y => -(Y - H / 2) / view.s + view.cy;
function milesVisible() { const mid = uy(H / 2), lat = (2 * Math.atan(Math.exp(mid)) - Math.PI / 2) / R2D; return (H / view.s) * 6371008.8 * Math.cos(lat * R2D) / 1609.344 }
function scaleForMiles(mi, lat) { return H / (mi * 1609.344 / (6371008.8 * Math.cos(lat * R2D))) }
function resize() { const r = cv.getBoundingClientRect(); DPR = window.devicePixelRatio || 1; W = r.width; H = r.height; cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR); if (T) draw() }
function fitTrail() { let y0 = 1e9, y1 = -1e9, x0 = 1e9, x1 = -1e9; for (let i = 0; i < T.n; i += 20) { y0 = Math.min(y0, T.y[i]); y1 = Math.max(y1, T.y[i]); x0 = Math.min(x0, T.x[i]); x1 = Math.max(x1, T.x[i]) } view.cx = (x0 + x1) / 2; view.cy = (y0 + y1) / 2; view.s = Math.min(H / (y1 - y0), W / (x1 - x0)) / 1.08; draw() }
function centerOnMe() { const p = effPos(); view.cx = p.x; view.cy = p.y; view.s = scaleForMiles(35, p.lat); draw() }
function draw() {
  if (!T || !W) return; ctx.setTransform(DPR, 0, 0, DPR, 0, 0); ctx.clearRect(0, 0, W, H);
  const pos = effPos(), pi = idxAtMile(pos.mile), dir = st.nobo ? 1 : -1;
  // track in two tones: ahead / behind
  const path = (a, b) => { ctx.beginPath(); let lx = -1e9, ly = -1e9, started = false; for (let i = a; i <= b; i++) { const X = px(T.x[i]), Y = py(T.y[i]); if (!started) { ctx.moveTo(X, Y); started = true; lx = X; ly = Y; continue } if (Math.abs(X - lx) + Math.abs(Y - ly) >= 1.3 || i === b) { ctx.lineTo(X, Y); lx = X; ly = Y } } ctx.stroke() };
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  const behind = dir === 1 ? [0, pi] : [pi, T.n - 1], ahead = dir === 1 ? [pi, T.n - 1] : [0, pi];
  ctx.strokeStyle = '#cbbfae'; ctx.lineWidth = 3; path(behind[0], behind[1]);
  ctx.strokeStyle = '#4a4540'; ctx.lineWidth = 3; path(ahead[0], ahead[1]);
  // mile ticks
  const mv = milesVisible(), step = mv < 40 ? 5 : mv < 120 ? 10 : mv < 350 ? 25 : mv < 900 ? 100 : 250;
  ctx.font = '11px -apple-system,Segoe UI,Roboto,sans-serif'; ctx.fillStyle = '#4a4540'; ctx.textBaseline = 'middle';
  for (let m = 0; m <= T.total; m += step) { const p = xyAtMile(m), X = px(p.x), Y = py(p.y); if (X < -20 || X > W + 20 || Y < -20 || Y > H + 20) continue; ctx.fillStyle = '#faf8f4'; ctx.strokeStyle = '#4a4540'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(X, Y, 2.6, 0, 7); ctx.fill(); ctx.stroke(); ctx.fillStyle = '#4a4540'; ctx.fillText(m, X + 6, Y - 7) }
  // sources
  const rad = clamp(2.6 + (1 / (mv + 4)) * 90, 3.2, 9), labels = [], showNames = mv < 22; ctx.textBaseline = 'alphabetic';
  const vis = [];
  for (let i = 0; i < S.length; i++) { const s = S[i], p = PG[i]; if (st.good && p < st.thr && st.sel !== i) continue; const X = px(s.x), Y = py(s.y); if (X < -12 || X > W + 12 || Y < -12 || Y > H + 12) continue; vis.push([i, X, Y]) }
  vis.sort((a, b) => PG[a[0]] - PG[b[0]]);   // draw best on top
  for (const [i, X, Y] of vis) {
    ctx.beginPath(); ctx.arc(X, Y, rad, 0, 7); ctx.fillStyle = colorFor(PG[i]); ctx.fill(); ctx.lineWidth = 1.2; ctx.strokeStyle = 'rgba(26,46,38,.55)'; ctx.stroke();
    if (st.sel === i) { ctx.beginPath(); ctx.arc(X, Y, rad + 4, 0, 7); ctx.lineWidth = 2.5; ctx.strokeStyle = '#e87d5a'; ctx.stroke() }
    if (showNames || st.sel === i) { const t = S[i].name; const w = ctx.measureText(t).width; const r = [X + rad + 3, Y - 12, w, 14]; if (!labels.some(q => r[0] < q[0] + q[2] && r[0] + r[2] > q[0] && r[1] < q[1] + q[3] && r[1] + r[3] > q[1]) || st.sel === i) { labels.push(r); ctx.fillStyle = 'rgba(250,248,244,.85)'; ctx.fillRect(r[0] - 2, r[1] - 1, w + 4, 14); ctx.fillStyle = '#1c1c1c'; ctx.fillText(t, r[0], r[1] + 10) } }
  }
  // you
  const X = px(pos.x), Y = py(pos.y);
  if (pos.src === 'gps' && pos.acc) { const ar = pos.acc / ((6371008.8 * Math.cos(pos.lat * R2D)) / view.s); ctx.beginPath(); ctx.arc(X, Y, Math.max(ar, 8), 0, 7); ctx.fillStyle = 'rgba(37,99,235,.15)'; ctx.fill() }
  ctx.beginPath(); ctx.arc(X, Y, 8.5, 0, 7); ctx.fillStyle = '#fff'; ctx.fill();
  ctx.beginPath(); ctx.arc(X, Y, 6, 0, 7); ctx.fillStyle = pos.src === 'gps' ? '#2563eb' : '#fff'; ctx.fill();
  if (pos.src !== 'gps') { ctx.lineWidth = 2.5; ctx.strokeStyle = '#2563eb'; ctx.setLineDash([3, 2]); ctx.stroke(); ctx.setLineDash([]) }
}
// gestures
const ptrs = new Map(); let tapStart = null, pinch0 = null;
cv.addEventListener('pointerdown', e => { cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, { x: e.offsetX, y: e.offsetY }); if (ptrs.size === 1) tapStart = { x: e.offsetX, y: e.offsetY, t: Date.now(), moved: false }; else { tapStart = null; const [a, b] = [...ptrs.values()]; pinch0 = { d: Math.hypot(a.x - b.x, a.y - b.y), s: view.s } } });
cv.addEventListener('pointermove', e => {
  if (!ptrs.has(e.pointerId)) return; const p = ptrs.get(e.pointerId), dx = e.offsetX - p.x, dy = e.offsetY - p.y; p.x = e.offsetX; p.y = e.offsetY;
  if (ptrs.size === 1) { if (tapStart && Math.hypot(e.offsetX - tapStart.x, e.offsetY - tapStart.y) > 6) tapStart.moved = true; view.cx -= dx / view.s; view.cy += dy / view.s; draw() }
  else if (ptrs.size === 2 && pinch0) { const [a, b] = [...ptrs.values()], d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2; const wx = ux(mx), wy = uy(my); view.s = clamp(pinch0.s * d / pinch0.d, 20, 6e6); view.cx = wx - (mx - W / 2) / view.s; view.cy = wy + (my - H / 2) / view.s; draw() }
});
function endPtr(e) { if (tapStart && !tapStart.moved && Date.now() - tapStart.t < 450) tap(e.offsetX, e.offsetY); ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch0 = null; tapStart = null }
cv.addEventListener('pointerup', endPtr); cv.addEventListener('pointercancel', e => { ptrs.delete(e.pointerId); pinch0 = null; tapStart = null });
cv.addEventListener('wheel', e => { e.preventDefault(); const f = Math.exp(-e.deltaY * 0.0015), wx = ux(e.offsetX), wy = uy(e.offsetY); view.s = clamp(view.s * f, 20, 6e6); view.cx = wx - (e.offsetX - W / 2) / view.s; view.cy = wy + (e.offsetY - H / 2) / view.s; draw() }, { passive: false });
function zoomBy(f) { view.s = clamp(view.s * f, 20, 6e6); draw() }
function tap(X, Y) {
  let best = -1, bd = 26;
  for (let i = 0; i < S.length; i++) { if (st.good && PG[i] < st.thr) continue; const dx = px(S[i].x) - X, dy = py(S[i].y) - Y, d = Math.hypot(dx, dy); if (d < bd) { bd = d; best = i } }
  if (best >= 0) { st.sel = best; showSheet(best) } else { st.sel = null; $('#sheet').style.display = 'none' }
  draw();
}

/* ---------- detail card ---------- */
function spark(s) {
  const w = 300, h = 74, L = 22, R = 6, T = 6, B = 16, g = D.grid, X = d => L + (d - g[0]) / (g[g.length - 1] - g[0]) * (w - L - R), Y = v => T + (100 - v) / 100 * (h - T - B);
  const line = (arr, col) => `<path d="${arr.map((v, i) => (i ? 'L' : 'M') + X(g[i]).toFixed(1) + ',' + Y(v).toFixed(1)).join('')}" fill="none" stroke="${col}" stroke-width="2.2" stroke-linejoin="round"/>`;
  const dry = s.c[0].map(v => 100 - v), mon = [['Apr', 91], ['May', 121], ['Jun', 152], ['Jul', 182], ['Aug', 213], ['Sep', 244], ['Oct', 274]];
  const cd = clamp(st.doy, g[0], g[g.length - 1]);
  const band = s.vol ? `<path d="${s.vol[1].map((v, i) => (i ? 'L' : 'M') + X(g[i]).toFixed(1) + ',' + Y(v).toFixed(1)).join('')}${s.vol[0].map((v, i) => 'L' + X(g[g.length - 1 - i]).toFixed(1) + ',' + Y(s.vol[0][s.vol[0].length - 1 - i]).toFixed(1)).join('')}Z" fill="#8fb09c" fill-opacity=".30"/>` : '';
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Seasonal chance of good water"><g stroke="#e3d9cc">${[0, 50, 100].map(v => `<line x1="${L}" x2="${w - R}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('')}</g>
  ${[0, 50, 100].map(v => `<text x="${L - 3}" y="${Y(v) + 3}" text-anchor="end">${v}</text>`).join('')}${mon.map(([m, d]) => `<text x="${X(d)}" y="${h - 3}" text-anchor="middle">${m}</text>`).join('')}
  ${band}${line(dry, '#c8603e')}${line(s.c[2], '#2d4a3e')}<line x1="${X(cd)}" x2="${X(cd)}" y1="${T}" y2="${h - B}" stroke="#2563eb" stroke-width="1.5" stroke-dasharray="3 2"/></svg>
  <div class="chips"><span class="chip"><i style="background:#2d4a3e"></i>Good water</span><span class="chip"><i style="background:#c8603e"></i>Dry</span><span class="chip"><i style="background:#2563eb"></i>Selected date</span>${s.vol ? '<span class="chip"><i style="background:#8fb09c;opacity:.5"></i>Range across years</span>' : ''}</div>`;
}
function distBar(s) { const d = distAt(s, st.doy); return `<div class="dist" title="${LEVELS.map((l, i) => l + ' ' + Math.round(d[i]) + '%').join(', ')}">${d.map((v, i) => `<i style="width:${v}%;background:${DCOL[i]}"></i>`).join('')}</div>` }
function distText(s) { const d = distAt(s, st.doy); return LEVELS.map((l, i) => `<span class="chip"><i style="background:${DCOL[i]}"></i>${l} ${Math.round(d[i])}%</span>`).join('') }
function confNote(s) { return s.conf === 0 ? 'Limited data for this source: treat the estimate as rough (it leans on nearby sources).' : '' }
function volNote(s, i) { return s.vol ? `<div class="vnote"><b>Varies a lot year to year.</b> Some years this source is dry and others it flows well. In any given year the chance of good water around this date could be anywhere from ${Math.round(interp(s.vol[0], st.doy, D.grid))}% to ${Math.round(interp(s.vol[1], st.doy, D.grid))}% (typical year: ${Math.round(PG[i])}%).</div>` : '' }
function detail(i) {
  const s = S[i], g = Math.round(PG[i]);
  const vn = volNote(s, i);
  const cn = confNote(s); return `${vn}${distBar(s)}<div class="chips">${distText(s)}</div>${spark(s)}${cn ? `<div class="meta">${cn}</div>` : ''}`;
}
function showSheet(i) {
  const s = S[i], pos = effPos(), dm = (s.mile - pos.mile) * (st.nobo ? 1 : -1);
  $('#sheetBody').innerHTML = `<h2>${esc(s.name)}</h2><div class="meta">Mile ${s.mile.toFixed(1)} · ${s.elev != null ? s.elev.toLocaleString() + ' ft · ' : ''}${D.sec[s.sec]}${s.off > 0.25 ? ` · ${s.off.toFixed(1)} mi off route` : ''}<br>${dm >= 0 ? dm.toFixed(1) + ' mi ahead' : (-dm).toFixed(1) + ' mi behind'} · <span class="pct">${Math.round(PG[i])}%</span> chance of good water on ${st.date}</div>${s.alts ? `<div class="meta">Also listed as: ${esc([...new Set(s.alts)].slice(0, 4).join(', '))}</div>` : ''}${detail(i)}`;
  $('#sheet').style.display = 'block';
}

/* ---------- list view ---------- */
function renderList() {
  const pos = effPos(), dir = st.nobo ? 1 : -1, el = $('#listView');
  const rel = S.map((s, i) => ({ i, dm: (s.mile - pos.mile) * dir })).filter(o => !st.good || PG[o.i] >= st.thr);
  const ahead = rel.filter(o => o.dm >= -0.05 && o.dm <= 120).sort((a, b) => a.dm - b.dm).slice(0, 10);
  const behind = rel.filter(o => o.dm < -0.05 && o.dm >= -40).sort((a, b) => b.dm - a.dm).slice(0, 3);
  const nextGood = S.map((s, i) => ({ i, dm: (s.mile - pos.mile) * dir })).filter(o => o.dm >= -0.05 && PG[o.i] >= st.thr).sort((a, b) => a.dm - b.dm)[0];
  const row = (o, prevDm) => {
    const s = S[o.i], g = Math.round(PG[o.i]), open = st.open.has(o.i), gap = prevDm == null ? '' : (() => { const d = Math.abs(o.dm - prevDm); return `<div class="gap" ${d > 15 ? 'style="color:#c8603e;font-weight:700"' : ''}>— ${d.toFixed(1)} mi${d > 15 ? ' carry' : ''} —</div>` })();
    return `${gap}<div class="row" data-i="${o.i}"><div class="hd"><span class="nm">${esc(s.name)}</span><span class="dm">${o.dm >= 0 ? '+' : '−'}${Math.abs(o.dm).toFixed(1)} mi</span></div>
      <div class="sub"><span class="pct" style="color:${g >= 50 ? '#2d4a3e' : '#c8603e'}">${g}% good</span> · mile ${s.mile.toFixed(0)}${s.elev != null ? ' · ' + s.elev.toLocaleString() + ' ft' : ''}${s.off > 0.25 ? ' · ' + s.off.toFixed(1) + ' mi off route' : ''}${s.conf === 0 ? ' · limited data' : ''}${s.vol ? ' · <b style="color:#c8603e">varies by year</b>' : ''}</div>${distBar(s)}${open ? `${volNote(s, o.i)}<div class="chips">${distText(s)}</div>${spark(s)}` : ''}</div>`;
  };
  let h = `<div class="you"><b>Mile ${pos.mile.toFixed(1)}</b> · ${st.nobo ? 'northbound' : 'southbound'}<small>${pos.src === 'gps' ? `GPS ±${Math.round(pos.acc)} m${pos.off > 1 ? ' · ' + pos.off.toFixed(1) + ' mi from the route' : ''}` : 'Planning (no GPS)'} · ${st.date}</small>
    <input type="range" id="mileRange" min="0" max="${Math.round(T.total)}" step="0.5" value="${pos.mile.toFixed(1)}" aria-label="Plan from mile">
    <div class="btnrow" style="display:flex;gap:6px;margin-top:6px"><button id="useGps" ${st.plan ? '' : 'aria-pressed="true"'}>${st.plan ? 'Use GPS' : 'Using GPS ✓'}</button><small style="align-self:center">${st.plan ? 'Planning from slider' : 'Drag slider to plan ahead'}</small></div></div>`;
  if (!st.gps && !st.plan) h += `<div class="warn">No GPS fix yet. Showing mile ${pos.mile.toFixed(0)} — drag the slider to plan, or step outside for a fix.</div>`;
  if (nextGood) h += `<div class="meta" style="margin-top:8px">Next likely-good water (≥${st.thr}%): <b>${esc(S[nextGood.i].name)}</b>, ${nextGood.dm.toFixed(1)} mi ahead</div>`;
  h += `<h3>Ahead</h3>`; let prev = 0; if (!ahead.length) h += `<div class="meta">${st.good ? 'No sources above your threshold in the next 120 miles.' : 'No sources in the next 120 miles.'}</div>`;
  ahead.forEach((o, k) => { h += row(o, k ? prev : null); prev = o.dm });
  h += `<h3>Behind you</h3>`; if (!behind.length) h += `<div class="meta">Nothing in the last 40 miles.</div>`; behind.forEach(o => h += row(o, null));
  h += `<p class="meta" style="margin-top:14px">Probabilities describe typical conditions for the date, based on past conditions. They are not current conditions. Never rely on this as your only water information.</p>`;
  el.innerHTML = h;
  el.querySelectorAll('.row').forEach(r => r.addEventListener('click', () => { const i = +r.dataset.i; st.open.has(i) ? st.open.delete(i) : st.open.add(i); const sc = el.scrollTop; renderList(); el.scrollTop = sc }));
  const rg = $('#mileRange'); rg.addEventListener('input', e => { st.plan = true; st.manual = +e.target.value; LS.set('plan', true); LS.set('manual', st.manual); status(); const sc = el.scrollTop; renderList(); el.scrollTop = sc; const r2 = $('#mileRange'); r2.focus() });
  $('#useGps').addEventListener('click', () => { if (st.plan) { st.plan = false; LS.set('plan', false); if (st.gps) st.manual = st.gps.mile; startGps(false); status(); renderList() } });
}

/* ---------- settings ---------- */
function renderSettings() {
  $('#setBody').innerHTML = `<h2>Settings</h2><div class="set">
    <label>Good-water threshold <b id="thrv">${st.thr}%</b></label><input type="range" id="thr" min="40" max="95" step="5" value="${st.thr}" style="width:100%">
    <div class="meta">Sources at or above this chance count as "likely good" when filtering and when finding the next good water.</div>
    <div class="btnrow"><button id="upd">Check for update</button><button id="gpsb">Enable GPS</button></div>
    <div id="offstate" class="meta" style="margin-top:10px">Checking offline status…</div>
    <h3>About</h3><div class="meta">${D.template ? '<b style="color:#c8603e">Template data.</b> Synthetic sample data so the app runs out of the box. It says nothing about any real trail.<br><br>' : ''}Water sources within 5 miles of the route, with seasonal probabilities from a statistical model of past trail conditions. Miles run along this route (south to north) and differ from other guides' numbers. Year-to-year weather is modeled as a fixed effect, so probabilities describe a typical year.<br><br><b>Not a substitute for current information.</b> Check recent conditions, ask other hikers, and carry enough water.<br><br><button type="button" class="tp-ai-btn" data-tp-ai aria-haspopup="dialog" style="color:#2d4a3e;padding:0">AI &amp; data disclosure</button></div></div>`;
  $('#thr').addEventListener('input', e => { st.thr = +e.target.value; LS.set('thr', st.thr); $('#thrv').textContent = st.thr + '%'; draw() });
  $('#upd').addEventListener('click', async () => { try { const r = await navigator.serviceWorker.getRegistration(); if (r) { await r.update(); toast('Checked — reload to apply if a new version was found') } else toast('No service worker yet') } catch (e) { toast('Could not check (offline?)') } });
  $('#gpsb').addEventListener('click', () => startGps(true));
  if ('caches' in window) caches.match('data.json?v=' + V).then(r => { $('#offstate').innerHTML = r ? '<b style="color:#2d4a3e">✓ Ready offline.</b> The app and all data are saved on this phone.' : 'Not saved for offline yet — stay online for a moment and reload.' });
  else $('#offstate').textContent = 'Offline storage is not available in this browser.';
}

/* ---------- ui wiring ---------- */
let toastT; function toast(m) { const t = $('#toast'); t.textContent = m; t.style.display = 'block'; clearTimeout(toastT); toastT = setTimeout(() => t.style.display = 'none', 2600) }
function setView(v) {
  st.view = v; $('#mapView').style.display = v === 'map' ? 'block' : 'none'; $('#listView').style.display = v === 'list' ? 'block' : 'none'; $('#setView').style.display = v === 'set' ? 'block' : 'none';
  [['map', '#tMap'], ['list', '#tList'], ['set', '#tSet']].forEach(([k, id]) => $(id).setAttribute('aria-selected', k === v));
  if (v === 'map') { resize(); draw() } if (v === 'list') renderList(); if (v === 'set') renderSettings();
}
function refresh() { recompute(); status(); if (st.view === 'map') { draw(); if (st.sel != null) showSheet(st.sel) } else if (st.view === 'list') renderList() }
$('#tMap').onclick = () => setView('map'); $('#tList').onclick = () => setView('list'); $('#tSet').onclick = () => setView('set');
$('#sheetx').onclick = () => { st.sel = null; $('#sheet').style.display = 'none'; draw() }; $('#setx').onclick = () => setView('map');
$('#zin').onclick = () => zoomBy(1.6); $('#zout').onclick = () => zoomBy(1 / 1.6); $('#fit').onclick = fitTrail;
$('#me').onclick = () => { if (!st.gps && !st.plan) startGps(true); else centerOnMe() };
$('#date').addEventListener('change', e => { if (!e.target.value) return; st.date = e.target.value; st.doy = doyOfStr(st.date); refresh() });
$('#dir').addEventListener('click', () => { st.nobo = !st.nobo; LS.set('nobo', st.nobo); $('#dir').textContent = st.nobo ? 'NOBO' : 'SOBO'; refresh() });
$('#filt').addEventListener('click', () => { st.good = !st.good; LS.set('good', st.good); $('#filt').setAttribute('aria-pressed', st.good); refresh() });
function net() { $('#off').style.display = navigator.onLine ? 'none' : 'block' } addEventListener('online', net); addEventListener('offline', net); net();
new ResizeObserver(resize).observe($('#mapView'));

/* ---------- install banner (mobile, not yet installed) ---------- */
(function () {
  const el = $('#install'), txt = $('#installText'), go = $('#installGo');
  const ua = navigator.userAgent, mobile = /Android|iPhone|iPad|iPod|Mobile/i.test(ua) || (matchMedia('(pointer:coarse)').matches && innerWidth < 900);
  const ios = /iPhone|iPad|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  let deferred = null, shown = false;
  if (!mobile || standalone || Date.now() - (LS.get('installDismissed', 0)) < 7 * 864e5) return;
  const ready = async () => { try { return !!(await caches.match('data.json?v=' + V)) } catch (e) { return false } };
  async function show(kind) {
    if (shown && kind !== 'prompt') return; shown = true;
    const ok = await ready(), tail = ok ? '<small>\u2713 Saved on this phone: it will open with no signal.</small>' : '<small>Open it once with signal and it saves itself for offline use.</small>';
    if (kind === 'prompt') { txt.innerHTML = '<b>Install for offline use</b>' + tail; go.style.display = '' }
    else if (kind === 'ios') txt.innerHTML = '<b>Install for offline use:</b> tap the Share button, then <b>Add to Home Screen</b>.' + tail;
    else txt.innerHTML = '<b>Install for offline use:</b> open your browser menu and choose <b>Install app</b> or <b>Add to Home screen</b>.' + tail;
    el.style.display = 'flex';
  }
  addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferred = e; show('prompt') });
  addEventListener('appinstalled', () => { el.style.display = 'none'; toast('Installed') });
  go.addEventListener('click', async () => { if (!deferred) return; deferred.prompt(); try { await deferred.userChoice } catch (e) {} deferred = null; el.style.display = 'none' });
  $('#installX').addEventListener('click', () => { el.style.display = 'none'; LS.set('installDismissed', Date.now()) });
  setTimeout(() => { if (!shown) show(ios ? 'ios' : 'generic') }, 2500);   // browsers without an install prompt event
})();

/* ---------- boot ---------- */
async function boot() {
  const r = await fetch('data.json?v=' + V); D = await r.json(); T = buildTrack(D.track, D.legmap);
  S = D.wp.map(w => ({ name: w[0], mile: w[1], off: w[2], lat: w[3], lon: w[4], elev: w[5], conf: w[6], sec: w[7], c: w[8], alts: w[9], vol: w[10], x: w[4] * R2D, y: mercY(w[3]) }));
  st.date = todayStr(); st.doy = doyOfStr(st.date); $('#date').value = st.date;
  $('#dir').textContent = st.nobo ? 'NOBO' : 'SOBO'; $('#filt').setAttribute('aria-pressed', st.good);
  recompute(); resize(); status();
  if (st.manual > 0) { const p = effPos(); view.cx = p.x; view.cy = p.y; view.s = scaleForMiles(60, p.lat); draw() } else fitTrail();
  startGps(false);
}
boot().catch(e => { $('#status').textContent = 'Could not load data'; console.error(e) });
if ('serviceWorker' in navigator) {
  const hadController = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.register('sw.js', { updateViaCache: 'none' }).catch(() => {});
  navigator.serviceWorker.addEventListener('controllerchange', () => { if (hadController) { const t = $('#toast'); t.innerHTML = 'Updated. <u style="cursor:pointer">Tap to reload</u>'; t.style.display = 'block'; t.onclick = () => location.reload() } });
}
})();
