"""排課規格書裡所有「可調數值」集中在這裡。

想調整門檻、比例、事件恢復天數，改這個檔就好，不用動演算法。
標註「補值」的是規格書沒有給、為了讓演算法完整而補上的數字。
"""
from __future__ import annotations

# ───────────── 第 2 層：目標 → 需求週跑量（km） ─────────────
# (距離 km, 目標秒數, 週跑量下限, 上限)；同一距離內依目標時間線性內插
VOLUME_NEEDS = [
    # 5K / 10K：補值
    (5.0, 30 * 60, 15, 25), (5.0, 25 * 60, 25, 35), (5.0, 22 * 60, 35, 45), (5.0, 20 * 60, 45, 55),
    (10.0, 60 * 60, 20, 30), (10.0, 50 * 60, 30, 40), (10.0, 45 * 60, 40, 50), (10.0, 40 * 60, 55, 65),
    # 半馬：規格書
    (21.0975, 2 * 3600, 25, 35), (21.0975, 3600 + 45 * 60, 35, 45),
    (21.0975, 3600 + 35 * 60, 50, 60), (21.0975, 3600 + 25 * 60, 65, 80),
    # 全馬：規格書（4:30、3:00 為補值）
    (42.195, 4 * 3600 + 30 * 60, 35, 45), (42.195, 4 * 3600, 40, 50),
    (42.195, 3 * 3600 + 30 * 60, 55, 70), (42.195, 3 * 3600, 75, 90),
]

# 週增率 r（依基線分級）
def growth_rate(baseline_km: float) -> float:
    if baseline_km < 25:
        return 0.06
    if baseline_km <= 50:
        return 0.08
    return 0.10


LOW_BASE_ABS_CAP_KM = 4.0          # 基線 < 25 km 時，單週絕對增幅上限
CONSISTENCY_RAMP_FACTOR = 0.7      # 一致性受限：前期增幅再打七折
CONSISTENCY_FLAT_WEEKS = 6         # 一致性受限：前 6 週不疊量
DELOAD_FACTOR = 0.70
TAPER_CURVE = [0.75, 0.55, 0.35]   # 減量期：峰值的 75% → 55% → 35%（最後一個是賽週）

FEASIBLE_BUFFER = 0.85             # W_need ≤ 可用週數 × 0.85 → 有緩衝

# ───────────── 第 1 層：診斷門檻 ─────────────
DECOUPLE_HIGH = 1.25               # 脅鉤比 > 1.25
DECOUPLE_LOW = 1.15                # 脅鉤比 ≤ 1.15
LONG_HR_ENDURANCE = 0.88           # 長距離心率 > 88% max → 耐力受限
LONG_HR_PACING = 0.80              # 長距離心率 < 80% max → 配速自我控制
LONG_MIN_SHARE = 0.60              # 長距離要 ≥ 目標距離 60% 才拿來診斷
CONSISTENCY_LOW_SHARE = 0.50       # 週跑量 < 基線 50% 算一週「斷掉」
CONSISTENCY_LIMIT = 2              # ≥ 2 週斷掉 → 一致性受限
EASY_TOO_FAST_HR = 0.80            # 短課平均心率 > 80% max → 輕鬆跑習慣跑太快
RIEGEL_EXP = 1.06

# ───────────── 第 3 層：分期比例（基礎:進展:專項，減量另計） ─────────────
PHASE_SPLIT = {
    "default": (35, 25, 30),
    "endurance": (42, 25, 23),     # 基礎期拉到 40–45%，壓縮專項期
    "speed": (25, 25, 40),         # 基礎期縮到 25%，專項期拉到 40%
}
TAPER_WEEKS = 3

PHASE_ZH = {"base": "基礎期", "build": "進展期", "specific": "專項期", "taper": "減量期",
            "race": "比賽週", "hold": "維持期", "recover": "恢復週"}
PHASE_PURPOSE = {
    "base": "堆量、修正輕鬆跑心率；只有輕鬆跑和快步",
    "build": "加入節奏跑，提高乳酸閾值",
    "specific": "加入間歇與比賽配速，跑量到峰值後轉平",
    "taper": "課表種類不變、量減半，消除疲勞",
    "race": "比賽週：保持腿感",
    "hold": "目標還沒確定：維持現有跑量，只排輕鬆跑",
    "recover": "賽後或事件後恢復",
}

