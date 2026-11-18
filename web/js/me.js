"use strict";
// 我的：夥伴角色（養成）、跑友排行、目標與課表、健康參考範圍、行事曆、手錶與資料來源

function xpLine(g) {
  const pct = Math.min(100, ((g.xp - g.level_floor) / Math.max(1, g.level_ceil - g.level_floor)) * 100);
  return `<div class="xp" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}" aria-label="升級進度"><span style="width:${pct}%"></span></div>
    <p class="muted num">${g.xp - g.level_floor} / ${g.level_ceil - g.level_floor} XP，再約 ${Math.ceil((g.level_ceil - g.xp) / 10)} 公里升級</p>`;
}

async function viewMe() {
  const [g, lb] = await Promise.all([api("/api/game"), api("/api/leaderboard")]);
  const sort = S.lbSort || "week_km";
  lb.sort((a, b) => b[sort] - a[sort] || b.xp - a.xp);
  let hero;
  if (!g.character) {
    hero = `<section class="card warm" aria-labelledby="pick-h">
      <h2 id="pick-h">選一隻跑步夥伴</h2>
      <p class="muted" style="margin-top:4px">每跑一公里、每次破 PB，牠都會長大。共 ${S.chars.species.length} 種，各有 5 個成長階段。</p>
      <div style="display:flex;gap:6px;align-items:flex-end;height:64px;margin:12px 0;overflow:hidden">${S.chars.species.slice(0, 8).map((s) => charThumb(s.id, 0).replace("<img", '<img style="height:100%;image-rendering:pixelated"')).join("")}</div>
      <button class="btn block" data-go="#/me/pick">開始選角</button></section>`;
  } else {
    const sp = species(g.character.species);
    const nextLv = g.next_stage_level;
    hero = `<section class="hero" aria-labelledby="pet">
      <div class="stage"><button class="pet s${g.stage}" id="pet-btn" aria-label="跟${esc(g.character.nickname)}互動">
        <span class="pet-fx" aria-hidden="true"><i></i><i></i><i></i><i></i></span>${charImg(sp.id, g.stage, 200)}<span class="pet-shadow"></span></button></div>
      <div class="hero-info">
        <p class="sp">${esc(sp.name)}，第 ${g.stage + 1} 階段「${esc(g.character.stage_name)}」</p>
        <h2 id="pet">${esc(g.character.nickname)}</h2>
        <p class="lv">Lv <b class="num">${g.level}</b><span class="muted">${nextLv ? `Lv ${nextLv} 進化成「${esc(sp.stages[g.stage + 1].name)}」` : "已經是最終型態"}</span></p>
        ${xpLine(g)}
        <div class="evo" aria-label="成長階段">${sp.stages.map((st, i) => `<div class="${i > g.stage ? "lock" : ""} ${i === g.stage ? "cur" : ""}">
          <img src="${st.file}" alt="${i > g.stage ? "尚未解鎖" : esc(st.name)}">Lv ${g.rules.stage_levels[i]}</div>`).join("")}</div>
        <div class="btn-row"><button class="btn tonal" data-go="#/me/partner">${ico("trophy")}紀錄與經驗值</button></div>
      </div></section>`;
  }
  $app.innerHTML = `
    ${appbar("夥伴與設定")}
    ${hero}
    <section class="section" aria-labelledby="lbh">
      <div class="section-head"><h2 id="lbh">跑友排行</h2>
        <div class="seg" role="group" aria-label="排序" style="min-width:200px">
          ${[["week_km", "本週"], ["month_km", "本月"], ["xp", "等級"]].map(([k, t]) => `<button data-sort="${k}" aria-pressed="${sort === k}">${t}</button>`).join("")}</div></div>
      <div class="card board" style="padding:4px 10px">
        ${lb.map((p, i) => `<div class="row ${p.me ? "me" : ""}" style="cursor:default">
          <span class="rank num">${i + 1}</span>
          <span>${p.character ? charThumb(p.character.species, p.stage) : `<span class="ri">${ico("paw")}</span>`}</span>
          <span><b>${esc(p.name)}</b><small>${p.character ? `${esc(p.character.nickname)}，Lv ${p.level}` : "還沒選夥伴"}</small></span>
          <span class="rv"><b class="num">${sort === "xp" ? `Lv ${p.level}` : fmt(p[sort], 1)}</b>${sort === "xp" ? "" : " km"}</span></div>`).join("") || `<p class="muted" style="padding:12px">還沒有人上榜。</p>`}
      </div>
      ${lb.length < 2 ? `<p class="muted" style="margin-top:6px">把網址和邀請碼傳給朋友，他們註冊後就會出現在這裡。</p>` : ""}
    </section>

    <section class="section"><div class="card" style="padding:4px 12px">
      ${[["#/me/settings", "flag", "目標與課表偏好", "比賽、可跑日、強度、喜歡的課"],
         ["#/me/health", "shield", "健康參考範圍", "心率區間、配速、各項指標參考值"],
         ["#/me/briefs", "bell", "晨報、晚報、週報", "報告時間、手機通知、休息日肌力伸展"],
         ["#/me/calendar", "cal", "放進 iPhone 行事曆", "訂閱網址，課表變動自動更新"],
         ["#/me/device", "watch", "手錶與資料來源", S.me.provider === "demo" ? "目前使用範例資料" : S.me.provider === "none" ? "尚未連結，點這裡開始" : `Garmin：${S.me.garmin_email || ""}`]]
        .map(([to, ic, t, sub]) => `<button class="row" data-go="${to}"><span class="ri">${ico(ic)}</span><span><b>${t}</b><small>${esc(sub)}</small></span><span class="rv">${ico("chevron")}</span></button>`).join("")}
      <button class="row" id="big"><span class="ri">${ico("grid")}</span><span><b>大字模式</b><small>${document.documentElement.classList.contains("big") ? "目前：開啟" : "目前：關閉"}</small></span><span></span></button>
      <button class="row" id="logout"><span class="ri">${ico("logout")}</span><span><b>登出</b><small>${esc(S.me.username)}</small></span><span></span></button>
    </div></section>`;
  bindCommon();
  document.querySelectorAll("[data-sort]").forEach((b) => (b.onclick = () => { S.lbSort = b.dataset.sort; viewMe(); }));
  document.getElementById("big").onclick = () => {
    const on = document.documentElement.classList.toggle("big");
    localStorage.setItem("ic8_big", on ? "1" : "0"); viewMe();
  };
  document.getElementById("logout").onclick = async () => {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    S.token = null; localStorage.removeItem("ic8_token"); go("#/login");
  };
  const pet = document.getElementById("pet-btn");
  if (pet) pet.onclick = () => { pet.classList.remove("hop"); void pet.offsetWidth; pet.classList.add("hop"); };
  if (g.character) celebrate(g);
}

