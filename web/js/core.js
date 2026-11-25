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

// 同步、匯入可能要很久，不設逾時；其他請求 45 秒沒回應就當作失敗
const LONG_API = /^\/api\/(sync|import|garmin\/(login|mfa|demo))/;

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (S.token) headers.Authorization = `Bearer ${S.token}`;
  const ctl = new AbortController();
  const timer = LONG_API.test(path) ? null : setTimeout(() => ctl.abort(), 45000);
  let r;
  try {
    r = await fetch(path, { ...opts, headers, signal: ctl.signal, body: opts.body ? JSON.stringify(opts.body) : undefined });
  } catch (e) {
    throw new Error(e.name === "AbortError" ? "伺服器太久沒回應，請稍後再試一次。"
      : navigator.onLine === false ? "目前沒有網路，連上之後再試一次。" : "連不到伺服器，請確認網路後再試一次。");
  } finally { clearTimeout(timer); }
  if (r.status === 401) { S.token = null; localStorage.removeItem("ic8_token"); go("#/login"); throw new Error("請先登入"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = Array.isArray(data.detail) ? "填的內容格式不對，請檢查一下。" : data.detail;
    throw new Error(d || (r.status >= 500 ? "伺服器出了點問題，請稍後再試一次。" : `沒辦法完成（錯誤 ${r.status}）`));
  }
  return data;
}

// toast(訊息, { undo: async () => {...} })：有 undo 時多一顆「復原」，顯示久一點
function toast(msg, opt = {}) {
  const t = document.getElementById("toast");
  t.innerHTML = `<span>${esc(msg)}</span>${opt.undo ? `<button type="button" class="toast-undo">復原</button>` : ""}`;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), opt.undo ? 6000 : 2800);
  if (opt.undo) t.querySelector("button").onclick = async () => {
    clearTimeout(toast._t); t.hidden = true;
    try { await opt.undo(); toast("已復原"); opt.after && opt.after(); } catch (x) { toast(x.message); }
  };
}

async function busy(btn, fn) {
  if (!btn) return fn();
  const html = btn.innerHTML; btn.disabled = true; btn.setAttribute("aria-busy", "true");
  if (!btn.closest(".feel, .pain")) btn.textContent = "處理中…";
  try { return await fn(); } finally { btn.disabled = false; btn.removeAttribute("aria-busy"); btn.innerHTML = html; }
}

// 按鈕動作：處理中不能重複按，失敗一定跳出說明
async function act(btn, fn) {
  if (btn?.disabled) return;
  try { return await busy(btn, fn); } catch (x) { toast(x.message); }
}

// 最後一道防線：任何沒接住的錯誤都跳出來，不會按了沒反應
window.addEventListener("unhandledrejection", (e) => { e.preventDefault(); toast(e.reason?.message || "發生錯誤，請再試一次。"); });

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
  grip: '<circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/>',
  share: '<path d="M12 15V3M7.5 7.5L12 3l4.5 4.5M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/>',
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
  // 部位拆層（tools/rig_split.py 產生）：沒有這個檔也照常運作
  S.rigs ||= await fetch("/characters/rigs.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  return S.chars;
}
const species = (id) => S.chars?.species.find((s) => s.id === id);

// ── 夥伴特效：每個種族自己的光暈和環繞小物（從第 from 階段開始；越高階越亮）──
// 之後其他種族照這個格式加：aura 是光暈的 RGB，orb 是環繞的像素小圖（顏色 3 個：外、中、心）
const PET_FX = {
  // float：從第幾階段開始浮在空中（不再原地跳）；sway：尾巴、披風微微擺動
  fox_spirit: { from: 2, aura: "255,105,190", orb: "flame", colors: ["#C2369E", "#FF7AD0", "#FFE6F7"], spark: "#FFB8E6", float: 3, sway: 1 },
  succubus: { from: 2, aura: "150,70,230", orb: "bat", colors: ["#1E0B2E", "#6B35B8", "#FF4D7E"], spark: "#C79BFF", float: 2, sway: 2 },
  // 馴獸師：森林的綠光，周圍繞著飄葉；人和野獸站在地上（不浮空）
  beast_tamer: { from: 2, aura: "140,215,100", orb: "leaf", colors: ["#28501C", "#68AE36", "#D2F59A"], spark: "#CFF59A", sway: 2 },
};
const ORB_GRID = {
  flame: ["...1...", "..121..", "..121..", ".12221.", ".12321.", "1223321", "1233321", ".12321.", "..111.."],
  bat: ["1.......1", "11.....11", "111.1.111", "111222111", ".1232321.", "..12221..", "...1.1..."],
  leaf: ["....111", "...1221", "..12321", ".123221", "1232211", "122211.", "1111...", "1......"],
};

