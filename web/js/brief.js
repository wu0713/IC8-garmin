"use strict";
// 晨報、晚報、推播設定

const BRIEF_V = { good: ["done", "很好"], ok: ["", "正常"], warn: ["warn", "注意"], bad: ["missed", "偏差"], none: ["", "還沒同步"] };
const LEVEL_TONE = { go: "good", reduce: "warn", rest: "bad", good: "good", warn: "warn", bad: "bad" };
const briefKindNow = () => {
  const n = new Date();
  if (n.getDay() === 0 && n.getHours() >= 20) return "weekly";
  return n.getHours() < 15 ? "morning" : "evening";
};
const BRIEF_NAME = { morning: "晨報", evening: "晚報", weekly: "週報" };

// 今天頁最上面的小卡：夥伴的一句話
async function briefCard() {
  const kind = briefKindNow();
  const [b] = await Promise.all([api(`/api/brief/${kind}`).catch(() => null), loadChars().catch(() => null)]);
  if (!b) return "";
  const c = b.chat, tone = LEVEL_TONE[b.level];
  const line = c.lines.find((l, i) => i > 0 && /安靜心率|今天.{0,4}做了|沒有運動/.test(l)) || c.lines[1] || c.lines[0];
  return `<button class="card brief-card tone-${tone}" data-go="#/brief/${kind}" style="margin-bottom:14px" aria-label="${BRIEF_NAME[kind]}：${esc(b.headline)}">
    <span class="bc-pet">${c.species ? charImg(c.species, c.stage, 52) : ico("paw")}</span>
    <span><small>${ico(kind === "morning" ? "sun" : kind === "weekly" ? "cal" : "moon")}${esc(c.name)}的${BRIEF_NAME[kind]}</small>
      <span class="bc-bubble">${esc(line)}</span><b class="bc-cta">${esc(b.headline)}，點我聽完整報告</b></span></button>`;
}

async function viewBrief(kind) {
  $app.innerHTML = `${appbar(BRIEF_NAME[kind], { back: true })}<p class="muted">整理中…</p>`;
  const [b] = await Promise.all([api(`/api/brief/${kind}`), loadChars()]);
  renderBrief(b);
}

