"use strict";
// 共用：狀態、API、路由、圖示、角色圖片、圖表

const $app = document.getElementById("app");
const S = { token: localStorage.getItem("ic8_token"), me: null, meta: null, chars: null };
const WD = ["一", "二", "三", "四", "五", "六", "日"];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString("zh-TW", { maximumFractionDigits: d, minimumFractionDigits: d }));
const axisFmt = (v, small) => (v >= 10000 ? `${fmt(v / 1000, v % 1000 ? 1 : 0)}k` : fmt(v, small ? 1 : 0));
const paceStr = (s) => `${Math.floor(s / 60)}'${String(Math.round(s % 60)).padStart(2, "0")}"`;
const localISO = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (S.token) headers.Authorization = `Bearer ${S.token}`;
  const r = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (r.status === 401) { S.token = null; localStorage.removeItem("ic8_token"); go("#/login"); throw new Error("請先登入"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `錯誤 ${r.status}`);
  return data;
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2800);
}

async function busy(btn, fn) {
  if (!btn) return fn();
  const html = btn.innerHTML; btn.disabled = true; btn.textContent = "處理中…";
  try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = html; }
}

function agoText(iso) {
  if (!iso) return "尚未同步";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "剛剛同步";
  if (m < 60) return `${m} 分鐘前同步`;
  if (m < 1440) return `${Math.round(m / 60)} 小時前同步`;
  return `${Math.round(m / 1440)} 天前同步`;
}

