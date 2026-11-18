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
    ${appbar("跑步紀錄")}
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
      <p class="muted" style="text-align:center;margin-bottom:8px">已跑 ${m.runs} 次，共 ${fmt(m.total_km, 1)} 公里</p>
      <div class="cal" role="grid">
        ${WD.map((d) => `<div class="cal-wd" role="columnheader">${d}</div>`).join("")}${"<div></div>".repeat(lead)}
        ${m.days.map((d) => `<div class="cal-d ${d.actual_km ? "ran" : ""} ${d.date === today ? "today" : ""}" role="gridcell"
            aria-label="${d.date}${d.actual_km ? `，跑了 ${d.actual_km} 公里` : ""}${d.plan ? `，計畫 ${esc(d.plan.title)}` : ""}">
            <span class="n">${+d.date.slice(8)}${d.event ? " 🏁" : ""}</span>${d.actual_km ? `<b class="num">${d.actual_km}</b>` : ""}
            ${d.plan && d.plan.kind !== "rest" ? `<i class="${d.plan.intensity}" title="${esc(d.plan.title)}"></i>` : ""}</div>`).join("")}
      </div>
      <div class="legend"><span><i style="background:var(--c-run)"></i>已跑（公里）</span><span><i style="background:var(--ok)"></i>輕鬆課</span>
        <span><i style="background:#C79100"></i>中強度</span><span><i style="background:var(--bad)"></i>強度課</span></div>
      <button class="btn tonal block" data-go="#/season">${ico("flag")}整季規劃</button>
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
  const f = s.feasibility, g = s.goal, d = s.diag;
  const VERDICT = { ok: ["ok", "可行，有緩衝"], tight: ["tight", "可行但沒有緩衝"], infeasible: ["infeasible", "時間不夠"] };
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

    ${f ? `<section class="card" style="margin-top:14px" aria-labelledby="fh">
      <div class="section-head" style="margin:0"><h2 id="fh">可行性檢查</h2><span class="verdict ${VERDICT[f.verdict][0]}">${VERDICT[f.verdict][1]}</span></div>
      <ul class="why">${f.why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${f.verdict === "infeasible" ? `
        <p style="margin-top:10px">${s.hold ? "在你選擇之前，課表先維持現在的跑量、只排輕鬆跑。這是你的決定，三個選項各有代價：" : "你選了照原目標練，課表會疊到時間允許的最高跑量，受傷風險較高。"}</p>
        <div class="choice">
          ${f.achievable ? `<button data-choice="achievable"><b>把目標改成 ${f.achievable}</b><small>賽期不變，用剩下的時間做得到的成績。</small></button>` : ""}
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
    </section>`;
  bindCommon();
  document.querySelectorAll("[data-choice]").forEach((b) => (b.onclick = () => busy(b, async () => {
    const c = b.dataset.choice;
    if (c === "accept_risk" && !confirm("照原目標練：跑量只疊到時間允許的最高值，強度課仍用目標配速。受傷風險較高，確定嗎？")) return;
    await api("/api/plan/decision", { method: "POST", body: { choice: c } });
    toast({ achievable: "目標已調整，課表已重排", postpone: "賽期已延後，課表已重排", accept_risk: "已照原目標排課" }[c]); viewSeason();
  })));
  document.querySelectorAll("[data-accept]").forEach((b) => (b.onclick = () => busy(b, async () => {
    await api("/api/plan/checkpoint/accept", { method: "POST", body: { date: b.dataset.accept } });
    toast("目標時間已調整"); viewSeason();
  })));
}
