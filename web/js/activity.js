"use strict";
// 活動頁：本週跑量、本月月曆（含課表）、活動列表、單趟詳情、長期規劃

let ACT_MORE = null;
const RECENT_N = 5;   // 紀錄頁只列最近幾筆，其他到「全部紀錄」看

async function viewActivity() {
  S.ym ||= localISO().slice(0, 7);
  const [a, m] = await Promise.all([api("/api/activities"), api(`/api/month?ym=${S.ym}`)]);
  const w = a.this_week;
  const prevKm = a.weeks.slice(-5, -1).map((x) => x.km);
  const baseKm = prevKm.length ? prevKm.reduce((p, c) => p + c, 0) / prevKm.length : null;
  const [y, mo] = S.ym.split("-").map(Number);
  const lead = (new Date(y, mo - 1, 1).getDay() + 6) % 7;
  const today = localISO();
  const wdNow = (new Date().getDay() + 6) % 7;  // 0=週一
  const loadTxt = a.load_change == null ? "持續記錄跑步，就能和前 4 週比較。"
    : wdNow < 6 ? `這週還沒結束，目前累積的訓練負荷是前 4 週平均週負荷的 ${Math.round((1 + a.load_change) * 100)}%。`
    : `這週訓練負荷比前 4 週平均${a.load_change >= 0 ? "高" : "低"} ${Math.abs(Math.round(a.load_change * 100))}%。`;
  ACT_MORE = a.activities.length ? a.activities[a.activities.length - 1].date : null;

  $app.innerHTML = `
    ${appbar("運動紀錄")}
    <section class="card" aria-labelledby="wl">
      <div class="section-head" style="margin:0"><h2 id="wl" style="color:var(--c-run);display:flex;gap:6px;align-items:center">${ico("trend")}本週跑量</h2><span class="muted">近 13 週</span></div>
      <p style="font-size:2.25rem;font-weight:600;line-height:1.1" class="num">${fmt(w.km, 1)}<small style="font-size:1rem;font-weight:400"> 公里</small></p>
      <p class="muted">${loadTxt}</p>
      ${barChart(a.weeks.map((x) => ({ label: x.week_start.slice(5).replace("-", "/"), value: x.km })), { color: "var(--c-run)", avg: baseKm, h: 180, highlightLast: true })}
      <div class="tiles" style="margin-top:8px">
        <div class="tile"><p class="k">次數</p><p class="v num">${w.runs}</p></div>
        <div class="tile"><p class="k">時間</p><p class="v num">${Math.floor(w.minutes / 60) ? `${Math.floor(w.minutes / 60)}<small>h</small>` : ""}${w.minutes % 60}<small>m</small></p></div>
        <div class="tile"><p class="k">距離</p><p class="v num">${fmt(w.km, 1)}<small>km</small></p></div>
      </div>
      <p class="muted" style="margin-top:8px">虛線是前 4 週的平均週跑量。</p>
    </section>

    <section class="card" aria-labelledby="calh" style="margin-top:16px">
      <div class="section-head" style="margin:0 0 6px">
        <button class="icon-btn" id="prev" aria-label="上個月">${ico("back")}</button>
        <h2 id="calh">${y} 年 ${mo} 月</h2>
        <button class="icon-btn" id="next" aria-label="下個月" style="transform:scaleX(-1)">${ico("back")}</button>
      </div>
      <div class="rcal" role="grid">
        ${WD.map((d) => `<div class="rc-wd" role="columnheader">${d}</div>`).join("")}${"<div></div>".repeat(lead)}
        ${m.days.map((d) => recordCell(d, today)).join("")}
      </div>
      ${m.totals?.length ? `<div class="rc-totals">${m.totals.map(totalChip).join("")}</div>`
        : `<p class="muted" style="text-align:center;margin-top:10px">這個月還沒有運動紀錄</p>`}
      <p class="muted rc-legend">點運動可以看詳細內容。還沒到的日子，底下的小標籤是排好的課：<span class="pt easy">輕鬆</span><span class="pt moderate">中強度</span><span class="pt hard">強度</span></p>
      <button class="btn tonal block" data-go="#/season">${ico("flag")}整季規劃</button>
    </section>

    <section class="section" aria-labelledby="alh">
      <div class="section-head"><h2 id="alh">最近紀錄</h2>
        ${a.activities.length > RECENT_N ? `<button class="text-btn" data-go="#/activity/all">看全部</button>` : ""}</div>
      ${a.activities.length ? `<div class="card act-list">${a.activities.slice(0, RECENT_N).map(actRow).join("")}</div>
        ${a.activities.length > RECENT_N ? `<button class="btn outline block" data-go="#/activity/all" style="margin-top:10px">看全部紀錄（依月份）</button>` : ""}`
        : `<div class="card"><h2>還沒有跑步紀錄</h2><p class="muted">手錶同步到 Garmin Connect 後，這裡會自己抓進來。</p></div>`}
    </section>`;
  bindCommon();
  const shift = (n) => { const d = new Date(y, mo - 1 + n, 1); S.ym = localISO(d).slice(0, 7); viewActivity(); };
  document.getElementById("prev").onclick = () => shift(-1);
  document.getElementById("next").onclick = () => shift(1);
}