// 像素火焰（7×9 格），用 SVG 的小方塊畫，放大也是銳利的像素
function pixelOrb(kind, [c1, c2, c3]) {
  const F = ORB_GRID[kind] || ORB_GRID.flame;
  const fill = { 1: c1, 2: c2, 3: c3 };
  let r = "";
  F.forEach((row, y) => [...row].forEach((ch, x) => { if (fill[ch]) r += `<rect x="${x}" y="${y}" width="1" height="1" fill="${fill[ch]}"/>`; }));
  return `<svg viewBox="0 0 ${F[0].length} ${F.length}" class="orb-${kind}" shape-rendering="crispEdges" aria-hidden="true">${r}</svg>`;
}

function petFx(speciesId, stage) {
  const fx = PET_FX[speciesId];
  if (!fx || stage < fx.from) return "";
  const n = stage >= 4 ? 4 : stage >= 3 ? 3 : 2;
  return `<span class="pet-aura" style="--aura:${fx.aura}" aria-hidden="true"></span><span class="pet-wave" style="--aura:${fx.aura}" aria-hidden="true"></span>
    <span class="pet-orbit${stage >= 4 ? " fast" : ""}" style="--aura:${fx.aura}" aria-hidden="true">${Array.from({ length: n }, (_, i) =>
      `<i style="--k:${i};--n:${n}"><b>${pixelOrb(fx.orb, fx.colors)}</b></i>`).join("")}</span>`;
}
const petSpark = (speciesId) => PET_FX[speciesId]?.spark || "";
// 夥伴的動態樣式：浮空、擺動
function petCls(speciesId, stage) {
  const fx = PET_FX[speciesId];
  if (!fx) return "";
  return [fx.float != null && stage >= fx.float ? "floaty" : "", fx.sway != null && stage >= fx.sway ? "sway" : ""].join(" ").trim();
}

