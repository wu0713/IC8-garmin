"""像素角色整理 v3：剝掉切圖時帶進來的灰邊 → 銳利化、提色 → 減色 → 身體加 1px 墨線（飄在旁邊的特效不加）。"""
from PIL import Image, ImageFilter, ImageEnhance
import numpy as np
from scipy import ndimage

INK = (27, 30, 60)
CROSS = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])


def peel_grey(a, mask, passes=8):
    """外圈的中性灰（切圖殘渣）一層一層剝掉；很亮的白（毛、衣服）和很暗的原描邊保留。"""
    rgb = a[..., :3].astype(int)
    spread = rgb.max(-1) - rgb.min(-1)
    val = rgb.max(-1)
    greyish = (spread < 24) & (val > 140) & (val < 232) | (spread < 14) & (val > 70) & (val <= 140)
    for _ in range(passes):
        rim = mask & ~ndimage.binary_erosion(mask, CROSS)
        kill = rim & greyish
        if not kill.any():
            break
        mask = mask & ~kill
    return mask


def drop_grey_holes(a, mask, min_size=10):
    """被翅膀、魔力圈包在裡面的大片中性灰（原背景的棋盤格）挖成透明。只給本身沒有灰色部位的角色用。"""
    rgb = a[..., :3].astype(int)
    spread = rgb.max(-1) - rgb.min(-1)
    val = rgb.max(-1)
    grey = mask & (spread < 22) & (val > 150) & (val < 225)
    lab, n = ndimage.label(grey, structure=np.ones((3, 3)))
    if n:
        sizes = ndimage.sum(grey, lab, range(1, n + 1))
        for i, sz in enumerate(sizes, 1):
            if sz >= min_size:
                mask = mask & ~(lab == i)
    return mask


def shift_hue(img, lo, hi, by, darken=1.0, min_sat=40):
    """把某段色相（0–360 度）整段轉到別的顏色，例如粉紅轉成紫紅；金髮、膚色不在範圍內就不動。"""
    hsv = np.array(img.convert("HSV")).astype(int)
    h = hsv[..., 0] * 360 // 256
    sel = ((h >= lo) | (h < hi)) if lo > hi else ((h >= lo) & (h < hi))
    sel &= hsv[..., 1] >= min_sat
    hsv[..., 0] = np.where(sel, ((h + by) % 360) * 256 // 360, hsv[..., 0])
    hsv[..., 2] = np.where(sel, (hsv[..., 2] * darken).clip(0, 255), hsv[..., 2])
    return Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")


def polish(path, colors=96, sat=1.12, contrast=1.06, holes=False, hue=None):
    im = Image.open(path).convert("RGBA")
    a = np.array(im)
    mask = a[..., 3] > 0
    mask = peel_grey(a, mask)
    if holes:
        mask = drop_grey_holes(a, mask)
    lab, n = ndimage.label(mask)
    if n:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        for i, s in enumerate(sizes, 1):
            if s < 8:
                mask[lab == i] = False
        lab, n = ndimage.label(mask)
    sizes = ndimage.sum(mask, lab, range(1, n + 1)) if n else []
    big = max(sizes) if n else 0
    body = np.zeros_like(mask)
    for i, s in enumerate(sizes, 1):
        if s >= big * 0.12:
            body |= lab == i
    img = Image.fromarray(a[..., :3], "RGB").filter(ImageFilter.UnsharpMask(radius=1.0, percent=60, threshold=2))
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(sat)).enhance(contrast)
    for hs in hue or []:
        img = shift_hue(img, *hs)
    arr = np.array(img)
    fill = arr[mask]
    arr[~mask] = np.median(fill, axis=0).astype(np.uint8) if len(fill) else 0
    q = np.array(Image.fromarray(arr).quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).convert("RGB"))
    out = np.dstack([q, np.where(mask, 255, 0).astype(np.uint8)])
    H, W = mask.shape
    pad = np.zeros((H + 2, W + 2, 4), np.uint8)
    pad[1:-1, 1:-1] = out
    b2 = np.zeros((H + 2, W + 2), bool); b2[1:-1, 1:-1] = body
    m2 = np.zeros((H + 2, W + 2), bool); m2[1:-1, 1:-1] = mask
    edge = ndimage.binary_dilation(b2, CROSS) & ~m2
    pad[edge] = (*INK, 255)
    return Image.fromarray(pad)


# 每個種族可以個別調整：sat 彩度、contrast 對比、colors 顏色數
TUNE = {
    "fox_spirit": {"sat": 1.25, "contrast": 1.08},   # 和服的紅、紫、金更華麗
    # 魅魔：跟九尾狐的粉紅分開 → 粉紅轉成深紫紅、整體暗一點；翅膀和魔力圈裡夾著背景棋盤格
    "succubus": {"sat": 1.2, "contrast": 1.12, "holes": True, "hue": [(296, 8, -30, 0.84)]},
}


# ───────── 指令列：python tools/pixel_polish.py fox_spirit [golem …] ─────────
if __name__ == "__main__":
    import json, shutil, sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    chars = root / "web" / "characters"
    orig = root / "tools" / "characters_orig"
    manifest = json.loads((chars / "manifest.json").read_text(encoding="utf-8"))
    for sp in sys.argv[1:]:
        (orig / sp).mkdir(parents=True, exist_ok=True)
        entry = next(s for s in manifest["species"] if s["id"] == sp)
        for i, st in enumerate(entry["stages"], 1):
            src_png = orig / sp / f"{i}.png"
            if not src_png.exists():                       # 第一次：先把原圖收好，之後都從原圖重做
                shutil.copy2(chars / sp / f"{i}.png", src_png)
            out = polish(src_png, **TUNE.get(sp, {}))
            out.save(chars / sp / f"{i}.png", optimize=True)
            st["w"], st["h"] = out.width, out.height
        print(f"{sp}: 完成 {len(entry['stages'])} 張")
    (chars / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