// 升級／進化動畫：只在等級真的變高時播一次
function celebrate(g) {
  const key = `ic8_lv_${S.me.username}_${g.character.species}`;
  let prev = null;
  try { prev = JSON.parse(localStorage.getItem(key)); } catch {}
  try { localStorage.setItem(key, JSON.stringify({ level: g.level, stage: g.stage })); } catch {}
  if (!prev || g.level <= prev.level) return;
  const evolved = g.stage > prev.stage;
  const ov = document.createElement("div");
  ov.className = "levelup"; ov.setAttribute("role", "dialog"); ov.setAttribute("aria-label", evolved ? "進化" : "升級");
  ov.innerHTML = `<div class="card">${charImg(g.character.species, g.stage, 180)}
    <h2>${evolved ? `進化成「${esc(g.character.stage_name)}」！` : "升級了！"}</h2>
    <p class="num" style="font-size:1.25rem;margin:4px 0 14px">Lv ${prev.level} → <b style="font-size:1.75rem;color:var(--c-run)">${g.level}</b></p>
    <button class="btn" type="button">太棒了</button></div>`;
  document.body.append(ov);
  const b = ov.querySelector("button"); b.focus(); b.onclick = () => ov.remove();
}

async function viewPartner() {
  const g = await api("/api/game");
  if (!g.character) return go("#/me/pick");
  const fmtRec = (r) => {
    if (r.rtype === "longest") return `${(r.value / 1000).toFixed(2)} km`;
    const s = Math.round(r.value), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  };
  $app.innerHTML = `
    ${appbar("紀錄與經驗值", { back: true })}
    <section class="card"><h2>個人最佳</h2>
      ${g.records.length ? `<ul class="list" style="margin-top:6px">${g.records.map((r) => `<li class="row" style="cursor:default;grid-template-columns:1fr auto;min-height:48px">
        <span><b>${esc(r.label)}</b><small>${r.date.replaceAll("-", "/")}</small></span><span class="rv"><b class="num" style="font-size:1.125rem">${fmtRec(r)}</b></span></li>`).join("")}</ul>
        ${g.record_source === "estimate" ? `<p class="muted" style="margin-top:6px">範例模式：用整趟平均配速估算。連結 Garmin 後會改用 Garmin 的正式 PB。</p>` : ""}`
        : `<p class="muted">還沒有紀錄。</p>`}</section>
    <section class="card" style="margin-top:16px"><h2>最近的經驗值</h2>
      <ul class="list" style="margin-top:6px">${g.events.slice(0, 15).map((e) => `<li class="row" style="cursor:default;grid-template-columns:1fr auto;min-height:44px">
        <span><b style="${e.kind === "pb" || e.kind === "first" ? "color:var(--bad)" : ""}">${esc(e.label)}</b><small>${e.date.replaceAll("-", "/")}</small></span>
        <span class="rv"><b class="num" style="color:var(--ok)">+${e.xp}</b></span></li>`).join("") || `<p class="muted">開始跑步就會累積經驗值。</p>`}</ul></section>
    <section class="card" style="margin-top:16px"><h2>經驗值怎麼算</h2>
      <ul style="margin:8px 0 0;padding-left:1.2em;color:var(--ink-2);line-height:1.8">
        <li>每跑 1 公里 +${g.rules.xp_per_km}</li>
        <li>在有排課的日子完成跑步 +${g.rules.planned}</li>
        <li>一週跑滿 3 次 +${g.rules.streak_week}</li>
        <li>破 PB：${Object.entries(g.rules.pb).map(([k, v]) => `${k} +${v}`).join("、")}</li>
        <li>第一次完成：${Object.entries(g.rules.first).map(([k, v]) => `${k} +${v}`).join("、")}</li>
        <li>Lv ${g.rules.stage_levels.slice(1).join("、")} 時進化</li>
        <li>選夥伴前的紀錄會變成「孵化禮」：${Math.round(g.rules.gift_rate * 100)}%，最多 ${g.rules.gift_cap} XP</li>
      </ul></section>
    <button class="btn outline block" data-go="#/me/pick">換一隻夥伴（等級保留）</button>`;
  bindCommon();
}

