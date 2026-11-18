"use strict";
// 分析頁：本週／本月課表、建議、回覆與調整、能力概況、長期趨勢

const KINDS = [
  ["easy", "輕鬆跑", "E，心率上限內"], ["recovery", "恢復跑", "更輕鬆"], ["long", "長跑", "耐力"],
  ["tempo", "節奏跑", "T，舒服的辛苦"], ["intervals", "間歇跑", "I，高強度"], ["hills", "坡道間歇", "I 的變化"],
  ["fartlek", "變速跑", "快慢交替"], ["race_pace", "比賽配速", "練節奏感"], ["progression", "漸進跑", "由慢到快"],
  ["rest", "休息", "這天不跑"],
];
const SHORT = { easy: "輕鬆", recovery: "恢復", long: "長跑", long_m: "長跑M", fartlek: "變速", tempo: "節奏", intervals: "間歇", hills: "坡道",
  progression: "漸進", race_pace: "配速", test: "測驗", rest: "休", race: "比賽", event: "行程", done: "完成",
  strength: "肌力", custom: "課" };
const STATUS = { done: ["done", "已完成"], missed: ["missed", "沒跑到"], today: ["today", "今天"], upcoming: ["", "待跑"],
  extra: ["extra", "額外"], skipped: ["missed", "跳過"], rest: ["", ""] };
const LV = { alert: "需要注意", warn: "留意", info: "建議", good: "做得好" };
const tipHtml = (t) => `<div class="tip ${t.level}"><p class="muted">${LV[t.level]}</p><h3>${esc(t.title)}</h3><p>${esc(t.why)}</p><p class="act">${esc(t.action)}</p>
  ${t.do ? `<button class="btn small" style="margin-top:8px" ${t.do.type === "resume" ? "data-resume" : `data-quick="${t.do.date}" data-kind="${t.do.type}"`}>${esc(t.do.label || "改成輕鬆跑")}</button>` : ""}</div>`;

function seasonHead(w) {
  const pct = w.n_weeks ? Math.round(((w.week_idx ?? 0) + 1) / w.n_weeks * 100) : 0;
  return `<button class="card" data-go="${w.imported ? "#/me/settings" : "#/season"}" style="display:block;width:100%;text-align:left;font:inherit;cursor:pointer" aria-label="看整季規劃">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span class="badge ${PHASE_CLS[w.phase] || ""}">${esc(w.phase_label || "—")}</span>
      <b>第 ${(w.week_idx ?? 0) + 1} / ${w.n_weeks} 週</b>
      ${w.verdict === "infeasible" || w.hold ? `<span class="verdict infeasible">目標要調整</span>` : w.verdict === "tight" ? `<span class="verdict tight">沒有緩衝</span>` : ""}
      <span class="muted" style="margin-left:auto">${esc(w.limiter_label || "")}</span></div>
    <div class="progress" style="margin-top:8px"><span style="width:${pct}%"></span></div>
    <p class="muted" style="margin-top:6px">${w.hold ? "目標在剩下的時間內做不到，點這裡選擇怎麼調整。" : `本週目標 ${fmt(w.target_km, 1)} km${w.next_checkpoint ? `；下一個檢核點 ${w.next_checkpoint.date.slice(5).replace("-", "/")}（${esc(w.next_checkpoint.label)}）` : ""}。`}
      <span style="color:var(--primary);font-weight:700">${w.imported ? "匯入的課表 ›" : "整季規劃 ›"}</span></p></button>`;
}

