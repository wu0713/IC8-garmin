"use strict";
// 登入、連結 Garmin、摘要頁、指標趨勢頁

function viewGate(mode = "login") {
  const reg = mode === "register";
  $app.innerHTML = `
    <section class="gate">
      <div class="brand"><img src="/icons/icon-192.png" alt=""><div><h1>跑課</h1><p class="muted">Garmin 數據自動排課</p></div></div>
      <form class="card form" id="gate">
        <h2>${reg ? "建立帳號" : "登入"}</h2>
        <label><span>帳號</span><input type="text" name="username" autocomplete="username" autocapitalize="off" required minlength="3"></label>
        <label><span>密碼</span><input type="password" name="password" autocomplete="${reg ? "new-password" : "current-password"}" required minlength="6"></label>
        ${reg ? `<label><span>暱稱</span><input type="text" name="display_name"></label>
          ${S.meta?.invite_required ? `<label><span>邀請碼</span><input type="text" name="invite" required autocapitalize="off"></label>` : ""}` : ""}
        <p class="err" id="err" hidden></p>
        <button class="btn block" type="submit">${reg ? "建立帳號" : "登入"}</button>
        <button class="text-btn" type="button" id="swap" style="width:100%;margin-top:8px">${reg ? "已經有帳號了？登入" : "第一次用？建立帳號"}</button>
      </form>
    </section>`;
  document.getElementById("swap").onclick = () => viewGate(reg ? "login" : "register");
  document.getElementById("gate").onsubmit = async (e) => {
    e.preventDefault();
    const err = document.getElementById("err");
    try {
      const r = await busy(e.submitter, () => api(reg ? "/api/register" : "/api/login", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }));
      S.token = r.token; localStorage.setItem("ic8_token", r.token);
      go("#/summary");
    } catch (x) { err.textContent = x.message; err.hidden = false; }
  };
}

function viewLink() {
  const mfa = S.me.mfa_pending;
  $app.innerHTML = `
    ${appbar("連結手錶")}
    <form class="card form" id="gl" ${mfa ? "hidden" : ""}>
      <div class="device" style="grid-template-columns:56px 1fr"><div class="watch">${ico("watch")}</div>
        <div><b>Garmin Connect</b><small>用 Garmin 帳號登入，密碼只用來換登入憑證，不會存下來。</small></div></div>
      <label><span>Garmin 帳號 Email</span><input type="text" name="email" inputmode="email" autocomplete="username" autocapitalize="off" required></label>
      <label><span>Garmin 密碼</span><input type="password" name="password" autocomplete="current-password" required></label>
      <p class="err" id="e1" hidden></p>
      <button class="btn block" type="submit">連結並同步</button>
    </form>
    <form class="card form" id="gm" ${mfa ? "" : "hidden"}>
      <h2>輸入 Garmin 驗證碼</h2>
      <p class="muted">你的帳號有開兩步驟驗證，請到 Email 或驗證 App 取得 6 位數驗證碼。</p>
      <label><span>驗證碼</span><input type="text" name="code" inputmode="numeric" autocomplete="one-time-code" required></label>
      <p class="err" id="e2" hidden></p>
      <button class="btn block" type="submit">送出</button>
    </form>
    ${S.meta?.allow_demo ? `<div class="card"><h2>先用範例資料看看</h2><p class="muted" style="margin-top:4px">不連手錶，用一年份的擬真資料體驗所有功能。</p>
      <button class="btn tonal block" id="demo">使用範例資料</button></div>` : ""}
    <div class="card"><h2>還沒有手錶資料也能先逛</h2><p class="muted" style="margin-top:4px">先選一隻夥伴、看看朋友的排行，之後再回來連結。</p>
      <button class="btn outline block" data-go="#/me">先去選夥伴</button></div>`;
  bindCommon();
  const after = async () => {
    toast("連結成功，正在抓最近一年的資料…");
    try { await api("/api/sync?full=true", { method: "POST" }); } catch (x) { toast(x.message); }
    go("#/summary");
  };
  document.getElementById("gl").onsubmit = async (e) => {
    e.preventDefault();
    const err = document.getElementById("e1");
    try {
      const r = await busy(e.submitter, () => api("/api/garmin/login", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }));
      if (r.mfa) { S.me.mfa_pending = true; return viewLink(); }
      await after();
    } catch (x) { err.textContent = x.message; err.hidden = false; }
  };
  document.getElementById("gm").onsubmit = async (e) => {
    e.preventDefault();
    const err = document.getElementById("e2");
    try { await busy(e.submitter, () => api("/api/garmin/mfa", { method: "POST", body: Object.fromEntries(new FormData(e.target)) })); await after(); }
    catch (x) { err.textContent = x.message; err.hidden = false; }
  };
  const demo = document.getElementById("demo");
  if (demo) demo.onclick = () => busy(demo, async () => { await api("/api/garmin/demo", { method: "POST" }); await after(); });
}