async function viewPick() {
  const g = await api("/api/game");
  const cur = g.character?.species || S.chars.species[0].id;
  $app.innerHTML = `
    ${appbar(g.character ? "換一隻夥伴" : "選一隻夥伴", { back: true })}
    <p class="muted">${g.character ? "換夥伴不會失去等級，牠會直接是你現在的階段。" : "從今天開始累積經驗值，之前的跑步紀錄會變成孵化禮。"}</p>
    <form id="pick" class="form">
      <div class="pick" style="margin-top:12px">${S.chars.species.map((s) => `
        <label><input type="radio" name="species" value="${s.id}" ${s.id === cur ? "checked" : ""}>
          <span class="pc"><span class="pc-main"><span class="pc-big"><img src="${s.stages[2].file}" alt=""></span>
              <span><b>${esc(s.name)}</b><small class="muted">${esc(s.blurb)}</small></span></span>
            <span class="strip">${s.stages.map((st, i) => `<span class="cell"><span class="box"><img src="${st.file}" alt="${esc(st.name)}"></span><small>${esc(st.name)}</small></span>`).join("")}</span></span></label>`).join("")}</div>
      <label><span>幫牠取名字（選填，最多 12 字）</span><input type="text" name="nickname" maxlength="12" value="${esc(g.character?.nickname && g.character.nickname !== g.character.stage_name ? g.character.nickname : "")}"></label>
      <button class="btn block" type="submit">${g.character ? "換成這隻" : "就決定是你了"}</button>
    </form>`;
  bindCommon();
  document.getElementById("pick").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    await busy(e.submitter, () => api("/api/character", { method: "PUT", body: { species: f.get("species"), nickname: f.get("nickname") } }));
    go("#/me");
  };
}