function weekHtml(w) {
  const pct = w.planned ? Math.round((w.done / w.planned) * 100) : 0;
  return `
    <p style="font-size:1.75rem;font-weight:600" class="num">${w.done}<small style="font-size:1rem;font-weight:400;color:var(--ink-2)"> / ${w.planned} 堂完成</small></p>
    <div class="progress" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="本週完成度"><span style="width:${pct}%"></span></div>
    <p class="muted">已跑 ${fmt(w.actual_km, 1)} km，本週排了 ${fmt(w.planned_km, 1)} km${w.target_km ? `（目標 ${fmt(w.target_km, 1)} km）` : ""}</p>
    <ul class="wk" style="margin-top:8px">
      ${w.days.map((d) => {
        const p = d.plan, [cls, txt] = STATUS[d.status] || ["", ""];
        const act = d.actual[0];
        const isRest = (!p || p.kind === "rest") && !d.actual.length;
        return `<li class="${d.is_today ? "is-today" : ""} ${isRest ? "rest-day" : ""}">
          <span class="wd"><b class="num">${+d.date.slice(8)}</b><small>${d.weekday}</small></span>
          <span>
            <span class="wt"><span class="int ${p && p.kind !== "rest" ? p.intensity : "rest"}"></span>${p && p.steps?.length && d.status !== "done" ? `<button data-detail="${d.date}" aria-label="看${esc(p.title)}的內容">${esc(p.title)}</button>` : esc(p && p.kind !== "done" ? p.title : act ? act.name : p ? p.title : "休息日")}
              ${txt ? `<span class="badge ${cls}">${txt}</span>` : ""}${p?.override ? `<span class="badge edit">已調整</span>` : ""}
              ${(isRest || p?.kind === "strength") && d.strength ? `<button class="mini st-chip ${d.strength.done ? "done" : ""}" data-go="#/strength/${d.date}">${ico(d.strength.done ? "check" : "gym")}${esc(d.strength.title)} ${d.strength.minutes} 分</button>` : ""}
              ${isRest && d.editable ? `<button class="mini" data-adjust="${d.date}">想跑？排一堂</button>` : ""}</span>
            ${p && p.minutes && d.status !== "done" ? `<span class="ws">${p.time ? `${p.time}，` : ""}${p.distance_km ? `${fmt(p.distance_km, 1)} km、` : ""}約 ${p.minutes} 分${p.hr_cap ? `，心率 ≤ ${p.hr_cap}` : ""}</span>` : ""}
            ${d.actual.map((x) => `<button class="text-btn" data-go="#/workout/${encodeURIComponent(x.id)}" style="padding:2px 0;min-height:0;display:block;text-align:left">
              實際：${fmt(x.km, 1)} km，${x.minutes} 分${x.avg_hr ? `，${x.avg_hr} bpm` : ""}。看訓練報告</button>`).join("")}
            ${d.editable && d.status !== "done" && !isRest ? `<span class="wk-actions"><button class="btn small tonal" data-adjust="${d.date}">調整</button></span>` : ""}
          </span></li>`;
      }).join("")}
    </ul>`;
}