function renderBrief(b) {
  const kind = b.kind, tone = LEVEL_TONE[b.level], c = b.chat;
  const when = b.generated_at.slice(11, 16);
  const seenKey = `ic8_brief_${b.date}_${kind}`;
  const animate = !sessionStorage.getItem(seenKey) && !matchMedia("(prefers-reduced-motion: reduce)").matches;
  sessionStorage.setItem(seenKey, "1");
  const tabs = `<div class="seg brief-seg" style="margin-bottom:14px">
    <button aria-pressed="${kind === "morning"}" data-go="#/brief/morning">${ico("sun")}晨報</button>
    <button aria-pressed="${kind === "evening"}" data-go="#/brief/evening">${ico("moon")}晚報</button>
    <button aria-pressed="${kind === "weekly"}" data-go="#/brief/weekly">${ico("cal")}週報</button></div>`;

  let detail = "";
  if (kind === "weekly") {
    const tile = (k, v, u = "") => `<div class="tile"><p class="k">${k}</p><p class="v num">${v}<small>${u}</small></p></div>`;
    detail += `<h3>${b.week_start.slice(5).replace("-", "/")} 那一週</h3>
      <div class="tiles two" style="margin-top:8px">
        ${tile("跑步", b.run.km, "km")}${tile("次數", b.run.count, "次")}
        ${tile("比前一週", b.run.change == null ? "–" : `${b.run.change > 0 ? "+" : ""}${b.run.change}`, b.run.change == null ? "" : "%")}${tile("前 4 週平均", b.run.avg4_km, "km")}
        ${tile("課表完成", b.plan.rate == null ? "–" : b.plan.rate, b.plan.rate == null ? "" : "%")}${tile("肌力伸展", b.strength.done, "次")}
        ${tile("安靜心率", b.rhr.this ? Math.round(b.rhr.this) : "–", "bpm")}${tile("經驗值", `+${b.xp}`)}
      </div>
      ${b.longest ? `<button class="strip" data-go="#/workout/${encodeURIComponent(b.longest.id)}" style="margin-top:10px">${ico("run")}<span><b>最長：${b.longest.km} 公里</b><small>${b.longest.date.slice(5).replace("-", "/")}</small></span>${ico("chevron")}</button>` : ""}
      ${b.fastest ? `<button class="strip" data-go="#/workout/${encodeURIComponent(b.fastest.id)}" style="margin-top:8px">${ico("bolt")}<span><b>最快：${b.fastest.pace}/km</b><small>${b.fastest.date.slice(5).replace("-", "/")} · ${b.fastest.km} 公里</small></span>${ico("chevron")}</button>` : ""}`;
  } else if (kind === "morning") {
    detail += `<h3>今天早上 vs 昨天 vs 平常</h3>
      <table class="cmp num"><thead><tr><th></th><th>今天</th><th>昨天</th><th>平常</th></tr></thead><tbody>
      ${b.metrics.map((m) => `<tr><th scope="row">${esc(m.label)}${m.verdict !== "ok" ? ` <span class="badge ${(BRIEF_V[m.verdict] || BRIEF_V.ok)[0]}">${(BRIEF_V[m.verdict] || BRIEF_V.ok)[1]}</span>` : ""}</th>
        <td><b>${m.today == null ? "–" : typeof m.today === "number" ? fmt(m.today, m.unit === "小時" ? 1 : 0) : esc(m.today)}</b></td>
        <td>${m.yesterday == null ? "–" : typeof m.yesterday === "number" ? fmt(m.yesterday, m.unit === "小時" ? 1 : 0) : esc(m.yesterday)}</td>
        <td>${m.range ? `${fmt(m.range[0])}–${fmt(m.range[1])}` : m.baseline == null ? "–" : fmt(m.baseline, m.unit === "小時" ? 1 : 0)}</td></tr>
        <tr class="why"><td colspan="4">${esc(m.text)}</td></tr>`).join("")}
      </tbody></table><p class="muted" style="margin-top:6px">「平常」是你自己近 4 週的正常範圍（平均 ± 1 個標準差）；HRV 看的是 7 天平均有沒有掉出範圍，比單日可靠。</p>
      ${b.yesterday_eval ? `<h3 style="margin-top:14px">昨天的課</h3><button class="strip" data-go="#/workout/${encodeURIComponent(b.yesterday_eval.activity_id)}" style="margin-top:6px">${ico("run")}<span><b>${esc(b.yesterday_eval.title)}${b.yesterday_eval.score ? ` · ${b.yesterday_eval.score} 分` : ""}</b><small>疲勞${{ low: "低", moderate: "中等", high: "偏高" }[b.yesterday_eval.fatigue]}${b.yesterday_eval.fatigue_why.length ? `：${esc(b.yesterday_eval.fatigue_why.join("、"))}` : ""}</small></span>${ico("chevron")}</button>` : ""}
      <h3 style="margin-top:14px">昨天的運動</h3>${b.yesterday_acts.length ? actMini(b.yesterday_acts) : `<p class="muted">昨天沒有運動紀錄。</p>`}`;
  } else {
    detail += `<h3>今天的身體</h3>
      <div class="tiles two" style="margin-top:8px">${b.items.map((i) => `<div class="tile"><p class="k">${esc(i.label)}${i.verdict && i.verdict !== "ok" ? ` <span class="badge ${(BRIEF_V[i.verdict] || BRIEF_V.ok)[0]}">${(BRIEF_V[i.verdict] || BRIEF_V.ok)[1]}</span>` : ""}</p>
        <p class="v num">${fmt(i.value)}<small>${esc(i.unit)}</small></p>${i.text ? `<p class="muted" style="font-size:.8125rem">${esc(i.text)}</p>` : ""}</div>`).join("")}</div>
      <h3 style="margin-top:14px">今天的運動</h3>${b.acts.length ? actMini(b.acts) : `<p class="muted">今天沒有運動紀錄。</p>`}
      ${b.session ? `<button class="strip" data-go="#/workout/${encodeURIComponent(b.session.activity_id)}" style="margin-top:10px">${ico("flag")}<span><b>${esc(b.session.title)}${b.session.score ? ` · ${b.session.score} 分` : ""}</b><small>${esc(b.session.headline || "")}${b.fatigue ? `；疲勞${{ low: "低", moderate: "中等", high: "偏高" }[b.fatigue.level]}` : ""}</small></span>${ico("chevron")}</button>` : ""}`;
  }

  const sch = b.schedule || {};
  $app.innerHTML = `${appbar(BRIEF_NAME[kind], { back: true, actions: `<button class="icon-btn" id="rf" aria-label="同步並更新">${ico("sync")}</button>` })}
    ${tabs}
    <section class="chat-stage tone-${tone}" aria-label="${esc(c.name)}">
      <span class="pet chat-pet">${c.species ? charImg(c.species, c.stage, 96) : `<span class="egg">${ico("paw")}</span>`}<span class="pet-shadow"></span></span>
      <span><b>${esc(c.name)}</b><small>${+b.date.slice(5, 7)}/${+b.date.slice(8)} ${when} · ${esc(b.headline)}</small></span>
    </section>
    <div class="chat" id="chat" aria-live="polite"></div>
    <div class="chat-actions" id="acts"></div>
    <details class="card" style="margin-top:16px"><summary>看詳細數字</summary><div style="margin-top:10px">${detail}</div></details>
    <button class="strip" data-go="#/me/briefs" style="margin-top:14px">${ico("bell")}
      <span><b>${sch.enabled ? `每天 ${sch.morning} 晨報、${sch.evening} 晚報${sch.weekly ? `，週日 ${sch.weekly} 週報` : ""}` : "晨報、晚報推播已關閉"}</b><small>設定時間與手機通知</small></span>${ico("chevron")}</button>`;
  bindCommon();

  const box = document.getElementById("chat"), acts = document.getElementById("acts");
  const say = (text, me = false) => box.insertAdjacentHTML("beforeend", `<p class="bubble ${me ? "me" : ""}">${esc(text)}</p>`);
  // 按鈕：回答感覺、跑後感受、套用改課。伺服器回傳夥伴接下來的話，可以一直接下去
  const showActions = (list = c.actions) => {
    if (!list || !list.length) { acts.innerHTML = ""; return; }
    const chips = list.every((a) => a.type === "feel" || a.type === "rpe");
    acts.className = `chat-actions${chips ? " chips-row" : ""}`;
    acts.innerHTML = list.map((a, i) => `<button class="btn ${chips ? "small outline" : i === 0 && a.type === "option" ? "" : "outline"}" data-i="${i}">
      ${esc(a.label)}${a.detail && !chips ? `<small style="display:block;font-weight:400;opacity:.8">${esc(a.detail)}</small>` : ""}</button>`).join("");
    acts.querySelectorAll("[data-i]").forEach((btn) => (btn.onclick = () => act(btn, async () => {
      const a = list[+btn.dataset.i];
      acts.innerHTML = ""; say(a.label, true);
      if (a.type === "legacy") {
        await api("/api/brief/morning/apply", { method: "POST" });
        say(`改好了！今天的課已經換成${b.suggestion.title}，行事曆和手錶下次同步也會跟著更新。`);
        return;
      }
      const r = await api(`/api/brief/${kind}/reply`, { method: "POST", body: { type: a.type, value: a.value ?? null, label: a.label, apply: a.apply || null } });
      await sayAll(r.lines);
      showActions(r.actions);
    })));
  };
  const sayAll = (lines) => new Promise((res) => {
    if (!animate) { lines.forEach((l) => say(l)); return res(); }
    let i = 0;
    const next = () => {
      if (i >= lines.length) return res();
      box.insertAdjacentHTML("beforeend", `<p class="bubble typing" aria-hidden="true"><i></i><i></i><i></i></p>`);
      setTimeout(() => { box.lastElementChild.remove(); say(lines[i++]); next(); }, Math.min(1100, 350 + lines[i].length * 12));
    };
    next();
  });
  sayAll(c.lines).then(() => showActions());

  document.getElementById("rf").onclick = (e) => act(e.currentTarget, async () => {
    sessionStorage.removeItem(seenKey);
    renderBrief(await api(`/api/brief/${kind}?refresh=true`)); toast("已同步並更新");
  });
}

