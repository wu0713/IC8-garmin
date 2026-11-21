"""把晨報、晚報改寫成「夥伴對你說話」。

每個種族一種說話口吻（自稱、怎麼叫你、口頭禪）。內容一律來自報告本身的數字，口吻只改說法，不改判斷。
"""
from __future__ import annotations

import random

# 自稱 I、叫你 U、早安 gm、晚安 ge、收尾（照課表／要休息／晚上）、驚呼
VOICES = {
    "beast_tamer": dict(I="我", U="夥伴", gm="早安，夥伴！動物們都醒了，換你囉！", ge="夥伴，今天辛苦啦！",
                        go="走吧，我們一起出發！", rest="今天就跟動物們一起賴床吧！", night="晚安，明天見！", wow="哇"),
    "spirit": dict(I="我", U="你", gm="早安～風告訴我你醒了喔。", ge="今天也辛苦了喔～",
                   go="我會在旁邊幫你吹順風的～", rest="今天就讓身體好好休息吧，我陪你～", night="晚安，做個好夢喔～", wow="咦"),
    "necromancer": dict(I="吾", U="活人", gm="……醒了啊，活人。吾已讀過你昨夜的生命跡象。", ge="……一天又結束了，活人。",
                        go="去吧。別讓吾在墓地等到你。", rest="今天就像吾的部下一樣，安靜地躺著吧。", night="去睡吧。睡眠是活人僅有的復活術。", wow="哼哼"),
    "golem": dict(I="我", U="主人", gm="主人，早安。晨間檢測，開始。", ge="主人，今日檢測，開始。",
                  go="結論：照計畫執行。我，跟上。", rest="結論：今日停機保養。", night="檢測完畢。主人，晚安。", wow="警告"),
    "demon": dict(I="本大爺", U="人類", gm="哼，人類，總算醒了。本大爺已經看過你的狀況了。", ge="哼，人類，今天還算有點表現。",
                  go="去吧，別讓本大爺失望。", rest="今天准你休息。這是本大爺的命令。", night="快去睡，明天還要替本大爺跑。", wow="嘖"),
    "succubus": dict(I="人家", U="親愛的", gm="早安呀，親愛的～人家一早就在等你醒來呢。", ge="親愛的，今天辛苦了～",
                     go="去吧～跑完回來跟人家說說今天的感覺。", rest="今天就好好休息嘛，人家會心疼的～", night="晚安呀，親愛的～", wow="哎呀"),
    "orc": dict(I="俺", U="兄弟", gm="兄弟！起床啦！俺看過你的身體了！", ge="兄弟！今天幹得不錯！",
                go="衝吧兄弟！哈！", rest="今天就大吃大睡！這也是戰士的修行！", night="睡飽才有力氣打仗！晚安！", wow="喔喔"),
    "sea_dragon": dict(I="吾", U="汝", gm="晨潮已至，汝也醒了。吾觀汝之氣息如下。", ge="潮汐將退，汝今日之事，吾已盡知。",
                       go="去吧，如浪而行。", rest="今日宜靜，如深海般休養。", night="安歇吧。明日潮起，再與汝同行。", wow="嗯"),
    "fox_spirit": dict(I="本狐", U="你呀", gm="早呀～本狐偷偷看了你昨晚睡得怎麼樣呢。", ge="你呀，今天也辛苦了呢～",
                       go="去吧，本狐在尾巴上幫你加油呢。", rest="今天就窩著吧，本狐把尾巴借你當枕頭呢。", night="晚安呀～明天見呢。", wow="哎呀呀"),
    "goblin": dict(I="哥布", U="老大", gm="老大老大！早安！哥布幫你偷看了數字！嘻嘻！", ge="老大！今天報告來啦！嘻嘻！",
                   go="出發出發！哥布幫你提鞋！", rest="今天偷懶日！哥布最喜歡偷懶日！嘻嘻！", night="老大晚安！哥布去數金幣了！", wow="嘿嘿"),
    "paladin": dict(I="在下", U="勇者", gm="勇者，早安。在下已為你檢視今晨的狀態。", ge="勇者，今日的征途辛苦了。",
                    go="願光明與你同行，出發吧。", rest="休息也是修練的一部分，今日請好好養傷。", night="願你今夜安眠，勇者。", wow="嗯"),
    "flame_dragon": dict(I="本龍", U="搭檔", gm="搭檔！早安！本龍的火已經點起來了！", ge="搭檔！今天燒得怎麼樣！",
                         go="燃起來吧，搭檔！", rest="今天先把火收起來，養足了再燒！", night="晚安，搭檔！明天繼續燃燒！", wow="喔"),
}
DEFAULT = dict(I="我", U="你", gm="早安！我是還沒孵化的蛋，先幫你看一下今天的狀況。", ge="今天辛苦了！我是還沒孵化的蛋。",
               go="出發吧！", rest="今天好好休息吧。", night="晚安！", wow="嗯")