function monthHtml(m) {
  const [y, mo] = m.month.split("-").map(Number);
  const lead = (new Date(y, mo - 1, 1).getDay() + 6) % 7;
  const anyProj = m.days.some((d) => d.plan?.projected);
  return `
    <div class="section-head" style="margin:4px 0">
      <button class="icon-btn" id="pm-prev" aria-label="上個月">${ico("back")}</button>
      <h3 style="font-size:1.125rem">${y} 年 ${mo} 月</h3>
      <button class="icon-btn" id="pm-next" aria-label="下個月" style="transform:scaleX(-1)">${ico("back")}</button></div>
    <p class="muted" style="text-align:center;margin-bottom:8px">已跑 ${m.runs} 次、${fmt(m.total_km, 1)} 公里${m.others ? `，其他運動 ${m.others} 次` : ""}；還有 ${m.planned_runs} 堂、約 ${fmt(m.planned_km, 0)} 公里</p>
    <div class="cal plan-cal" role="grid">
      ${WD.map((d) => `<div class="cal-wd" role="columnheader">${d}</div>`).join("")}${"<div></div>".repeat(lead)}
      ${m.days.map((d) => {
        const p = d.plan, k = d.race ? (d.race.kind === "event" ? "event" : "race") : p?.kind;
        const label = d.actual_km ? `${d.actual_km}` : k ? SHORT[k] || "" : "";
        const cls = d.actual_km ? "ran" : k && k !== "rest" ? `k-${d.race ? (k === "event" ? "event" : "race") : p.intensity}` : "";
        return `<button class="cal-d ${cls} ${d.is_today ? "today" : ""} ${p?.projected ? "proj" : ""}" data-day="${d.date}" role="gridcell"
          aria-label="${d.date}${d.actual_km ? `，跑了 ${d.actual_km} 公里` : ""}${otherAria(d)}${p ? `，${esc(p.title)}` : ""}">
          <span class="n">${+d.date.slice(8)}${p?.override ? "·" : ""}</span>${otherIcons(d)}<b>${label}</b>
          ${!d.actual_km && p?.distance_km && !d.race ? `<small class="num">${fmt(p.distance_km, 0)}k</small>` : d.actual_km ? `<small>km</small>` : ""}</button>`;
      }).join("")}
    </div>
    <div class="legend"><span><i style="background:var(--c-run)"></i>已跑</span><span><i style="background:#DCEFD8"></i>輕鬆</span>
      <span><i style="background:#FCE9C2"></i>中強度</span><span><i style="background:#FBDAD5"></i>強度課</span><span><i style="background:var(--ink)"></i>比賽</span>
      <span class="lg-ox">${sportIco("bike")}其他運動</span></div>
    <p class="muted" style="margin-top:8px">點任一天可以看內容或自己改課。${anyProj ? "虛線框是預估：真的到那一週，會再依當時的身體狀態微調；你手動改過的會保留。" : ""}</p>
    <div class="btn-row"><button class="btn small tonal" data-go="#/me/settings">${m.mode === "manual" ? "修改我的每週課表" : m.mode === "imported" ? "匯入的課表" : "排課方式與比賽"}</button>
      <button class="btn small outline" data-go="#/season">整季規劃</button></div>`;
}