# ───────────── 第 4 層：長跑上限 ─────────────
LONG_RATIO = {"base": 0.30, "build": 0.32, "specific": 0.35, "taper": 0.35, "race": 0.30, "hold": 0.30, "recover": 0.25}


def long_race_cap(race_km: float) -> float:
    """κ：長跑對比賽距離的上限倍數。半馬 1.4、全馬 0.75；5K/10K 為補值。"""
    return DIST_PROFILE[dist_key(race_km)]["kappa"]


# ───────────── 依目標距離的課表輪廓 ─────────────
# 5K/10K：長跑到某個長度就夠（12/16 km），專項期不再加長；重心放在間歇、快跑（R）。
# 半馬：節奏跑 + 長跑後段比賽配速。全馬：基礎期最長、長跑逐步加到 30 km 附近。
DIST_PROFILE = {
    "5k": {"label": "5K", "split": (30, 30, 30), "kappa": 2.4, "long_hold_specific": True},
    "10k": {"label": "10K", "split": (32, 28, 30), "kappa": 1.6, "long_hold_specific": True},
    "half": {"label": "半馬", "split": (35, 25, 30), "kappa": 1.4, "long_hold_specific": False},
    "full": {"label": "全馬", "split": (40, 25, 25), "kappa": 0.75, "long_hold_specific": False},
}


def dist_key(km: float) -> str:
    return "5k" if km < 8 else "10k" if km < 15 else "half" if km < 30 else "full"


# ───────────── 跑者程度 ─────────────
LEVEL_ZH = {"novice": "新手", "intermediate": "有基礎", "experienced": "有經驗"}
LEVEL_SHAPE = {
    "novice": "先把頻率和有氧底子練起來：基礎期只有輕鬆跑和快步，進展期才加節奏跑，最後才是間歇。",
    "intermediate": "基礎期隔週一堂坡道衝刺，進展期起節奏跑和間歇輪替。",
    "experienced": "從第一週就混合練：基礎期每週一堂強度（節奏或坡道），進展期兩堂，專項期以比賽需要的強度為主。",
}
# 判斷門檻（補值）
LEVEL_NOVICE_KM = 15               # 近 8 週週跑量中位數 < 15 km → 新手
LEVEL_EXP_KM = 35                  # ≥ 35 km 且 VDOT ≥ 45、規律、有練過強度 → 有經驗
LEVEL_EXP_VDOT = 45
LEVEL_EXP_HARD_RUNS = 3            # 近 8 週至少 3 次強度課（平均心率 ≥ 84% max）
# 每週可期待的 VDOT 進步（比例）與整季上限（補值，取保守值）
VDOT_GAIN_PER_WEEK = {"novice": 0.008, "intermediate": 0.005, "experienced": 0.003}
VDOT_GAIN_CAP = {"novice": 0.20, "intermediate": 0.12, "experienced": 0.06}


LONG_STEP_KM = 2.0                 # 單次長跑每週最多 +2 km

# ───────────── 第 5 層：每週質量課 ─────────────
QUALITY_PER_PHASE = {"base": 0, "build": 1, "specific": 2, "taper": 2, "race": 1, "hold": 0, "recover": 0}
HARD_TIME_SHARE_MAX = 0.20         # 高強度時間 ≤ 週總時間 20%
VOLUME_TOLERANCE = 0.05            # 週總量與目標誤差 ≤ 5%
MAX_CONSECUTIVE_RUN_DAYS = 3
MAX_CONSECUTIVE_RUN_DAYS_CONSISTENCY = 2
MIN_EASY_MINUTES = 20

# ───────────── 第 6 層：配速區間（%VO2max）與心率上限（%max HR） ─────────────
VDOT_FRACTION = {"E": (0.62, 0.70), "T": 0.88, "I": 0.975, "R": 1.06}
HR_CAP = {"E": 0.74, "M": 0.82, "T": 0.88, "I": 0.92}
HR_ALERT_MARGIN = 2                # 心率警示 = 上限 − 2 bpm
EASY_SLOWDOWN_S = 18               # 輕鬆跑習慣跑太快 → 基礎期 E 配速往慢偏 15–20 秒/km
REGRESSION_MIN_SAMPLES = 8
REGRESSION_MAX_DRIFT = 0.05        # 前後半漂移 < 5% 才能進回歸
REGRESSION_MAX_SPEEDUP_S = 30      # 校正後的 E 不得比表定值快過 30 秒/km
TARGET_VDOT_MAX_GAIN = 0.08        # 補值：強度課配速最多比現況快 8%（目標太遠時保護）