// ── 圖示（線條風格，對應參考 UI） ──
const ICON = {
  home: '<path d="M12 20.5s-7.5-4.6-7.5-10.3A4.2 4.2 0 0 1 12 7.6a4.2 4.2 0 0 1 7.5 2.6c0 5.7-7.5 10.3-7.5 10.3z"/>',
  run: '<circle cx="14" cy="4.5" r="1.8"/><path d="M8 20l3-6 3 2.5V21M6.5 11l2.5-3.5 4 1 2 3.5 3 1M11 14l-1.5-4"/>',
  chart: '<path d="M4 19V11M10 19V5M16 19v-6M3 19h18"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/>',
  back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  sync: '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16m0 4v-4h-4"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M4.2 6.5l2.6 1.5M17.2 16l2.6 1.5M4.2 17.5l2.6-1.5M17.2 8l2.6-1.5"/>',
  watch: '<rect x="6" y="6" width="12" height="12" rx="3.5"/><path d="M9 6l.7-3h4.6l.7 3M9 18l.7 3h4.6l.7-3M12 9.5V12l1.8 1"/>',
  steps: '<path d="M8 14c-1.5 0-2.5-1.7-2.5-4S6.5 5 8 5s2.5 2.3 2.5 5-1 4-2.5 4zM16 19c-1.5 0-2.5-1.7-2.5-4s1-5 2.5-5 2.5 2.3 2.5 5-1 4-2.5 4zM6.5 17h3M14.5 22h3"/>',
  hr: '<path d="M12 20s-7.5-4.6-7.5-10.3A4.2 4.2 0 0 1 12 7.1a4.2 4.2 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/>',
  sleep: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  stress: '<circle cx="12" cy="6" r="2"/><path d="M5 13c2 0 3-2 7-2s5 2 7 2M8 20l1.5-5M16 20l-1.5-5M6 20h12"/>',
  hrv: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M6 12h3l1.5-3 3 6 1.5-3h3"/>',
  bb: '<rect x="3" y="7" width="16" height="10" rx="2.5"/><path d="M21 10.5v3M7 10v4M10.5 10v4"/>',
  kcal: '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.2 0-3.7 3-5.6 4-9.3 2.3 1.6 3 3.4 3 5 .9-.6 1.6-1.6 1.8-3 1.9 1.6 4.2 4.3 4.2 7.3 0 3.6-2.6 6.2-6.5 6.2z"/>',
  bolt: '<path d="M13 3L5 13h6l-1 8 8-10h-6z"/>',
  fit: '<path d="M3 17l5-5 4 3 8-8M15 7h5v5"/>',
  score: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  paw: '<circle cx="7" cy="10" r="1.8"/><circle cx="17" cy="10" r="1.8"/><circle cx="10" cy="6.5" r="1.8"/><circle cx="14" cy="6.5" r="1.8"/><path d="M12 12.5c-2.6 0-5 3.5-4 5.5s3 .8 4 .8 3 1.2 4-.8-1.4-5.5-4-5.5z"/>',
  list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  chevron: '<path d="M9 5l7 7-7 7"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  book: '<path d="M5 4.5h9a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h9"/>',
  bike: '<circle cx="6" cy="16" r="3.5"/><circle cx="18" cy="16" r="3.5"/><path d="M6 16l4-7h5l3 7M10 9l3 7h-7M13 6h3"/>',
  swim: '<circle cx="16" cy="6" r="1.8"/><path d="M3 18c1.5 1 3 1 4.5 0s3-1 4.5 0 3 1 4.5 0 3-1 4.5 0M6 14l4-4 3 2 3-3"/>',
  gym: '<path d="M6 8v8M18 8v8M3 10v4M21 10v4M6 12h12"/>',
  calm: '<circle cx="12" cy="5.5" r="1.8"/><path d="M12 8v5M6 11l6 2 6-2M8 20l4-7 4 7"/>',
  walk: '<circle cx="13" cy="4.5" r="1.8"/><path d="M10 21l2-6 3 3v3M9 12l2-4 3 1 2 3M12 15l-1-5"/>',
  ball: '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4c3 3 3 13 0 16M12 4c-3 3-3 13 0 16"/>',
  other: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  bell: '<path d="M6 17V11a6 6 0 0 1 12 0v6l1.5 2h-15zM10 21h4"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  up: '<path d="M6 15l6-6 6 6"/>', down: '<path d="M6 9l6 6 6-6"/>',
  cal: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  shield: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z"/>',
  trend: '<path d="M3 17l6-6 4 4 8-8"/>',
  trophy: '<path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8.5 20h7"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H4"/>',
};
const ico = (name, cls = "ico") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON[name] || ""}</svg>`;

const METRIC_STYLE = {
  readiness: ["score", "var(--c-run)"], steps: ["steps", "var(--c-steps)"], rhr: ["hr", "var(--c-hr)"],
  hrv: ["hrv", "var(--c-hrv)"], sleep: ["sleep", "var(--c-sleep)"], sleep_score: ["sleep", "var(--c-sleep)"],
  stress: ["stress", "var(--c-stress)"], body_battery: ["bb", "var(--c-bb)"], active_kcal: ["kcal", "var(--c-kcal)"],
  intensity: ["bolt", "var(--c-kcal)"], run_km: ["run", "var(--c-run)"], ctl: ["fit", "var(--c-fit)"], partner: ["paw", "var(--c-run)"],
};
const mcolor = (k) => (METRIC_STYLE[k] || ["chart", "var(--ink)"])[1];
const micon = (k) => ico((METRIC_STYLE[k] || ["chart"])[0]);

function appbar(title, { back = false, actions = "" } = {}) {
  return `<header class="appbar ${back ? "sub" : ""}">
    ${back ? `<button class="icon-btn" data-back aria-label="返回">${ico("back")}</button>` : ""}
    <h1>${esc(title)}</h1>${actions}</header>`;
}

function bindCommon(root = $app) {
  root.querySelectorAll("[data-back]").forEach((b) => (b.onclick = () => (history.length > 1 ? history.back() : go("#/summary"))));
  root.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => go(b.dataset.go)));
}

// 課表內容（熱身／主課／緩和 + 配速 + 心率上限）
function stepsHtml(steps, inner = false) {
  const name = { warmup: "熱身", cooldown: "緩和", interval: "主課", recovery: "恢復", run: "跑" };
  return (steps || []).map((s) => {
    if (s.type === "repeat") return `<li class="rep">重複 ${s.n} 次</li>${stepsHtml(s.steps, true)}`;
    const t = s.by === "km" ? `${fmt(s.km, s.km % 1 ? 1 : 0)} km` : s.minutes >= 1 ? `${Math.round(s.minutes)} 分` : `${Math.round(s.minutes * 60)} 秒`;
    const p = s.pace_lo ? `${paceStr(s.pace_hi)}–${paceStr(s.pace_lo)}` : "";
    return `<li class="${inner ? "in" : ""}"><span class="k">${name[s.type] || s.type}</span><span>${t}${s.note ? `，${esc(s.note)}` : ""}</span>
      <span class="p">${p}${s.hr_cap ? `<small class="hrcap">♥ ≤ ${s.hr_cap}</small>` : ""}</span></li>`;
  }).join("");
}
const PHASE_CLS = { base: "ph-base", build: "ph-build", specific: "ph-specific", taper: "ph-taper", race: "ph-race", hold: "ph-hold", recover: "ph-taper" };
if (localStorage.getItem("ic8_big") === "1") document.documentElement.classList.add("big");

// ── 角色圖片（manifest 驅動） ──
async function loadChars() {
  S.chars ||= await (await fetch("/characters/manifest.json")).json();
  return S.chars;
}
const species = (id) => S.chars?.species.find((s) => s.id === id);

// scale：同一系列所有階段用同一個倍率，所以越後面的階段畫面上越大
function charImg(id, stage, boxH = 200, cls = "") {
  const sp = species(id);
  if (!sp) return "";
  const st = sp.stages[stage] || sp.stages[0];
  const maxH = Math.max(...sp.stages.map((x) => x.h));
  const scale = boxH / maxH;
  return `<img class="${cls}" src="${st.file}" alt="${esc(st.name)}" width="${Math.round(st.w * scale)}" height="${Math.round(st.h * scale)}">`;
}
const charThumb = (id, stage) => {
  const sp = species(id);
  return sp ? `<img src="${(sp.stages[stage] || sp.stages[0]).file}" alt="${esc((sp.stages[stage] || sp.stages[0]).name)}">` : "";
};

// ── 底部抽屜 ──
function sheet(html, onMount) {
  const back = document.createElement("div");
  back.className = "sheet-back";
  back.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><div class="grab"></div>${html}</div>`;
  const close = () => back.remove();
  back.addEventListener("click", (e) => { if (e.target === back) close(); });
  document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", k); } });
  document.body.append(back);
  onMount && onMount(back.querySelector(".sheet"), close);
  back.querySelector("button, input, select")?.focus();
  return close;
}