async function syncNow(btn) {
  btn && (btn.disabled = true);
  toast("同步中…");
  try {
    const before = await api("/api/game");
    const r = await api("/api/sync", { method: "POST" });
    const after = await api("/api/game");
    const gained = after.xp - before.xp;
    const s2 = gained > 0 ? await api("/api/summary").catch(() => null) : null;
    if (s2?.today_activity) { toast(`跑完了！夥伴 +${gained} XP，來看訓練報告`); return go(`#/workout/${encodeURIComponent(s2.today_activity)}`); }
    toast(`同步完成，${r.synced.activities} 筆跑步${gained > 0 ? `，夥伴 +${gained} XP` : ""}${r.garmin_push?.uploaded ? `，${r.garmin_push.uploaded} 堂課已送到手錶` : ""}`);
    render();
  } catch (x) { toast(x.message); btn && (btn.disabled = false); }
}

// ───────────────────── 摘要 ─────────────────────
async function viewSummary() {
  const [d, bc] = await Promise.all([api("/api/summary"), briefCard()]);
  const dev = d.device, t = d.today, r = d.readiness;
  const listMode = localStorage.getItem("ic8_cards") === "list";
  const avatar = d.partner.species ? charThumb(d.partner.species, d.partner.stage) : ico("paw");

  $app.innerHTML = `
    ${appbar("今天", { actions: `
      <button class="icon-btn" id="sync" aria-label="立即同步">${ico("sync")}</button>
      ` })}
    ${d.last_sync_error ? `<div class="note-card" style="margin-bottom:12px">${ico("bolt")}<span>上次同步失敗：${esc(d.last_sync_error)}。按上面的同步再試一次；一直失敗就到「我的 → 手錶與資料來源」重新連結。</span></div>` : ""}

    ${bc}
    <button class="card quest" data-go="${d.today_activity ? `#/workout/${encodeURIComponent(d.today_activity)}` : "#/analysis"}" aria-label="今天的任務：${esc(t.title)}">
      <span class="quest-top">
        <span><span class="tag">今日任務 ${new Date().getMonth() + 1}/${new Date().getDate()}</span>
          <h2>${esc(t.title.replace(" ✅", ""))}</h2>
          <p>${t.minutes ? `${t.time} 開始，約 ${t.minutes} 分鐘${t.distance_km ? `、${t.distance_km} 公里` : ""}` : esc(t.summary)}</p></span>
        <span class="quest-pet">${d.partner.species ? charThumb(d.partner.species, d.partner.stage) : ""}</span>
      </span>
      <span class="quest-bot">
        <span class="hp"><span>今日體力</span><span class="segbar ${r.level}"><span style="width:${r.score}%"></span></span><span class="num">${r.score}/100</span></span>
        <span class="muted">${{ green: "狀態不錯，照課表跑", yellow: "狀態普通，別勉強", red: "需要恢復，放輕鬆" }[r.level]}${t.minutes ? `。${esc(t.summary)}` : ""}</span>
        <span style="color:var(--primary);font-weight:700">${d.today_activity ? "看今天的訓練報告" : t.steps?.length ? "看課表內容與調整" : "看本週課表"}</span>
      </span>
    </button>
    ${d.novice ? `<div class="note-card" style="margin-top:14px">${ico("flag")}<span><b>新手期</b>：紀錄還不多，先排輕鬆跑幫你養成習慣。規律跑滿 3 週、累積 6 次以上，就會開始加入變化。體能趨勢這類數字也要幾週後才會準。</span></div>` : ""}

    <section class="card" style="margin-top:18px" aria-label="今天與本週的進度">
      <div class="goals">${d.rings.map((x) => `<div class="goal"><b>${x.label}</b>
        <span class="segbar c-${x.key}"><span style="width:${Math.min(100, (x.value / (x.goal || 1)) * 100)}%"></span></span>
        <span class="val num">${fmt(x.value)} / ${fmt(x.goal)} ${x.unit}</span></div>`).join("")}</div>
    </section>

    <button class="strip" data-go="#/me/device" style="margin-top:14px">${ico("watch")}
      <span><b>${esc(dev?.name || (d.provider === "demo" ? "範例資料" : "Garmin"))}</b>
        <small>${d.provider === "demo" ? "範例資料，尚未連結你的 Garmin" : agoText(d.last_sync)}</small></span>
      <span class="chip"><span class="dot ${d.last_sync_error ? "off" : ""}"></span>${d.last_sync_error ? "同步失敗" : "已連線"}</span></button>

    <section class="section" aria-labelledby="mh">
      <div class="section-head"><h2 id="mh">我的指標</h2>
        <span><button class="icon-btn" id="mode" aria-label="${listMode ? "格狀顯示" : "清單顯示"}">${ico(listMode ? "grid" : "list")}</button>
        <button class="text-btn" id="edit">編輯</button></span></div>
      <div class="metrics ${listMode ? "list" : ""}">
        ${d.cards.map((c) => c.key === "partner" ? `
          <button class="mcard partner" style="--mc:var(--pill)" data-go="${c.species ? "#/me" : "#/me/pick"}">
            <span class="mh" style="color:var(--c-run)">${micon("partner")}${esc(c.label)}</span>
            <span class="mv num">${esc(c.value)}</span><span class="ms">${esc(c.sub)}</span>
            <span class="mbar"><span style="width:${Math.round((c.progress || 0) * 100)}%"></span></span>
            ${c.species ? charThumb(c.species, c.stage).replace("<img", '<img class="pimg"') : ""}
          </button>` : `
          <button class="mcard" style="--mc:${mcolor(c.key)}" data-go="${c.key === "readiness" ? "#/analysis" : `#/metric/${c.key}`}">
            <span class="mh" style="color:${mcolor(c.key)}">${micon(c.key)}${esc(c.label)}</span>
            <span><span class="mv num">${c.value == null ? "–" : fmt(c.value, c.key === "sleep" || c.key === "run_km" ? 1 : 0)}<small>${esc(c.unit)}</small></span>
            <span class="ms" style="display:block">${c.value == null ? "手錶沒有這項資料" : esc(c.sub || "")}</span></span>
            ${c.spark ? sparkline(c.spark, mcolor(c.key)) : ""}
          </button>`).join("")}
      </div>
    </section>`;
  bindCommon();
  document.getElementById("sync").onclick = (e) => syncNow(e.currentTarget);
  document.getElementById("mode").onclick = () => { localStorage.setItem("ic8_cards", listMode ? "grid" : "list"); viewSummary(); };
  document.getElementById("edit").onclick = () => editDashboard(d);
}

function editDashboard(d) {
  let order = [...d.dashboard, ...Object.keys(d.card_options).filter((k) => !d.dashboard.includes(k))];
  let on = new Set(d.dashboard);
  const draw = (box) => {
    box.querySelector("ul").innerHTML = order.map((k, i) => `<li>
      <label><input type="checkbox" data-k="${k}" ${on.has(k) ? "checked" : ""}><span style="color:${mcolor(k)}">${micon(k)}</span>${esc(d.card_options[k])}</label>
      <button class="icon-btn" data-up="${i}" aria-label="往上移">${ico("up")}</button>
      <button class="icon-btn" data-down="${i}" aria-label="往下移">${ico("down")}</button><span></span></li>`).join("");
    box.querySelectorAll("[data-k]").forEach((c) => (c.onchange = () => (c.checked ? on.add(c.dataset.k) : on.delete(c.dataset.k))));
    box.querySelectorAll("[data-up]").forEach((b) => (b.onclick = () => { const i = +b.dataset.up; if (i > 0) { [order[i - 1], order[i]] = [order[i], order[i - 1]]; draw(box); } }));
    box.querySelectorAll("[data-down]").forEach((b) => (b.onclick = () => { const i = +b.dataset.down; if (i < order.length - 1) { [order[i + 1], order[i]] = [order[i], order[i + 1]]; draw(box); } }));
  };
  sheet(`<h2>編輯我的指標</h2><p class="muted">勾選要顯示的卡片，用箭頭調整順序。</p><ul class="edit-list" style="margin-top:8px"></ul>
    <div class="btn-row"><button class="btn" id="save">儲存</button><button class="btn outline" id="reset">恢復預設</button></div>`, (box, close) => {
    draw(box);
    box.querySelector("#save").onclick = async () => {
      await api("/api/prefs", { method: "PUT", body: { dashboard: order.filter((k) => on.has(k)) } });
      close(); toast("已更新儀表板"); viewSummary();
    };
    box.querySelector("#reset").onclick = async () => {
      await api("/api/prefs", { method: "PUT", body: { dashboard: S.meta.default_cards } });
      close(); viewSummary();
    };
  });
}

// ───────────────────── 指標趨勢 ─────────────────────
async function viewMetric(key) {
  const rng = sessionStorage.getItem("ic8_rng") || "W";
  const showTrend = localStorage.getItem("ic8_trend") === "1";
  const m = await api(`/api/metric/${key}?range=${rng}`);
  const color = mcolor(key);
  const chart = m.chart === "bar"
    ? barChart(m.points, { color, avg: m.avg_label === "每週平均" ? null : m.avg, goal: rng === "W" || rng === "M" ? m.goal : null, trend: showTrend })
    : lineChart(m.points, { color, avg: m.avg, trend: showTrend });
  const diff = m.avg != null && m.prev_avg ? m.avg - m.prev_avg : null;
  const dec = m.unit === "km" || m.unit === "小時" ? 1 : 0;
  $app.innerHTML = `
    ${appbar(m.label, { back: true })}
    <div class="seg" role="group" aria-label="時間範圍">
      ${[["W", "週"], ["M", "月"], ["6M", "6 個月"], ["Y", "年"]].map(([k, t]) => `<button data-r="${k}" aria-pressed="${rng === k}">${t}</button>`).join("")}
    </div>
    <div class="big-num"><p class="k">${esc(m.avg_label)}</p>
      <p class="v num">${fmt(m.avg, dec)}<small>${esc(m.unit)}</small></p>
      <p class="p">${m.period.replaceAll("-", "/")}</p></div>
    <div class="chart-card">${chart}</div>
    ${m.avg == null ? `<div class="note-card" style="margin-top:12px">${ico("watch")}<span>這段期間手錶沒有傳回${esc(m.label)}。${{ hrv: "HRV 需要戴著手錶睡覺，新手錶要連續戴約 3 週才會開始有數字。", sleep: "睡眠需要戴著手錶睡覺。", sleep_score: "睡眠分數需要戴著手錶睡覺。", body_battery: "Body Battery 需要全天配戴。", stress: "壓力需要全天配戴。" }[key] || "到「夥伴 → 手錶與資料來源」按「開始檢查」，可以看是手錶沒量到還是抓取失敗。"}</span></div>` : ""}
    <label class="toggle-row"><span>顯示趨勢線</span><input type="checkbox" id="tr" ${showTrend ? "checked" : ""} style="width:20px;height:20px;accent-color:var(--primary)"></label>
    <div class="tiles" style="margin-top:12px">
      ${m.total != null ? `<div class="tile"><p class="k">總計</p><p class="v num">${fmt(m.total, dec)}</p></div>` : ""}
      <div class="tile"><p class="k">平均</p><p class="v num">${fmt(m.avg, dec)}</p></div>
      <div class="tile"><p class="k">最低</p><p class="v num">${fmt(m.min, dec)}</p></div>
      <div class="tile"><p class="k">最高</p><p class="v num">${fmt(m.max, dec)}</p></div>
    </div>
    ${diff != null ? `<div class="note-card" style="margin-top:12px">${ico("trend")}<span>${m.avg_label}比前一段時間${diff >= 0 ? "高" : "低"} ${fmt(Math.abs(diff), dec)} ${esc(m.unit)}（之前 ${fmt(m.prev_avg, dec)}）。</span></div>` : ""}
    <section class="section"><h2>關於${esc(m.label)}</h2><p style="color:var(--ink-2);margin-top:6px">${esc(m.about)}</p></section>`;
  bindCommon();
  document.querySelectorAll("[data-r]").forEach((b) => (b.onclick = () => { sessionStorage.setItem("ic8_rng", b.dataset.r); viewMetric(key); }));
  document.getElementById("tr").onchange = (e) => { localStorage.setItem("ic8_trend", e.target.checked ? "1" : "0"); viewMetric(key); };
}