function actMini(list) {
  return `<ul class="list" style="margin-top:6px">${list.map((a) => `<li style="display:flex;gap:10px;align-items:center;padding:6px 0;border-top:1px solid var(--line)">
    <span style="color:${SPORT_COLOR[a.group] || SPORT_COLOR.other};display:flex">${a.group === "run" ? ico("run") : sportIco(a.group)}</span>
    <span style="flex:1"><b>${esc(a.name)}</b><small class="muted" style="display:block">${esc(a.label)} · ${a.minutes} 分${a.km ? ` · ${fmt(a.km, 1)} km` : ""}${a.avg_hr ? ` · 心率 ${a.avg_hr}` : ""}</small></span>
    ${a.load != null ? `<span class="num muted">負荷 ${a.load}</span>` : ""}</li>`).join("")}</ul>`;
}

// ───────────────────── 晨報、晚報設定與推播 ─────────────────────
function b64ToBytes(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function viewBriefSettings() {
  const [p, info] = await Promise.all([api("/api/prefs"), api("/api/push")]);
  const prefs = p.prefs || p;
  const standalone = window.navigator.standalone || matchMedia("(display-mode: standalone)").matches;
  const canPush = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const perm = canPush ? Notification.permission : "unsupported";
  let pushHelp;
  if (!info.available) pushHelp = "伺服器還沒安裝推播套件。在電腦重新執行 run.ps1（會自動安裝），再回來這裡。";
  else if (location.protocol !== "https:" && location.hostname !== "localhost") pushHelp = "通知只能在 https 網址使用。請用 Tailscale Funnel 給的網址打開 App。";
  else if (!canPush && /iPhone|iPad/.test(navigator.userAgent) && !standalone) pushHelp = "iPhone 要先在 Safari 按「分享 → 加入主畫面」，再從主畫面的圖示打開，才能開啟通知（需要 iOS 16.4 以上）。";
  else if (!canPush) pushHelp = "這個瀏覽器不支援網頁通知。";
  else if (perm === "denied") pushHelp = "通知被封鎖了。到 iPhone「設定 → 通知 → 跑課」重新允許。";
  else pushHelp = info.devices ? `已經有 ${info.devices} 台裝置會收到通知。` : "還沒開啟。按下面的按鈕，手機會問你要不要允許通知。";

  $app.innerHTML = `${appbar("報告與休息日", { back: true })}
    <section class="card form">
      <label class="check" style="display:flex;gap:10px;align-items:center"><input type="checkbox" id="en" ${prefs.brief_enabled !== false ? "checked" : ""}>每天自動產生並推播</label>
      <div class="tiles" style="margin-top:12px;grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
        <label class="tile"><span class="k">${ico("sun")} 晨報時間</span><input type="time" id="mt" value="${esc(prefs.brief_morning || "07:30")}"></label>
        <label class="tile"><span class="k">${ico("moon")} 晚報時間</span><input type="time" id="et" value="${esc(prefs.brief_evening || "21:30")}"></label>
      </div>
      <label class="check" style="display:flex;gap:10px;align-items:center;margin-top:14px"><input type="checkbox" id="wk" ${prefs.weekly_review !== false ? "checked" : ""}>週日晚上的每週回顧</label>
      <label class="tile" style="margin-top:8px;display:block"><span class="k">${ico("cal")} 週報時間（週日）</span><input type="time" id="wt" value="${esc(prefs.brief_weekly || "20:30")}"></label>
      <label class="check" style="display:flex;gap:10px;align-items:center;margin-top:14px"><input type="checkbox" id="st" ${prefs.strength_enabled !== false ? "checked" : ""}>休息日排肌力或伸展（10–15 分鐘）</label>
      <p class="muted" style="margin-top:8px">晨報會等抓到今天的安靜心率才送出（手錶要先同步到手機的 Garmin Connect），最晚延後 3 小時。</p>
      <button class="btn block" id="save" style="margin-top:12px">儲存</button>
    </section>
    <section class="card" style="margin-top:14px">
      <h2 style="display:flex;gap:6px;align-items:center">${ico("bell")}手機通知</h2>
      <p style="margin-top:6px">${esc(pushHelp)}</p>
      ${canPush && info.available && perm !== "denied" ? `<button class="btn block" id="sub" style="margin-top:10px">${info.devices ? "這支手機也開啟通知" : "開啟通知"}</button>` : ""}
      ${info.devices ? `<div style="display:flex;gap:8px;margin-top:8px"><button class="btn tonal" id="test" style="flex:1">送一則測試</button><button class="btn outline" id="off" style="flex:1">全部關閉</button></div>` : ""}
    </section>`;
  bindCommon();
  document.getElementById("save").onclick = (e) => act(e.currentTarget, async () => {
    await api("/api/prefs", { method: "PUT", body: { brief_enabled: document.getElementById("en").checked,
      brief_morning: document.getElementById("mt").value, brief_evening: document.getElementById("et").value,
      weekly_review: document.getElementById("wk").checked, brief_weekly: document.getElementById("wt").value,
      strength_enabled: document.getElementById("st").checked } });
    toast("已儲存");
  });
  const sub = document.getElementById("sub");
  if (sub) sub.onclick = () => act(sub, async () => {
    const ok = await Notification.requestPermission();
    if (ok !== "granted") { toast("沒有允許通知"); return viewBriefSettings(); }
    const reg = await navigator.serviceWorker.ready;
    const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(info.key) });
    await api("/api/push/subscribe", { method: "POST", body: { subscription: s.toJSON() } });
    toast("通知已開啟"); viewBriefSettings();
  });
  const test = document.getElementById("test");
  if (test) test.onclick = () => act(test, async () => { const r = await api("/api/push/test", { method: "POST" }); toast(`已送出到 ${r.sent} 台`); });
  const off = document.getElementById("off");
  if (off) off.onclick = () => act(off, async () => {
    const reg = await navigator.serviceWorker.ready;
    const s = await reg.pushManager.getSubscription();
    if (s) await s.unsubscribe().catch(() => {});
    await api("/api/push/subscribe", { method: "DELETE" }); toast("已關閉通知"); viewBriefSettings();
  });
}
