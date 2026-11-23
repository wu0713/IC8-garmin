"""排課全面檢查：跨跑量、距離、程度、可跑天數、準備週數跑整季課表，統計違反規則的次數。

用法：PYTHONPATH=. python scripts/audit_plans.py（約 1–2 分鐘）。「比賽日沒標記」「單趟比比賽長很多」是腳本本身的簡化，可忽略。
"""
import itertools, re
from collections import Counter
from datetime import date, timedelta
from app import engine
from app import plan_tables as T
from app.week import schedule_week
Q = {"tempo","intervals","hills","fartlek","race_pace","long_m","test","reps","progression"}
LONGISH = {"long","long_m"}
issues = Counter(); examples = {}
bybase = Counter()
def flag(k, ex):
    issues[k]+=1
    examples.setdefault(k, ex)
    m = re.search(r"base(\d+) (\w+)", ex)
    if m: bybase[(k, int(m.group(1)), m.group(2))]+=1
def run(base, vdot5k_s, dist, target, weeks, level, days, liked, cons_weeks=False, age=None):
    wk=[base]*12
    if cons_weeks: wk[3]=base*0.3; wk[7]=base*0.2
    ath={"weekly_km":wk,"week_starts":[],"max_hr":190,"best_effort":{"km":5,"seconds":vdot5k_s,"date":"2026-11-01"},
         "long_runs":[{"km":base*0.3,"seconds":base*0.3*360,"avg_hr":150,"date":"2026-11-08"}],"easy_hr_share":0.75,"novice":base<10,
         "hard_runs_8w":4 if level=="experienced" else 1,"history_weeks":12,"recent_longest_km":base*0.35,"age":age}
    start=date(2026,11,23)
    goal={"distance_km":dist,"target_s":target,"race_date":(start+timedelta(weeks=weeks,days=-1)).isoformat(),"name":"x","type":"race"}
    diag=engine.diagnose(ath,goal)
    lv=level or engine.athlete_level(ath,diag)["level"]
    feas=engine.feasibility(diag,goal,weeks,None,lv)
    snap={"plan_start":start.isoformat(),"diag":diag,"v_base":diag["baseline_km"],"feasibility":feas,"level":lv,"start_long_km":base*0.35,"insertions":{},"decision":None}
    prefs={"days":sorted(days),"long_run_day":6,"max_long_minutes":150,"events":[],"age":age}
    se=engine.build_season(ath=ath,goal=goal,prefs=prefs,snap=snap,today=start)
    g2=dict(goal)
    if se.get("provisional") and feas.get("achievable_s"): g2["target_s"]=feas["achievable_s"]
    tag=f"{dist}k base{base} {lv} days{len(days)} wk{weeks}"
    tail={}; prev=None; prevlong=None; all_days=[]
    for w in se["weeks"]:
        z=engine.derive_zones(diag,g2,w["phase"],190,None)["zones"]
        sch=schedule_week(w,{"avail":set(days),"long_run_day":6,"zones":z,"diag":diag,"goal":g2,"event_days":se["event_days"],"prev_tail":tail,"max_minutes_weekday":70,"liked":liked,"max_hr":190,"level":lv})
        ds=sorted(sch["days"].items()); all_days+=ds
        tail={k:v["kind"] for k,v in ds[-3:]}
        runs=[(date.fromisoformat(k),v) for k,v in ds if v["kind"] not in ("rest",)]
        tot=sum(v.get("distance_km") or 0 for _,v in runs)
        if w["target_km"]>0 and abs(tot-w["target_km"])/w["target_km"]>0.2 and w["phase"]!="race":
            flag("weekly total "+("over" if tot>w["target_km"] else "UNDER")+" >20%", f"{tag} {w['week_start']} {w['phase']} target {w['target_km']} got {tot:.1f} " + " ".join(f"{v['kind'][:5]}{v['distance_km']:g}" for _,v in runs))
        longs=[v for _,v in runs if v["kind"] in LONGISH]
        easies=[v for _,v in runs if v["kind"] in ("easy","recovery")]
        if longs and easies and max(e["distance_km"] for e in easies) > longs[0]["distance_km"]: flag("easy longer than long", f"{tag} {w['week_start']} long {longs[0]['distance_km']} easy {max(e['distance_km'] for e in easies)}")
        qd=[d for d,v in runs if v["kind"] in Q|LONGISH]
        for a,b in zip(qd,qd[1:]):
            if (b-a).days<2: flag("hard days back-to-back", f"{tag} {a} {b}")
        hard_km=sum(v["distance_km"] for _,v in runs if v["kind"] in Q and v["kind"]!="long_m")
        if tot and hard_km/tot>0.45 and w["phase"] not in ("race","taper"): flag("quality share >45% of week km", f"{tag} {w['week_start']} {w['phase']} {hard_km:.1f}/{tot:.1f} " + " ".join(f"{v['kind'][:5]}{v['distance_km']:g}" for _,v in runs))
        if lv=="novice" and w["phase"]=="base" and any(v["kind"] in Q-{"test"} for _,v in runs): flag("novice quality in base", f"{tag} {w['week_start']}")
        if len(days)>=5 and len(runs)<=2 and w["phase"] not in ("race",) and tot>15: flag("only <=2 runs with >=5 days avail & >15km", f"{tag} {w['week_start']} {tot:.0f}km")
        mx=max([v["distance_km"] for _,v in runs] or [0])
        if mx > dist*1.6+3 and dist<30: flag("single run much longer than race", f"{tag} {w['week_start']} {mx}")
        for d,v in runs:
            if v["kind"]=="test" and prevlong and v["distance_km"]>prevlong+6: flag("test far longer than previous long", f"{tag} {d} test {v['distance_km']} prevlong {prevlong}")
            if not v.get("hr_cap") and v["kind"] in ("easy","long","recovery"): flag("easy without hr cap", f"{tag} {d}")
            if v.get("minutes") and v["minutes"]>200: flag("run > 200 min", f"{tag} {d} {v['kind']} {v['distance_km']}km {v['minutes']}")
        if longs: prevlong=max(prevlong or 0, longs[0]["distance_km"])
        if prev and w["phase"] not in ("taper","race") and w.get("tag")!="deload" and prev.get("tag")!="deload" and w["target_km"]>prev["target_km"]*1.16+0.5: flag("weekly jump >15%", f"{tag} {prev['target_km']}->{w['target_km']}")
        prev=w
    # race week: is race day scheduled?
    rd=goal["race_date"]
    if not any(k==rd and v["kind"] in ("race","event") for k,v in all_days): flag("race day not marked as race", f"{tag} {rd} -> {[v['kind'] for k,v in all_days if k==rd]}")
    # taper: last 2 weeks volume < peak
    peak=max(w["target_km"] for w in se["weeks"]); last=se["weeks"][-2]["target_km"]
    if last>peak*0.8: flag("taper not reducing", f"{tag} peak {peak} pre-race {last}")
    return se