# ───────────── 第 7 層：固定事件 ─────────────
# pre/post: (天數, 該段的模式)。模式：light = 只排短的輕鬆跑；easy = 不排質量課；
# hrcap = 輕鬆跑且心率 < 70% max；rest = 不跑
EVENT_TYPES = {
    "race_a": {"label": "A 級目標賽", "pre": (21, "taper"), "post_by_km": [(21.1, 14), (99, 21)], "post_mode": "easy",
               "damage": "肌肉 + 代謝"},
    "race_b": {"label": "B 級測驗賽", "pre": (3, "light"), "post_by_km": [(10, 2), (99, 3)], "post_mode": "easy",
               "damage": "肌肉", "post_first_rest": True},
    "race_c": {"label": "C 級練習賽", "pre": (1, "light"), "post_by_km": [(10, 1), (99, 2)], "post_mode": "easy",
               "damage": "輕微"},
    "endurance": {"label": "超長時單車／泳渡", "pre_week": {"max_runs": 2, "no_quality": True, "no_long": True},
                  "post": (7, "easy"), "post_hrcap_days": 3, "week_after_mult": 0.6, "damage": "睡眠剝奪"},
    "trail": {"label": "越野跑／超馬", "pre": (21, "taper"), "post": (24, "easy"), "post_first_rest": True,
              "damage": "離心損傷（最重）"},
    "hike": {"label": "登山", "pre": (1, "light"), "post": (2, "light"), "damage": "離心損傷（下坡）"},
    "travel": {"label": "出差／旅行", "week_mult": 0.6, "damage": "作息中斷"},
    "illness": {"label": "生病（發燒）", "during": "rest", "post_per_day": "light", "damage": "免疫"},
    "other": {"label": "其他（婚禮、活動）", "pre": (0, "light"), "post": (1, "light"), "damage": "作息"},
}
A_RACE_GUARD_DAYS = 21             # 其他事件落入 A 級賽前 3 週 → 警告

# ───────────── 第 8 層：檢核點 ─────────────
CHECKPOINTS = [
    {"at": 0.30, "div": 4, "effort": "全力", "purpose": "確認基礎期有效，重算 VDOT", "event": "race_c"},
    {"at": 0.55, "div": 2, "effort": "全力", "purpose": "關鍵檢核點，決定要不要調目標", "event": "race_c", "key": True},
    {"at": 0.82, "div": 1, "effort": "控速", "purpose": "演練裝備與補給，驗證配速", "event": "race_b"},
]
MIN_TEST_KM = 3.0                  # 補值：測驗距離最短 3 km
CHECK_BANDS = [   # (相對等效成績的上限比例, 判讀, 動作代碼, 說明)
    (0.0, "超前", "hold", "照表走，不加量"),
    (0.045, "符合進度", "on_track", "照表走"),
    (0.09, "略落後", "slow_small", "目標調慢 2–3%，跑量不再上調"),
    (9.99, "本期做不到", "slow_big", "目標調慢 5–7%，把這場賽當跳板"),
]
GOAL_SLOWDOWN = {"slow_small": 0.025, "slow_big": 0.06}

# ───────────── 第 9 層：停損 ─────────────
PAIN_DAYS = 3
RHR_DELTA = 7
RHR_DAYS = 3
RHR_VOLUME_CUT = 0.30
COMPLETION_MIN = 0.80
COMPLETION_WEEKS = 2
DRIFT_OVERTRAIN = 0.10
DRIFT_RUNS = 3

PAIN_SITES = {"knee": "膝蓋", "shin": "小腿脛骨", "calf": "小腿肚", "ankle": "腳踝", "foot": "足底／腳掌",
              "hip": "髖部", "thigh": "大腿", "back": "下背", "other": "其他"}