// ───────────────────── 圖表（純 SVG） ─────────────────────
function niceMax(v) {
  if (!v || v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
}

// 長條圖：pts=[{label,value}], 可畫平均虛線、目標線、趨勢線
const SPORT_COLOR = { run: "var(--c-run)", bike: "#2B8A8F", swim: "#2F6FD6", gym: "#B5502F", calm: "#7A5BC7", walk: "#5C8A33", ball: "#C27A12", other: "#6B6F8A" };
const sportIco = (g) => ico(ICON[g] ? g : "other");

// 長條圖；點可以帶 stack（疊在上面的第二段，例如其他運動）
function barChart(pts, { color = "var(--c-run)", avg = null, goal = null, trend = false, h = 220, highlightLast = false, stackColor = "#6B6F8A" } = {}) {
  const W = 360, H = h, L = 8, R = 40, T = 12, B = 24;
  const vals = pts.map((p) => (p.value || 0) + (p.stack || 0));
  const max = niceMax(Math.max(...vals, goal || 0, avg || 0) * 1.05);
  const n = pts.length, bw = (W - L - R) / n;
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const every = Math.ceil(n / 8);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="長條圖">`;
  [0, max / 2, max].forEach((v) => {
    s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${W - R + 6}" y="${y(v) + 4}">${axisFmt(v, max < 10)}</text>`;
  });
  pts.forEach((p, i) => {
    const v = p.value || 0, x = L + i * bw + bw * 0.18, w = bw * 0.64;
    const op = highlightLast && i < n - 1 ? 0.55 : 1;
    if (v > 0) s += `<rect x="${x}" y="${y(v)}" width="${w}" height="${y(0) - y(v)}" rx="${Math.min(4, w / 3)}" fill="${color}" opacity="${op}"/>`;
    if (p.stack > 0) s += `<rect x="${x}" y="${y(v + p.stack)}" width="${w}" height="${y(v) - y(v + p.stack)}" rx="${Math.min(4, w / 3)}" fill="${stackColor}" opacity="${op}"/>`;
    if (i % every === 0 || i === n - 1) s += `<text x="${L + i * bw + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(p.label)}</text>`;
  });
  if (avg) s += `<line x1="${L}" x2="${W - R}" y1="${y(avg)}" y2="${y(avg)}" stroke="${color}" stroke-dasharray="4 4" opacity=".8"/>`;
  if (goal) s += `<line x1="${L}" x2="${W - R}" y1="${y(goal)}" y2="${y(goal)}" stroke="var(--ink-3)" stroke-dasharray="2 3"/>`;
  if (trend) s += trendLine(pts, (i) => L + i * bw + bw / 2, y, color);
  return s + "</svg>";
}

