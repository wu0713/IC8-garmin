"use strict";
// 分享圖卡：在手機上直接畫一張限時動態尺寸（1080×1920）的圖，用系統分享或下載。
// 路線只畫形狀、不畫地圖，頭尾各去掉一小段，避免從圖上看出家在哪裡。

const CARD_W = 1080, CARD_H = 1920;
const CARD_FONT = '"PingFang TC", "Noto Sans TC", "Microsoft JhengHei", system-ui, sans-serif';
const CARD_C = { page: "#ECEEF8", ink: "#1B1E3C", ink2: "#454A6E", ink3: "#6B7092", pill: "#F5B921", run: "#4B45C6", surface: "#FFFFFF", hi: "#E3E5F6" };

function loadImg(src) {
  return new Promise((ok) => { const im = new Image(); im.onload = () => ok(im); im.onerror = () => ok(null); im.src = src; });
}

// 路線：頭尾各去掉約 8%，再縮放到框框裡（保持長寬比、緯度方向修正）
function routeShape(points) {
  const pts = (points || []).filter((p) => p.lat != null && p.lon != null);
  if (pts.length < 20) return null;
  const cut = Math.floor(pts.length * 0.08);
  return pts.slice(cut, pts.length - cut);
}

function drawRoute(ctx, pts, box, style = null) {
  const k = Math.cos((pts[0].lat * Math.PI) / 180);
  const xs = pts.map((p) => p.lon * k), ys = pts.map((p) => -p.lat);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const s = Math.min(box.w / (maxX - minX || 1e-6), box.h / (maxY - minY || 1e-6));
  const ox = box.x + (box.w - (maxX - minX) * s) / 2, oy = box.y + (box.h - (maxY - minY) * s) / 2;
  ctx.save();
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  ctx.beginPath();
  pts.forEach((_, i) => { const X = ox + (xs[i] - minX) * s, Y = oy + (ys[i] - minY) * s; i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y); });
  if (style === "white") {
    ctx.shadowColor = "rgba(0,0,0,.45)"; ctx.shadowBlur = 12;
    ctx.strokeStyle = "#FFFFFF"; ctx.lineWidth = 12; ctx.stroke();
  } else {
    ctx.strokeStyle = CARD_C.ink; ctx.lineWidth = 26; ctx.stroke();
    ctx.strokeStyle = CARD_C.pill; ctx.lineWidth = 14; ctx.stroke();
  }
  ctx.restore();
}

// 沒有路線時：畫配速曲線（越上面越快）
function drawPace(ctx, points, box) {
  const P = (points || []).filter((p) => p.pace && p.pace < 900);
  if (P.length < 5) return false;
  const sorted = P.map((p) => p.pace).sort((a, b) => a - b);
  const lo = sorted[Math.floor(sorted.length * 0.03)], hi = sorted[Math.floor(sorted.length * 0.97)];
  const tMax = P[P.length - 1].t || 1;
  ctx.save(); ctx.beginPath();
  P.forEach((p, i) => {
    const X = box.x + (p.t / tMax) * box.w;
    const Y = box.y + ((Math.min(hi, Math.max(lo, p.pace)) - lo) / (hi - lo || 1)) * box.h;
    i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
  });
  ctx.strokeStyle = CARD_C.run; ctx.lineWidth = 10; ctx.lineJoin = "round"; ctx.stroke(); ctx.restore();
  return true;
}

function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

// 「冒險手冊」風格的卡片：粗墨線＋硬陰影
function inkCard(ctx, x, y, w, h, fill) {
  rrect(ctx, x + 14, y + 14, w, h, 18); ctx.fillStyle = CARD_C.ink; ctx.fill();
  rrect(ctx, x, y, w, h, 18); ctx.fillStyle = fill; ctx.fill();
  ctx.lineWidth = 6; ctx.strokeStyle = CARD_C.ink; ctx.stroke();
}