async function viewAnalysis() {
  const view = S.planView || "week";
  S.pym ||= localISO().slice(0, 7);
  const [w, a, prof, m] = await Promise.all([api("/api/week"), api("/api/analysis"), api("/api/profile"),
    view === "month" ? api(`/api/month?ym=${S.pym}`) : null]);
  const r = w.readiness, ck = w.checkin;
  $app.innerHTML = `
    ${appbar("課表與分析")}
    ${seasonHead(w)}
    <section class="card" aria-labelledby="wh" style="margin-top:14px">
      <div class="section-head" style="margin:0 0 8px"><h2 id="wh">課表</h2>
        <div class="seg" role="group" aria-label="課表範圍" style="min-width:150px">
          <button data-pv="week" aria-pressed="${view === "week"}">本週</button><button data-pv="month" aria-pressed="${view === "month"}">本月</button></div></div>
      ${view === "week" ? weekHtml(w) : monthHtml(m)}
    </section>

    <section class="section" aria-labelledby="th">
      <h2 id="th" style="margin-bottom:10px">本週建議</h2>
      ${w.tips.map(tipHtml).join("")}
      ${w.notes.length ? `<p class="muted" style="margin-top:10px">${w.notes.map(esc).join("；")}</p>` : ""}
    </section>

    <section class="card" style="margin-top:16px" aria-labelledby="fh">
      <h2 id="fh">今天身體感覺如何？</h2>
      <p class="muted">恢復分數 ${r.score}。感覺只用來提醒你，不會自動改課；要改請在上面的課表按「調整」。</p>
      <div class="feel">${[["1", "很糟"], ["2", "累"], ["3", "普通"], ["4", "不錯"], ["5", "超好"]].map(([v, t]) =>
        `<button data-feel="${v}" aria-pressed="${ck?.feeling == v}"><b>${v}</b>${t}</button>`).join("")}</div>
      <h3 style="margin-top:14px">有沒有哪裡痛？</h3>
      <p class="muted">同一個地方連續痛 3 天，課表會自動暫停（停損規則）。</p>
      <div class="pain">${[["none", "都不痛"], ...Object.entries(S.meta.pain_sites)].map(([k, t]) =>
        `<button data-pain="${k}" aria-pressed="${(ck?.pain || (ck ? "none" : "")) === k}">${t}</button>`).join("")}</div>
      <button class="btn outline block" id="skip">今天沒辦法跑（跳過，不補）</button>
    </section>

    <section class="card" style="margin-top:16px" aria-labelledby="ph">
      <h2 id="ph">我的能力概況</h2><p class="muted">依近 4 週的資料，分數越低越需要補足。</p>
      <ul class="list" style="margin-top:8px">${prof.dims.map((d) => `<li style="padding:6px 0">
        <div style="display:flex;justify-content:space-between;font-size:.9375rem"><b style="font-weight:500">${d.label}</b><span class="muted">${esc(d.value)}</span></div>
        <div class="progress" style="margin:4px 0"><span style="width:${d.score}%;background:${d.score >= 80 ? "var(--ok)" : d.score >= 55 ? "#C79100" : "var(--bad)"}"></span></div></li>`).join("")}</ul>
      ${prof.focus.length ? prof.focus.map((f) => `<div class="note-card" style="margin-top:8px">${ico("flag")}<span><b>先補「${f.label}」</b><br>${esc(f.tip)}</span></div>`).join("")
        : `<p class="muted" style="margin-top:6px">五個面向都很均衡，照目前的方式繼續。</p>`}
    </section>

    <section class="section" aria-labelledby="lh">
      <h2 id="lh" style="margin-bottom:10px">長期趨勢與建議</h2>
      <div class="tiles two">
        <div class="tile"><p class="k">近一週加量幅度</p><p class="v num">${a.acwr ?? "–"}</p><p class="k">${a.acwr == null ? "紀錄滿 4 週後才會顯示" : "和前 4 週比，0.8–1.3 最安全"}</p></div>
        <div class="tile"><p class="k">輕鬆強度佔比（4 週）</p><p class="v num">${Math.round((1 - a.hard_share_4w) * 100)}%</p><p class="k">建議約 80%</p></div>
        <div class="tile"><p class="k">安靜心率（7 天）</p><p class="v num">${a.recovery.rhr_7 ?? "–"}<small>bpm</small></p><p class="k">平常 ${a.recovery.rhr_base ?? "–"}</p></div>
        <div class="tile"><p class="k">HRV（7 天）</p><p class="v num">${a.recovery.hrv_7 ?? "–"}<small>ms</small></p><p class="k">平常 ${a.recovery.hrv_base ?? "–"}</p></div>
      </div>
      <div style="margin-top:12px">${a.insights.map(tipHtml).join("")}</div>
      ${a.ef_trend.length >= 3 ? `<div class="card" style="margin-top:12px"><h3>有氧效率</h3><p class="muted">輕鬆跑時，同樣心率跑多快。往上 = 有氧能力變好${a.ef_change != null ? `（近期 ${a.ef_change > 0 ? "+" : ""}${Math.round(a.ef_change * 100)}%）` : ""}。</p>
        ${lineChart(a.ef_trend.map((p) => ({ label: p.date.slice(5).replace("-", "/"), value: Math.round(p.ef * 1000) / 10 })), { color: "var(--c-hrv)", trend: true, h: 160 })}</div>` : ""}
    </section>`;
  bindCommon();
  const refresh = () => viewAnalysis();
  document.querySelectorAll("[data-pv]").forEach((b) => (b.onclick = () => { S.planView = b.dataset.pv; refresh(); }));
  document.querySelectorAll("[data-detail]").forEach((b) => (b.onclick = () => {
    const day = w.days.find((d) => d.date === b.dataset.detail), p = day.plan;
    sheet(`<h2>${day.weekday} ${+day.date.slice(5, 7)}/${+day.date.slice(8)}：${esc(p.title)}</h2>
      <p class="muted" style="margin-top:4px">${p.time ? `${p.time} 開始，` : ""}${fmt(p.distance_km, 1)} 公里、約 ${p.minutes} 分鐘${p.hr_cap ? `，心率上限 ${p.hr_cap} bpm` : ""}</p>
      <p style="margin-top:8px">${esc(p.summary || "")}</p>
      ${p.why ? `<p class="why-line">為什麼排這堂：${esc(p.why)}</p>` : ""}
      <ul class="steps-list">${stepsHtml(p.steps)}</ul>
      <p style="margin-top:8px">${learnLink(p.kind === "long_m" ? "long" : p.kind === "test" ? "race_pace" : p.kind, `${p.title}是什麼？怎麼跑？`)}</p>
      <p class="muted" style="margin-top:8px">右邊是配速（每公里）和心率上限。心率上限是硬規則：到上限前 2 下就放慢。開啟「把課表推到 Garmin 手錶」後，輕鬆跑會用心率當目標、強度課用配速。</p>
      <div class="btn-row"><button class="btn tonal" id="d-adj">調整這堂課</button><button class="btn outline" id="d-x">關閉</button></div>`, (box, close) => {
      box.querySelector("#d-x").onclick = close;
      box.querySelector("#d-adj").onclick = () => { close(); adjustSheet(day, w.days.filter((x) => x.editable && x.date !== day.date && x.status !== "done"), refresh); };
    });
  }));
  document.querySelectorAll("[data-adjust]").forEach((b) => (b.onclick = () => {
    const day = w.days.find((d) => d.date === b.dataset.adjust);
    adjustSheet(day, w.days.filter((d) => d.editable && d.date !== day.date && d.status !== "done"), refresh);
  }));
  if (m) {
    const [y, mo] = m.month.split("-").map(Number);
    const shift = (n) => { S.pym = localISO(new Date(y, mo - 1 + n, 1)).slice(0, 7); refresh(); };
    document.getElementById("pm-prev").onclick = () => shift(-1);
    document.getElementById("pm-next").onclick = () => shift(1);
    document.querySelectorAll("[data-day]").forEach((b) => (b.onclick = () => {
      const d = m.days.find((x) => x.date === b.dataset.day);
      if (d.activity_id) return go(`#/workout/${encodeURIComponent(d.activity_id)}`);
      if (d.race) return sheet(`<h2>${esc(d.race.name)}</h2><p class="muted" style="margin-top:4px">${d.date.replaceAll("-", "/")}</p>
        <p style="margin-top:10px">${esc(d.race.summary)}</p>
        <div class="btn-row"><button class="btn tonal" data-go="#/me/settings">修改行程</button></div>`, (box, close) => { bindCommon(box); box.querySelectorAll("[data-go]").forEach((x) => x.addEventListener("click", close)); });
      if (d.activity_id && d.plan == null) return go(`#/workout/${encodeURIComponent(d.activity_id)}`);
      if (!d.editable) return sheet(`<h2>${d.weekday} ${+d.date.slice(5, 7)}/${+d.date.slice(8)}</h2><p class="muted" style="margin-top:6px">${d.past ? (d.plan ? `當天排的是「${esc(d.plan.title)}」，沒有跑步紀錄。` : "這天沒有排課，也沒有跑步紀錄。") : "太遠的日子還不能調整，最多可以排到 60 天後。"}</p>`);
      adjustSheet(d, d.swappable ? m.days.filter((x) => x.swappable && x.date !== d.date && !x.actual_km) : [], refresh);
    }));
  }
  document.querySelectorAll("[data-quick]").forEach((b) => (b.onclick = () => busy(b, async () => {
    await api("/api/override", { method: "PUT", body: { date: b.dataset.quick, kind: b.dataset.kind } });
    toast("課表已更新"); refresh();
  })));
  document.querySelectorAll("[data-feel]").forEach((b) => (b.onclick = async () => {
    const res = await api("/api/checkin", { method: "POST", body: { feeling: +b.dataset.feel } });
    toast(res.plan.notes.find((n) => n.includes("恢復分數")) || "收到"); refresh();
  }));
  document.querySelectorAll("[data-pain]").forEach((b) => (b.onclick = async () => {
    const res = await api("/api/checkin", { method: "POST", body: { pain: b.dataset.pain } });
    toast(res.plan.stoploss?.some((x) => x.rule === "pain") ? "已暫停課表，先休息、建議就醫" : b.dataset.pain === "none" ? "收到" : "記下了，連續 3 天會暫停課表"); refresh();
  }));
  document.querySelectorAll("[data-resume]").forEach((b) => (b.onclick = () => busy(b, async () => {
    await api("/api/plan/resume", { method: "POST" }); toast("已恢復課表"); refresh();
  })));
  document.getElementById("skip").onclick = async () => {
    await api("/api/checkin", { method: "POST", body: { skip: true } });
    toast("今天改成休息；這堂不會補回來"); refresh();
  };
}

