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
    # 關節附近多留一點重疊在身體層，部位轉動時才不會露出縫
    near = ndimage.binary_dilation(body, iterations=OVERLAP)
    keep = solid & (body | (taken & near))
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
