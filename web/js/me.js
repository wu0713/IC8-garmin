"use strict";
// 我的：夥伴角色（養成）、跑友排行、目標與課表、健康參考範圍、行事曆、手錶與資料來源

function xpLine(g) {
  const pct = Math.min(100, ((g.xp - g.level_floor) / Math.max(1, g.level_ceil - g.level_floor)) * 100);
  return `<div class="xp" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}" aria-label="升級進度"><span style="width:${pct}%"></span></div>
    <p class="muted num">${g.xp - g.level_floor} / ${g.level_ceil - g.level_floor} XP，再約 ${Math.ceil((g.level_ceil - g.xp) / 10)} 公里升級</p>`;
}

// 夥伴頁的設定選單：分組、一行一項，副標只留重點；右邊可以放狀態（例如「已連結」）
function menuGroup(title, items) {
  const id = `mg-${title.length}-${items.length}`;
  return `<section class="menu-group" aria-labelledby="${id}"><h2 id="${id}">${esc(title)}</h2><div class="card menu">
    ${items.map(([to, ic, t, sub, status]) => `<button class="mrow" data-go="${to}"><span class="mi">${ico(ic)}</span>
      <span class="mt"><b>${esc(t)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</span>
      <span class="mv">${status ? `<span class="mstat">${esc(status)}</span>` : ""}${ico("chevron")}</span></button>`).join("")}
  </div></section>`;
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
      <div class="stage ${sceneCls(sp.id)}" data-scene="${sp.id}">${petScene(sp.id)}<button class="pet s${g.stage} ${petCls(sp.id, g.stage)}" id="pet-btn" data-sp="${sp.id}" style="${petSpark(sp.id) ? `--spark:${petSpark(sp.id)}` : ""}" aria-label="跟${esc(g.character.nickname)}互動">
        ${petFx(sp.id, g.stage)}<span class="pet-fx" aria-hidden="true"><i></i><i></i><i></i><i></i></span>${petSprite(sp.id, g.stage, 200)}<span class="pet-shadow"></span></button>${petFront(sp.id)}</div>
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

    ${menuGroup("訓練", [
      ["#/me/settings", "flag", "目標與課表偏好", "比賽、可跑日、想做的課"],
      ["#/me/health", "shield", "配速與心率上限", "區間、個人最佳、成績預測"],
      ["#/learn", "book", "跑步小教室", "課表、心率、名詞說明"]])}
    ${menuGroup("資料與通知", [
      ["#/me/device", "watch", "手錶與資料來源", "", S.me.provider === "demo" ? "範例資料" : S.me.provider === "none" ? "未連結" : "已連結"],
      ["#/me/import", "upload", "匯入舊紀錄", "Strava、.fit／.gpx／.tcx"],
      ["#/me/calendar", "cal", "放進 iPhone 行事曆", "課表變動自動更新"],
      ["#/me/briefs", "bell", "晨報、晚報、週報", "時間、通知、肌力伸展"]])}
    <section class="menu-group" aria-labelledby="mg-acc"><h2 id="mg-acc">帳號與顯示</h2><div class="card menu">
      <label class="mrow"><span class="mi">${ico("grid")}</span><span class="mt"><b>大字模式</b></span>
        <input type="checkbox" class="switch" id="big" ${document.documentElement.classList.contains("big") ? "checked" : ""} aria-label="大字模式"></label>
      <button class="mrow danger" id="logout"><span class="mi">${ico("logout")}</span><span class="mt"><b>登出</b><small>${esc(S.me.username)}</small></span><span></span></button>
    </div></section>`;
  bindCommon();
  document.querySelectorAll("[data-sort]").forEach((b) => (b.onclick = () => { S.lbSort = b.dataset.sort; viewMe(); }));
  document.getElementById("big").onchange = (e) => {
    const on = e.target.checked;
    document.documentElement.classList.toggle("big", on);
    localStorage.setItem("ic8_big", on ? "1" : "0");
  };
  document.getElementById("logout").onclick = async () => {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    S.token = null; localStorage.removeItem("ic8_token"); go("#/login");
  };
  const pet = document.getElementById("pet-btn");
  // 點夥伴：跳一下；有專屬特效的種族，環繞的小東西會散開再飛回來，加一圈光波
  if (pet) pet.onclick = () => {
    pet.classList.remove("hop", "react"); void pet.offsetWidth; pet.classList.add("hop");
    if (pet.querySelector(".pet-orbit")) { pet.classList.add("react"); clearTimeout(pet._r); pet._r = setTimeout(() => pet.classList.remove("react"), 1300); }
  };
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
  const burst = evolved && PET_FX[g.character.species]
    ? `<span class="evo-burst" style="--aura:${PET_FX[g.character.species].aura}" aria-hidden="true">${Array.from({ length: 10 }, (_, i) =>
      `<i style="--k:${i}">${pixelOrb(PET_FX[g.character.species].orb, PET_FX[g.character.species].colors)}</i>`).join("")}</span>` : "";
  ov.innerHTML = `<div class="card"><div class="evo-pic">${burst}${charImg(g.character.species, g.stage, 180)}</div>
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
            <span class="strip">${s.stages.map((st, i) => `<span class="cell" data-prev="${s.id}:${i}" role="button" tabindex="0" aria-label="預覽${esc(st.name)}"><span class="box"><img src="${st.file}" alt="${esc(st.name)}"></span><small>${esc(st.name)}</small></span>`).join("")}</span></span></label>`).join("")}</div>
      <p class="muted" style="margin-top:8px">點任一個階段的小圖，可以預覽牠進化後的樣子和動作。</p>
      <label><span>幫牠取名字（選填，最多 12 字）</span><input type="text" name="nickname" maxlength="12" value="${esc(g.character?.nickname && g.character.nickname !== g.character.stage_name ? g.character.nickname : "")}"></label>
      <button class="btn block" type="submit">${g.character ? "換成這隻" : "就決定是你了"}</button>
    </form>`;
  bindCommon();
  document.querySelectorAll("[data-prev]").forEach((c) => {
    const open = (e) => { e.preventDefault(); e.stopPropagation(); const [id, i] = c.dataset.prev.split(":"); previewPet(id, +i); };
    c.addEventListener("click", open);
    c.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") open(e); });
  });
  document.getElementById("pick").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    await busy(e.submitter, () => api("/api/character", { method: "PUT", body: { species: f.get("species"), nickname: f.get("nickname") } }));
    go("#/me");
  };
}

