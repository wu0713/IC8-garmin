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

function drawRoute(ctx, pts, box) {
  const k = Math.cos((pts[0].lat * Math.PI) / 180);
  const xs = pts.map((p) => p.lon * k), ys = pts.map((p) => -p.lat);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const s = Math.min(box.w / (maxX - minX || 1e-6), box.h / (maxY - minY || 1e-6));
  const ox = box.x + (box.w - (maxX - minX) * s) / 2, oy = box.y + (box.h - (maxY - minY) * s) / 2;
  ctx.save();
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  ctx.beginPath();
  pts.forEach((_, i) => { const X = ox + (xs[i] - minX) * s, Y = oy + (ys[i] - minY) * s; i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y); });
  ctx.strokeStyle = CARD_C.ink; ctx.lineWidth = 26; ctx.stroke();
  ctx.strokeStyle = CARD_C.pill; ctx.lineWidth = 14; ctx.stroke();
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

function fmtDur(min) {
  const s = Math.round((min || 0) * 60), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
}

async function openShareCard(w) {
  const game = await api("/api/game").catch(() => null);
  await loadChars().catch(() => null);
  const hasRoute = !!routeShape(w.points);
  let showRoute = hasRoute, blob = null;
  sheet(`<h2>分享這一趟</h2>
    <div class="share-prev"><p class="muted">產生圖卡中…</p></div>
    ${hasRoute ? `<label class="toggle-row"><span>顯示路線形狀<small>不畫地圖，頭尾各去掉一小段</small></span><input type="checkbox" class="switch" id="sr" checked></label>` : ""}
    <div class="btn-row"><button class="btn" id="sh-go">${ico("share")}分享</button><button class="btn outline" id="sh-dl">下載圖片</button></div>`,
  async (box) => {
    const prev = box.querySelector(".share-prev");
    const draw = async () => {
      prev.innerHTML = `<p class="muted">產生圖卡中…</p>`;
      const cv = await drawShareCard(w, game, { showRoute });
      blob = await new Promise((ok) => cv.toBlob(ok, "image/png"));
      prev.innerHTML = "";
      const im = new Image(); im.src = URL.createObjectURL(blob); im.alt = "分享圖卡預覽";
      prev.append(im);
    };
    await draw();
    const sr = box.querySelector("#sr");
    if (sr) sr.onchange = () => { showRoute = sr.checked; draw(); };
    const fname = `跑課-${w.date}.png`;
    const dl = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = fname; a.click(); };
    box.querySelector("#sh-dl").onclick = dl;
    box.querySelector("#sh-go").onclick = async () => {
      const file = new File([blob], fname, { type: "image/png" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        try { await navigator.share({ files: [file], title: "跑課" }); } catch (e) { if (e.name !== "AbortError") toast("沒辦法分享，改用下載"); }
      } else { dl(); toast("這個瀏覽器不能直接分享，已經下載圖片"); }
    };
  });
}
