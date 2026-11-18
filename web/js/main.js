"use strict";
// 路由表 + 啟動
route(/^#\/login$/, () => viewGate("login"));
route(/^#\/register$/, () => viewGate("register"));
route(/^#\/summary$/, viewSummary, "summary");
route(/^#\/metric\/(\w+)$/, viewMetric, "summary");
route(/^#\/activity$/, viewActivity, "activity");
route(/^#\/workout\/(.+)$/, (id) => viewWorkout(decodeURIComponent(id)), "activity");
route(/^#\/season$/, viewSeason, "activity");
route(/^#\/analysis$/, viewAnalysis, "analysis");
route(/^#\/me$/, viewMe, "me");
route(/^#\/me\/partner$/, viewPartner, "me");
route(/^#\/me\/pick$/, viewPick, "me");
route(/^#\/me\/settings$/, viewSettings, "me");
route(/^#\/me\/health$/, viewHealth, "me");
route(/^#\/me\/calendar$/, viewCalendar, "me");
route(/^#\/me\/device$/, viewDevice, "me");

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
render();