// ───────────────────── 目標與課表偏好 ─────────────────────
async function viewSettings() {
  const p = await api("/api/prefs");
  const m = S.meta, g = p.goal;
  const pace = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
  const chk = (n, v, on, t) => `<label><input type="checkbox" name="${n}" value="${v}" ${on ? "checked" : ""}><span>${t}</span></label>`;
  const rad = (n, v, on, t) => `<label><input type="radio" name="${n}" value="${v}" ${on ? "checked" : ""}><span>${t}</span></label>`;
  $app.innerHTML = `
    ${appbar("目標與課表偏好", { back: true })}
    <form class="form" id="pf">
      <section class="card"><h2>我的目標</h2>
        <label><span>用一句話描述</span><input type="text" name="goal_name" value="${esc(g.name)}" placeholder="明年三月半馬破 1:45"></label>
        <fieldset><legend>現在比較想要</legend><div class="chips">${Object.entries(m.goal_types).map(([k, v]) => rad("goal_type", k, g.type === k, v)).join("")}</div></fieldset>
        <div id="race" ${g.type === "race" ? "" : "hidden"}>
          <div class="two"><label><span>比賽日期</span><input type="date" name="event_date" value="${esc(g.event_date || "")}"></label>
            <label><span>距離（公里）</span><input type="number" step="0.1" min="1" name="event_distance_km" value="${esc(g.event_distance_km || "")}"></label></div>
          <label><span>目標時間（選填）</span><input type="text" name="target_time" value="${esc(g.target_time || "")}" placeholder="1:45:00"></label>
        </div></section>

      <section class="card" style="margin-top:12px"><h2>排課方式</h2>
        <div class="chips" style="margin-top:8px">${rad("plan_mode", "auto", p.plan_mode !== "manual", "自動排課")}${rad("plan_mode", "manual", p.plan_mode === "manual", "我自己安排")}</div>
        <p class="muted" id="mode-hint" style="margin-top:8px"></p>
        <div id="tpl" ${p.plan_mode === "manual" ? "" : "hidden"}>
          ${WD.map((w, i) => { const t = (p.template || {})[i] || {}; return `<div class="two" style="grid-template-columns:3rem 1fr 6rem;align-items:center;margin-top:8px">
            <b>週${w}</b>
            <select name="tk${i}" aria-label="週${w}的課">${[["rest", "休息"], ...KINDS.filter(([k]) => k !== "rest").map(([k, n]) => [k, n])].map(([k, n]) => `<option value="${k}" ${(t.kind || "rest") === k ? "selected" : ""}>${n}</option>`).join("")}</select>
            <input type="number" name="tm${i}" min="10" max="300" placeholder="分鐘" value="${t.minutes ?? ""}" aria-label="週${w}分鐘數"></div>`; }).join("")}
        </div></section>

      <section class="card" style="margin-top:12px"><h2>比賽</h2>
        <p class="muted" style="margin-top:4px">加進來的比賽，前 3 天不排強度和長跑、前一天只短跑一下，賽後自動安排休息與恢復跑，當週跑量降 20%。</p>
        <ul class="list" id="races" style="margin-top:6px"></ul>
        <div class="two" style="margin-top:4px"><label><span>比賽名稱</span><input type="text" id="rn" maxlength="30" placeholder="河濱 10K"></label>
          <label><span>日期</span><input type="date" id="rd"></label></div>
        <div class="two"><label><span>距離（公里）</span><input type="number" id="rk" step="0.1" min="1" placeholder="10"></label>
          <label><span>目標時間（選填）</span><input type="text" id="rt" placeholder="50:00"></label></div>
        <button class="btn tonal small" type="button" id="radd" style="margin-top:10px">加入這場比賽</button></section>

      <section class="card" style="margin-top:12px"><h2>什麼時候跑</h2>
        <fieldset><legend>可以跑的日子</legend><div class="chips">${WD.map((w, i) => chk("days", i, p.days.includes(i), `週${w}`)).join("")}</div></fieldset>
        <div class="two"><label><span>長跑放在</span><select name="long_run_day">${WD.map((w, i) => `<option value="${i}" ${p.long_run_day == i ? "selected" : ""}>週${w}</option>`).join("")}</select></label>
          <label><span>通常幾點跑</span><input type="time" name="run_time" value="${esc(p.run_time)}"></label></div>
        <div class="two"><label><span>平日最多（分鐘）</span><input type="number" min="20" max="180" name="max_minutes_weekday" value="${p.max_minutes_weekday}"></label>
          <label><span>長跑最多（分鐘）</span><input type="number" min="30" max="300" name="max_long_minutes" value="${p.max_long_minutes}"></label></div></section>

      <section class="card" style="margin-top:12px"><h2>怎麼練</h2>
        <fieldset><legend>強度偏好</legend><input type="range" name="intensity" min="0" max="100" step="5" value="${p.intensity}" aria-label="強度偏好">
          <div class="muted" style="display:flex;justify-content:space-between"><span>全部輕鬆跑</span><span>每週兩堂強度課</span></div></fieldset>
        <fieldset><legend>進步速度</legend><div class="chips">${rad("progression", "gentle", p.progression === "gentle", "保守")}${rad("progression", "normal", p.progression === "normal", "一般")}${rad("progression", "bold", p.progression === "bold", "積極")}</div></fieldset>
        <fieldset><legend>想做的課</legend><div class="chips">${Object.entries(m.workouts).map(([k, v]) => chk("workouts", k, p.workouts[k], v)).join("")}</div></fieldset>
        <label><span>固定每週跑量（分鐘，留空 = 自動）</span><input type="number" min="30" name="fixed_weekly_minutes" value="${p.fixed_weekly_minutes ?? ""}"></label>
        <label><span>減量週</span><select name="deload_every">${[[0, "不要"], [3, "每 3 週"], [4, "每 4 週"], [5, "每 5 週"]].map(([v, t]) => `<option value="${v}" ${p.deload_every == v ? "selected" : ""}>${t}</option>`).join("")}</select></label></section>

      <section class="card" style="margin-top:12px"><h2>每日目標</h2>
        <div class="two"><label><span>步數目標（留空 = 用 Garmin 的）</span><input type="number" min="1000" step="500" name="step_goal" value="${p.step_goal ?? ""}"></label>
          <label><span>活動熱量（kcal）</span><input type="number" min="100" step="50" name="kcal_goal" value="${p.kcal_goal ?? 500}"></label></div>
        <label><span>睡眠目標（小時）</span><input type="number" min="5" max="12" step="0.5" name="sleep_goal" value="${p.sleep_goal ?? 8}"></label></section>

      <section class="card" style="margin-top:12px"><h2>個人數據（留空自動估算）</h2>
        <div class="two"><label><span>最大心率</span><input type="number" name="max_hr" value="${p.max_hr ?? ""}" placeholder="自動"></label>
          <label><span>性別（心率負荷公式）</span><select name="sex"><option value="M" ${p.sex === "M" ? "selected" : ""}>男</option><option value="F" ${p.sex === "F" ? "selected" : ""}>女</option></select></label></div>
        <div class="two"><label><span>輕鬆配速（分:秒/km）</span><input type="text" name="easy_pace" value="${p.easy_pace_s ? pace(p.easy_pace_s) : ""}" placeholder="自動"></label>
          <label><span>閾值配速</span><input type="text" name="threshold_pace" value="${p.threshold_pace_s ? pace(p.threshold_pace_s) : ""}" placeholder="自動"></label></div></section>

      <section class="card" style="margin-top:12px"><h2>其他</h2>
        <label class="check"><input type="checkbox" name="push_to_garmin" ${p.push_to_garmin ? "checked" : ""} ${S.me.provider !== "garmin" ? "disabled" : ""}>把課表推到 Garmin 手錶</label>
        <label class="check"><input type="checkbox" name="show_on_board" ${p.show_on_board !== false ? "checked" : ""}>在跑友排行顯示我</label>
        <label><span>行事曆提醒（開始前幾分鐘，0 = 不提醒）</span><input type="number" min="0" max="240" name="calendar_alarm_min" value="${p.calendar_alarm_min}"></label></section>
      <p class="err" id="perr" hidden></p>
      <button class="btn block" type="submit">儲存並重排課表</button>
    </form>`;
  bindCommon();
  const f = document.getElementById("pf");
  let races = [...(p.races || [])];
  const drawRaces = () => {
    document.getElementById("races").innerHTML = races.map((r, i) => `<li class="row" style="cursor:default;grid-template-columns:1fr auto;min-height:48px">
      <span><b>🏁 ${esc(r.name)}</b><small>${r.date.replaceAll("-", "/")}${r.distance_km ? `，${r.distance_km} km` : ""}${r.target_time ? `，目標 ${esc(r.target_time)}` : ""}</small></span>
      <button class="btn small outline" type="button" data-rm="${i}">移除</button></li>`).join("") || `<li class="muted" style="padding:6px 0">還沒有加入比賽。</li>`;
    document.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { races.splice(+b.dataset.rm, 1); drawRaces(); }));
  };
  drawRaces();
  document.getElementById("radd").onclick = () => {
    const q = (id) => document.getElementById(id);
    if (!q("rd").value) return toast("請先選比賽日期");
    races.push({ name: q("rn").value || "比賽", date: q("rd").value, distance_km: +q("rk").value || null, target_time: q("rt").value });
    races.sort((a, b) => a.date.localeCompare(b.date));
    ["rn", "rd", "rk", "rt"].forEach((id) => (q(id).value = "")); drawRaces(); toast("已加入，記得按最下面的儲存");
  };
  const modeHint = () => {
    const man = f.plan_mode.value === "manual";
    document.getElementById("tpl").hidden = !man;
    document.getElementById("mode-hint").textContent = man
      ? "每週固定照你排的課。App 不會自動改課，只會在分析頁給建議；單獨某一天仍可在月曆上改。"
      : "依你的資料和下面的偏好自動排，每天依身體狀態微調。任何一天都可以在分析頁的月曆自己改。";
  };
  f.querySelectorAll("[name=plan_mode]").forEach((r) => (r.onchange = modeHint)); modeHint();
  f.querySelectorAll("[name=goal_type]").forEach((r) => (r.onchange = () => (document.getElementById("race").hidden = f.goal_type.value !== "race")));
  const pp = (s) => { const x = /^(\d{1,2}):(\d{2})$/.exec((s || "").trim()); return x ? +x[1] * 60 + +x[2] : null; };
  const num = (v) => (v === "" || v == null ? null : +v);
  f.onsubmit = async (e) => {
    e.preventDefault();
    const d = new FormData(f);
    const body = {
      goal: { name: d.get("goal_name"), type: d.get("goal_type"), event_date: d.get("event_date") || null, event_distance_km: num(d.get("event_distance_km")), target_time: d.get("target_time") || "" },
      days: d.getAll("days").map(Number), long_run_day: +d.get("long_run_day"), run_time: d.get("run_time"),
      max_minutes_weekday: +d.get("max_minutes_weekday"), max_long_minutes: +d.get("max_long_minutes"),
      intensity: +d.get("intensity"), progression: d.get("progression"),
      workouts: Object.fromEntries(Object.keys(m.workouts).map((k) => [k, d.getAll("workouts").includes(k)])),
      fixed_weekly_minutes: num(d.get("fixed_weekly_minutes")), deload_every: +d.get("deload_every"),
      step_goal: num(d.get("step_goal")), kcal_goal: num(d.get("kcal_goal")), sleep_goal: num(d.get("sleep_goal")),
      max_hr: num(d.get("max_hr")), sex: d.get("sex"), easy_pace_s: pp(d.get("easy_pace")), threshold_pace_s: pp(d.get("threshold_pace")),
      push_to_garmin: d.get("push_to_garmin") === "on", show_on_board: d.get("show_on_board") === "on",
      calendar_alarm_min: +d.get("calendar_alarm_min"),
      plan_mode: d.get("plan_mode"), races,
      template: Object.fromEntries(WD.map((_, i) => [i, { kind: d.get(`tk${i}`), minutes: num(d.get(`tm${i}`)) || 40 }]).filter(([, t]) => t.kind && t.kind !== "rest")),
    };
    const err = document.getElementById("perr");
    try { await busy(e.submitter, () => api("/api/prefs", { method: "PUT", body })); toast("已儲存，課表已重排"); history.back(); }
    catch (x) { err.textContent = x.message; err.hidden = false; }
  };
}