async function drawShareCard(w, game, { showRoute = true } = {}) {
  const cv = document.createElement("canvas");
  cv.width = CARD_W; cv.height = CARD_H;
  const ctx = cv.getContext("2d");
  const F = (wt, px) => `${wt} ${px}px ${CARD_FONT}`;

  // 背景：霧藍紙＋點點
  ctx.fillStyle = CARD_C.page; ctx.fillRect(0, 0, CARD_W, CARD_H);
  ctx.fillStyle = "#D5D8EC";
  for (let y = 30; y < CARD_H; y += 40) for (let x = 30; x < CARD_W; x += 40) ctx.fillRect(x, y, 4, 4);

  // 標題
  const d = new Date(`${w.date}T00:00:00`);
  ctx.fillStyle = CARD_C.pill; ctx.fillRect(80, 120, 22, 70);
  ctx.strokeStyle = CARD_C.ink; ctx.lineWidth = 5; ctx.strokeRect(80, 120, 22, 70);
  ctx.fillStyle = CARD_C.ink; ctx.font = F(800, 64); ctx.textBaseline = "middle";
  ctx.fillText(w.name || "跑步", 130, 158);
  ctx.fillStyle = CARD_C.ink3; ctx.font = F(600, 40);
  ctx.fillText(`${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} 週${WD[(d.getDay() + 6) % 7]}${w.start ? `　${w.start.slice(11, 16)}` : ""}`, 82, 240);

  // 大數字：距離
  inkCard(ctx, 80, 300, 920, 430, CARD_C.surface);
  ctx.fillStyle = CARD_C.ink; ctx.font = F(800, 230); ctx.textBaseline = "alphabetic";
  const km = (w.km || 0).toFixed(2);
  ctx.fillText(km, 130, 545);
  const kmW = ctx.measureText(km).width;
  ctx.font = F(700, 70); ctx.fillStyle = CARD_C.ink2; ctx.fillText("km", 150 + kmW, 545);
  // 三個小數字
  const stats = [["配速", w.pace ? `${w.pace.replace(":", "'")}"` : "–"], ["時間", fmtDur(w.minutes)], ["平均心率", w.avg_hr ? `${w.avg_hr}` : "–"]];
  stats.forEach(([k, v], i) => {
    const x = 130 + i * 290;
    ctx.font = F(600, 34); ctx.fillStyle = CARD_C.ink3; ctx.fillText(k, x, 620);
    ctx.font = F(800, 58); ctx.fillStyle = CARD_C.ink; ctx.fillText(v, x, 690);
  });

  // 路線或配速曲線
  inkCard(ctx, 80, 790, 920, 590, CARD_C.surface);
  const shape = showRoute ? routeShape(w.points) : null;
  if (shape) drawRoute(ctx, shape, { x: 150, y: 850, w: 780, h: 470 });
  else if (drawPace(ctx, w.points, { x: 140, y: 860, w: 800, h: 410 })) {
    ctx.font = F(600, 32); ctx.fillStyle = CARD_C.ink3; ctx.fillText("配速（越上面越快）", 140, 1340);
  } else {
    ctx.font = F(700, 60); ctx.fillStyle = CARD_C.ink2; ctx.textAlign = "center";
    ctx.fillText("今天也完成了 ✓", CARD_W / 2, 1080); ctx.textAlign = "left";
  }

  // 夥伴
  inkCard(ctx, 80, 1440, 920, 330, "#FFF1C2");
  const ch = game?.character;
  const sp = ch && species(ch.species);
  if (sp) {
    const st = sp.stages[game.stage] || sp.stages[0];
    const im = await loadImg(st.file);
    if (im) {
      const h = 260, wpx = (im.width / im.height) * h;
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(im, 130, 1475, Math.min(wpx, 300), h);
    }
    ctx.textBaseline = "alphabetic";
    ctx.font = F(800, 54); ctx.fillStyle = CARD_C.ink;
    ctx.fillText(ch.nickname || st.name, 470, 1570);
    ctx.font = F(600, 38); ctx.fillStyle = CARD_C.ink2;
    ctx.fillText(`${st.name} · Lv ${game.level}`, 470, 1630);
    ctx.fillText(`這趟 +${w.xp || 0} 經驗值`, 470, 1690);
  } else {
    ctx.font = F(800, 54); ctx.fillStyle = CARD_C.ink; ctx.fillText("跑完了！", 130, 1590);
    ctx.font = F(600, 38); ctx.fillStyle = CARD_C.ink2; ctx.fillText(`這趟 +${w.xp || 0} 經驗值`, 130, 1660);
  }

  // 頁尾
  ctx.font = F(800, 40); ctx.fillStyle = CARD_C.ink; ctx.textAlign = "center";
  ctx.fillText("跑課 · 每天幫你排好跑步課表", CARD_W / 2, 1855); ctx.textAlign = "left";
  return cv;
}

