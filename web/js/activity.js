"use strict";
// 活動頁：本週跑量、本月月曆（含課表）、活動列表、單趟詳情、長期規劃

let ACT_MORE = null;

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

    ${volumeHtml(a)}

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
      <button class="btn tonal block" data-go="#/season">${ico("flag")}查看長期規劃</button>
    </section>

    <section class="section" aria-labelledby="alh">
      <h2 id="alh" class="sr-only">活動列表</h2>
      <div id="acts">${actRows(a.activities)}</div>
      ${a.activities.length >= 40 ? `<button class="btn outline block" id="more">載入更多</button>` : ""}
      ${a.activities.length ? "" : `<div class="card"><h2>還沒有跑步紀錄</h2><p class="muted">手錶同步到 Garmin Connect 後，在摘要頁按同步。</p></div>`}
    </section>`;
  bindCommon();
  const shift = (n) => { const d = new Date(y, mo - 1 + n, 1); S.ym = localISO(d).slice(0, 7); viewActivity(); };
  document.getElementById("prev").onclick = () => shift(-1);
  document.getElementById("next").onclick = () => shift(1);
  const more = document.getElementById("more");
  if (more) more.onclick = () => busy(more, async () => {
    const r = await api(`/api/activities?before=${ACT_MORE}`);
    document.getElementById("acts").insertAdjacentHTML("beforeend", actRows(r.activities, ACT_MORE.slice(0, 7)));
    bindCommon(document.getElementById("acts"));
    ACT_MORE = r.activities.length ? r.activities[r.activities.length - 1].date : null;
    if (r.activities.length < 40) more.remove();
  });
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

function volumeHtml(a) {
  const v = a.volume;
  if (!v) return "";
  const V = { ok: ["done", "適中"], low: ["warn", v.verdict?.label], warn: ["warn", "偏多"], bad: ["missed", "過多"] };
  const pts = a.weeks.map((x) => ({ label: x.week_start.slice(5).replace("-", "/"), value: x.run_load, stack: x.other_load }));
  const base = a.weeks.slice(-5, -1).map((x) => x.load);
  const avg = base.length === 4 ? base.reduce((p, c) => p + c, 0) / 4 : null;
  const hm = (m) => (m >= 60 ? `${Math.floor(m / 60)}<small>h</small>${m % 60}<small>m</small>` : `${m}<small>m</small>`);
  return `<section class="card" style="margin-top:16px" aria-labelledby="vh">
    <div class="section-head" style="margin:0"><h2 id="vh" style="display:flex;gap:6px;align-items:center">${ico("bolt")}總活動量</h2>
      ${v.verdict ? `<span class="badge ${V[v.verdict.level][0]}">${esc(V[v.verdict.level][1])}</span>` : ""}</div>
    <p style="margin-top:4px">${v.verdict ? `近 7 天訓練負荷是前 4 週平均的 <b class="num">${Math.round(v.ratio * 100)}%</b>。${esc(v.verdict.text)}`
      : "跑步加上其他運動的總負荷。累積滿 5 週紀錄後，會開始判斷活動量是否過多。"}</p>
    ${barChart(pts, { color: "var(--c-run)", stackColor: SPORT_COLOR.other, avg, h: 160, highlightLast: true })}
    <div class="legend"><span><i style="background:var(--c-run)"></i>跑步</span><span><i style="background:${SPORT_COLOR.other}"></i>其他運動</span><span>虛線：前 4 週平均</span></div>
    <div class="tiles" style="margin-top:8px">
      <div class="tile"><p class="k">本週跑步</p><p class="v num">${hm(v.run_minutes)}</p></div>
      <div class="tile"><p class="k">本週其他運動</p><p class="v num">${hm(v.other_minutes)}</p></div>
      <div class="tile"><p class="k">跑步占負荷</p><p class="v num">${v.run_share ?? "–"}<small>%</small></p></div>
    </div>
    ${v.sports.length ? `<p class="muted" style="margin-top:8px">本週其他運動：${v.sports.map((s) => `${esc(s.label)} ${s.count} 次 ${s.minutes} 分`).join("、")}</p>` : ""}
    <p class="muted" style="margin-top:6px">負荷用心率和時間估算；其他運動也會算進恢復分數和課表的疲勞判斷。</p>
  </section>`;
}

function actRows(list, prevMonth = null) {
  let out = "", open = false, lastM = prevMonth;
  for (const x of list) {
    const ym = x.date.slice(0, 7);
    if (ym !== lastM || !open) {
      if (open) out += "</div>";
      if (ym !== lastM) out += `<p class="month-label">${+ym.slice(0, 4)} 年 ${+ym.slice(5)} 月</p>`;
      out += `<div class="card" style="padding:4px 12px">`;
      open = true; lastM = ym;
    }
    const st = x.start ? x.start.slice(11, 16) : "";
    if (x.cross) {
      out += `<button class="row" data-go="#/workout/${encodeURIComponent(x.id)}">
      <span class="ri" style="color:${SPORT_COLOR[x.group] || SPORT_COLOR.other}">${sportIco(x.group)}</span>
      <span><small>${+x.date.slice(5, 7)}/${+x.date.slice(8)} ${st}${x.sport_label && x.sport_label !== x.name ? ` · ${esc(x.sport_label)}` : ""}</small><b>${esc(x.name)}</b>
        <small class="num">${x.minutes} 分${x.km ? ` · ${fmt(x.km, 1)} km` : ""}${x.kcal ? ` · ${x.kcal} kcal` : ""}</small></span>
      <span class="rv">${x.avg_hr ? `<b class="num">${x.avg_hr}</b> bpm` : ""} ${ico("chevron")}</span></button>`;
      continue;
    }
    out += `<button class="row" data-go="#/workout/${encodeURIComponent(x.id)}">
      <span class="ri" style="color:var(--c-run)">${ico("run")}</span>
      <span><small>${+x.date.slice(5, 7)}/${+x.date.slice(8)} ${st}</small><b>${esc(x.name)}</b>
        <small class="num">${x.minutes} 分 · ${fmt(x.km, 2)} km${x.pace ? ` · ${x.pace.replace(":", "'")}"/km` : ""}${x.kcal ? ` · ${x.kcal} kcal` : ""}</small></span>
      <span class="rv">${x.avg_hr ? `<b class="num">${x.avg_hr}</b> bpm` : ""} ${ico("chevron")}</span></button>`;
  }
  return out + (open ? "</div>" : "");
}

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
  const V = { good: ["done", "好"], ok: ["today", "普通"], bad: ["missed", "要加強"] };
  const lv = r.score >= 85 ? "green" : r.score >= 65 ? "yellow" : "red";
  return `<section class="card warm" style="margin-top:16px" aria-labelledby="rph">
    <div class="today-head"><span><span class="muted">訓練報告</span><h2 id="rph" style="font-size:1.25rem">${esc(r.headline)}</h2>
      ${w.plan && !["rest", "done"].includes(w.plan.kind) ? `<span class="muted">當天課表：${esc(w.plan.title)}${w.plan.minutes ? `（${w.plan.minutes} 分）` : ""}</span>` : `<span class="muted">這天沒有排課，只分析跑步本身</span>`}</span>
      <span class="score ${lv}" aria-label="這堂課 ${r.score} 分">${r.score}</span></div>
    <ul class="list" style="margin-top:10px">${r.items.map((i) => `<li style="padding:10px 0;border-top:1px solid var(--line)">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><b style="font-weight:600">${i.label}</b>
        <span><span class="num" style="margin-right:6px">${esc(i.value)}</span><span class="badge ${V[i.verdict][0]}">${V[i.verdict][1]}</span></span></div>
      <p style="font-size:.875rem;color:var(--ink-2)">${esc(i.text)}</p></li>`).join("")}</ul>
    ${r.improve.length ? `<h3 style="margin-top:12px">下次可以這樣調整</h3>
      <ul style="margin:6px 0 0;padding-left:1.2em;font-size:.9375rem;line-height:1.6">${r.improve.map((x) => `<li><b>${x.label}：</b>${esc(x.tip)}</li>`).join("")}</ul>`
      : `<p style="margin-top:10px">這堂沒有需要調整的地方，維持這個感覺。</p>`}
    ${w.notes.map((n) => `<p style="margin-top:8px;font-size:.9375rem;color:var(--ink-2)">${esc(n)}</p>`).join("")}
    ${w.focus.length ? `<div class="tile" style="margin-top:12px;background:var(--page)"><p class="k">近 4 週最需要補足</p>
      ${w.focus.map((f) => `<p style="margin-top:4px;font-size:.9375rem"><b>${f.label}</b>（${esc(f.value)}）：${esc(f.tip)}</p>`).join("")}</div>` : ""}
    <p class="muted" style="margin-top:10px">這趟讓夥伴獲得約 +${w.xp} XP。</p>
  </section>`;
}

async function viewSeason() {
  const s = await api("/api/season");
  const maxT = Math.max(...s.weeks.map((w) => w.target_minutes), 1);
  const phaseColor = { base: "#DCEFD8", build: "#FCE9C2", peak: "#F7D3CB", taper: "#DDE6F5", race_week: "#C62828", post: "#DDE6F5", open: "#EFE5CF" };
  $app.innerHTML = `
    ${appbar("長期規劃", { back: true })}
    <p class="muted">${s.event_date ? `距離「${esc(s.goal_name || "比賽")}」（${s.event_date.replaceAll("-", "/")}）還有 ${s.weeks.length} 週。` : "接下來 12 週的方向。到「我的 → 目標與課表」設定比賽日期，就會依比賽倒數安排。"}
      每天的實際課表仍會依當天狀態調整。</p>
    <ol class="list card" style="margin-top:12px;padding:4px 14px">
      ${s.weeks.map((w, i) => `<li style="padding:12px 0;${i ? "border-top:1px solid var(--line)" : ""}">
        <div style="display:grid;grid-template-columns:5rem auto 1fr;gap:8px;align-items:center">
          <b class="num" style="${i === 0 ? "color:var(--primary)" : ""}">${w.week_start.slice(5).replace("-", "/")}${i === 0 ? " 本週" : ""}</b>
          <span class="badge" style="background:${w.deload ? "var(--surface-hi)" : phaseColor[w.phase]};${w.phase === "race_week" ? "color:#fff" : ""}">${w.deload ? "減量週" : w.phase_label}</span>
          <span class="num" style="text-align:right;font-weight:600">${w.target_minutes} 分</span></div>
        <div class="progress"><span style="width:${(w.target_minutes / maxT) * 100}%;background:${w.has_event ? "var(--bad)" : w.deload ? "var(--ink-3)" : "var(--c-run)"};opacity:${w.actual_minutes != null ? 1 : 0.55}"></span></div>
        <p class="muted">${esc(w.focus)}${w.long_minutes ? `；長跑約 ${w.long_minutes} 分` : ""}${w.has_event ? `；🏁 ${esc((w.races || []).join("、") || "比賽")}在這週` : ""}${w.actual_minutes != null ? `；本週已跑 ${w.actual_minutes} 分` : ""}</p></li>`).join("")}
    </ol>`;
  bindCommon();
}