// ── 夥伴專屬背景：用 160×80 的像素格畫成 SVG（放大也銳利），再疊一點會動的小東西 ──
const PX = (x, y, w, h, c) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${c}"/>`;
const bands = (y0, colors, h = 4) => colors.map((c, i) => PX(0, y0 + i * h, 160, h, c)).join("");
function pxCircle(cx, cy, r, c) {
  let s = "";
  for (let y = -r; y <= r; y++) { const w = Math.round(Math.sqrt(r * r - y * y)); s += PX(cx - w, cy + y, w * 2 + 1, 1, c); }
  return s;
}
const PET_SCENE = {
  // 九尾狐：黃昏的神社，粉紫晚霞、月亮、鳥居、櫻花，花瓣慢慢飄下來
  fox_spirit: () => {
    let s = bands(0, ["#3A2C6E", "#4A2F7A", "#5E3486", "#7A3C92", "#9A479C", "#B9549F", "#D8669F", "#EE7FA1", "#F99BA4", "#FFB8A8", "#FFD0B0", "#FFE0BC"]);
    s += pxCircle(30, 15, 8, "#FFF3DA") + pxCircle(28, 13, 2, "#F4E1C2") + pxCircle(33, 18, 1, "#F4E1C2");
    // 遠山
    for (let x = 0; x < 160; x += 2) { const h = 6 + Math.round(5 * Math.sin(x / 13) + 3 * Math.sin(x / 5.3)); s += PX(x, 48 - h, 2, h, "#6A3E86"); }
    for (let x = 0; x < 160; x += 2) { const h = 3 + Math.round(3 * Math.sin(x / 9 + 2)); s += PX(x, 50 - h, 2, h, "#53306F"); }
    // 地面、石板參道
    s += PX(0, 50, 160, 30, "#3C5A48") + PX(0, 50, 160, 2, "#4F7058") + PX(0, 60, 160, 20, "#335043");
    for (let i = 0; i < 6; i++) s += PX(66 + i * 2 - 6, 52 + i * 5, 28 - i * 4 + 12 + i * 8, 3, i % 2 ? "#8E8499" : "#A79DB0");
    // 鳥居（右邊）
    s += PX(116, 16, 34, 3, "#2A0F18") + PX(114, 15, 38, 2, "#2A0F18") + PX(118, 18, 30, 2, "#D3343F") + PX(118, 24, 30, 2, "#D3343F");
    s += PX(122, 20, 3, 34, "#C22E39") + PX(141, 20, 3, 34, "#C22E39") + PX(125, 20, 1, 34, "#8E1E2A") + PX(144, 20, 1, 34, "#8E1E2A");
    s += PX(131, 20, 4, 4, "#2A0F18") + PX(121, 53, 5, 2, "#2A0F18") + PX(140, 53, 5, 2, "#2A0F18");
    // 石燈籠（左邊）
    s += PX(16, 40, 9, 2, "#6E6676") + PX(18, 42, 5, 4, "#9B93A3") + PX(19, 43, 3, 2, "#FFD27A") + PX(16, 46, 9, 2, "#6E6676") + PX(19, 48, 3, 6, "#8A8292") + PX(16, 54, 9, 2, "#6E6676");
    // 櫻花樹枝（右上）
    s += PX(88, 0, 2, 8, "#4A2A2E") + PX(90, 6, 10, 2, "#4A2A2E") + PX(100, 8, 8, 2, "#4A2A2E") + PX(106, 10, 4, 2, "#4A2A2E");
    [[86, 2, 7], [94, 3, 8], [102, 5, 7], [110, 8, 6], [92, 9, 5]].forEach(([x, y, r]) => {
      s += pxCircle(x, y, Math.round(r / 2), "#FF9EC8") + pxCircle(x + 1, y - 1, Math.round(r / 3), "#FFC7E0");
    });
    const petals = Array.from({ length: 7 }, (_, i) => `<i class="petal" style="--i:${i}"></i>`).join("");
    return `<svg class="scene-art" viewBox="0 0 160 80" preserveAspectRatio="xMidYMax slice" shape-rendering="crispEdges">${s}</svg>${petals}`;
  },
  // 魅魔：夜晚的城堡，深紫夜空、緋紅月亮、星星一閃一閃、地上飄著紫霧
  succubus: () => {
    let s = bands(0, ["#0D0618", "#120821", "#170A2A", "#1E0C35", "#260F40", "#30124B", "#3B1556", "#461960", "#521C68", "#5C1F6E", "#64236F", "#6A286E"]);
    s += pxCircle(122, 17, 11, "#7A1530") + pxCircle(122, 17, 10, "#C8304F") + pxCircle(119, 14, 3, "#A9243F") + pxCircle(126, 21, 2, "#A9243F") + pxCircle(125, 12, 1, "#E0566F");
    // 遠方的山和城堡剪影
    for (let x = 0; x < 160; x += 2) { const h = 4 + Math.round(4 * Math.sin(x / 11) + 2 * Math.sin(x / 4.1)); s += PX(x, 46 - h, 2, h, "#24102F"); }
    const tower = (x, w, top) => PX(x, top, w, 46 - top, "#0E0716") + PX(x + Math.floor(w / 2) - 1, top - 6, 2, 6, "#0E0716") + PX(x + Math.floor(w / 2) - 2, top - 3, 4, 3, "#0E0716");
    s += tower(18, 8, 22) + tower(28, 12, 16) + tower(42, 8, 26) + PX(18, 32, 34, 14, "#0E0716");
    s += PX(30, 22, 2, 3, "#FF4F7A") + PX(36, 22, 2, 3, "#FF4F7A") + PX(32, 30, 2, 3, "#FFB04F") + PX(21, 30, 2, 2, "#FF4F7A") + PX(45, 32, 2, 2, "#FF4F7A");
    // 地面：暗紫岩地
    s += PX(0, 46, 160, 34, "#22122E") + PX(0, 46, 160, 2, "#3A1E4A") + PX(0, 58, 160, 22, "#1A0D24");
    [[10, 50, 6], [60, 53, 4], [104, 51, 7], [140, 55, 5], [80, 62, 6]].forEach(([x, y, w]) => (s += PX(x, y, w, 2, "#3A1E4A")));
    // 枯樹（右下）
    s += PX(146, 30, 2, 18, "#0E0716") + PX(140, 34, 6, 2, "#0E0716") + PX(148, 32, 6, 2, "#0E0716") + PX(138, 32, 2, 2, "#0E0716") + PX(154, 30, 2, 2, "#0E0716");
    const stars = [[8, 6], [44, 4], [70, 10], [92, 3], [150, 8], [60, 20], [100, 26], [14, 18]]
      .map(([x, y], i) => `<i class="star" style="left:${(x / 160) * 100}%;top:${(y / 80) * 100}%;--i:${i}"></i>`).join("");
    return `<svg class="scene-art" viewBox="0 0 160 80" preserveAspectRatio="xMidYMax slice" shape-rendering="crispEdges">${s}</svg>${stars}<i class="mist"></i><i class="mist m2"></i>`;
  },
  // 馴獸師：清晨的森林空地，晨光從左邊照進來，松樹林、遠山、樹樁，葉子飄落、鳥飛過
  beast_tamer: () => {
    let s = bands(0, ["#8FC6D6", "#9CCDD6", "#AAD3D3", "#B8D8CE", "#C8DCC4", "#D8DFB8", "#E6E0AC", "#F0DF9E", "#F6DA92", "#F8D486"], 5);
    s += pxCircle(24, 22, 9, "#FFF1C4") + pxCircle(24, 22, 7, "#FFF8DE");
    // 晨光光束（左上斜下來）
    for (let i = 0; i < 5; i++) s += `<polygon points="${10 + i * 14},0 ${18 + i * 14},0 ${52 + i * 16},60 ${44 + i * 16},60" fill="#FFF6D8" opacity=".14"/>`;
    // 遠山
    for (let x = 0; x < 160; x += 2) { const h = 8 + Math.round(5 * Math.sin(x / 15 + 1) + 3 * Math.sin(x / 6.1)); s += PX(x, 44 - h, 2, h, "#7FA8A0"); }
    // 松樹：一層一層疊成三角形
    const pine = (x, base, h, c, c2) => {
      let t = PX(x - 1, base - 3, 2, 3, "#4A3524");
      for (let y = 0; y < h; y++) { const w = 1 + Math.floor((y % Math.ceil(h / 3)) * 0.9 + y / 4); t += PX(x - w, base - 3 - h + y, w * 2, 1, y % 3 ? c : c2); }
      return t;
    };
    [[6, 48, 22], [16, 46, 16], [138, 48, 24], [150, 47, 18], [128, 46, 14], [36, 45, 12], [112, 45, 11]].forEach(([x, b, h]) => (s += pine(x, b, h, "#3E6B48", "#4E8055")));
    [[0, 52, 28], [156, 52, 30]].forEach(([x, b, h]) => (s += pine(x, b, h, "#2C4F36", "#355D3F")));
    // 地面：草地、泥土小路
    s += PX(0, 46, 160, 34, "#5C8F3E") + PX(0, 46, 160, 2, "#76AA4C") + PX(0, 60, 160, 20, "#4F7E35");
    for (let i = 0; i < 7; i++) s += PX(70 - i * 3, 50 + i * 4, 20 + i * 6, 3, i % 2 ? "#9C7B52" : "#B08D60");
    [[12, 52, 5], [40, 56, 4], [118, 54, 6], [146, 60, 4], [100, 66, 5]].forEach(([x, y, w]) => (s += PX(x, y, w, 2, "#86BB58")));
    // 樹樁和石頭
    s += PX(126, 50, 12, 6, "#6E4A2C") + PX(126, 49, 12, 2, "#C9A06A") + PX(129, 49, 6, 1, "#A57E4E") + PX(124, 55, 16, 2, "#4E331F");
    s += PX(22, 54, 8, 4, "#8E9290") + PX(23, 53, 6, 1, "#ADB1AE") + PX(20, 57, 12, 1, "#5E6260");
    const leaves = Array.from({ length: 6 }, (_, i) => `<i class="petal leaf" style="--i:${i}"></i>`).join("");
    const birds = [0, 1].map((i) => `<i class="bird" style="--i:${i}"></i>`).join("");
    return `<svg class="scene-art" viewBox="0 0 160 80" preserveAspectRatio="xMidYMax slice" shape-rendering="crispEdges">${s}</svg>${leaves}${birds}`;
  },
};
function petScene(id) {
  const f = PET_SCENE[id];
  return f ? `<span class="scene scene-${id}" aria-hidden="true">${f()}</span>` : "";
}
// 前景：疊在角色前面的草叢、霧，讓角色像站在場景裡
const PET_FRONT = {
  fox_spirit: () => {
    let s = "";
    for (let x = 0; x < 160; x += 3) {
      const h = 2 + ((x * 7) % 5);
      s += PX(x, 10 - h, 2, h, x % 2 ? "#2E4A3A" : "#3E6249") + PX(x + 1, 10 - h + 1, 1, h - 1, "#4F7A58");
    }
    return `<svg class="front-art" viewBox="0 0 160 10" preserveAspectRatio="none" shape-rendering="crispEdges">${s}</svg>` +
      `<i class="petal front" style="--i:2"></i><i class="petal front" style="--i:5"></i>`;
  },
  succubus: () => `<i class="mist front"></i><i class="mist front m2"></i>`,
  beast_tamer: () => {
    let s = "";
    for (let x = 0; x < 160; x += 2) {
      const h = 3 + ((x * 11) % 6);
      s += PX(x, 10 - h, 1, h, x % 4 ? "#3F6E2C" : "#4F8A35") + PX(x + 1, 10 - h + 2, 1, h - 2, "#67A644");
    }
    return `<svg class="front-art" viewBox="0 0 160 10" preserveAspectRatio="none" shape-rendering="crispEdges">${s}</svg>` +
      `<i class="petal leaf front" style="--i:1"></i><i class="petal leaf front" style="--i:4"></i>`;
  },
};
const petFront = (id) => (PET_FRONT[id] ? `<span class="scene-front" aria-hidden="true">${PET_FRONT[id]()}</span>` : "");