// 一筆紀錄一列：左邊日期、中間名稱＋一行數字、右邊平均心率
function actRow(x) {
  const d = new Date(`${x.date}T00:00:00`);
  const stats = x.cross
    ? `${x.sport_label && x.sport_label !== x.name ? `${esc(x.sport_label)} · ` : ""}${x.minutes} 分${x.km ? ` · ${fmt(x.km, 1)} km` : ""}`
    : `${fmt(x.km, 2)} km · ${x.minutes} 分${x.pace ? ` · ${x.pace.replace(":", "'")}"` : ""}`;
  const color = x.cross ? SPORT_COLOR[x.group] || SPORT_COLOR.other : "var(--c-run)";
  return `<button class="arow" data-go="#/workout/${encodeURIComponent(x.id)}">
    <span class="ad"><b class="num">${d.getDate()}</b><small>週${WD[(d.getDay() + 6) % 7]}</small></span>
    <span class="am"><b><span class="aic" style="color:${color}">${x.cross ? sportIco(x.group) : ico("run")}</span>${esc(x.name)}</b><small class="num">${stats}</small></span>
    <span class="av">${x.avg_hr ? `<b class="num">${x.avg_hr}</b><small>bpm</small>` : ""}</span>
    ${ico("chevron")}</button>`;
}

// 全部紀錄：依月份收合，最新的月份展開；往下按「載入更早的紀錄」
let ALL_ACTS = [], ALL_OPEN = null;
async function viewActivityAll() {
  const r = await api("/api/activities");
  ALL_ACTS = r.activities;
  ALL_OPEN = new Set(ALL_ACTS.length ? [ALL_ACTS[0].date.slice(0, 7)] : []);
  ACT_MORE = r.activities.length >= 40 ? r.activities[r.activities.length - 1].date : null;
  $app.innerHTML = `${appbar("全部紀錄", { back: true })}<div id="months"></div>
    <button class="btn outline block" id="more" style="margin-top:14px" ${ACT_MORE ? "" : "hidden"}>載入更早的紀錄</button>`;
  drawMonths();
  bindCommon();
  const more = document.getElementById("more");
  more.onclick = () => act(more, async () => {
    const r2 = await api(`/api/activities?before=${ACT_MORE}`);
    const seen = new Set(ALL_ACTS.map((x) => x.id));
    ALL_ACTS = ALL_ACTS.concat(r2.activities.filter((x) => !seen.has(x.id)));
    ACT_MORE = r2.activities.length >= 40 ? r2.activities[r2.activities.length - 1].date : null;
    drawMonths();
    more.hidden = !ACT_MORE;
  });
}

function drawMonths() {
  const groups = [];
  for (const x of ALL_ACTS) {
    const ym = x.date.slice(0, 7);
    if (!groups.length || groups[groups.length - 1].ym !== ym) groups.push({ ym, list: [] });
    groups[groups.length - 1].list.push(x);
  }
  const box = document.getElementById("months");
  box.innerHTML = groups.map((g, gi) => {
    const partial = ACT_MORE && gi === groups.length - 1;   // 最舊的那個月可能還沒載完
    const runs = g.list.filter((x) => !x.cross), others = g.list.length - runs.length;
    const km = runs.reduce((p, x) => p + (x.km || 0), 0);
    return `<details class="mgroup" data-ym="${g.ym}" ${ALL_OPEN.has(g.ym) ? "open" : ""}>
      <summary><span><b>${+g.ym.slice(0, 4)} 年 ${+g.ym.slice(5)} 月</b>
        <small class="num">跑步 ${runs.length} 次 · ${fmt(km, 1)} km${others ? ` · 其他運動 ${others} 次` : ""}${partial ? "（還沒載完）" : ""}</small></span>${ico("down")}</summary>
      <div class="card act-list">${g.list.map(actRow).join("")}</div></details>`;
  }).join("") || `<div class="card"><p class="muted">還沒有紀錄。</p></div>`;
  box.querySelectorAll("details").forEach((d) => (d.ontoggle = () => (d.open ? ALL_OPEN.add(d.dataset.ym) : ALL_OPEN.delete(d.dataset.ym))));
  bindCommon(box);
}