// 折線圖（含 min–max 範圍帶）
function lineChart(pts, { color = "var(--c-hr)", avg = null, trend = false, h = 220 } = {}) {
  const W = 360, H = h, L = 8, R = 40, T = 12, B = 24;
  const vals = pts.flatMap((p) => [p.value, p.min, p.max]).filter((v) => v != null);
  if (!vals.length) return `<p class="muted" style="padding:40px 0;text-align:center">這段期間沒有資料</p>`;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.15 || 2; lo = Math.max(0, lo - pad); hi += pad;
  const n = pts.length, step = (W - L - R) / Math.max(1, n - 1);
  const x = (i) => L + i * step, y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const every = Math.ceil(n / 8);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="折線圖">`;
  [lo, (lo + hi) / 2, hi].forEach((v) => (s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${W - R + 6}" y="${y(v) + 4}">${axisFmt(v)}</text>`));
  pts.forEach((p, i) => {
    if (p.min != null && p.max != null) s += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(p.min)}" y2="${y(p.max)}" stroke="${color}" stroke-width="6" stroke-linecap="round" opacity=".22"/>`;
    if (i % every === 0 || i === n - 1) s += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(p.label)}</text>`;
  });
  let d = "", started = false;
  pts.forEach((p, i) => { if (p.value == null) { started = false; return; } d += `${started ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`; started = true; });
  s += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round"/>`;
  pts.forEach((p, i) => p.value != null && n <= 31 && (s += `<circle cx="${x(i)}" cy="${y(p.value)}" r="3" fill="${color}"/>`));
  if (avg != null) s += `<line x1="${L}" x2="${W - R}" y1="${y(avg)}" y2="${y(avg)}" stroke="${color}" stroke-dasharray="4 4" opacity=".7"/>`;
  if (trend) s += trendLine(pts, x, y, color);
  return s + "</svg>";
}

function trendLine(pts, x, y, color) {
  const xs = [], ys = [];
  pts.forEach((p, i) => p.value != null && (xs.push(i), ys.push(p.value)));
  if (xs.length < 3) return "";
  const mx = xs.reduce((a, b) => a + b) / xs.length, my = ys.reduce((a, b) => a + b) / ys.length;
  const k = xs.reduce((a, xi, j) => a + (xi - mx) * (ys[j] - my), 0) / xs.reduce((a, xi) => a + (xi - mx) ** 2, 0);
  const f = (i) => my + k * (i - mx);
  const a = xs[0], b = xs[xs.length - 1];
  return `<line x1="${x(a)}" y1="${y(Math.max(0, f(a)))}" x2="${x(b)}" y2="${y(Math.max(0, f(b)))}" stroke="${color}" stroke-width="1.6" opacity=".55"/>`;
}

function sparkline(vals, color) {
  const v = vals.map((x) => (x == null ? null : x));
  const nn = v.filter((x) => x != null);
  if (nn.length < 2) return "";
  const lo = Math.min(...nn), hi = Math.max(...nn), r = hi - lo || 1;
  const pts = v.map((x, i) => (x == null ? null : `${(i / (v.length - 1)) * 64},${22 - ((x - lo) / r) * 20}`)).filter(Boolean);
  return `<svg class="spark" viewBox="0 0 64 24" aria-hidden="true"><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

function ringsSvg(rings) {
  const colors = ["var(--c-run)", "var(--c-kcal)", "var(--c-steps)"];
  const C = 70;
  return `<svg viewBox="0 0 140 140" role="img" aria-label="${rings.map((r) => `${r.label} ${r.value}/${r.goal}`).join("，")}">` +
    rings.map((r, i) => {
      const rad = 60 - i * 17, len = 2 * Math.PI * rad, p = Math.min(1, (r.value || 0) / (r.goal || 1));
      return `<circle cx="${C}" cy="${C}" r="${rad}" fill="none" stroke="${colors[i]}" stroke-opacity=".2" stroke-width="13"/>
        <circle cx="${C}" cy="${C}" r="${rad}" fill="none" stroke="${colors[i]}" stroke-width="13" stroke-linecap="round"
          stroke-dasharray="${(len * p).toFixed(1)} ${len.toFixed(1)}" transform="rotate(-90 ${C} ${C})"/>`;
    }).join("") + "</svg>";
}

// 心率點狀圖（參考 UI 的 Workout detail）
function hrDots(points, { max_hr }) {
  const P = points.filter((p) => p.hr);
  if (!P.length) return `<p class="muted">沒有心率資料</p>`;
  const W = 360, H = 200, L = 8, R = 36, T = 12, B = 24;
  const tMax = P[P.length - 1].t || 1;
  const lo = Math.min(80, ...P.map((p) => p.hr)) - 5, hi = Math.max(max_hr || 190, ...P.map((p) => p.hr)) + 5;
  const x = (t) => L + (t / tMax) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const step = Math.max(1, Math.floor(P.length / 60));
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="心率曲線">`;
  [lo + 5, Math.round((lo + hi) / 2), hi - 5].forEach((v) => (s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${W - R + 6}" y="${y(v) + 4}">${Math.round(v)}</text>`));
  for (let m = 0; m <= tMax / 60; m += Math.max(3, Math.ceil(tMax / 60 / 8 / 3) * 3)) s += `<text x="${x(m * 60)}" y="${H - 6}" text-anchor="middle">${m}m</text>`;
  P.filter((_, i) => i % step === 0).forEach((p) => (s += `<circle cx="${x(p.t).toFixed(1)}" cy="${y(p.hr).toFixed(1)}" r="3.2" fill="var(--c-hr)"/>`));
  return s + "</svg>";
}

function paceLine(points) {
  const P = points.filter((p) => p.pace && p.pace < 900);
  if (P.length < 3) return "";
  const W = 360, H = 160, L = 8, R = 40, T = 10, B = 22;
  const tMax = P[P.length - 1].t || 1;
  const sorted = P.map((p) => p.pace).sort((a, b) => a - b);
  const lo = sorted[Math.floor(sorted.length * 0.03)] - 10, hi = sorted[Math.floor(sorted.length * 0.97)] + 10;
  const x = (t) => L + (t / tMax) * (W - L - R), y = (v) => T + ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * (H - T - B);
  let d = P.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.pace).toFixed(1)}`).join("");
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="配速曲線（越上面越快）">`;
  [lo, (lo + hi) / 2, hi].forEach((v) => (s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${W - R + 6}" y="${y(v) + 4}">${paceStr(v)}</text>`));
  return s + `<path d="${d}" fill="none" stroke="var(--c-bb)" stroke-width="2"/></svg>`;
}

// ───────────────────── 路由 ─────────────────────
const ROUTES = [];
const route = (re, fn, tab) => ROUTES.push({ re, fn, tab });
function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }

const TABS = [["summary", "今天", "flag"], ["activity", "紀錄", "run"], ["analysis", "課表", "cal"], ["me", "夥伴", "paw"]];
function setNav(tab) {
  const nav = document.getElementById("nav");
  nav.hidden = !tab;
  if (!tab) return;
  nav.innerHTML = TABS.map(([id, label, icon]) => `<button data-tab="${id}" ${tab === id ? 'aria-current="page"' : ""}>${ico(icon)}<span>${label}</span></button>`).join("");
  nav.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => go(`#/${b.dataset.tab}`)));
}

