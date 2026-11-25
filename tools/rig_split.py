"""把夥伴圖拆成可以各自動的部位（頭、翅膀、尾巴、下襬），給點擊時的部位動作用。

用法：python tools/rig_split.py            # 依 RIGS 重新拆全部
      python tools/rig_split.py succubus   # 只拆一種

每個部位用一個矩形框住，加上關節位置（轉動的軸心）。框裡「跟身體連在一起」的像素才算這個部位，
飄在旁邊的小東西（狐火、光點）留在身體層。部位從身體層挖掉，但關節附近多留 3px 重疊，轉動時不會露出縫。

輸出：web/characters/<種族>/rig/<階段>_<部位>.png、<階段>_base.png，以及 web/characters/rigs.json
座標都是處理後（pixel_polish 之後）圖片的像素。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
CHARS = ROOT / "web" / "characters"

# 部位：(名稱, [x0, y0, x1, y1], [關節 x, y], 動作)
# 動作：tilt 歪頭、flapL/flapR 拍翅、fanL/fanR 尾巴（或魔力圈）往外展開、wag 搖尾巴、breath 上半身呼吸（腳不動）、nod 動物點頭、bob 漂浮、flicker 火焰跳動
# 前面的部位優先：同一個像素落在兩個框裡，算給前面那個
RIGS: dict[str, dict[int, list]] = {
    "succubus": {
        1: [("head", [30, 0, 82, 47], [52, 47], "tilt"), ("tail", [6, 64, 37, 97], [37, 85], "wag"), ("torso", [18, 47, 76, 76], [50, 76], "breath")],
        2: [("head", [28, 0, 82, 57], [55, 57], "tilt"), ("tail", [0, 85, 41, 126], [41, 92], "wag"), ("torso", [26, 57, 82, 92], [55, 92], "breath")],
        3: [("head", [38, 8, 92, 57], [63, 57], "tilt"), ("wingL", [2, 58, 33, 112], [33, 64], "flapL"),
            ("wingR", [92, 68, 112, 108], [92, 70], "flapR"), ("torso", [33, 57, 95, 110], [63, 110], "breath")],
        4: [("head", [66, 4, 110, 47], [88, 47], "tilt"), ("wingL", [8, 18, 60, 108], [60, 42], "flapL"),
            ("wingR", [116, 18, 165, 120], [116, 42], "flapR"), ("torso", [60, 47, 118, 100], [88, 100], "breath")],
        5: [("head", [52, 22, 90, 60], [70, 60], "tilt"), ("ringL", [0, 8, 46, 100], [46, 50], "fanL"),
            ("ringR", [94, 8, 130, 100], [94, 50], "fanR"), ("torso", [46, 58, 94, 100], [70, 100], "breath")],
    },
    # 馴獸師：人只動頭和上半身（腳不動）；動物的頭點頭、低吼，尾巴搖；水晶漂浮、火焰跳動
    "beast_tamer": {
        1: [("head", [36, 15, 71, 48], [53, 48], "tilt"), ("crystal", [78, 23, 100, 57], [89, 40], "bob"),
            ("tail", [3, 61, 20, 92], [20, 86], "wag"), ("beast", [80, 61, 100, 80], [86, 80], "nod"),
            ("torso", [33, 48, 72, 84], [53, 84], "breath")],
        2: [("head", [54, 16, 84, 44], [68, 44], "tilt"), ("beast", [118, 52, 152, 96], [118, 78], "nod"),
            ("torso", [46, 44, 86, 74], [66, 74], "breath")],
        3: [("head", [58, 37, 90, 68], [74, 68], "tilt"), ("mane", [98, 53, 158, 92], [125, 92], "flicker"),
            ("torso", [40, 68, 95, 108], [68, 108], "breath")],
        4: [("head", [84, 20, 120, 57], [102, 56], "tilt"), ("beast", [150, 75, 198, 132], [152, 100], "nod"),
            ("torso", [56, 57, 125, 110], [92, 110], "breath")],
        5: [("head", [82, 26, 122, 62], [102, 62], "tilt"), ("beast", [165, 85, 203, 132], [170, 108], "nod"),
            ("tail", [20, 102, 42, 132], [42, 126], "wag"), ("torso", [85, 62, 132, 105], [106, 105], "breath")]},
    # 精靈使：人動頭和上半身；身邊的光球、小精靈、幽靈漂浮；第 3 階段的火焰跳動。第 5 階段柔光是半透明，只動臉和上半身
    "spirit": {
        1: [("head", [8, 3, 44, 40], [26, 40], "tilt"), ("wisp", [49, 5, 73, 39], [61, 22], "bob"), ("torso", [3, 40, 46, 78], [24, 78], "breath")],
        2: [("head", [22, 22, 60, 58], [40, 58], "tilt"), ("fairy", [70, 2, 116, 56], [92, 30], "bob"), ("torso", [16, 58, 62, 98], [38, 98], "breath")],
        3: [("head", [42, 35, 78, 72], [60, 72], "tilt"), ("fairy", [100, 24, 140, 92], [118, 60], "bob"),
            ("flame", [78, 18, 100, 92], [90, 90], "flicker"), ("torso", [38, 72, 85, 112], [62, 112], "breath")],
        4: [("head", [65, 30, 105, 72], [85, 72], "tilt"), ("ghost", [118, 16, 169, 112], [140, 100], "bob"), ("torso", [55, 72, 118, 112], [86, 112], "breath")],
        5: [("head", [84, 44, 106, 72], [95, 72], "tilt"), ("torso", [66, 72, 120, 112], [93, 112], "breath")]},
    # 死靈法師：人動頭和上半身；召喚出來的骷髏、騎士、狼的頭會點頭；第 3 階段紫水晶裡的魂火跳動
    "necromancer": {
        1: [("head", [22, 10, 55, 40], [38, 40], "tilt"), ("minion", [62, 86, 92, 112], [76, 108], "nod"), ("torso", [12, 40, 58, 75], [35, 75], "breath")],
        2: [("head", [42, 2, 82, 38], [60, 38], "tilt"), ("minion", [88, 40, 130, 68], [106, 68], "nod"), ("torso", [20, 38, 82, 78], [50, 78], "breath")],
        3: [("head", [55, 10, 95, 50], [75, 50], "tilt"), ("flame", [100, 2, 134, 50], [115, 48], "flicker"),
            ("minion", [128, 42, 153, 72], [140, 72], "nod"), ("torso", [42, 50, 105, 95], [72, 95], "breath")],
        4: [("minion", [128, 58, 156, 82], [142, 82], "nod"), ("torso", [55, 70, 108, 115], [82, 115], "breath")],
        5: [("head", [90, 30, 116, 62], [103, 62], "tilt"), ("beast", [158, 92, 196, 136], [162, 112], "nod"),
            ("torso", [72, 62, 130, 110], [100, 110], "breath")]},
    # 魔像：岩石本體呼吸起伏；頭上的芽搖；樹人頭冠擺；最終階段鹿角頭冠、上半身、雙臂擺動
    "golem": {
        1: [("torso", [8, 6, 52, 40], [30, 48], "breath")],
        2: [("tail", [16, 2, 40, 20], [30, 20], "wag"), ("tailR", [66, 0, 92, 20], [75, 20], "wag"), ("torso", [12, 20, 94, 62], [52, 76], "breath")],
        3: [("head", [40, 4, 116, 72], [78, 72], "tilt")],
        4: [("head", [60, 6, 106, 55], [83, 55], "tilt"), ("torso", [38, 55, 126, 95], [83, 95], "breath")],
        5: [("head", [36, 8, 140, 70], [88, 70], "tilt"), ("armL", [18, 82, 56, 152], [50, 86], "swayL"),
            ("armR", [118, 82, 156, 150], [124, 86], "swayR"), ("torso", [55, 70, 120, 120], [88, 120], "breath")]},
    # 惡魔：頭、上半身；小惡魔拍翅、搖尾巴；騎士型態只動頭和上半身；最終階段披風飄
    "demon": {
        1: [("head", [36, 6, 74, 40], [55, 40], "tilt"), ("wingL", [2, 12, 36, 52], [34, 30], "flapL"), ("torso", [30, 40, 72, 62], [50, 62], "breath")],
        2: [("head", [50, 8, 94, 48], [70, 48], "tilt"), ("wingL", [2, 12, 50, 74], [48, 30], "flapL"), ("wingR", [94, 12, 121, 74], [96, 30], "flapR"),
            ("tail", [22, 74, 48, 96], [46, 85], "wag"), ("torso", [44, 48, 96, 78], [70, 78], "breath")],
        3: [("head", [40, 6, 100, 48], [70, 48], "tilt"), ("torso", [28, 48, 110, 90], [70, 90], "breath")],
        4: [("head", [40, 6, 100, 48], [70, 48], "tilt"), ("torso", [28, 48, 110, 90], [70, 90], "breath")],
        5: [("head", [50, 8, 100, 45], [75, 45], "tilt"), ("cape", [108, 60, 154, 154], [110, 62], "swayR"),
            ("torso", [44, 45, 108, 88], [76, 88], "breath")]},
    # 獸人：頭、上半身；手上的武器微微晃，點一下揮下去（雙劍往外揮）
    "orc": {
        1: [("head", [10, 4, 46, 38], [28, 38], "tilt"), ("weapon", [46, 18, 72, 52], [47, 50], "swing"), ("torso", [8, 38, 50, 62], [28, 62], "breath")],
        2: [("head", [22, 8, 62, 42], [42, 42], "tilt"), ("weapon", [72, 32, 114, 72], [73, 62], "swing"), ("torso", [12, 42, 72, 80], [40, 80], "breath")],
        3: [("head", [48, 4, 88, 42], [68, 42], "tilt"), ("weapon", [100, 40, 150, 92], [100, 72], "swing"), ("torso", [14, 42, 100, 90], [60, 90], "breath")],
        4: [("head", [58, 2, 118, 52], [86, 52], "tilt"), ("armL", [2, 24, 40, 98], [36, 86], "swayL"), ("armR", [124, 24, 166, 98], [128, 86], "swayR"),
            ("torso", [40, 52, 124, 100], [86, 100], "breath")],
        5: [("head", [60, 14, 108, 58], [84, 58], "tilt"), ("weapon", [118, 0, 178, 92], [124, 90], "swing"), ("torso", [20, 58, 118, 110], [80, 110], "breath")]},
    # 哥布林：頭（大耳朵）、上半身、武器揮動；最終階段座騎狼點頭、搖尾巴
    "goblin": {
        1: [("head", [2, 2, 54, 38], [28, 38], "tilt"), ("weapon", [50, 20, 74, 56], [50, 50], "swing"), ("torso", [8, 38, 50, 62], [28, 62], "breath")],
        2: [("head", [4, 2, 62, 40], [34, 40], "tilt"), ("weapon", [64, 26, 90, 60], [64, 58], "swing"), ("torso", [12, 40, 64, 72], [36, 72], "breath")],
        3: [("head", [10, 2, 64, 42], [36, 42], "tilt"), ("weapon", [66, 44, 94, 78], [66, 64], "swing"), ("torso", [8, 42, 66, 82], [36, 82], "breath")],
        4: [("head", [10, 2, 64, 42], [38, 42], "tilt"), ("weapon", [70, 44, 101, 82], [70, 66], "swing"), ("torso", [8, 42, 70, 86], [36, 86], "breath")],
        5: [("head", [52, 12, 112, 52], [80, 52], "tilt"), ("weapon", [118, 8, 150, 80], [124, 72], "swing"),
            ("beast", [132, 70, 163, 120], [134, 95], "nod"), ("tail", [0, 106, 46, 130], [42, 115], "wag"),
            ("torso", [48, 52, 120, 90], [84, 90], "breath")]},
    # 聖騎士：頭、上半身、劍揮動；第 4 階段披風飄；最終階段熾天使雙翼拍動
    "paladin": {
        1: [("head", [6, 2, 44, 36], [24, 36], "tilt"), ("weapon", [44, 2, 66, 58], [44, 52], "swing"), ("torso", [4, 36, 46, 66], [24, 66], "breath")],
        2: [("head", [24, 2, 54, 36], [38, 36], "tilt"), ("weapon", [58, 12, 86, 60], [58, 56], "swing"), ("torso", [6, 36, 62, 70], [36, 70], "breath")],
        3: [("head", [34, 2, 62, 34], [48, 34], "tilt"), ("weapon", [58, 10, 106, 62], [60, 60], "swing"), ("torso", [16, 34, 72, 70], [44, 70], "breath")],
        4: [("head", [40, 10, 90, 46], [64, 46], "tilt"), ("weapon", [90, 66, 134, 106], [90, 74], "swing"),
            ("cape", [2, 60, 40, 114], [38, 62], "swayL"), ("torso", [38, 46, 95, 78], [64, 78], "breath")],
        5: [("head", [84, 28, 104, 52], [94, 52], "tilt"), ("torso", [72, 52, 118, 85], [95, 85], "breath"),
            ("wingL", [10, 0, 80, 100], [78, 45], "flapL"), ("wingR", [112, 0, 193, 95], [114, 45], "flapR")]},
    # 天海神龍：魚身擺、尾巴搖；龍頭擺、尾巴捲；周圍的雲左右飄（整隻浮空）
    "sea_dragon": {
        1: [("tail", [25, 60, 52, 94], [40, 64], "wag"), ("torso", [15, 8, 86, 60], [50, 60], "breath")],
        2: [("tail", [0, 8, 44, 72], [42, 40], "wag"), ("beast", [86, 8, 123, 72], [88, 40], "nod")],
        3: [("head", [90, 0, 134, 70], [95, 55], "tilt"), ("tail", [96, 80, 134, 132], [100, 95], "wag")],
        4: [("head", [85, 0, 136, 55], [95, 50], "tilt"), ("cloudL", [4, 8, 46, 42], [25, 25], "drift"), ("cloudR", [94, 104, 146, 138], [120, 120], "drift")],
        5: [("head", [70, 8, 130, 74], [100, 72], "tilt"), ("cloudL", [0, 4, 62, 48], [30, 25], "drift"), ("cloudR", [112, 8, 162, 50], [136, 30], "drift")]},
    "fox_spirit": {
        1: [("head", [36, 0, 77, 42], [52, 40], "tilt"), ("tail", [4, 18, 36, 66], [36, 52], "wag")],
        2: [("head", [48, 8, 82, 46], [64, 46], "tilt"), ("tailL", [6, 38, 50, 112], [50, 70], "fanL"), ("torso", [46, 46, 85, 95], [64, 95], "breath")],
        3: [("head", [70, 14, 100, 60], [86, 60], "tilt"), ("tailL", [8, 40, 62, 125], [62, 80], "fanL"),
            ("tailR", [110, 55, 130, 120], [110, 80], "fanR"), ("torso", [62, 60, 112, 118], [86, 118], "breath")],
        4: [("head", [70, 4, 104, 58], [87, 58], "tilt"), ("tailL", [0, 10, 62, 150], [62, 85], "fanL"),
            ("tailR", [112, 10, 161, 150], [112, 85], "fanR"), ("torso", [62, 58, 112, 105], [87, 105], "breath")],
        5: [("head", [66, 0, 100, 58], [83, 58], "tilt"), ("tailL", [0, 15, 64, 150], [64, 90], "fanL"),
            ("tailR", [102, 15, 165, 150], [102, 90], "fanR"), ("torso", [64, 58, 102, 118], [83, 118], "breath")],
    },
}
OVERLAP = 3


def split(species: str, stage: int, parts: list) -> dict:
    src = CHARS / species / f"{stage}.png"
    im = np.array(Image.open(src).convert("RGBA"))
    H, W = im.shape[:2]
    solid = im[..., 3] > 0
    # 跟身體連在一起：最大的幾塊（> 最大塊的 12%）
    lab, n = ndimage.label(solid, structure=np.ones((3, 3)))
    sizes = ndimage.sum(solid, lab, range(1, n + 1)) if n else []
    main = np.zeros_like(solid)
    for i, s in enumerate(sizes, 1):
        if s >= max(sizes) * 0.12:
            main |= lab == i
    taken = np.zeros_like(solid)
    masks = []
    for name, (x0, y0, x1, y1), pivot, motion in parts:
        box = np.zeros_like(solid)
        box[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = True
        m = box & main & ~taken
        taken |= m
        masks.append((name, m, pivot, motion))
    body = solid & ~taken
    # 關節附近多留一點重疊在身體層，部位轉動時才不會露出縫。
    # 部位和部位相接的地方（例如頭和上半身）也要墊，不然兩塊各動各的會露出縫
    keep = body.copy()
    for _, m, _, _ in masks:
        keep |= m & ndimage.binary_dilation(solid & ~m, iterations=OVERLAP)
    keep &= solid
    out_dir = CHARS / species / "rig"
    out_dir.mkdir(exist_ok=True)
    base = im.copy()
    base[~keep] = 0
    Image.fromarray(base).save(out_dir / f"{stage}_base.png", optimize=True)
    layers = []
    for name, m, pivot, motion in masks:
        if not m.any():
            continue
        ys, xs = np.where(m)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        part = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
        part[m[y0:y1, x0:x1]] = im[y0:y1, x0:x1][m[y0:y1, x0:x1]]
        fn = f"{stage}_{name}.png"
        Image.fromarray(part).save(out_dir / fn, optimize=True)
        layers.append({"name": name, "file": f"/characters/{species}/rig/{fn}", "x": int(x0), "y": int(y0),
                       "w": int(x1 - x0), "h": int(y1 - y0), "px": pivot[0], "py": pivot[1], "motion": motion})
    return {"w": W, "h": H, "base": f"/characters/{species}/rig/{stage}_base.png", "parts": layers}


if __name__ == "__main__":
    want = sys.argv[1:] or list(RIGS)
    path = CHARS / "rigs.json"
    rigs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for sp in want:
        rigs[sp] = {str(st): split(sp, st, parts) for st, parts in RIGS[sp].items()}
        print(f"{sp}: {len(rigs[sp])} 個階段，部位 " + "、".join(
            f"{st}:{'/'.join(p['name'] for p in r['parts'])}" for st, r in rigs[sp].items()))
    path.write_text(json.dumps(rigs, ensure_ascii=False, indent=1), encoding="utf-8")