// 夥伴圖：有拆部位的（rigs.json）用分層組回角色，點擊時頭、翅膀、尾巴、下襬各自動；沒有的用整張圖
// 圖層順序：翅膀、尾巴、魔力圈在身體後面；上半身、頭在前面。腳留在身體層，不另外動
const RIG_Z = { wingL: 0, wingR: 0, tail: 0, tailL: 0, tailR: 0, ringL: 0, ringR: 0, torso: 2, head: 3, beast: 2, mane: 2, crystal: 3 };
function petSprite(id, stage, boxH) {
  const rig = S.rigs?.[id]?.[stage + 1], sp = species(id);
  if (!rig || !sp) return charImg(id, stage, boxH);
  const s = boxH / Math.max(...sp.stages.map((x) => x.h));
  const px = (v) => `${Math.round(v * s * 100) / 100}px`;
  const layer = (cls, file, x, y, w, h, z, origin = "") =>
    `<i class="rp ${cls}" style="left:${px(x)};top:${px(y)};width:${px(w)};height:${px(h)};z-index:${z};background-image:url('${file}');${origin}"></i>`;
  const st = sp.stages[stage] || sp.stages[0];
  return `<span class="wavy rig" role="img" aria-label="${esc(st.name)}" style="width:${px(rig.w)};height:${px(rig.h)}">
    ${layer("rp-base", rig.base, 0, 0, rig.w, rig.h, 1)}
    ${rig.parts.map((p) => layer(`rp-${p.motion} rp-${p.name}`, p.file, p.x, p.y, p.w, p.h, RIG_Z[p.name] ?? 2,
      `transform-origin:${px(p.px - p.x)} ${px(p.py - p.y)};`)).join("")}</span>`;
}
const sceneCls = (id) => (PET_SCENE[id] ? "has-scene" : "");

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
  const opener = document.activeElement;
  const back = document.createElement("div");
  back.className = "sheet-back";
  back.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><div class="grab" aria-hidden="true"></div>${html}</div>`;
  const panel = back.querySelector(".sheet");
  const onKey = (e) => { if (e.key === "Escape") close(); };
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    back.remove();
    document.removeEventListener("keydown", onKey);
    document.body.classList.remove("sheet-open");
    if (opener && document.contains(opener)) opener.focus?.({ preventScroll: true });
  }
  back.addEventListener("click", (e) => { if (e.target === back) close(); });
  document.addEventListener("keydown", onKey);
  // 往下滑關閉：只在抽屜捲到最上面時才算
  let y0 = null, dy = 0;
  panel.addEventListener("touchstart", (e) => { y0 = panel.scrollTop <= 0 ? e.touches[0].clientY : null; dy = 0; }, { passive: true });
  panel.addEventListener("touchmove", (e) => {
    if (y0 == null) return;
    dy = Math.max(0, e.touches[0].clientY - y0);
    panel.style.transform = dy ? `translateY(${dy}px)` : "";
  }, { passive: true });
  panel.addEventListener("touchend", () => {
    if (y0 != null && dy > 90) close(); else panel.style.transform = "";
    y0 = null;
  });
  document.body.append(back);
  document.body.classList.add("sheet-open");
  onMount && onMount(panel, close);
  panel.querySelector("button, input, select")?.focus({ preventScroll: true });
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
// invert：數字越小畫越上面（例如成績時間，越快越好）；yfmt：Y 軸標籤格式；goal：目標虛線
function lineChart(pts, { color = "var(--c-hr)", avg = null, trend = false, h = 220, invert = false, yfmt = null, goal = null, goalLabel = "" } = {}) {
  const W = 360, H = h, L = 8, R = 40, T = 12, B = 24;
  const vals = pts.flatMap((p) => [p.value, p.min, p.max]).filter((v) => v != null).concat(goal != null ? [goal] : []);
  if (!vals.length) return `<p class="muted" style="padding:40px 0;text-align:center">這段期間沒有資料</p>`;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.15 || 2; lo = Math.max(0, lo - pad); hi += pad;
  const n = pts.length, step = (W - L - R) / Math.max(1, n - 1);
  const x = (i) => L + i * step, y = (v) => T + ((invert ? (v - lo) : (hi - v)) / (hi - lo)) * (H - T - B);
  const every = Math.ceil(n / 8);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="折線圖">`;
  [lo, (lo + hi) / 2, hi].forEach((v) => (s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${W - R + 6}" y="${y(v) + 4}">${yfmt ? yfmt(v) : axisFmt(v)}</text>`));
  if (goal != null) s += `<line x1="${L}" x2="${W - R}" y1="${y(goal)}" y2="${y(goal)}" stroke="var(--ok)" stroke-width="1.6" stroke-dasharray="6 4"/>` +
    (goalLabel ? `<text x="${L + 4}" y="${y(goal) - 5}" fill="var(--ok)" style="font-weight:700">${esc(goalLabel)}</text>` : "");
  pts.forEach((p, i) => {
    if (p.min != null && p.max != null) s += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(p.min)}" y2="${y(p.max)}" stroke="${color}" stroke-width="6" stroke-linecap="round" opacity=".22"/>`;
    if (i % every === 0 || (i === n - 1 && (n - 1) % every > every / 2)) s += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(p.label)}</text>`;
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

const TABS = [["summary", "摘要", "flag"], ["metrics", "指標", "chart"], ["activity", "紀錄", "run"], ["analysis", "課表", "cal"], ["me", "夥伴", "paw"]];
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
  box.querySelectorAll("[data-opt]").forEach((b) => (b.onclick = () => act(b, async () => {
    const o = opts[+b.dataset.opt];
    const changed = await applyOption(o);
    box.innerHTML = `<p class="note-card" style="margin:0">${ico("check")}<span>${changed ? `已改成「${esc(o.label)}」，課表和手錶會跟著更新。` : "好，照原本的課。"}</span></p>`;
    if (after) after(o, changed);
  })));
}
