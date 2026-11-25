"use strict";
// Apple Watch：用 iPhone「捷徑」把健康資料送進來

async function viewApple() {
  let a;
  try { a = await api("/api/apple"); }
  catch { a = await api("/api/apple/link", { method: "POST" }); S.me.provider = "apple"; }
  const last = a.last;
  const FIELD = { rhr: "安靜心率", hrv_avg: "HRV", sleep_hours: "睡眠", steps: "步數", active_kcal: "活動熱量" };
  const status = a.last_at
    ? `<span class="badge done">上次收到 ${new Date(a.last_at).toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
       <p style="margin-top:6px">${[last?.fields?.length ? `身體資料：${last.fields.map((f) => FIELD[f] || f).join("、")}` : "",
         last?.runs ? `跑步 ${last.runs} 筆` : "", last?.others ? `其他運動 ${last.others} 筆` : ""].filter(Boolean).join("；") || "有收到，但沒有可用的欄位"}</p>
       ${last?.skipped?.length ? `<p class="err" style="margin-top:4px">有 ${last.skipped.length} 筆看不懂：${esc(last.skipped[0])}</p>` : ""}`
    : `<span class="badge warn">還沒收到資料</span><p class="muted" style="margin-top:6px">照下面的步驟在 iPhone 做好捷徑，執行一次，這裡就會更新。</p>`;

  const step = (n, title, body) => `<li class="ap-step"><span class="ap-n num">${n}</span><div><b>${title}</b>${body}</div></li>`;
  const act = (t) => `<span class="ap-act">${esc(t)}</span>`;

  $app.innerHTML = `
    ${appbar("Apple Watch", { back: true })}
    <section class="card warm">
      <div class="device"><span class="watch">${ico("watch")}</span><span><b>Apple Watch</b><small>透過 iPhone 的「捷徑」App 上傳，不用安裝其他 App</small></span></div>
      <div style="margin-top:12px">${status}</div>
    </section>

    <section class="card" style="margin-top:14px">
      <h2>你的專屬上傳網址</h2>
      <p class="muted" style="margin-top:4px">捷徑會把資料送到這個網址。網址裡含密鑰，等於你的資料鑰匙，不要給別人。</p>
      <div class="ap-url"><code id="url">${esc(a.url)}</code></div>
      <div class="btn-row"><button class="btn" id="copy">${ico("link")}複製網址</button><button class="btn outline" id="rot">換一組新網址</button></div>
      ${a.https ? "" : `<div class="note-card" style="margin-top:10px">${ico("bolt")}<span><b>這個網址 iPhone 連不到。</b>現在是用電腦本機（${esc(location.host)}）開的。請用 Tailscale Funnel 給的 https 網址打開 App，再回來複製。</span></div>`}
    </section>

    <section class="card" style="margin-top:14px">
      <h2>捷徑一：每天早上送身體資料</h2>
      <p class="muted" style="margin-top:4px">晨報、恢復分數用的。在 iPhone 打開「捷徑」App，按右上角 ＋ 新增捷徑，依序加入下面的動作。</p>
      <ol class="ap-steps">
        ${step(1, "抓安靜心率", `<p>加入 ${act("尋找健康樣本")}，類型選「靜止心率」，加上條件「開始日期 在過去 1 天內」，排序「最新的優先」，限制 1 個。</p>`)}
        ${step(2, "抓 HRV", `<p>再加一個 ${act("尋找健康樣本")}，類型選「心率變異性」，其他設定同上。</p>`)}
        ${step(3, "送出", `<p>加入 ${act("取得 URL 的內容")}：</p>
          <ul><li>網址：貼上上面的專屬網址</li><li>方法：POST</li><li>要求本文：JSON</li>
          <li>新增欄位 <code>resting_hr</code>，值選第 1 步的結果</li><li>新增欄位 <code>hrv</code>，值選第 2 步的結果</li></ul>
          <p class="muted">睡眠、步數也想送的話，同樣用 ${act("尋找健康樣本")} 抓，欄位名稱用 <code>sleep_hours</code>、<code>steps</code>。</p>`)}
        ${step(4, "試跑一次", `<p>按右下角的 ▶ 執行。第一次會問要不要允許讀取健康資料，選「允許」。成功的話會顯示「已收到：1 天身體資料」，這頁上方也會更新。</p>`)}
        ${step(5, "設成每天自動", `<p>到「捷徑」下方的「自動化」→ ＋ →「特定時間」，時間設在晨報前（例如 7:15），選「立即執行」，動作選剛做好的捷徑。</p>`)}
      </ol>
    </section>

    <section class="card" style="margin-top:14px">
      <h2>捷徑二：跑完送運動紀錄</h2>
      <p class="muted" style="margin-top:4px">課表、訓練報告、經驗值用的。</p>
      <ol class="ap-steps">
        ${step(1, "抓今天的運動", `<p>加入 ${act("尋找健康樣本")}，類型選「體能訓練」，條件「開始日期 在過去 1 天內」。</p>
          <p class="muted">找不到「體能訓練」這個類型，代表你的 iOS 版本不支援，請看最下面的替代做法。</p>`)}
        ${step(2, "整理每一筆", `<p>加入 ${act("重複每一個")}，裡面放一個 ${act("辭典")}，欄位：</p>
          <ul><li><code>type</code>：重複項目的「類型」</li><li><code>start</code>：重複項目的「開始日期」（點日期 → 日期格式選 ISO 8601）</li>
          <li><code>duration_min</code>：重複項目的「時間長度」</li><li><code>distance_km</code>：重複項目的「距離」</li>
          <li><code>kcal</code>：重複項目的「能量」（有的話）</li></ul>`)}
        ${step(3, "送出", `<p>在重複外面加入 ${act("取得 URL 的內容")}，網址、方法、要求本文同捷徑一，新增欄位 <code>workouts</code>，類型選「陣列」，值選「重複結果」。</p>`)}
        ${step(4, "設成跑完自動送", `<p>「自動化」→ ＋ →「Apple Watch 體能訓練」→ 選「結束」，選「立即執行」，動作選這個捷徑。</p>`)}
      </ol>
      <div class="note-card" style="margin-top:10px">${ico("bolt")}<span><b>替代做法：</b>如果捷徑抓不到體能訓練，可以在 App Store 買一次買斷的匯出工具（例如「Health Exporter & Shortcuts」，約台幣 40 元），用它的「匯出體能訓練」動作取代第 1、2 步，第 3 步一樣送到這個網址。</span></div>
    </section>

    <details class="card" style="margin-top:14px"><summary>給會寫程式的人：上傳格式</summary>
      <pre class="ap-pre">POST ${esc(a.url.replace(/[^/]+$/, "（密鑰）"))}
Content-Type: application/json

{
  "date": "2026-11-18",
  "resting_hr": 55, "hrv": 48, "sleep_hours": 7.2, "steps": 8123,
  "workouts": [
    {"type": "Running", "start": "2026-11-17T20:44:00+08:00",
     "duration_min": 29.8, "distance_km": 4.0,
     "avg_hr": 135, "max_hr": 160, "kcal": 239}
  ]
}</pre>
      <p class="muted">數值可以帶單位（"55 count/min"、"4.0 km"、"0:29:47"），也可以一次送多天：<code>{"days": [ ... ]}</code>。</p>
    </details>`;
  bindCommon();
  document.getElementById("copy").onclick = async () => {
    try { await navigator.clipboard.writeText(a.url); toast("已複製"); }
    catch { const r = document.createRange(); r.selectNodeContents(document.getElementById("url")); getSelection().removeAllRanges(); getSelection().addRange(r); toast("請長按選取後複製"); }
  };
  document.getElementById("rot").onclick = (e) => act(e.currentTarget, async () => {
    if (!confirmInline(e.currentTarget, "換了之後，舊網址會失效，捷徑要改貼新網址。再按一次確認")) return;
    await api("/api/apple/rotate", { method: "POST" }); toast("已換新網址，記得更新捷徑"); viewApple();
  });
}

// 不用瀏覽器的確認框：第一次按顯示提示，第二次按才執行
function confirmInline(btn, msg) {
  if (btn.dataset.armed) return true;
  btn.dataset.armed = "1";
  btn.insertAdjacentHTML("afterend", `<p class="err" style="margin-top:6px">${esc(msg)}</p>`);
  return false;
}