def _n(v, d=0):
    return round(v) if d == 0 else round(v, d)


def chat(brief: dict, pet: dict | None, seed: str = "") -> dict:
    """回傳 {name, species, stage, lines:[文字], actions:[{id,label}]}。"""
    sp = (pet or {}).get("species")
    v = VOICES.get(sp, DEFAULT)
    I, U = v["I"], "你"   # 怎麼叫你只用在打招呼和道別，內文一律用「你」比較好讀
    rnd = random.Random(seed + brief["date"] + brief["kind"])
    lines: list[str] = []
    actions: list[dict] = []

    if brief["kind"] == "weekly":
        return {"name": _name(pet), "species": sp, "lines": _weekly_lines(brief, v, I), "actions": []}
    if brief["kind"] == "morning":
        lines.append(v["gm"])
        m = {x["key"]: x for x in brief["metrics"]}
        r = m.get("rhr")
        if r and r.get("today") is not None:
            t, base, y = _n(r["today"]), r.get("baseline"), r.get("yesterday")
            rg = r.get("range")
            normal = f"你平常的範圍是 {_n(rg[0])}–{_n(rg[1])}" if rg else f"平常大約 {_n(base)}" if base else ""
            if r["verdict"] == "bad":
                lines.append(f"{v['wow']}，{U}今天早上的安靜心率是 {t}，{normal}，比平常高了 {_n(t - base) if base else '不少'} 下。身體還在忙著恢復，可能是累積的疲勞、沒睡好，或是快感冒了。")
            elif r["verdict"] == "warn":
                lines.append(f"{U}今天的安靜心率 {t}，比{normal.replace('你平常的範圍是', '你平常的範圍')}高一點。有些疲勞還沒散。")
            elif r["verdict"] == "good":
                lines.append(f"{U}今天的安靜心率 {t}，{normal}，比平常還低，恢復得很好！")
            else:
                lines.append(f"{U}今天的安靜心率 {t}，在你平常的範圍裡（{_n(rg[0])}–{_n(rg[1])}），很穩定。" if rg
                             else f"{U}今天的安靜心率 {t}，跟平常差不多，很穩定。")
        else:
            lines.append(f"{I}還沒拿到{U}今天的安靜心率。手錶先同步到手機，{I}再幫{U}看一次。")

        others = [x for k, x in m.items() if k != "rhr" and x["verdict"] in ("warn", "bad")]
        goods = [x for k, x in m.items() if k != "rhr" and x["verdict"] == "good"]
        if others:
            parts = []
            for x in others[:2]:
                if x["key"] == "hrv":
                    parts.append(f"HRV 的 7 天平均掉出你的正常範圍" if "7 天平均" in x.get("text", "") else f"昨晚 HRV {_n(x['today'])}（平常 {_n(x['baseline'])}）")
                elif x["key"] == "sleep":
                    parts.append(f"睡眠{'分數 ' + str(_n(x['today'])) if x['unit'] == '分' else ' ' + str(x['today']) + ' 小時'}")
                elif x["key"] == "bb":
                    parts.append(f"Body Battery 只有 {_n(x['today'])}")
            lines.append(f"另外，{'、'.join(parts)}，也在提醒{U}要留意。")
        elif goods:
            lines.append(f"{'、'.join(x['label'] for x in goods[:2])}都不錯。")

        ye = brief.get("yesterday_eval")
        ya = [a for a in brief.get("yesterday_acts", [])]
        if ye:
            FZ = {"low": "疲勞不高", "moderate": "疲勞中等", "high": "疲勞偏高"}
            lines.append(f"昨天的「{ye['title']}」" + (f"{ye['score']} 分，" if ye.get("score") else "") + f"{FZ[ye['fatigue']]}"
                         + (f"（{'、'.join(ye['fatigue_why'])}）" if ye.get("fatigue_why") and ye["fatigue"] != "low" else "") + "。")
        elif ya:
            desc = "、".join(f"{a['label'] if a['group'] != 'run' else a['name']} {a['minutes']} 分" for a in ya[:3])
            extra = next((n for n in brief.get("notes", []) if "倍" in n), None)
            lines.append(f"昨天{U}做了{desc}。" + (extra or ""))
        if brief.get("volume") and brief["volume"]["level"] in ("warn", "bad"):
            lines.append(f"這 7 天的活動量是平常的 {_n(brief['ratio'] * 100)}%，{brief['volume']['label']}了。")

        lines.append(brief["advice"])
        runs_today = (brief.get("plan") or {}).get("kind") not in (None, "rest", "done", "strength", "event")
        if brief.get("options"):
            actions = option_actions(brief["options"])
            lines.append(f"要不要{I}幫{U}把今天的課改一下？" if brief["level"] != "go" else f"如果熱身完腿還是很沉，可以讓{I}幫{U}縮短：")
        elif brief.get("suggestion"):        # 舊版晨報（沒有 options）
            s_ = brief["suggestion"]
            actions = [{"id": "apply", "type": "legacy", "label": f"好，改成{s_['title']}" + (f" {s_['minutes']} 分" if s_.get("minutes") else "")},
                       {"id": "keep", "type": "keep", "label": "我還是照原本的跑"}]
            lines.append(f"要不要{I}幫{U}把今天的課改掉？")
        elif runs_today and not brief.get("checked_in"):
            lines.append(f"出門前跟{I}說一下，{U}今天身體感覺怎樣？")
            actions = FEEL_ACTIONS
        else:
            lines.append(v["go"] if brief["level"] == "go" and runs_today else v["rest"])
    else:
        early = brief.get("preliminary")
        if early:
            lines.append(f"{v['wow']}，還沒到晚報時間（{brief.get('evening_time')}）喔。{I}先跟你說到目前為止的狀況。")
        else:
            lines.append(v["ge"])
        acts = brief.get("acts", [])
        if acts:
            desc = "、".join(f"{a['label'] if a['group'] != 'run' else a['name']} {a['minutes']} 分" for a in acts[:3])
            lines.append(f"今天{U}做了{desc}，運動負荷 {brief['day_load']}。")
        else:
            lines.append(f"今天到現在{U}還沒有運動紀錄。" if early else f"今天{U}沒有運動紀錄，讓身體休息也很好。")
        if brief.get("plan_text"):
            lines.append(brief["plan_text"])
        ss = brief.get("session")
        if ss and ss.get("score"):
            lines.append(f"{I}看了今天的「{ss['title']}」：{ss['score']} 分，{ss['headline']}。"
                         + (f"下次可以注意{ss['improve'][0]['label']}。" if ss.get("improve") else ""))
        fat = brief.get("fatigue")
        ask = None
        if ss and not brief.get("rpe") and not early:
            ask = f"最後，今天跑起來的感覺跟{I}說一下？這會影響{I}對明天的建議。"
            actions = RPE_ACTIONS
        elif fat and fat.get("options"):
            actions = option_actions(fat["options"])
        it = {x["key"]: x for x in brief.get("items", [])}
        body = []
        if "steps" in it:
            body.append(f"走了 {it['steps']['value']:,} 步")
        if "bb" in it:
            body.append(f"電量剩 {it['bb']['value']}")
        if "stress" in it:
            body.append(f"平均壓力 {it['stress']['value']}")
        if body:
            lines.append(f"身體這邊：{'、'.join(body)}。")
        if "bb" in it and it["bb"]["verdict"] == "bad":
            lines.append(f"{v['wow']}，電量好低，{I}都替{U}累了。")
        if brief.get("volume") and brief["volume"]["level"] in ("warn", "bad"):
            lines.append(f"另外，這 7 天累積的活動量是平常的 {_n(brief['ratio'] * 100)}%，{brief['volume']['label']}了，接下來幾天別再加課。")
        if early:
            lines.append(f"晚上 {brief.get('evening_time')} {I}再跟你說完整的晚報。")
        else:
            lines.extend(brief.get("tips", []))
            if ask:
                lines.append(ask)
            elif actions and actions[0]["type"] == "option":
                lines.append(f"要不要{I}先幫{U}把明天的課改一下？")
            else:
                lines.append(v["night"])

    return {"name": _name(pet), "species": sp, "lines": lines, "actions": actions}