// 白字數據（照片版、透明版共用）：Strava 那種「小標＋大數字」
function statBlock(ctx, x, y, label, value, unit, size = 96, align = "left") {
  ctx.save();
  ctx.textBaseline = "alphabetic";
  ctx.shadowColor = "rgba(0,0,0,.45)"; ctx.shadowBlur = 10; ctx.shadowOffsetY = 2;
  ctx.fillStyle = "rgba(255,255,255,.85)"; ctx.font = `600 ${Math.round(size * 0.36)}px ${CARD_FONT}`;
  ctx.textAlign = align; ctx.fillText(label, x, y);
  ctx.textAlign = "left";
  ctx.font = `800 ${size}px ${CARD_FONT}`;
  const vw = ctx.measureText(value).width;
  const uFont = `700 ${Math.round(size * 0.42)}px ${CARD_FONT}`;
  ctx.font = uFont;
  const uw = unit ? ctx.measureText(unit).width + 12 : 0;
  const x0 = align === "center" ? x - (vw + uw) / 2 : x, yv = y + size * 1.02;
  ctx.fillStyle = "#FFFFFF"; ctx.font = `800 ${size}px ${CARD_FONT}`; ctx.fillText(value, x0, yv);
  if (unit) { ctx.font = uFont; ctx.fillText(unit, x0 + vw + 12, yv); }
  ctx.restore();
}

function wordmark(ctx, x, y, size = 40, align = "left") {
  ctx.save();
  ctx.font = `800 ${size}px ${CARD_FONT}`; ctx.textBaseline = "middle"; ctx.textAlign = "left";
  const tw = ctx.measureText("跑課").width, bw = size * 0.38, total = bw + 12 + tw;
  const x0 = align === "center" ? x - total / 2 : x;
  ctx.shadowColor = "rgba(0,0,0,.45)"; ctx.shadowBlur = 8;
  ctx.fillStyle = CARD_C.pill; ctx.fillRect(x0, y - size * 0.5, bw, size);
  ctx.fillStyle = "#FFFFFF"; ctx.fillText("跑課", x0 + bw + 12, y + 2);
  ctx.restore();
}

const paceTxt = (w) => (w.pace ? `${w.pace.replace(":", "'")}"` : "–");

// 照片版：自己的照片當背景（裁成直式），下面一層漸層讓白字看得清楚
async function drawPhotoCard(w, photo, { showRoute = true } = {}) {
  const cv = document.createElement("canvas");
  cv.width = CARD_W; cv.height = CARD_H;
  const ctx = cv.getContext("2d");
  if (photo) {
    const s = Math.max(CARD_W / photo.width, CARD_H / photo.height);
    const dw = photo.width * s, dh = photo.height * s;
    ctx.drawImage(photo, (CARD_W - dw) / 2, (CARD_H - dh) / 2, dw, dh);
  } else {
    const g = ctx.createLinearGradient(0, 0, 0, CARD_H);
    g.addColorStop(0, "#4B45C6"); g.addColorStop(1, "#1B1E3C");
    ctx.fillStyle = g; ctx.fillRect(0, 0, CARD_W, CARD_H);
  }
  const top = ctx.createLinearGradient(0, 0, 0, 360);
  top.addColorStop(0, "rgba(0,0,0,.45)"); top.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = top; ctx.fillRect(0, 0, CARD_W, 360);
  const bot = ctx.createLinearGradient(0, CARD_H * 0.45, 0, CARD_H);
  bot.addColorStop(0, "rgba(0,0,0,0)"); bot.addColorStop(1, "rgba(0,0,0,.72)");
  ctx.fillStyle = bot; ctx.fillRect(0, CARD_H * 0.45, CARD_W, CARD_H * 0.55);

  const d = new Date(`${w.date}T00:00:00`);
  ctx.save(); ctx.shadowColor = "rgba(0,0,0,.5)"; ctx.shadowBlur = 10;
  ctx.fillStyle = "#FFFFFF"; ctx.font = `800 60px ${CARD_FONT}`; ctx.textBaseline = "alphabetic";
  ctx.fillText(w.name || "跑步", 80, 170);
  ctx.fillStyle = "rgba(255,255,255,.85)"; ctx.font = `600 38px ${CARD_FONT}`;
  ctx.fillText(`${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}${w.start ? `　${w.start.slice(11, 16)}` : ""}`, 80, 228);
  ctx.restore();

  const shape = showRoute ? routeShape(w.points) : null;
  if (shape) drawRoute(ctx, shape, { x: 120, y: 980, w: 840, h: 420 }, "white");
  statBlock(ctx, 80, 1520, "距離", (w.km || 0).toFixed(2), "km", 110);
  statBlock(ctx, 80, 1720, "配速", paceTxt(w), "/km", 72);
  statBlock(ctx, 420, 1720, "時間", fmtDur(w.minutes), "", 72);
  if (w.avg_hr) statBlock(ctx, 760, 1720, "心率", `${w.avg_hr}`, "", 72);
  wordmark(ctx, CARD_W - 80 - 140, 1560, 44);
  return cv;
}