// 預覽某一隻夥伴的某個階段：完整的場景、特效、浮空和點擊反應（不會改到你現在的夥伴）
function previewPet(id, stage) {
  const sp = species(id);
  const lv = S.chars.stage_levels || [];
  const draw = (box, i) => {
    box.querySelector(".prev-body").innerHTML = `
      <p class="muted">第 ${i + 1} 階段「${esc(sp.stages[i].name)}」${lv[i] ? `，Lv ${lv[i]} 進化` : ""}</p>
      <div class="stage ${sceneCls(id)}" data-scene="${id}" style="margin-top:8px">${petScene(id)}<button class="pet s${i} ${petCls(id, i)}" type="button" id="prev-pet" style="${petSpark(id) ? `--spark:${petSpark(id)}` : ""}" aria-label="跟牠互動">
        ${petFx(id, i)}<span class="pet-fx" aria-hidden="true"><i></i><i></i><i></i><i></i></span>${petSprite(id, i, 200)}<span class="pet-shadow"></span></button>${petFront(id)}</div>
      <div class="seg prev-stages" role="group" aria-label="階段">${sp.stages.map((_, k) => `<button type="button" data-st="${k}" aria-pressed="${k === i}">${k + 1}</button>`).join("")}</div>
      <p class="muted" style="margin-top:6px;text-align:center">點牠看看反應</p>`;
    const pet = box.querySelector("#prev-pet");
    pet.onclick = () => {
      pet.classList.remove("hop", "react"); void pet.offsetWidth; pet.classList.add("hop");
      if (pet.querySelector(".pet-orbit")) { pet.classList.add("react"); clearTimeout(pet._r); pet._r = setTimeout(() => pet.classList.remove("react"), 1300); }
    };
    box.querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => draw(box, +b.dataset.st)));
  };
  sheet(`<h2>${esc(sp.name)}</h2><div class="prev-body"></div>
    <div class="btn-row"><button class="btn outline" type="button" id="prev-x">關閉</button></div>`, (box, close) => {
    draw(box, stage);
    box.querySelector("#prev-x").onclick = close;
  });
}