// 紀錄月曆：每一天列出每一趟運動（圖示＋公里；沒有距離的運動顯示分鐘）
const kmTxt = (km) => (km >= 100 ? Math.round(km) : km.toFixed(1));
function actChip(id, group, km, minutes, label) {
  const val = km ? kmTxt(km) : `${minutes}′`;
  return `<button class="ac g-${group}" data-go="#/workout/${encodeURIComponent(id)}" aria-label="${esc(label)} ${km ? `${km} 公里` : `${minutes} 分鐘`}">${group === "run" ? ico("run") : sportIco(group)}<b class="num">${val}</b></button>`;
}
function recordCell(d, today) {
  const chips = (d.runs || []).map((r) => actChip(r.id, "run", r.km, r.minutes, "跑步"))
    .concat((d.others || []).map((o) => actChip(o.id, o.group, o.km, o.minutes, o.label)));
  const p = d.plan, future = d.date >= today && !chips.length && p && !["rest", "done"].includes(p.kind);
  const tag = future ? `<span class="pt ${d.race ? "race" : p.intensity}">${esc(d.race ? "比賽" : (typeof SHORT !== "undefined" && SHORT[p.kind]) || p.title)}</span>` : "";
  return `<div class="rc-d ${d.date === today ? "today" : ""}" role="gridcell" aria-label="${d.date}">
    <span class="n">${+d.date.slice(8)}${d.event ? " 🏁" : ""}</span>${chips.join("")}${tag}</div>`;
}
function totalChip(t) {
  const val = t.km ? `${fmt(t.km, t.km >= 100 ? 0 : 1)} km` : otherTime(t.minutes);
  return `<span class="tc g-${t.group}">${t.group === "run" ? ico("run") : sportIco(t.group)}<span><b class="num">${val}</b><small>${esc(t.label)} ${t.count} 次</small></span></span>`;
}

// 月曆上的其他運動：右上角小圖示（最多 2 個）
const otherTime = (m) => (m >= 60 ? `${Math.floor(m / 60)} 小時${m % 60 ? ` ${m % 60} 分` : ""}` : `${m} 分`);
function otherIcons(d) {
  if (!d.others?.length) return "";
  return `<span class="ox">${d.others.slice(0, 2).map((o) => `<span style="color:${SPORT_COLOR[o.group] || SPORT_COLOR.other}">${sportIco(o.group)}</span>`).join("")}</span>`;
}
const otherAria = (d) => (d.others?.length ? `，${d.others.map((o) => `${o.label} ${o.minutes} 分`).join("、")}` : "");