// 透明版：只有白字和路線，背景透明，可以貼到 IG 限時動態的任何照片上
async function drawStickerCard(w, { showRoute = true } = {}) {
  const shape = showRoute ? routeShape(w.points) : null;
  const H = shape ? 1500 : 1000;
  const cv = document.createElement("canvas");
  cv.width = CARD_W; cv.height = H;
  const ctx = cv.getContext("2d");
  const cx = CARD_W / 2;
  statBlock(ctx, cx, 120, "距離", (w.km || 0).toFixed(2), "km", 130, "center");
  statBlock(ctx, cx, 400, "配速", paceTxt(w), "/km", 88, "center");
  statBlock(ctx, cx, 620, "時間", fmtDur(w.minutes), "", 88, "center");
  if (shape) drawRoute(ctx, shape, { x: 240, y: 880, w: 600, h: 430 }, "white");
  wordmark(ctx, cx, H - 70, 44, "center");
  return cv;
}

function fmtDur(min) {
  const s = Math.round((min || 0) * 60), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
}

async function openShareCard(w) {
  const game = await api("/api/game").catch(() => null);
  await loadChars().catch(() => null);
  const hasRoute = !!routeShape(w.points);
  let showRoute = hasRoute, blob = null, mode = "card", photo = null;
  sheet(`<h2>分享這一趟</h2>
    <div class="seg share-mode" role="group" aria-label="圖片樣式">
      <button data-mode="card" aria-pressed="true">圖卡</button><button data-mode="photo" aria-pressed="false">照片</button><button data-mode="sticker" aria-pressed="false">透明</button></div>
    <div class="share-photo" hidden><label class="btn tonal small">${ico("upload")}選一張照片<input type="file" accept="image/*" id="sh-photo" hidden></label>
      <span class="muted" id="sh-photo-hint">沒選照片會用預設底色</span></div>
    <div class="share-prev"><p class="muted">產生圖片中…</p></div>
    <p class="muted share-note" hidden>透明背景的 PNG：在 IG 限時動態裡用「貼圖 → 相片」貼到任何照片上。</p>
    ${hasRoute ? `<label class="toggle-row"><span>顯示路線形狀<small>不畫地圖，頭尾各去掉一小段</small></span><input type="checkbox" class="switch" id="sr" checked></label>` : ""}
    <div class="btn-row"><button class="btn" id="sh-go">${ico("share")}分享</button><button class="btn outline" id="sh-dl">下載圖片</button></div>`,
  async (box) => {
    const prev = box.querySelector(".share-prev");
    let gen = 0;
    const draw = async () => {
      const my = ++gen;   // 連續切換時只保留最後一次的結果
      prev.innerHTML = `<p class="muted">產生圖片中…</p>`;
      const cv = mode === "photo" ? await drawPhotoCard(w, photo, { showRoute })
        : mode === "sticker" ? await drawStickerCard(w, { showRoute }) : await drawShareCard(w, game, { showRoute });
      const out = await new Promise((ok) => cv.toBlob(ok, "image/png"));
      if (my !== gen) return;
      blob = out;
      prev.innerHTML = "";
      prev.classList.toggle("checker", mode === "sticker");
      const im = new Image(); im.src = URL.createObjectURL(blob); im.alt = "分享圖片預覽";
      prev.append(im);
    };
    await draw();
    box.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => {
      mode = b.dataset.mode;
      box.querySelectorAll("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      box.querySelector(".share-photo").hidden = mode !== "photo";
      box.querySelector(".share-note").hidden = mode !== "sticker";
      draw();
    }));
    box.querySelector("#sh-photo").onchange = async (e) => {
      const f = e.target.files?.[0];
      if (!f) return;
      photo = await loadImg(URL.createObjectURL(f));
      box.querySelector("#sh-photo-hint").textContent = photo ? f.name : "這張照片讀不出來，換一張試試";
      draw();
    };
    const sr = box.querySelector("#sr");
    if (sr) sr.onchange = () => { showRoute = sr.checked; draw(); };
    const fname = () => `跑課-${w.date}${mode === "sticker" ? "-透明" : mode === "photo" ? "-照片" : ""}.png`;
    const dl = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = fname(); a.click(); };
    box.querySelector("#sh-dl").onclick = dl;
    box.querySelector("#sh-go").onclick = async () => {
      const file = new File([blob], fname(), { type: "image/png" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        try { await navigator.share({ files: [file], title: "跑課" }); } catch (e) { if (e.name !== "AbortError") toast("沒辦法分享，改用下載"); }
      } else { dl(); toast("這個瀏覽器不能直接分享，已經下載圖片"); }
    };
  });
}