FEEL_ACTIONS = [{"id": f"feel{n}", "type": "feel", "value": n, "label": t}
                for n, t in ((1, "很糟"), (2, "很累"), (3, "普通"), (4, "不錯"), (5, "超好"))]
RPE_ACTIONS = [{"id": f"rpe{n}", "type": "rpe", "value": n, "label": t}
               for n, t in ((2, "很輕鬆"), (3, "剛剛好"), (4, "很硬"), (5, "快撐不住"))]


def option_actions(opts: list[dict]) -> list[dict]:
    return [{"id": o["id"], "type": "option" if o["apply"]["type"] != "keep" else "keep", "label": o["label"],
             "detail": o.get("detail"), "apply": o["apply"]} for o in opts]


def reply(kind: str, action: dict, result: dict, pet: dict | None) -> dict:
    """使用者在對話裡按了按鈕之後，夥伴的回應。result 由 main.py 處理完動作後帶進來。"""
    v = VOICES.get((pet or {}).get("species"), DEFAULT)
    I = v["I"]
    t = action.get("type")
    lines, actions = [], []
    if t == "feel":
        a = result.get("advice") or {}
        n = action.get("value")
        if a.get("options"):
            lines.append(f"{v['wow']}，{'、'.join(a.get('why') or ['身體不太舒服'])}。{a.get('headline') or ''}。")
            lines.append(f"{I}建議：{a['options'][0]['label']}。{a['options'][0]['detail']}")
            actions = option_actions(a["options"])
        elif n and n >= 4:
            lines.append(f"太好了！那今天就照課表，{v['go']}")
        else:
            lines.append(f"普通也很正常。熱身 10 分鐘後還是很沉的話，主課少跑一兩趟就好。{v['go']}")
    elif t == "rpe":
        f = result.get("fatigue") or {}
        FZ = {"low": "疲勞不高，明天照課表就好", "moderate": "疲勞中等", "high": "疲勞偏高"}
        lines.append(f"收到！綜合起來，今天這堂{FZ.get(f.get('level'), '')}" + (f"（{'、'.join(f['why'][:2])}）" if f.get("why") and f.get("level") != "low" else "") + "。")
        if f.get("advice"):
            lines.append(f["advice"])
        if f.get("options"):
            lines.append(f"要不要{I}先幫你把明天的課改一下？")
            actions = option_actions(f["options"])
        else:
            lines.append(v["night"] if kind == "evening" else v["go"])
    elif t == "option":
        lines.append(f"改好了！已經換成「{action.get('label')}」，行事曆和手錶下次同步會跟著更新。")
        lines.append(v["rest"] if (action.get("apply") or {}).get("kind") == "rest" else ("好好休息，" + v["night"] if kind == "evening" else v["go"]))
    else:
        lines.append("好，那就照原本的課。跑的途中心率降不下來、腿很沉，就直接改成輕鬆跑喔。" if kind == "morning"
                     else f"好，照原本的課。明天早上{I}再看一次你的恢復狀況。{v['night']}")
    return {"lines": lines, "actions": actions}