async function viewWorkout(id) {
  const w = await api(`/api/activities/${encodeURIComponent(id)}`);
  if (w.cross) return viewCross(w);
  const st = w.start ? new Date(w.start.replace(" ", "T")) : null;
  const end = st ? new Date(st.getTime() + w.minutes * 60000) : null;
  const hm = (d) => d && `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  const sec = Math.round(w.minutes * 60);
  const zMax = Math.max(1, ...w.zones.map((z) => z.minutes));
  $app.innerHTML = `
    ${appbar("跑步", { back: true })}
    <div class="device" style="grid-template-columns:48px 1fr;margin-bottom:12px">
      <span class="watch" style="width:48px;height:48px;background:var(--surface-hi);color:var(--c-run)">${ico("run")}</span>
      <span><b style="font-size:1.375rem">${esc(w.name)}</b><small>${w.date.replaceAll("-", "/")} ${st ? `${hm(st)} – ${hm(end)}` : ""}</small></span>
    </div>
    <div class="tiles two">
      <div class="tile"><p class="k">運動時間</p><p class="v num">${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}</p></div>
      <div class="tile"><p class="k">距離</p><p class="v num">${fmt(w.km, 2)}<small>km</small></p></div>
      <div class="tile"><p class="k">活動熱量</p><p class="v num">${w.kcal ?? "–"}<small>kcal</small></p></div>
      <div class="tile"><p class="k">平均配速</p><p class="v num">${w.pace ? w.pace.replace(":", "'") + '"' : "–"}<small>/km</small></p></div>
      <div class="tile"><p class="k">平均心率</p><p class="v num">${w.avg_hr ?? "–"}<small>bpm</small></p></div>
      <div class="tile"><p class="k">最高心率</p><p class="v num">${w.max_hr ?? "–"}<small>bpm</small></p></div>
      ${w.elevation_gain != null ? `<div class="tile"><p class="k">爬升</p><p class="v num">${Math.round(w.elevation_gain)}<small>m</small></p></div>` : ""}
      ${w.cadence ? `<div class="tile"><p class="k">步頻</p><p class="v num">${w.cadence}<small>spm</small></p></div>` : ""}
    </div>

    ${reportHtml(w)}

    <section class="card" style="margin-top:16px" aria-labelledby="hrh">
      <h2 id="hrh" style="color:var(--c-hr);font-weight:500">心率</h2>
      ${w.points.length ? hrDots(w.points, { max_hr: w.max_hr_ref }) : `<p class="muted">${esc(w.series_error || "這趟沒有心率曲線資料")}</p>`}
      ${w.hr_low ? `<p class="muted">平均 ${w.avg_hr} · 最低 ${w.hr_low} · 最高 ${w.hr_high} bpm</p>` : ""}
    </section>

    ${w.points.some((p) => p.pace) ? `<section class="card" style="margin-top:12px" aria-labelledby="ph">
      <h2 id="ph" style="color:var(--c-bb);font-weight:500">配速</h2>${paceLine(w.points)}<p class="muted">越上面越快</p></section>` : ""}

    ${w.points.length ? `<section class="card" style="margin-top:12px" aria-labelledby="zh">
      <h2 id="zh" style="font-weight:500">心率區間</h2>
      <ul class="list" style="margin-top:8px">${w.zones.map((z, i) => `<li style="display:grid;grid-template-columns:5.5rem 1fr 3.5rem;gap:8px;align-items:center;padding:4px 0">
        <span style="font-size:.875rem">${z.zone} ${z.label}</span>
        <span style="height:12px;border-radius:6px;background:var(--surface-hi);overflow:hidden"><span style="display:block;height:100%;width:${(z.minutes / zMax) * 100}%;background:${["#9E9E9E", "#1565C0", "#2E7D32", "#E8A200", "#C62828"][i]}"></span></span>
        <span class="num" style="text-align:right;font-size:.875rem">${z.minutes} 分</span></li>`).join("")}</ul>
      <p class="muted" style="margin-top:6px">以最大心率 ${w.max_hr_ref} 計算。</p></section>` : ""}

    ${w.splits.length ? `<section class="card" style="margin-top:12px" aria-labelledby="sh">
      <h2 id="sh" style="font-weight:500">每公里分段</h2>
      <table style="width:100%;border-collapse:collapse;margin-top:6px;font-size:.9375rem" class="num">
        <thead><tr style="color:var(--ink-3);font-size:.8125rem;text-align:left"><th style="font-weight:500">公里</th><th style="font-weight:500">配速</th><th style="font-weight:500">平均心率</th></tr></thead>
        <tbody>${w.splits.map((s) => `<tr style="border-top:1px solid var(--line)"><td style="padding:6px 0">${s.km}</td><td>${s.pace.replace(":", "'")}"</td><td>${s.hr ?? "–"}</td></tr>`).join("")}</tbody></table></section>` : ""}


    <div class="card warm" style="margin-top:12px;display:flex;gap:12px;align-items:center">${ico("watch")}
      <span><small class="muted" style="display:block">資料來源</small>${esc(w.device || "Garmin")}</span></div>`;
  bindCommon();
  if (w.fatigue) bindOptions("fat", w.fatigue.options);
}

function viewCross(w) {
  const col = SPORT_COLOR[w.group] || SPORT_COLOR.other;
  const sec = Math.round(w.minutes * 60);
  $app.innerHTML = `
    ${appbar(w.sport_label || "其他運動", { back: true })}
    <div class="device" style="grid-template-columns:48px 1fr;margin-bottom:12px">
      <span class="watch" style="width:48px;height:48px;background:var(--surface-hi);color:${col}">${sportIco(w.group)}</span>
      <span><b style="font-size:1.375rem">${esc(w.name)}</b><small>${w.date.replaceAll("-", "/")} ${w.start ? w.start.slice(11, 16) : ""}</small></span>
    </div>
    <div class="tiles two">
      <div class="tile"><p class="k">運動時間</p><p class="v num">${Math.floor(sec / 60)}<small>分</small></p></div>
      ${w.km ? `<div class="tile"><p class="k">距離</p><p class="v num">${fmt(w.km, 2)}<small>km</small></p></div>` : ""}
      <div class="tile"><p class="k">活動熱量</p><p class="v num">${w.kcal ?? "–"}<small>kcal</small></p></div>
      <div class="tile"><p class="k">平均心率</p><p class="v num">${w.avg_hr ?? "–"}<small>bpm</small></p></div>
      <div class="tile"><p class="k">最高心率</p><p class="v num">${w.max_hr ?? "–"}<small>bpm</small></p></div>
      <div class="tile"><p class="k">訓練負荷</p><p class="v num">${w.load}</p></div>
    </div>
    <section class="card warm" style="margin-top:16px">
      <span class="muted">對跑步訓練的影響</span>
      <h2 style="font-size:1.125rem;margin-top:2px">${esc(w.effect)}</h2>
      ${w.load_vs_easy_run ? `<p style="margin-top:6px">這次的負荷大約等於 <b class="num">${w.load_vs_easy_run}</b> 趟你平常的輕鬆跑${w.hr_pct ? `（平均心率約最大心率的 ${w.hr_pct}%）` : ""}。</p>` : ""}
      <p class="muted" style="margin-top:6px">其他運動不算進跑量和夥伴經驗值，但會算進總活動量、恢復分數和課表的疲勞判斷。</p>
    </section>`;
  bindCommon();
}

function reportHtml(w) {
  const r = w.report;
  if (!r) return "";
  if (r.score == null) return `<section class="card" style="margin-top:16px"><span class="muted">訓練報告</span><h2 style="font-size:1.0625rem">${esc(r.headline)}</h2>
    ${r.items.map((i) => `<p style="margin-top:6px;font-size:.9375rem"><b>${i.label} ${esc(i.value)}</b>：${esc(i.text)}</p>`).join("")}
    <p class="muted" style="margin-top:6px">戶外跑 3 公里以上、有心率資料，就會有完整的評分。</p></section>`;
  const V = { good: ["done", "好"], ok: ["today", "普通"], bad: ["missed", "要加強"], info: ["info", "參考"] };
  const lv = r.score >= 85 ? "green" : r.score >= 65 ? "yellow" : "red";
  return `<section class="card warm" style="margin-top:16px" aria-labelledby="rph">
    <div class="today-head"><span><span class="muted">訓練報告</span><h2 id="rph" style="font-size:1.25rem">${esc(r.headline)}</h2>
      ${w.plan && !["rest", "done"].includes(w.plan.kind) ? `<span class="muted">當天課表：${esc(w.plan.title)}${w.plan.minutes ? `（${w.plan.minutes} 分）` : ""}</span>` : `<span class="muted">這天沒有排課，只分析跑步本身</span>`}</span>
      <span class="score ${lv}" aria-label="這堂課 ${r.score} 分">${r.score}</span></div>
    <ul class="list" style="margin-top:10px">${r.items.map((i) => `<li style="padding:10px 0;border-top:1px solid var(--line)">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><b style="font-weight:600">${i.label}</b>
        <span><span class="num" style="margin-right:6px">${esc(i.value)}</span><span class="badge ${(V[i.verdict] || V.info)[0]}">${(V[i.verdict] || V.info)[1]}</span></span></div>
      <p style="font-size:.875rem;color:var(--ink-2)">${esc(i.text)}</p></li>`).join("")}</ul>
    ${r.improve.length ? `<h3 style="margin-top:12px">下次可以這樣調整</h3>
      <ul style="margin:6px 0 0;padding-left:1.2em;font-size:.9375rem;line-height:1.6">${r.improve.map((x) => `<li><b>${x.label}：</b>${esc(x.tip)}</li>`).join("")}</ul>`
      : `<p style="margin-top:10px">這堂沒有需要調整的地方，維持這個感覺。</p>`}
    ${w.notes.map((n) => `<p style="margin-top:8px;font-size:.9375rem;color:var(--ink-2)">${esc(n)}</p>`).join("")}
    <p class="muted" style="margin-top:10px">這趟讓夥伴獲得約 +${w.xp} XP。</p>
  </section>
  ${fatigueHtml(w.fatigue)}
  ${progressHtml(w.progress)}`;
}

function fatigueHtml(f) {
  if (!f) return "";
  const L = { low: "低", moderate: "中等", high: "高" };
  return `<section class="card" style="margin-top:14px" aria-labelledby="fth">
    <h2 id="fth" style="font-size:1.0625rem">這堂課的疲勞程度</h2>
    <div class="fat"><span class="lvl ${f.level}">${L[f.level]}</span><span>${esc(f.label.split("：")[1] || "")}</span></div>
    ${f.why.length ? `<ul class="why">${f.why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted" style="margin-top:6px">負荷在你平常的範圍內，恢復得了。</p>`}
    ${f.advice ? `<p style="margin-top:8px">${esc(f.advice)}</p>` : ""}
    ${optionsHtml(f.options, "fat")}
  </section>`;
}

function progressHtml(p) {
  if (!p) return "";
  return `<section class="card" style="margin-top:14px" aria-labelledby="pgh">
    <h2 id="pgh" style="font-size:1.0625rem">離目標還有多遠</h2>
    <p style="margin-top:6px">目前 VDOT <b>${p.cur_vdot}</b>，${esc(p.distance_label)}大約能跑 <b>${esc(p.pred || "—")}</b>${p.target ? `；目標 <b>${esc(p.target)}</b>（VDOT ${p.target_vdot}）` : ""}。</p>
    ${p.gap_pct != null ? `<p style="margin-top:4px">${p.gap_pct <= 0 ? "目前能力已經達標，接下來是把它穩定在比賽當天發揮出來。" : `還差 ${p.gap_pct}% 的能力。${p.verdict === "unrealistic" && p.realistic ? `以剩下的時間，比較實際的成績是 ${esc(p.realistic)}，課表先照這個排。` : ""}`}</p>` : ""}
    <p class="muted" style="margin-top:6px">VDOT 由近期最好的 3–10 km 成績推算，跑出新成績或檢核點測驗後會更新。</p>
    <button class="btn tonal block" style="margin-top:8px" data-go="#/season">看整季規劃</button>
  </section>`;
}

function assessHtml(s) {
  const a = s.assess || {}, lv = s.level || {};
  const V = { realistic: ["ok", "目標合理"], ambitious: ["tight", "目標偏積極"], unrealistic: ["infeasible", "目標太遠"] };
  const SEV = { danger: "alert", warn: "warn", info: "" };
  const g = s.goal;
  return `<section class="card" style="margin-top:14px" aria-labelledby="ah">
    <div class="section-head" style="margin:0"><h2 id="ah">實力評估</h2><span class="badge">${esc(lv.label || "")}${lv.source === "manual" ? "（自己指定）" : ""}</span></div>
    <p style="margin-top:6px">${a.cur_vdot ? `目前 VDOT 約 <b>${a.cur_vdot}</b>，${esc(s.distance_label)}現在大約能跑 <b>${esc(a.pred_goal || "—")}</b>` : "還沒有夠快的紀錄可以推估能力，先用平常配速估計"}${g.target ? `；目標 ${esc(g.target)}` : ""}。</p>
    ${a.verdict && V[a.verdict] ? `<p style="margin-top:6px"><span class="verdict ${V[a.verdict][0]}">${V[a.verdict][1]}</span> ${esc(a.text || "")}</p>` : ""}
    ${lv.shape ? `<p style="margin-top:8px"><b>課表形狀：</b>${esc(lv.shape)}</p>` : ""}
    ${(lv.why || []).length ? `<ul class="why">${lv.why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${(a.risks || []).map((r) => `<div class="tip ${SEV[r.severity] || ""}" style="margin-top:10px"><h3>${r.severity === "danger" ? "⚠️ " : ""}${esc(r.title)}</h3><p>${esc(r.text)}</p></div>`).join("")}
    <p class="muted" style="margin-top:8px">程度判斷不準的話，可以到「我的 → 目標與課表 → 我的程度」自己指定。</p>
  </section>`;
}

// 跑量偏低的週：如果是生病、出差或沒戴錶，標記後不算進基線和一致性
function excuseHtml(d, excused) {
  const low = d.low_week_list || [];
  const weeks = [...new Map([...low.map((x) => [x.week, x.km]), ...excused.map((w) => [w, null])]).entries()].sort();
  if (!weeks.length) return "";
  const md = (w) => w.slice(5).replace("-", "/");
  return `<div class="tile" style="margin-top:10px;background:var(--page)">
    <p class="k">這幾週跑量偏低，是生病、出差或沒戴錶嗎？</p>
    <p class="muted" style="margin-top:2px">標記後這週不算進跑量基線和「一致性」，課表會重新診斷。</p>
    <div class="chips" style="margin-top:6px">${weeks.map(([w, km]) => `<label><input type="checkbox" data-excuse="${w}" ${excused.includes(w) ? "checked" : ""}>
      <span>${md(w)} 那週${km != null ? `（${fmt(km, 1)} km）` : ""}</span></label>`).join("")}</div>
  </div>`;
}

async function viewSeason() {
  const [s, rn] = await Promise.all([api("/api/season"), api("/api/runner").catch(() => null)]);
  const f = s.feasibility, g = s.goal, d = s.diag;
  const VERDICT = { ok: ["ok", "可行，有緩衝"], tight: ["tight", "可行但沒有緩衝"], volume_short: ["tight", "做得到，但跑量不足"], infeasible: ["infeasible", "時間不夠"] };
  const TAG = { deload: "減量週", peak: "峰值", repeat: "重跑", phase_repeat: "回上一階段", flat: "固定頻率" };
  const maxK = Math.max(...s.weeks.map((w) => Math.max(w.target_km || 0, w.actual_km || 0)), 1);
  const sec = (x) => { const v = Math.round(x), h = Math.floor(v / 3600), m = Math.floor((v % 3600) / 60), ss = v % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`; };
  const z = s.zones.zones;
  const zrow = (k, n) => z[k] ? `<tr><td>${n}</td><td class="n">${paceStr(z[k].min)}–${paceStr(z[k].max)}</td><td class="cap">${z[k].hrCap ? `≤ ${z[k].hrCap}` : "不看"}</td></tr>` : "";

  $app.innerHTML = `
    ${appbar("整季規劃", { back: true })}
    <section class="card">
      <h2>${esc(g.name || (s.mode === "race" ? "目標賽" : "訓練週期"))}</h2>
      <p class="muted" style="margin-top:4px">${s.mode === "race"
        ? `${g.race_date.replaceAll("-", "/")}，${fmt(g.distance_km, g.distance_km % 1 ? 1 : 0)} 公里${g.target ? `，目標 ${g.target}` : "，沒填目標時間（用目前能力推估）"}。從 ${s.plan_start.slice(5).replace("-", "/")} 起共 ${s.n_weeks} 週。`
        : `${g.race_passed ? "目標賽已經結束。" : ""}沒有設定比賽：以 ${s.n_weeks} 週為一個週期循環，週期結束會依新資料重新診斷。設定比賽日期和目標時間，就會照規格書的分期倒數。`}</p>
      <div class="phasebar" aria-label="分期">${s.phases.map((p) => `<span class="${PHASE_CLS[p.phase]}" style="flex:${p.weeks}" title="${esc(p.label)} ${p.weeks} 週">${p.weeks >= 2 ? `${esc(p.label)} ${p.weeks}` : ""}</span>`).join("")}</div>
      <ul class="why">${s.phases.map((p) => `<li><b>${esc(p.label)}</b> ${p.weeks} 週：${esc(p.purpose)}</li>`).join("")}</ul>
    </section>

    ${assessHtml(s)}

    ${f && f.verdict ? `<section class="card" style="margin-top:14px" aria-labelledby="fh">
      <div class="section-head" style="margin:0"><h2 id="fh">可行性檢查</h2><span class="verdict ${(VERDICT[f.verdict] || ["", ""])[0]}">${(VERDICT[f.verdict] || ["", f.verdict])[1]}</span></div>
      <ul class="why">${f.why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${f.verdict === "infeasible" ? `
        <p style="margin-top:10px">${s.provisional ? `還沒選之前，課表先照「做得到的成績」${s.provisional_time ? `（${esc(s.provisional_time)}）` : ""}排：強度課和長跑照常，跑量只疊到時間允許的範圍。三個選項各有代價：` : "你選了照原目標練，課表會疊到時間允許的最高跑量，受傷風險較高。"}</p>
        <div class="choice">
          ${f.achievable ? `<button data-choice="achievable"><b>把目標改成 ${f.achievable}</b><small>賽期不變，用剩下的時間做得到的成績${f.achievable_basis ? `（依據：${esc(f.achievable_basis)}）` : ""}。</small></button>` : ""}
          ${f.suggested_date ? `<button data-choice="postpone"><b>改報 ${f.suggested_date.replaceAll("-", "/")} 之後的比賽</b><small>目標不變，給身體足夠的疊量時間（需要 ${f.required_weeks} 週）。</small></button>` : ""}
          ${s.decision !== "accept_risk" ? `<button data-choice="accept_risk"><b>照原目標練，接受較高的受傷風險</b><small>跑量只疊到 ${fmt(f.v_reachable, 0)} km/週，強度課配速仍以目標推算。</small></button>` : ""}
        </div>` : ""}
      ${f.assumed_target ? `<p class="muted" style="margin-top:8px">你沒填目標時間，先用目前能力推估的成績檢查。到「我的 → 目標與課表」填上目標會更準。</p>` : ""}
    </section>` : ""}

    <section class="card" style="margin-top:14px" aria-labelledby="dh">
      <div class="section-head" style="margin:0"><h2 id="dh">能力診斷</h2><span class="badge">${esc(d.limiter_label)}</span></div>
      <p style="margin-top:6px"><b>課表形狀：</b>${esc(d.shape)}</p>
      <p class="why-line">判斷依據：${esc(d.rule)}</p>
      <ul class="why">${d.why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${excuseHtml(d, s.excused_weeks || [])}
      <p class="muted" style="margin-top:6px">診斷在開季時做一次並固定下來，避免課表每天變樣；配速區間則會跟著新成績更新。</p>
    </section>

    ${s.warnings.length || s.triggers.length ? `<section class="section">${s.warnings.map((x) => `<div class="tip warn"><h3>${esc(x)}</h3></div>`).join("")}
      ${s.triggers.map((t) => `<div class="tip alert"><p class="muted">停損 · ${t.date.slice(5).replace("-", "/")}</p><h3>${esc(t.title)}</h3><p class="act">${esc(t.action)}</p></div>`).join("")}</section>` : ""}

    <section class="section" aria-labelledby="wh2">
      <h2 id="wh2" style="margin-bottom:8px">每週跑量</h2>
      <div class="card" style="padding:4px 14px">
        ${s.weeks.map((w) => `<div class="wkrow ${w.current ? "cur" : ""}">
          <div class="top"><b class="num">${w.week_start.slice(5).replace("-", "/")}</b>
            <span class="badge ${PHASE_CLS[w.phase]}">${esc(w.phase_label)}</span>
            <span class="muted" style="font-size:.8125rem">${TAG[w.tag] ? `<span class="badge">${TAG[w.tag]}</span> ` : ""}${w.checkpoint ? "🎯 " : ""}${w.events.length ? `📌 ${esc(w.events.join("、"))}` : ""}</span>
            <span class="num" style="font-weight:700">${fmt(w.target_km, 1)} km</span></div>
          <div class="bar" aria-hidden="true"><i style="width:${(w.target_km / maxK) * 100}%"></i>${w.actual_km != null ? `<i class="act" style="width:${(w.actual_km / maxK) * 100}%"></i>` : ""}</div>
          <p class="muted" style="font-size:.8125rem;margin-top:4px">長跑 ${fmt(w.long_km, 1)} km${w.quality.length ? `；${esc(w.quality.join("、"))}` : "；只有輕鬆跑"}${w.actual_km != null ? `；已跑 ${fmt(w.actual_km, 1)} km` : ""}
            ${[...w.notes, ...w.sched_notes].length ? `<br>${esc([...w.notes, ...w.sched_notes].join("；"))}` : ""}</p></div>`).join("")}
      </div>
      <p class="muted" style="margin-top:6px">深色細線是實際跑量。每 3–4 週一個減量週（× 0.7），峰值在賽前第 3–4 週，最後三週照 75% → 55% → 35% 遞減。</p>
    </section>

    ${s.checkpoints.length ? `<section class="section" aria-labelledby="ch">
      <h2 id="ch" style="margin-bottom:8px">檢核點</h2>
      ${s.checkpoints.map((c) => { const r = c.result; return `<div class="card" style="margin-top:10px">
        <div class="section-head" style="margin:0"><h3>${c.date.slice(5).replace("-", "/")} · ${esc(c.label)}</h3>${c.key ? `<span class="badge">關鍵</span>` : ""}</div>
        <p class="muted">${esc(c.purpose)}${c.equivalent_s ? `。照目標進度的等效成績 ${sec(c.equivalent_s)}` : ""}</p>
        ${c.thresholds ? `<div class="bands">${c.thresholds.map((t, i) => `<span class="${r && r.verdict === t.verdict ? "hit" : ""}">${i === 3 ? `> ${sec(c.thresholds[2].max_s)}` : `≤ ${sec(t.max_s)}`}</span><span class="${r && r.verdict === t.verdict ? "hit" : ""}">${esc(t.verdict)}：${esc(t.text)}</span>`).join("")}</div>` : ""}
        ${r ? `<div class="note-card" style="margin-top:10px">${ico("flag")}<span><b>結果 ${sec(r.seconds)}：${esc(r.verdict)}</b><br>${esc(r.text)}。配速區間已依這次成績重算。
          ${["slow_small", "slow_big"].includes(r.action) && !r.accepted ? `<br><button class="btn small" style="margin-top:8px" data-accept="${c.date}">採用建議，調整目標時間</button>` : r.accepted ? "<br>已調整目標。" : ""}</span></div>` : ""}
      </div>`; }).join("")}
    </section>` : ""}

    <section class="card" style="margin-top:16px" aria-labelledby="zh">
      <h2 id="zh">這階段的配速與心率上限</h2>
      <p class="muted">輕鬆跑用「目前」能力（VDOT ${s.zones.vdot_easy}），強度課依階段往目標內推（VDOT ${s.zones.vdot_quality}）。最大心率 ${s.max_hr}${s.max_hr_source === "estimated" ? "（公式估算，建議實測）" : ""}。</p>
      <table class="zt"><thead><tr><th>區間</th><th>配速 /km</th><th>心率上限</th></tr></thead><tbody>
        ${zrow("E", "輕鬆 E")}${zrow("M", "馬拉松 M")}${zrow("T", "節奏 T")}${zrow("I", "間歇 I")}${zrow("R", "快步 R")}${zrow("RP", "目標配速")}</tbody></table>
      ${s.zones.notes.map((n) => `<p class="why-line">${esc(n)}</p>`).join("")}
      <p class="muted" style="margin-top:8px">輕鬆跑個人化校正：${s.model?.active ? `已啟用（${s.model.sample_count} 趟）` : esc(s.model?.reason || "資料不足")}。只有前後半心率漂移 < 5% 的輕鬆跑才算數。</p>
      <button class="btn tonal block" data-go="#/me/health">心率區間說明（和手錶的 Z2 為什麼不一樣）</button>
    </section>
    ${rn ? `<div style="margin-top:16px">${runnerHtml(rn)}</div>` : ""}`;
  bindCommon();
  document.querySelectorAll("[data-choice]").forEach((b) => (b.onclick = () => act(b, async () => {
    const c = b.dataset.choice;
    if (c === "accept_risk" && !confirm("照原目標練：跑量只疊到時間允許的最高值，強度課仍用目標配速。受傷風險較高，確定嗎？")) return;
    await api("/api/plan/decision", { method: "POST", body: { choice: c } });
    toast({ achievable: "目標已調整，課表已重排", postpone: "賽期已延後，課表已重排", accept_risk: "已照原目標排課" }[c]); viewSeason();
  })));
  document.querySelectorAll("[data-accept]").forEach((b) => (b.onclick = () => act(b, async () => {
    await api("/api/plan/checkpoint/accept", { method: "POST", body: { date: b.dataset.accept } });
    toast("目標時間已調整"); viewSeason();
  })));
  document.querySelectorAll("[data-excuse]").forEach((b) => (b.onchange = async () => {
    const weeks = [...document.querySelectorAll("[data-excuse]:checked")].map((x) => x.dataset.excuse);
    await api("/api/prefs", { method: "PUT", body: { excused_weeks: weeks } });
    toast("已重新診斷，課表已重排"); viewSeason();
  }));
}