// ───────────────────── 目標與課表偏好 ─────────────────────
async function viewSettings() {
  const p = await api("/api/prefs");
  const m = S.meta, g = p.goal, sr = p.self_report || {};
  const chk = (n, v, on, t) => `<label><input type="checkbox" name="${n}" value="${v}" ${on ? "checked" : ""}><span>${t}</span></label>`;
  const rad = (n, v, on, t) => `<label><input type="radio" name="${n}" value="${v}" ${on ? "checked" : ""}><span>${t}</span></label>`;
  const ET = m.event_types;
  const multiDay = ["travel", "illness", "endurance", "trail"];
  $app.innerHTML = `
    ${appbar("目標與課表偏好", { back: true })}
    <form class="form settings" id="pf">
      <section class="card"><h2>我的目標</h2><p class="card-sub">課表會依這裡的方向安排。</p>
        <label><span>用一句話描述</span><input type="text" name="goal_name" value="${esc(g.name)}" placeholder="明年三月半馬破 1:45"></label>
        <fieldset><legend>現在比較想要</legend><div class="chips g2">${Object.entries(m.goal_types).map(([k, v]) => rad("goal_type", k, g.type === k, v)).join("")}</div></fieldset>
        <div id="race" ${g.type === "race" ? "" : "hidden"}>
          <div class="two"><label><span>比賽日期</span><input type="date" name="event_date" value="${esc(g.event_date || "")}"></label>
            <label><span>距離（公里）</span><input type="number" step="0.0001" min="1" name="event_distance_km" value="${esc(g.event_distance_km || "")}"></label></div>
          <div class="chips" style="margin-top:6px">${[[5, "5K"], [10, "10K"], [21.0975, "半馬"], [42.195, "全馬"]].map(([k, t]) => `<button type="button" class="btn small outline" data-dist="${k}">${t}</button>`).join("")}</div>
          <label><span>目標時間（建議填）</span><input type="text" name="target_time" value="${esc(g.target_time || "")}" placeholder="1:45:00"></label>
          <p class="card-sub" style="margin-top:8px">可行性檢查和配速都靠目標時間。儲存後會先做可行性檢查：時間不夠時不會硬排課，會請你選擇改目標、延後賽期，或接受較高風險。</p>
        </div></section>

      <section class="card" id="intro"><h2>我最近的跑步狀況</h2>
        <p class="card-sub">剛買手錶、App 裡的紀錄不到 4 週時，課表會用這裡填的當起點；紀錄滿 4 週後就改用實際資料。舊紀錄也可以到「我的 → 匯入舊紀錄」上傳。</p>
        <div class="two" style="margin-top:8px">
          <label><span>近一個月每週平均（km）</span><input type="number" min="0" max="250" step="1" name="sr_weekly_km" value="${esc(sr.weekly_km ?? "")}" placeholder="20"></label>
          <label><span>近一個月最長一次（km）</span><input type="number" min="0" max="100" step="0.5" name="sr_longest_km" value="${esc(sr.longest_km ?? "")}" placeholder="10"></label></div>
        <fieldset><legend>每週有幾堂比較快的課（節奏、間歇、比賽）</legend>
          <div class="chips g3">${[[0, "沒有"], [1, "1 堂"], [2, "2 堂以上"]].map(([k, t]) => rad("sr_hard", k, (sr.hard_per_week ?? -1) === k, t)).join("")}</div></fieldset>
        <fieldset><legend>跑步多久了</legend>
          <div class="chips g2">${[["new", "剛開始"], ["lt1", "不到 1 年"], ["1to3", "1–3 年"], ["gt3", "3 年以上"]].map(([k, t]) => rad("sr_years", k, sr.years === k, t)).join("")}</div></fieldset>
        <div class="two" style="margin-top:8px">
          <label><span>近半年比賽或測驗（選填）</span><select name="sr_race_km"><option value="">沒有</option>
            ${[[5, "5K"], [10, "10K"], [21.0975, "半馬"], [42.195, "全馬"]].map(([k, t]) => `<option value="${k}" ${+sr.race_km === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
          <label><span>成績</span><input type="text" name="sr_race_time" value="${esc(sr.race_time || "")}" placeholder="25:30 或 1:52:00"></label></div>
      </section>

      <section class="card"><h2>我的程度</h2>
        <div class="chips g2" style="margin-top:12px">${Object.entries(m.levels || {}).map(([k, v]) => rad("level", k, (p.level || "auto") === k, v)).join("")}</div>
        <p class="card-sub" style="margin-top:8px">決定課表一開始要不要就有強度課。「自動判斷」看你近 8 週的跑量、成績和有沒有練過強度；新手會先打有氧底子，有經驗的人從第一週就節奏、坡道、間歇混合練。</p></section>

      <section class="card"><h2>排課方式</h2>
        <div class="chips ${p.imported ? "g3" : "g2"}" style="margin-top:12px">${rad("plan_mode", "auto", !["manual", "imported"].includes(p.plan_mode), "自動排課")}${rad("plan_mode", "manual", p.plan_mode === "manual", "每週固定課表")}${p.imported ? rad("plan_mode", "imported", p.plan_mode === "imported", "照匯入的課表") : ""}</div>
        <p class="card-sub" id="mode-hint" style="margin-top:8px"></p>
        <div id="tpl" ${p.plan_mode === "manual" ? "" : "hidden"}>
          ${WD.map((w, i) => { const t = (p.template || {})[i] || {}; return `<div class="two" style="grid-template-columns:3rem 1fr 6rem;align-items:center;margin-top:8px">
            <b>週${w}</b>
            <select name="tk${i}" aria-label="週${w}的課">${[["rest", "休息"], ...KINDS.filter(([k]) => k !== "rest").map(([k, n]) => [k, n])].map(([k, n]) => `<option value="${k}" ${(t.kind || "rest") === k ? "selected" : ""}>${n}</option>`).join("")}</select>
            <input type="number" name="tm${i}" min="1" max="60" step="0.5" placeholder="公里" value="${t.km ?? ""}" aria-label="週${w}公里數"></div>`; }).join("")}
        </div>
        <div style="margin-top:14px;border-top:2px dashed var(--line, #ccd);padding-top:12px">
          <h3>匯入自己的逐日課表</h3>
          <p class="card-sub">上傳 Word（.docx）課表：要有一張「配速與代號」表和一張每日行事曆表（週一到週日七欄，每格像「11/10 5 km E」）。匯入後每天照表排，App 不會自動改課；疼痛、晨間心率偏高等停損規則仍然有效。</p>
          ${p.imported ? `<p style="margin-top:8px">目前匯入：<b>${esc(p.imported.name)}</b><br><small class="muted">${p.imported.start} 到 ${p.imported.end}，共 ${p.imported.n_days} 天</small></p>
            ${(p.imported.unknown || []).length ? `<p class="muted" style="margin-top:4px">這幾格看不懂，照原文顯示：${p.imported.unknown.map(esc).join("、")}</p>` : ""}` : ""}
          <div class="btn-row" style="margin-top:8px"><label class="btn small tonal" style="cursor:pointer">${p.imported ? "換一份課表" : "選擇 .docx 檔"}<input type="file" id="impf" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden></label>
            ${p.imported ? `<button type="button" class="btn small outline" id="impdel">取消匯入</button>` : ""}</div>
          <p class="err" id="imperr" hidden></p>
        </div></section>

      <section class="card"><h2>固定行程與比賽</h2>
        <p class="card-sub">每種行程有自己的賽前減量和賽後恢復（按損傷類型，不是按時間長短）。例如超長單車沒有肌肉損傷，但會睡眠不足，所以隔週是恢復週、前 3 天心率壓低。行程落在目標賽前 3 週內會提醒你。</p>
        <ul class="list" id="evs" style="margin-top:6px"></ul>
        <div class="race-add"><p class="race-add-h">新增一個行程或比賽</p>
        <div class="two"><label><span>類型</span><select id="et">${Object.entries(ET).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("")}</select></label>
          <label><span>名稱</span><input type="text" id="en" maxlength="30" placeholder="河濱 10K"></label></div>
        <div class="two"><label><span>日期</span><input type="date" id="ed"></label>
          <label id="eel" hidden><span>結束日期</span><input type="date" id="ee"></label></div>
        <div class="two" id="erace"><label><span>距離（公里）</span><input type="number" id="ek" step="0.1" min="1" placeholder="10"></label>
          <label><span>目標時間（選填）</span><input type="text" id="ett" placeholder="50:00"></label></div>
        <p class="muted" id="ehint" style="margin-top:6px"></p>
        <button class="btn tonal block" type="button" id="eadd" style="margin-top:12px">加入</button></div></section>

      <section class="card"><h2>什麼時候跑</h2><p class="card-sub">勾選有空的日子，課表只會排在這些天。</p>
        <fieldset><legend>可以跑的日子</legend><div class="chips days">${WD.map((w, i) => chk("days", i, p.days.includes(i), w)).join("")}</div></fieldset>
        <div class="two"><label><span>長跑放在</span><select name="long_run_day">${WD.map((w, i) => `<option value="${i}" ${p.long_run_day == i ? "selected" : ""}>週${w}</option>`).join("")}</select></label>
          <label><span>通常幾點跑</span><input type="time" name="run_time" value="${esc(p.run_time)}"></label></div>
        <div class="two"><label><span>平日最多（分鐘）</span><input type="number" min="20" max="240" name="max_minutes_weekday" value="${p.max_minutes_weekday}"></label>
          <label><span>長跑最多（分鐘）</span><input type="number" min="30" max="400" name="max_long_minutes" value="${p.max_long_minutes}"></label></div>
        <p class="card-sub" style="margin-top:8px">可跑時間撐不起目標需要的跑量時，可行性檢查會告訴你。</p></section>

      <section class="card"><h2>想做的課</h2>
        <p class="card-sub">每週幾堂強度課、跑量加多快、什麼時候減量，都由能力診斷和分期決定，不用自己調。這裡只選你喜歡的形式：沒勾的課會換成效果接近、你有勾的課（例如沒勾坡道就換成間歇，沒勾變速就換成節奏跑）；基礎期的坡道、變速沒勾就不排。沒勾快步，輕鬆跑後面就不加快步。</p>
        <div class="chips g2" style="margin-top:12px">${Object.entries(m.workouts).map(([k, v]) => chk("workouts", k, p.workouts[k], v)).join("")}</div></section>

      <section class="card"><h2>每日目標</h2>
        <div class="three"><label><span>步數</span><input type="number" min="1000" step="500" name="step_goal" value="${p.step_goal ?? ""}" placeholder="自動"></label>
          <label><span>熱量（kcal）</span><input type="number" min="100" step="50" name="kcal_goal" value="${p.kcal_goal ?? 500}"></label>
        <label><span>睡眠（小時）</span><input type="number" min="5" max="12" step="0.5" name="sleep_goal" value="${p.sleep_goal ?? 8}"></label></div>
        <p class="card-sub" style="margin-top:8px">步數留空會用 Garmin 的目標。</p></section>

      <section class="card"><h2>個人數據</h2><p class="card-sub">最大心率實測最好，留空會自動估算。45 歲以上每 3 週減量一次。</p>
        <div class="two"><label><span>最大心率</span><input type="number" name="max_hr" value="${p.max_hr ?? ""}" placeholder="自動"></label>
          <label><span>年齡</span><input type="number" name="age" min="10" max="95" value="${p.age ?? ""}"></label></div>
        <label><span>性別（用在心率負荷公式）</span><select name="sex"><option value="M" ${p.sex === "M" ? "selected" : ""}>男</option><option value="F" ${p.sex === "F" ? "selected" : ""}>女</option></select></label>
        <p class="card-sub" style="margin-top:8px">配速區間從你的成績推算，跑滿 8 趟合格的輕鬆跑後會用心率資料自動校正，所以不用自己填配速。</p></section>

      <section class="card"><h2>其他</h2>
        <label class="check"><input type="checkbox" name="push_to_garmin" ${p.push_to_garmin ? "checked" : ""} ${S.me.provider !== "garmin" ? "disabled" : ""}><span>把課表推到 Garmin 手錶${S.me.provider !== "garmin" ? `<small>要先連結 Garmin 才能用</small>` : ""}</span></label>
        <label class="check"><input type="checkbox" name="show_on_board" ${p.show_on_board !== false ? "checked" : ""}><span>在跑友排行顯示我</span></label>
        <label class="inline-num"><span>行事曆提醒<small>開始前幾分鐘，0 = 不提醒</small></span><input type="number" min="0" max="240" name="calendar_alarm_min" value="${p.calendar_alarm_min}"></label></section>
      <div class="save-bar"><p class="err" id="perr" hidden></p><button class="btn block" type="submit">儲存並重排課表</button></div>
    </form>`;
  bindCommon();
  const f = document.getElementById("pf");
  const q = (id) => document.getElementById(id);
  let events = [...(p.events || [])];
  const drawEvents = () => {
    q("evs").innerHTML = events.map((e, i) => `<li class="row" style="cursor:default;grid-template-columns:1fr auto;min-height:48px">
      <span><b>${esc(e.name || ET[e.type]?.label)}</b><small>${esc(ET[e.type]?.label || "")} · ${e.date.replaceAll("-", "/")}${e.end_date ? `–${e.end_date.slice(5).replace("-", "/")}` : ""}${e.distance_km ? `，${e.distance_km} km` : ""}${e.target_time ? `，目標 ${esc(e.target_time)}` : ""}</small></span>
      <button class="btn small outline" type="button" data-rm="${i}">移除</button></li>`).join("") || `<li class="muted" style="padding:6px 0">還沒有加入行程。</li>`;
    document.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { events.splice(+b.dataset.rm, 1); drawEvents(); }));
  };
  drawEvents();
  const etHint = () => {
    const t = q("et").value;
    q("eel").hidden = !multiDay.includes(t);
    q("erace").hidden = !["race_b", "race_c", "trail"].includes(t);
    q("ehint").textContent = `主要損傷：${ET[t].damage}。` + ({ race_b: "賽前 3 天輕量、賽後 2–3 天恢復（第一天休息）。放在檢核點附近會直接當測驗賽。", race_c: "賽前 1 天輕量、賽後 1–2 天恢復。",
      endurance: "前一週只跑 2 次輕鬆跑；之後一整週恢復週，前 3 天心率 < 70% max。", trail: "跟 A 級賽一樣 3 週減量，賽後 3–4 週恢復。",
      travel: "沒有減量和恢復，但那週跑量打六折。", illness: "生病期間不跑，病後每病 1 天配 1 天輕量。", hike: "隔天和後天只排輕量（下坡的離心損傷）。" }[t] || "");
  };
  q("et").onchange = etHint; etHint();
  q("eadd").onclick = () => {
    if (!q("ed").value) return toast("請先選日期");
    const t = q("et").value;
    events.push({ type: t, name: q("en").value || ET[t].label, date: q("ed").value,
      end_date: multiDay.includes(t) && q("ee").value ? q("ee").value : null,
      distance_km: +q("ek").value || null, target_time: q("ett").value });
    events.sort((a, b) => a.date.localeCompare(b.date));
    ["en", "ed", "ee", "ek", "ett"].forEach((id) => (q(id).value = "")); drawEvents(); toast("已加入，記得按最下面的儲存");
  };
  document.querySelectorAll("[data-dist]").forEach((b) => (b.onclick = () => (f.event_distance_km.value = b.dataset.dist)));
  const modeHint = () => {
    const mode = f.plan_mode.value, man = mode === "manual";
    q("tpl").hidden = !man;
    q("mode-hint").textContent = mode === "imported"
      ? "每天照你匯入的課表（課名、公里數、配速都照文件）。App 不會自動改課；任何一天仍可在課表頁自己改。"
      : man
      ? "每週固定照你排的課（填公里數）。App 不會自動改課，但配速和心率上限仍依你的能力計算；疼痛等停損規則一樣有效。"
      : "依規格書自動排：先診斷你的限制因子，再決定分期、加量和每週課表。任何一天都可以在課表頁自己改。";
  };
  q("impf").onchange = async (e) => {
    const file = e.target.files[0], err = q("imperr");
    if (!file) return;
    err.hidden = true;
    try {
      const headers = { "Content-Type": "application/octet-stream" };
      if (S.token) headers.Authorization = `Bearer ${S.token}`;
      const r = await fetch("/api/import-plan", { method: "POST", headers, body: file });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `錯誤 ${r.status}`);
      toast(`已匯入 ${data.n_days} 天（${data.start} 到 ${data.end}）`);
      viewSettings();
    } catch (x) { err.textContent = x.message; err.hidden = false; e.target.value = ""; }
  };
  if (q("impdel")) q("impdel").onclick = async () => {
    await api("/api/import-plan", { method: "DELETE" }); toast("已取消匯入，回到自動排課"); viewSettings();
  };
  f.querySelectorAll("[name=plan_mode]").forEach((r) => (r.onchange = modeHint)); modeHint();
  f.querySelectorAll("[name=goal_type]").forEach((r) => (r.onchange = () => (q("race").hidden = f.goal_type.value !== "race")));
  const num = (v) => (v === "" || v == null ? null : +v);
  f.onsubmit = async (e) => {
    e.preventDefault();
    const d = new FormData(f);
    const body = {
      goal: { name: d.get("goal_name"), type: d.get("goal_type"), event_date: d.get("event_date") || null, event_distance_km: num(d.get("event_distance_km")), target_time: d.get("target_time") || "" },
      days: d.getAll("days").map(Number), long_run_day: +d.get("long_run_day"), run_time: d.get("run_time"),
      max_minutes_weekday: +d.get("max_minutes_weekday"), max_long_minutes: +d.get("max_long_minutes"),
      workouts: Object.fromEntries(Object.keys(m.workouts).map((k) => [k, d.getAll("workouts").includes(k)])),
      step_goal: num(d.get("step_goal")), kcal_goal: num(d.get("kcal_goal")), sleep_goal: num(d.get("sleep_goal")),
      max_hr: num(d.get("max_hr")), age: num(d.get("age")), sex: d.get("sex"),
      push_to_garmin: d.get("push_to_garmin") === "on", show_on_board: d.get("show_on_board") === "on",
      calendar_alarm_min: +d.get("calendar_alarm_min"),
      plan_mode: d.get("plan_mode"), events, level: d.get("level") || "auto",
      self_report: d.get("sr_weekly_km") === "" ? {} : {
        weekly_km: num(d.get("sr_weekly_km")), longest_km: num(d.get("sr_longest_km")),
        hard_per_week: d.get("sr_hard") == null ? null : +d.get("sr_hard"), years: d.get("sr_years"),
        race_km: num(d.get("sr_race_km")), race_time: d.get("sr_race_time") || null },
      template: Object.fromEntries(WD.map((_, i) => [i, { kind: d.get(`tk${i}`), km: num(d.get(`tm${i}`)) }]).filter(([, t]) => t.kind && t.kind !== "rest")),
    };
    const err = q("perr");
    try { await busy(e.submitter, () => api("/api/prefs", { method: "PUT", body })); toast("已儲存，課表已重排"); history.back(); }
    catch (x) { err.textContent = x.message; err.hidden = false; }
  };
}

// ───────────────────── 健康參考範圍 ─────────────────────
async function viewHealth() {
  if (S.me.provider === "none") {
    $app.innerHTML = `${appbar("配速與心率上限", { back: true })}<div class="card"><h2>需要先有跑步資料</h2><p class="muted" style="margin-top:4px">配速和心率上限是從你的紀錄算出來的。連結 Garmin 或使用範例資料後就會出現。</p>
      <button class="btn block" data-go="#/summary">去連結</button></div>`;
    return bindCommon();
  }
  const [h, rn] = await Promise.all([api("/api/health"), api("/api/runner").catch(() => null)]);
  $app.innerHTML = `
    ${appbar("配速與心率上限", { back: true })}
    <section class="card"><h2>你的訓練區間</h2>
      <p class="muted">最大心率 ${h.max_hr} bpm（${esc(h.max_hr_source)}），安靜心率 ${h.rest_hr} bpm。輕鬆跑用目前能力 VDOT ${h.vdot}，強度課用這個階段的 VDOT ${h.vdot_quality}。</p>
      <table class="zt"><thead><tr><th>區間</th><th>配速 /km</th><th>心率上限</th></tr></thead><tbody>
        ${h.zones.map((z) => `<tr><td>${esc(z.name)}${z.note ? `<br><small class="muted">${esc(z.note)}</small>` : ""}</td><td class="n">${z.pace.replaceAll(":", "'")}</td>
          <td class="cap">${z.hr_cap ? `≤ ${z.hr_cap}<br><small class="muted">${z.alert} 開始提醒</small>` : "—"}</td></tr>`).join("")}</tbody></table>
      ${h.zone_notes.map((n) => `<p class="why-line">${esc(n)}</p>`).join("")}
      <p class="muted" style="margin-top:8px">輕鬆跑個人化校正：${h.model?.active ? `已用 ${h.model.sample_count} 趟輕鬆跑的心率資料校正` : esc(h.model?.reason || "資料不足")}。</p>
      <p>${learnLink("hrcap", "心率上限怎麼看？")}</p></section>
    ${rn ? `<div style="margin-top:12px">${runnerHtml(rn)}</div>` : ""}
    <section class="card warm" style="margin-top:12px"><h2>為什麼和手錶的區間不一樣</h2>
      <p style="margin-top:6px">${esc(h.garmin_note)}</p>
      <p class="muted" style="margin-top:6px">跑步時看的是上面這些 bpm 數字，不是百分比：跑的時候沒辦法做除法。</p></section>
    <section class="card" style="margin-top:12px"><h2>參考值</h2>
      <ul class="list" style="margin-top:6px">${h.refs.map((r) => `<li style="padding:8px 0;border-top:1px solid var(--line)"><b>${r.name}</b>
        <p style="color:var(--ink-2);font-size:.9375rem">${esc(r.range)}${r.you ? `；你：${esc(r.you)}` : ""}</p></li>`).join("")}</ul>
      <p class="muted" style="margin-top:10px">${esc(h.disclaimer)}</p></section>
    <button class="btn tonal block" data-go="#/me/settings">修改最大心率或年齡</button>`;
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
      <p style="color:var(--ink-2);font-size:.9375rem;margin-top:8px">${me.provider === "apple" ? "資料由你 iPhone 上的捷徑送過來：每天早上一次身體資料，跑完一次運動紀錄。" : "手錶同步到 Garmin Connect App 之後，這裡大約 10 分鐘內就會自己抓進來（早上 5 點到晚上 11 點），抓到新的跑步會推播通知你，不用再按同步。"}</p>
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
  if (q("full")) q("full").onclick = (e) => act(e.currentTarget, async () => { await api("/api/sync?full=true", { method: "POST" }); toast("已重新抓取"); render(); });
  if (q("diag")) q("diag").onclick = (e) => act(e.currentTarget, async () => {
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
    if (q("pdel")) q("pdel").onclick = (e) => act(e.currentTarget, async () => {
      try { await api(`/api/garmin/push-test/${id}`, { method: "DELETE" }); localStorage.removeItem("ic8_test_workout"); q("ptest-out").innerHTML = `<p class="muted" style="margin-top:8px">已從 Garmin Connect 刪除測試課。</p>`; }
      catch (x) { toast(x.message); }
    });
  };
  if (q("ptest")) { drawTest(); q("ptest").onclick = (e) => act(e.currentTarget, async () => {
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


// ───────────────────── 匯入舊紀錄 ─────────────────────
function viewImport() {
  $app.innerHTML = `${appbar("匯入舊紀錄", { back: true })}
    <section class="card"><h2>為什麼要匯入</h2>
      <p style="margin-top:4px">課表的起始跑量、能力判斷都看你近 8–12 週的紀錄。剛換 Garmin 或 Apple Watch 的話，把以前在 Strava 或其他 App 的紀錄匯進來，課表一開始就會貼近你的程度。</p>
      <p class="muted" style="margin-top:6px">只會讀每趟的日期、距離、時間和心率；跟手錶已經同步過的同一趟會自動跳過。匯入後會重新診斷，整季規劃從這週重新開始。</p></section>

    <section class="card" style="margin-top:12px"><h2>從 Strava 匯出</h2>
      <ol class="steps" style="margin-top:6px">
        <li>用電腦打開 Strava 網站，右上角頭像 →「設定」→「我的帳號」。</li>
        <li>最下面「下載或刪除你的帳戶」→「開始」→「申請封存檔」。</li>
        <li>Strava 會寄信給你（通常幾分鐘到幾小時），下載那個 .zip。</li>
        <li>直接把 .zip 上傳到下面（檔案太大的話，解壓後只上傳裡面的 <b>activities.csv</b>）。</li>
      </ol>
      <p class="muted" style="margin-top:6px">其他 App（Garmin Connect、COROS、Polar、Suunto、Nike Run Club 匯出工具…）匯出的 .fit、.gpx、.tcx 檔也可以，一次可以選很多個。</p></section>

    <section class="card" style="margin-top:12px"><h2>上傳</h2>
      <label class="btn tonal block" style="cursor:pointer;margin-top:6px">${ico("upload")}選擇檔案
        <input type="file" id="histf" multiple accept=".zip,.csv,.fit,.gpx,.tcx,.gz" hidden></label>
      <div id="histst" style="margin-top:10px" aria-live="polite"></div></section>`;
  bindCommon();
  const q = (id) => document.getElementById(id);
  q("histf").onchange = async (e) => {
    const files = [...e.target.files], box = q("histst");
    if (!files.length) return;
    const tot = { runs: 0, others: 0, duplicates: 0, unreadable: 0, run_km: 0, first: null, last: null }, errs = [];
    for (const [i, file] of files.entries()) {
      box.innerHTML = `<p class="muted">上傳中 ${i + 1} / ${files.length}：${esc(file.name)}（${(file.size / 1e6).toFixed(1)} MB）…</p>`;
      try {
        const headers = { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) };
        if (S.token) headers.Authorization = `Bearer ${S.token}`;
        const r = await fetch("/api/import-history", { method: "POST", headers, body: file });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || `錯誤 ${r.status}`);
        for (const k of ["runs", "others", "duplicates", "unreadable", "run_km"]) tot[k] += d[k] || 0;
        if (d.first && (!tot.first || d.first < tot.first)) tot.first = d.first;
        if (d.last && (!tot.last || d.last > tot.last)) tot.last = d.last;
      } catch (x) { errs.push(`${file.name}：${x.message}`); }
    }
    const fd = (s) => (s ? s.replaceAll("-", "/") : "");
    box.innerHTML = `${tot.runs + tot.others ? `<p class="note-card">${ico("check")}<span>匯入 <b>${tot.runs}</b> 趟跑步（${fmt(tot.run_km, 0)} km）${tot.others ? `、${tot.others} 筆其他運動` : ""}，期間 ${fd(tot.first)} 到 ${fd(tot.last)}。課表已依新紀錄重新診斷。</span></p>` : `<p class="muted">沒有新的紀錄。</p>`}
      ${tot.duplicates ? `<p class="muted" style="margin-top:6px">${tot.duplicates} 趟手錶已經同步過，跳過。</p>` : ""}
      ${tot.unreadable ? `<p class="muted" style="margin-top:6px">${tot.unreadable} 個檔案看不懂，跳過。</p>` : ""}
      ${errs.map((x) => `<p class="err" style="margin-top:6px">${esc(x)}</p>`).join("")}
      ${tot.runs ? `<button class="btn block" style="margin-top:10px" data-go="#/season">看重新排好的整季規劃</button>` : ""}`;
    bindCommon(box);
    e.target.value = "";
  };
}