def _name(pet):
    return (pet or {}).get("nickname") or (pet or {}).get("species_name") or "還沒孵化的蛋"


def _weekly_lines(b: dict, v: dict, I: str) -> list[str]:
    W = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    wd = lambda ds: W[__import__("datetime").date.fromisoformat(ds).weekday()]  # noqa: E731
    r, p = b["run"], b["plan"]
    period = "這週" if b.get("sunday") else "上週"
    before = "上週" if b.get("sunday") else "前一週"
    upcoming = "下週" if b.get("sunday") else "這週"
    lines = [f"{v['wow']}，一週過去了！{I}來幫你整理{period}（{b['week_start'][5:].replace('-', '/')} 起）。" if b.get("sunday")
             else f"{v['wow']}，{I}幫你回顧上週（{b['week_start'][5:].replace('-', '/')} 起）。"]
    if r["count"]:
        cmp = ""
        if r["change"] is not None:
            cmp = f"，比{before}{'多' if r['change'] >= 0 else '少'} {abs(r['change'])}%" if abs(r["change"]) >= 5 else f"，跟{before}差不多"
        lines.append(f"{period}跑了 {r['count']} 次、{r['km']} 公里、{r['minutes']} 分鐘{cmp}。")
    else:
        lines.append(f"{period}沒有跑步紀錄。忙的話沒關係，{upcoming}從輕鬆跑慢慢接回來就好。")
    if p["planned"]:
        rate = p["rate"]
        tail = "，太棒了！" if rate >= 80 else "，還不錯。" if rate >= 50 else "。課表可能排太滿，要不要到設定裡少排一天？"
        lines.append(f"課表排了 {p['planned']} 堂，完成 {p['done']} 堂（{rate}%）{tail}")
    hi = []
    if b.get("longest"):
        hi.append(f"{wd(b['longest']['date'])}跑了最長的 {b['longest']['km']} 公里")
    if b.get("fastest") and (not b.get("longest") or b["fastest"]["id"] != b["longest"]["id"]):
        hi.append(f"{wd(b['fastest']['date'])}的 {b['fastest']['km']} 公里配速最快（{b['fastest']['pace']}）")
    if hi:
        lines.append(f"{period}亮點：" + "；".join(hi) + "。")
    ks = [x for x in b.get("sessions") or [] if x.get("score")]
    if ks:
        lines.append("重點課：" + "、".join(f"{wd(x['date'])}「{x['title']}」{x['score']} 分" for x in ks[:3]) + "。")
    pg = b.get("progress")
    if pg and pg.get("pred"):
        tail = ""
        if pg.get("gap_pct") is not None:
            tail = "，已經達到目標的能力了！" if pg["gap_pct"] <= 0 else f"，離目標 {pg['target']} 還差 {pg['gap_pct']}% 的能力。"
        lines.append(f"照目前的能力，{pg['distance_label']}大約能跑 {pg['pred']}{tail or '。'}")
    if b.get("others"):
        lines.append("其他運動：" + "、".join(f"{o['label']} {o['minutes']} 分" for o in b["others"][:3]) + "。")
    s = b["strength"]
    if s["done"]:
        lines.append(f"休息日的肌力伸展做了 {s['done']} 次，{I}看到了！")
    elif s["rest_days"]:
        lines.append(f"{period} {s['rest_days']} 天休息日都沒做肌力伸展，{upcoming}挑一天做 12 分鐘就好。")
    rh = b["rhr"]
    if rh["this"] and rh["prev"]:
        d = rh["this"] - rh["prev"]
        if d >= 3:
            lines.append(f"{period}安靜心率平均 {round(rh['this'])}，比{before}高 {round(d)} 下，身體有點累，{upcoming}多睡一點。")
        elif d <= -2:
            lines.append(f"{period}安靜心率平均 {round(rh['this'])}，比{before}低，體能在進步。")
    if b.get("volume") and b["volume"]["level"] in ("warn", "bad"):
        lines.append(f"不過這 7 天的總活動量是平常的 {round(b['ratio'] * 100)}%，{b['volume']['label']}了，{upcoming}別再加量。")
    if b["xp"]:
        lines.append(f"{period}{I}拿到 {b['xp']} 點經驗值，現在是 Lv {b['level_now']}。")
    n = b["next"]
    if n["runs"]:
        lines.append(f"{n.get('label', '下週')}排了 {n['runs']} 堂、約 {n['minutes']} 分鐘" + (f"，重點是{ '、'.join(n['key']) }。" if n["key"] else "，都是輕鬆跑。"))
    lines.append(v["night"] if b.get("sunday") else v["go"])
    return lines


def push_text(brief: dict, pet: dict | None) -> tuple[str, str]:
    c = chat(brief, pet)
    title = f"{c['name']}：{ {'morning': '晨報', 'evening': '晚報', 'weekly': '每週回顧'}[brief['kind']] }"
    body = " ".join(c["lines"][1:3]) if len(c["lines"]) > 2 else " ".join(c["lines"])
    return title, body[:180]
