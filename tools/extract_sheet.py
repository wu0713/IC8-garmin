"""從角色總覽圖（棋盤格背景烤在 JPG 裡）切出每一隻角色、去背，輸出透明 PNG。
用法：python extract.py <out_dir> <preview.png>
"""
import sys, json
from collections import deque
import numpy as np
from PIL import Image
from scipy import ndimage

UP = "/mnt/user-data/uploads/"
SHEETS = [  # 檔名, 是否有左上角中文標題要排除
    ("a866cdf6-image.jpg", True),
    ("65205043-image.jpg", False),
    ("1d35771c-image.jpg", False),
    ("0ee3865b-image.jpg", False),
]
SQ = 12.75
OX, OY = 0.5, 3.0


def bg_levels(g, sat):
    flat = g[sat < 8]
    h, _ = np.histogram(flat, bins=52, range=(0, 260))
    peaks = sorted(np.argsort(h)[::-1][:8])
    # 取兩個最高且相距 > 25 的峰
    order = np.argsort(h)[::-1]
    a = order[0]
    b = next(i for i in order[1:] if abs(i - a) * 5 > 25 and i * 5 > 25)
    return sorted([a * 5 + 2.5, b * 5 + 2.5])


def fit_grid(prof):
    """在梯度剖面上找最吻合的格子大小與偏移。"""
    best = (0, SQ, 0)
    n = len(prof)
    for sq in np.arange(12.55, 13.0, 0.01):
        for off in np.arange(0, sq, 0.25):
            pos = np.round(np.arange(off, n - 1, sq)).astype(int)
            pos = pos[pos < n]
            sc = prof[pos].sum() / len(pos)
            if sc > best[0]:
                best = (sc, sq, off)
    return best[1], best[2]


def background_mask(im):
    global SQ, OX, OY
    rgb = im.astype(float)
    g = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    lv = bg_levels(g, sat)
    H, W = g.shape
    gxp = np.abs(np.diff(g, axis=1)).sum(axis=0)
    gyp = np.abs(np.diff(g, axis=0)).sum(axis=1)
    sqx, offx = fit_grid(gxp)
    sqy, offy = fit_grid(gyp)
    SQ = (sqx + sqy) / 2
    # 邊界在 off+k*SQ 與 off+k*SQ+1 之間 → 方格索引 floor((x - off - 1)/SQ)
    OX, OY = -(offx + 1) % SQ, -(offy + 1) % SQ
    bg = np.zeros((H, W), bool)
    iy_all = np.floor((np.arange(H) + OY) / SQ).astype(int)
    ix_all = np.floor((np.arange(W) + OX) / SQ).astype(int)
    for iy in np.unique(iy_all):
        ys = np.where(iy_all == iy)[0]
        for ix in np.unique(ix_all):
            xs = np.where(ix_all == ix)[0]
            blk = g[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]
            bs = sat[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]
            best = None
            for L in lv:
                close = (np.abs(blk - L) < 13) & (bs < 14)
                frac = close.mean()
                if frac > 0.25 and blk[close].std() < 7 and (best is None or frac > best[1]):
                    best = (L, frac)
            if best:
                L = best[0]
                bg[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1] = (np.abs(blk - L) < 15) & (bs < 16)
    # 方格之間 1–2 px 的漸層接縫：落在格線上、低彩度、灰階介於兩色之間 → 也是背景
    gx = np.abs(((np.arange(W) + OX) % SQ + SQ / 2) % SQ - SQ / 2) < 2.1
    gy = np.abs(((np.arange(H) + OY) % SQ + SQ / 2) % SQ - SQ / 2) < 2.1
    grid = gx[None, :] | gy[:, None]
    seam = grid & (sat < 16) & (g > lv[0] - 14) & (g < lv[1] + 14)
    bg |= seam
    return bg, lv


def label_rows(im):
    white = im.min(axis=2) > 232
    rows = white.sum(axis=1)
    ys = [y for y in range(len(rows)) if rows[y] > 45]
    bands = []
    for y in ys:
        if bands and y - bands[-1][1] <= 8:
            bands[-1][1] = y
        else:
            bands.append([y, y])
    return [(a - 6, b + 6) for a, b in bands if b - a >= 3]


LABELS = [(163, 187), (347, 372), (545, 569)]
HEADERS = [(0, 0, 175, 52), (0, 188, 175, 246), (0, 378, 195, 432)]
BOUNDS = [190, 400, 610, 818]