function adjustSheet(day, others, done) {
  const p = day.plan || {}, dateStr = day.date;
  sheet(`
    <h2>${day.weekday} ${+dateStr.slice(5, 7)}/${+dateStr.slice(8)}：${esc(p.title || "休息日")}</h2>
    ${p.summary ? `<p class="muted" style="margin-top:4px">${esc(p.summary)}</p>` : ""}
    ${p.projected ? `<p class="muted" style="margin-top:4px">這是預估的課。你在這裡改的內容會固定下來。</p>` : ""}
    <h3 style="margin-top:16px">改成</h3>
    <div class="opts">${KINDS.map(([k, t, sub]) => `<button class="opt" data-kind="${k}" aria-pressed="${p.kind === k}"><b>${t}</b><small>${sub}</small></button>`).join("")}</div>
    ${p.why ? `<p class="why-line">原本為什麼排這堂：${esc(p.why)}</p>` : ""}
    <div class="form"><label><span>距離（公里，留空 = 自動）</span><input type="number" id="mins" min="1" max="60" step="0.5" value="${p.override && p.distance_km ? p.distance_km : ""}"></label></div>
    <p class="muted" style="margin-top:6px">提醒：手動改的課不會被自動排課覆蓋，但也不會影響整季的跑量曲線。</p>
    ${others.length ? `<h3 style="margin-top:16px">跟另一天換</h3>
      <div class="chips" style="margin-top:8px">${others.map((o) => `<button class="btn small outline" data-swap="${o.date}">${o.weekday}（${esc(o.plan?.title || "休息")}）</button>`).join("")}</div>` : ""}
    <div class="btn-row" style="margin-top:20px">
      ${p.override ? `<button class="btn outline" id="reset">恢復自動排課</button>` : ""}
      <button class="btn tonal" id="cancel">取消</button></div>`, (box, close) => {
    const km = () => { const v = +box.querySelector("#mins").value; return v >= 1 ? v : null; };
    box.querySelectorAll("[data-kind]").forEach((b) => (b.onclick = async () => {
      await api("/api/override", { method: "PUT", body: { date: dateStr, kind: b.dataset.kind, km: km() } });
      close(); toast("課表已更新，行事曆也會跟著改"); done();
    }));
    box.querySelectorAll("[data-swap]").forEach((b) => (b.onclick = async () => {
      await api("/api/swap", { method: "POST", body: { a: dateStr, b: b.dataset.swap } });
      close(); toast("已交換兩天的課"); done();
    }));
    const reset = box.querySelector("#reset");
    if (reset) reset.onclick = async () => { await api(`/api/override?date=${dateStr}`, { method: "DELETE" }); close(); toast("已恢復自動排課"); done(); };
    box.querySelector("#cancel").onclick = close;
  });
}
