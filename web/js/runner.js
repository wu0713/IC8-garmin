"use strict";
// 配速與心率上限：個人最佳、成績預測、我的數值、訓練配速

async function viewRunner() {
  const r = await api("/api/runner");
  $app.innerHTML = `${appbar("跑者數據", { back: true })}${runnerHtml(r)}
    <button class="btn tonal block" data-go="#/season" style="margin-top:14px">${ico("flag")}訓練配速與心率上限（整季規劃）</button>`;
  bindCommon();
}

// 個人最佳、成績預測、我的數值（整季規劃頁也用）
function runnerHtml(r) {
  const tile = (x, extra = "") => `<div class="tile rn-t"><p class="k">${esc(x.label)}</p>
    <p class="v num">${x.time || "–"}</p>${extra}</div>`;
  const src = { garmin: "Garmin 手錶的預測", vdot: r.engine_vdot ? `用排課引擎評估的目前能力推算（VDOT ${r.engine_vdot}）` : "用你近半年最好的一次表現推算（VDOT）" }[r.prediction_source];
  return `

    <section class="card" aria-labelledby="pbh">
      <div class="section-head" style="margin:0"><h2 id="pbh">個人最佳</h2>${learnLink("pb")}</div>
      <div class="tiles two" style="margin-top:8px">
        ${r.pbs.map((p) => p.time
          ? `<button class="tile rn-t" data-go="${p.activity_id ? `#/workout/${encodeURIComponent(p.activity_id)}` : "#/activity"}"><p class="k">${esc(p.label)}</p>
              <p class="v num">${p.time}</p><p class="rn-sub num">${p.pace}/km · ${p.date.replaceAll("-", "/")}</p></button>`
          : tile(p, `<p class="rn-sub">還沒有紀錄</p>`)).join("")}
      </div>
      ${r.pb_source === "estimate" ? `<p class="muted" style="margin-top:8px">沒有手錶的 PB 資料，這裡是用整趟跑步的平均配速估算，實際比賽通常會更快。</p>` : ""}
    </section>

    <section class="card" style="margin-top:14px" aria-labelledby="prh">
      <div class="section-head" style="margin:0"><h2 id="prh">成績預測</h2>${learnLink("prediction")}</div>
      ${r.predictions.some((p) => p.time) ? `
      <table class="cmp num rn-pred"><thead><tr><th></th><th>預測成績</th><th>配速</th></tr></thead><tbody>
        ${r.predictions.map((p) => `<tr><th scope="row">${esc(p.label)}</th><td><b>${p.time || "–"}</b></td><td>${p.pace ? `${p.pace}/km` : "–"}</td></tr>`).join("")}
      </tbody></table>
      <p class="muted" style="margin-top:8px">${esc(src || "")}${r.basis && r.prediction_source === "vdot" && !r.engine_vdot ? `：${r.basis.date.replaceAll("-", "/")} 的 ${r.basis.km} 公里 ${r.basis.time}（VDOT ${r.basis.vdot}）` : ""}。全馬預測通常偏樂觀，要有足夠的長跑才撐得住。</p>`
      : `<p class="muted" style="margin-top:6px">需要近半年內 3 公里以上的跑步紀錄才能預測。</p>`}
    </section>

    <section class="card" style="margin-top:14px" aria-labelledby="nmh">
      <h2 id="nmh">我的數值</h2>
      <div class="tiles two" style="margin-top:8px">
        ${r.numbers.map((n) => `<a class="tile rn-t" href="#/learn/${{ vo2max: "vo2max", max_hr: "maxhr", rhr: "rhr", hrv: "hrv", lt: "lt", weekly: "load" }[n.key]}">
          <p class="k">${esc(n.label)} ${ico("book")}</p>
          <p class="v num">${n.value ?? "–"}<small>${n.value != null ? esc(n.unit) : ""}</small></p>
          <p class="rn-sub">${esc(n.note)}</p></a>`).join("")}
      </div>
      <p class="muted" style="margin-top:8px">點任一格可以看說明。最大心率不準的話，到「目標與課表偏好」自己填，下面的心率上限會跟著改。</p>
    </section>

`;
}