// ───────────────────── 健康參考範圍 ─────────────────────
async function viewHealth() {
  if (S.me.provider === "none") {
    $app.innerHTML = `${appbar("健康參考範圍", { back: true })}<div class="card"><h2>需要先有跑步資料</h2><p class="muted" style="margin-top:4px">心率區間和配速是從你的紀錄算出來的。連結 Garmin 或使用範例資料後就會出現。</p>
      <button class="btn block" data-go="#/summary">去連結</button></div>`;
    return bindCommon();
  }
  const h = await api("/api/health");
  const zc = ["#9E9E9E", "#1565C0", "#2E7D32", "#E8A200", "#C62828"];
  $app.innerHTML = `
    ${appbar("健康參考範圍", { back: true })}
    <section class="card"><h2>你的心率區間</h2>
      <p class="muted">最大心率 ${h.max_hr} bpm（${h.max_hr_source}），安靜心率 ${h.rest_hr} bpm</p>
      <ul class="list" style="margin-top:8px">${h.zones.map((z, i) => `<li class="row" style="cursor:default;grid-template-columns:8px 1fr auto;min-height:52px">
        <span style="width:8px;height:36px;border-radius:4px;background:${zc[i]}"></span>
        <span><b>${z.zone} ${z.name}</b><small>${z.pct} · ${z.use}</small></span><span class="rv"><b class="num">${z.lo}–${z.hi}</b> bpm</span></li>`).join("")}</ul></section>
    <section class="card" style="margin-top:12px"><h2>你的訓練配速</h2>
      <div class="tiles two" style="margin-top:8px">${h.paces.map((p) => `<div class="tile"><p class="k">${p.name}</p><p class="v num">${p.pace.replace(":", "'")}"<small>/km</small></p></div>`).join("")}</div></section>
    <section class="card warm" style="margin-top:12px"><h2>參考值</h2>
      <ul class="list" style="margin-top:6px">${h.refs.map((r) => `<li style="padding:8px 0;border-top:1px solid var(--line)"><b>${r.name}</b>
        <p style="color:var(--ink-2);font-size:.9375rem">${esc(r.range)}${r.you ? `；你：${esc(r.you)}` : ""}</p></li>`).join("")}</ul>
      <p class="muted" style="margin-top:10px">${esc(h.disclaimer)}</p></section>
    <button class="btn tonal block" data-go="#/me/settings">修改最大心率或配速</button>`;
  bindCommon();
}

// ───────────────────── 行事曆 ─────────────────────
function viewCalendar() {
  const me = S.me;
  $app.innerHTML = `
    ${appbar("放進 iPhone 行事曆", { back: true })}
    <section class="card"><p>按下面的按鈕，iPhone 會問你要不要訂閱。之後課表一改，行事曆會自動更新。</p>
      <a class="btn block" href="${esc(me.calendar_webcal)}">${ico("cal")}訂閱行事曆</a>
      <p class="muted" style="margin-top:14px">沒有反應的話，複製網址，到「設定 → App → 行事曆 → 行事曆帳號 → 加入帳號 → 其他 → 加入已訂閱的行事曆」貼上。</p>
      <div style="display:flex;gap:8px;margin-top:10px" class="form"><input type="text" readonly value="${esc(me.calendar_url)}" aria-label="行事曆網址" style="font-size:.8125rem">
        <button class="btn tonal" id="copy" type="button">複製</button></div></section>
    <button class="btn outline block" id="rotate">網址外流了？換一組新網址</button>`;
  bindCommon();
  document.getElementById("copy").onclick = async () => {
    try { await navigator.clipboard.writeText(me.calendar_url); toast("已複製"); } catch { toast("請長按網址手動複製"); }
  };
  document.getElementById("rotate").onclick = async () => {
    if (!confirm("換新網址後，舊的訂閱會失效，需要重新訂閱。確定要換？")) return;
    await api("/api/calendar/rotate", { method: "POST" }); toast("已換新網址"); render();
  };
}

// ───────────────────── 手錶與資料來源 ─────────────────────
async function viewDevice() {
  const me = S.me;
  const s = me.provider !== "none" ? await api("/api/summary").catch(() => null) : null;
  const dev = s?.device;
  $app.innerHTML = `
    ${appbar("手錶與資料來源", { back: true })}
    ${dev ? `<section class="card warm">
      <div class="device"><span class="watch">${ico("watch")}</span><span><b>${esc(dev.name)}</b><small>${esc(dev.brand)}${dev.count > 1 ? `（帳號共有 ${dev.count} 支裝置）` : ""}</small></span>
        <span class="chip"><span class="dot ${me.last_sync_error ? "off" : ""}"></span>${me.last_sync_error ? "同步失敗" : "已連線"}</span></div>
      <div class="tiles two" style="margin-top:12px">
        <div class="tile"><p class="k">韌體</p><p class="v" style="font-size:1rem">${esc(dev.firmware || "—")}</p></div>
        <div class="tile"><p class="k">手錶最後上傳</p><p class="v" style="font-size:1rem">${dev.last_upload ? new Date(dev.last_upload).toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}</p></div>
      </div>
      <p class="muted" style="margin-top:8px">${agoText(me.last_sync)}${me.last_sync_error ? `；錯誤：${esc(me.last_sync_error)}` : ""}</p>
      <div class="btn-row"><button class="btn" id="sync">${ico("sync")}立即同步</button><button class="btn outline" id="full">重新抓一年資料</button></div>
    </section>` : ""}
    <section class="card" style="margin-top:12px"><h2>資料來源</h2>
      <p class="muted" style="margin-top:4px">${me.provider === "garmin" ? `Garmin Connect：${esc(me.garmin_email)}` : me.provider === "demo" ? "目前使用範例資料" : me.provider === "apple" ? "Apple Watch（iPhone 捷徑上傳）" : "尚未連結"}</p>
      <p style="color:var(--ink-2);font-size:.9375rem;margin-top:8px">${me.provider === "apple" ? "資料由你 iPhone 上的捷徑送過來：每天早上一次身體資料，跑完一次運動紀錄。" : "手錶先同步到 Garmin Connect App，這裡再從 Garmin Connect 抓資料，每天自動 3 次。"}</p>
      ${me.provider === "apple" ? `<button class="btn tonal block" data-go="#/me/apple" style="margin-top:10px">${ico("watch")}Apple Watch 上傳設定與狀態</button>` : ""}
      ${me.provider === "demo" ? `<div class="note-card" style="margin-top:10px">${ico("bolt")}<span><b>現在看到的不是你的手錶。</b>這個帳號目前是範例資料，所以顯示「Forerunner 165（範例）」。按下面的「連結 Garmin」登入後，才會換成你自己的手錶和資料。</span></div>` : ""}
      <div class="btn-row">
        ${me.provider !== "garmin" ? `<button class="btn" id="link">${ico("link")}連結 Garmin</button>` : ""}
        ${me.provider !== "none" ? `<button class="btn outline" id="unlink">中斷連結並刪除資料</button>` : ""}</div></section>
    ${me.provider === "garmin" ? `
    <section class="card" style="margin-top:12px"><h2>連線檢查</h2>
      <p class="muted" style="margin-top:4px">逐項向 Garmin 要資料，看哪些抓得到、哪些抓不到。大約 10–20 秒。</p>
      <button class="btn tonal block" id="diag">開始檢查</button><div id="diag-out"></div></section>
    <section class="card" style="margin-top:12px"><h2>測試把課表送到手錶</h2>
      <p class="muted" style="margin-top:4px">建立一堂 10 分鐘的「[IC8] 連線測試」，排在明天，並直接送到你最後同步的手錶。測完可以一鍵刪除。</p>
      <button class="btn tonal block" id="ptest">送一堂測試課</button><div id="ptest-out"></div></section>` : ""}`;
  bindCommon();
  const q = (id) => document.getElementById(id);
  if (q("sync")) q("sync").onclick = (e) => syncNow(e.currentTarget);
  if (q("full")) q("full").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/sync?full=true", { method: "POST" }); toast("已重新抓取"); render(); });
  if (q("diag")) q("diag").onclick = (e) => busy(e.currentTarget, async () => {
    try {
      const r = await api("/api/garmin/diagnose");
      q("diag-out").innerHTML = `<ul class="list" style="margin-top:8px">${r.checks.map((c) => `<li class="row" style="cursor:default;grid-template-columns:1fr auto;min-height:48px">
        <span><b>${esc(c.label)}</b><small>${esc(c.detail)}${!c.ok && c.empty && c.needs ? `（需要：${esc(c.needs)}）` : ""}</small></span>
        <span class="badge ${c.ok ? "done" : c.empty ? "today" : "missed"}">${c.ok ? "有資料" : c.empty ? "沒有資料" : "抓取失敗"}</span></li>`).join("")}</ul>
        <p class="muted" style="margin-top:6px">「沒有資料」通常是手錶沒量到（例如沒戴著睡）；「抓取失敗」是程式或 Garmin 的問題，請把這頁截圖給開發者。</p>`;
    } catch (x) { q("diag-out").innerHTML = `<p class="err">${esc(x.message)}</p>`; }
  });
  const drawTest = () => {
    const id = localStorage.getItem("ic8_test_workout");
    if (id && q("ptest-out") && !q("ptest-out").innerHTML) q("ptest-out").innerHTML = `<button class="btn outline block" id="pdel">刪除測試課</button>`;
    if (q("pdel")) q("pdel").onclick = (e) => busy(e.currentTarget, async () => {
      try { await api(`/api/garmin/push-test/${id}`, { method: "DELETE" }); localStorage.removeItem("ic8_test_workout"); q("ptest-out").innerHTML = `<p class="muted" style="margin-top:8px">已從 Garmin Connect 刪除測試課。</p>`; }
      catch (x) { toast(x.message); }
    });
  };
  if (q("ptest")) { drawTest(); q("ptest").onclick = (e) => busy(e.currentTarget, async () => {
    try {
      const r = await api("/api/garmin/push-test", { method: "POST" });
      localStorage.setItem("ic8_test_workout", r.workout_id);
      const li = (ok, t) => `<li>${ok ? "✅" : "❌"} ${t}</li>`;
      q("ptest-out").innerHTML = `<ul style="margin:10px 0 0;padding-left:1.2em;line-height:1.8;list-style:none">
        ${li(r.uploaded, "上傳到 Garmin Connect 課表庫")}${li(r.scheduled, `排到 Garmin 行事曆（${r.date || "明天"}）`)}${li(r.sent, "直接送到手錶")}</ul>
        ${r.errors.map((x) => `<p class="err">${esc(x)}</p>`).join("")}
        <p class="muted" style="margin-top:6px">接下來：打開手機的 Garmin Connect 讓手錶同步，然後在手錶按「跑步 → 訓練 → 訓練課程」找「[IC8] 連線測試」。</p>
        <button class="btn outline block" id="pdel">刪除測試課</button>`;
      drawTest();
    } catch (x) { q("ptest-out").innerHTML = `<p class="err">${esc(x.message)}</p>`; }
  }); }
  if (q("link")) q("link").onclick = async () => { await api("/api/garmin/unlink", { method: "POST" }); S.me.provider = "none"; go("#/summary"); };
  if (q("unlink")) q("unlink").onclick = async () => {
    if (!confirm("會刪除這個帳號存的跑步資料、課表和紀錄（夥伴保留），確定嗎？")) return;
    await api("/api/garmin/unlink", { method: "POST" }); go("#/summary");
  };
}