def process(path, has_headers):
    im = np.array(Image.open(path).convert("RGB"))
    H, W, _ = im.shape
    bg, lv = background_mask(im)
    for a, b in LABELS:
        bg[a:b] = True
    if has_headers:
        for x0, y0, x1, y1 in HEADERS:
            bg[y0:y1, x0:x1] = True
    lab, n = ndimage.label(bg)
    ids = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    for a, b in LABELS:
        ids |= set(np.unique(lab[a:b]))
    ids.discard(0)
    keep = ~np.isin(lab, list(ids))
    # 角色圍起來的縫隙：同時含有兩種棋盤灰的封閉區塊也是背景
    g = im.astype(float).mean(axis=2)
    area_sq = SQ * SQ
    for i in range(1, n + 1):
        if i in ids:
            continue
        m = lab == i
        cnt = m.sum()
        if cnt < 0.5 * area_sq:
            continue
        vals = g[m]
        a_ = (np.abs(vals - lv[0]) < 12).sum()
        b_ = (np.abs(vals - lv[1]) < 12).sum()
        if a_ > 0.3 * area_sq and b_ > 0.3 * area_sq:
            keep[m] = False
    out = []
    prev = 0
    for a, b in LABELS:
        band = keep[prev:a]
        grp, ng = ndimage.label(ndimage.binary_dilation(band, iterations=2))
        cols = [np.zeros_like(band) for _ in range(5)]
        xs_idx = np.arange(W)[None, :]
        for i in range(1, ng + 1):
            m = (grp == i) & band
            area = m.sum()
            if area < 20:
                continue
            ys_, xs_ = np.where(m)
            cs = [sum(1 for bd in BOUNDS if x >= bd) for x in (xs_.min(), xs_.max())]
            if cs[0] != cs[1]:
                parts = []
                for c in range(cs[0], cs[1] + 1):
                    lo = BOUNDS[c - 1] if c > 0 else 0
                    hi = BOUNDS[c] if c < 4 else W
                    parts.append((c, m & (xs_idx >= lo) & (xs_idx < hi)))
                big = [(c, pm) for c, pm in parts if pm.sum() > 0.3 * area]
                if len(big) >= 2:   # 兩隻角色黏在一起 → 依欄位切開
                    for c, pm in parts:
                        cols[c] |= pm
                else:               # 一隻角色的武器跨過去 → 整塊歸給主要那欄
                    c = max(parts, key=lambda t: t[1].sum())[0]
                    cols[c] |= m
            else:
                cols[cs[0]] |= m
        for c in range(5):
            m = cols[c]
            if m.sum() < 40:
                out.append(None)
                continue
            # 去掉零碎殘塊：只留主體附近或夠大的塊
            l2, n2 = ndimage.label(ndimage.binary_dilation(m, iterations=1))
            sizes = ndimage.sum(m, l2, range(1, n2 + 1))
            big = int(np.argmax(sizes)) + 1
            near = ndimage.binary_dilation(l2 == big, iterations=10)
            good = np.zeros_like(m)
            for i in range(1, n2 + 1):
                part = (l2 == i) & m
                if i == big or sizes[i - 1] >= 0.06 * sizes[big - 1] or (part & near).any():
                    good |= part
            ys_, xs_ = np.where(good)
            y0, y1, x0, x1 = ys_.min(), ys_.max() + 1, xs_.min(), xs_.max() + 1
            rgba = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
            rgba[..., :3] = im[prev + y0:prev + y1, x0:x1]
            rgba[..., 3] = (good[y0:y1, x0:x1] * 255).astype(np.uint8)
            out.append(Image.fromarray(rgba, "RGBA"))
        prev = b
    return out


if __name__ == "__main__":
    outdir = sys.argv[1]
    import os
    os.makedirs(outdir, exist_ok=True)
    allimgs = []
    for si, (f, hh) in enumerate(SHEETS):
        imgs = process(UP + f, hh)
        for k, img in enumerate(imgs):
            line, stage = si * 3 + k // 5, k % 5
            if img is not None:
                img.save(f"{outdir}/l{line:02d}_s{stage}.png")
            allimgs.append((line, stage, img))
    # 預覽：放在彩色底上檢查去背
    cell = 170
    sheet = Image.new("RGB", (5 * cell, 12 * cell), "#F3E6C9")
    for line, stage, img in allimgs:
        if img is None:
            continue
        s = min(1.0, (cell - 10) / max(img.size))
        im2 = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.NEAREST)
        sheet.paste(im2, (stage * cell + (cell - im2.width) // 2, line * cell + cell - im2.height - 5), im2)
    sheet.save(sys.argv[2])
    print("done", sum(1 for *_, i in allimgs if i is not None), "sprites")