async function render() {
  const h = location.hash || "#/summary";
  try {
    S.meta ||= await api("/api/meta");
    await loadChars();
    if (!S.token) { setNav(null); return viewGate(h === "#/register" ? "register" : "login"); }
    S.me = await api("/api/me");
    if (S.me.provider === "none" && !h.startsWith("#/me") && !h.startsWith("#/learn")) { setNav(null); return viewLink(); }
    for (const r of ROUTES) {
      const m = h.match(r.re);
      if (m) { setNav(r.tab); window.scrollTo(0, 0); return await r.fn(...m.slice(1)); }
    }
    go("#/summary");
  } catch (x) {
    if (S.token) $app.innerHTML = `<p class="boot">${esc(x.message)}<br><br><button class="btn" onclick="location.reload()">重新整理</button></p>`;
  }
}
window.addEventListener("hashchange", render);

// ───────────────────── 改課選項（跑後報告、晨報、身體狀況共用） ─────────────────────
function optionsHtml(opts, tag) {
  if (!opts || !opts.length) return "";
  return `<div class="choice" data-opts="${tag}">${opts.map((o, i) => `<button type="button" data-opt="${i}"${o.id === "keep" ? ' class="keep"' : ""}>
    <b>${i === 0 && o.id !== "keep" ? "建議：" : ""}${esc(o.label)}</b><small>${esc(o.detail)}</small></button>`).join("")}</div>`;
}

async function applyOption(o) {
  const a = o.apply || {};
  if (a.type === "override") {
    const body = { date: a.date, kind: a.kind };
    if (a.km) body.km = Math.max(1, a.km);
    await api("/api/override", { method: "PUT", body });
  } else if (a.type === "swap") {
    await api("/api/swap", { method: "POST", body: { a: a.a, b: a.b } });
  }
  return a.type !== "keep";
}

function bindOptions(tag, opts, after) {
  const box = document.querySelector(`[data-opts="${tag}"]`);
  if (!box) return;
  box.querySelectorAll("[data-opt]").forEach((b) => (b.onclick = () => busy(b, async () => {
    const o = opts[+b.dataset.opt];
    const changed = await applyOption(o);
    box.innerHTML = `<p class="note-card" style="margin:0">${ico("check")}<span>${changed ? `已改成「${esc(o.label)}」，課表和手錶會跟著更新。` : "好，照原本的課。"}</span></p>`;
    if (after) after(o, changed);
  })));
}
