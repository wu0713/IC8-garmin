"use strict";
// 休息日的肌力／伸展

async function viewStrength(ds, routine) {
  const r = await api(`/api/strength/${ds}${routine ? `?routine=${routine}` : ""}`);
  const d = new Date(ds + "T00:00:00");
  $app.innerHTML = `
    ${appbar(r.kind === "mobility" ? "伸展放鬆" : "休息日肌力", { back: true })}
    <section class="card warm">
      <span class="muted">${d.getMonth() + 1}/${d.getDate()} 休息日 · 約 ${r.minutes} 分鐘 · 不用器材</span>
      <h2 style="font-size:1.375rem;margin-top:2px">${esc(r.title)}</h2>
      <p style="margin-top:6px">${esc(r.why)}</p>
      <div class="st-switch">${r.others.map((o) => `<button class="mini" data-go="#/strength/${ds}/${o.id}">改做${esc(o.title)}</button>`).join("")}</div>
    </section>
    <ol class="st-list">
      ${r.items.map((it, i) => `<li class="card st-item" data-i="${i}">
        <label class="st-check"><input type="checkbox" aria-label="完成 ${esc(it.name)}"${r.done ? " checked" : ""}><span></span></label>
        <div class="st-body"><b>${esc(it.name)}</b><span class="st-dose num">${esc(it.dose)}</span>
          <p class="muted">${esc(it.cue)}</p>
          ${it.secs ? `<button class="btn tonal st-timer" data-secs="${it.secs}">${ico("clock")}計時 ${it.secs} 秒</button>` : ""}</div></li>`).join("")}
    </ol>
    <p class="muted" style="margin:12px 4px">動作會痛就停下來；練完有點痠是正常的，刺痛不是。</p>
    ${r.done
      ? `<div class="note-card">${ico("check")}<span><b>今天的${esc(r.title)}已完成</b>，夥伴拿到 ${r.xp} 點經驗值。</span></div>
         <button class="btn outline block" id="undo" style="margin-top:10px">取消完成</button>`
      : `<button class="btn block" id="done" style="margin-top:4px">${ico("check")}全部做完了（+${r.xp} 經驗值）</button>`}`;
  bindCommon();
  const q = (id) => document.getElementById(id);
  if (q("done")) q("done").onclick = (e) => busy(e.currentTarget, async () => {
    await api(`/api/strength/${ds}`, { method: "POST", body: { routine: r.id, done: true } });
    toast(`完成！夥伴 +${r.xp} 經驗值`); viewStrength(ds, r.id);
  });
  if (q("undo")) q("undo").onclick = (e) => busy(e.currentTarget, async () => {
    await api(`/api/strength/${ds}`, { method: "POST", body: { routine: r.id, done: false } }); viewStrength(ds, r.id);
  });
  document.querySelectorAll(".st-timer").forEach((b) => (b.onclick = () => {
    if (b.dataset.t) { clearInterval(+b.dataset.t); delete b.dataset.t; b.innerHTML = `${ico("clock")}計時 ${b.dataset.secs} 秒`; return; }
    let left = +b.dataset.secs;
    const tick = () => {
      b.innerHTML = `${ico("clock")}<span class="num">${left}</span> 秒（點一下停止）`;
      if (left-- <= 0) {
        clearInterval(+b.dataset.t); delete b.dataset.t;
        b.innerHTML = `${ico("check")}時間到`;
        navigator.vibrate?.(300);
        b.closest(".st-item").querySelector("input").checked = true;
      }
    };
    tick(); b.dataset.t = setInterval(tick, 1000);
  }));
}