liked_all={"tempo","intervals","hills","fartlek","reps","strides"}
import random
random.seed(1)
samples = {}
_flag = flag
def flag(k, ex):
    _flag(k, ex)
    m = re.search(r"base(\d+) ", ex); b = m.group(1) if m else "?"
    samples.setdefault((k, b), [])
    if len(samples[(k, b)]) < 300: samples[(k, b)].append(ex)
for base,v5,dist,tgt,weeks,lv,days in itertools.product([8,20,40,70],[30*60,24*60,20*60],[5,10,21.0975,42.195],[None],[8,16,24],["novice","intermediate","experienced"],[(1,3,6),(0,1,2,3,4,5,6),(1,2,4,6)]):
    pred=engine.equivalent_time(engine.vdot(5,v5),dist)
    for t in (pred*1.05, pred*0.92):
        try: run(base,v5,dist,round(t),weeks,lv,set(days),liked_all,cons_weeks=(base==20))
        except Exception as e: flag("EXCEPTION "+type(e).__name__, f"{base} {dist} {weeks} {lv} {days}: {e}")
for k,n in issues.most_common(): print(f"{n:5d}  {k}\n       e.g. {examples[k]}")
import re
print("---- by base")
for (k,b,l),n in sorted(bybase.items()):
    if k in ("weekly total off >20%","quality share >45% of week km","easy longer than long","run > 200 min","test far longer than previous long"): print(f"{k:40s} base{b:3d} {l:13s} {n}")

for k in ("weekly total over >20%","quality share >45% of week km","easy longer than long"):
  for b in ("8","20","40","70"):
    xs=[x for x in samples.get((k,b),[]) if "days7" in x or "days4" in x]
    print("==",k,b); [print("  ",x) for x in random.sample(xs, min(3,len(xs)))]
