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


def drop_debris(a, mask, passes=3):
    """原圖棋盤格留下的 1–2px 灰色細線：只挖「又細又灰」的像素，粗的灰色部位（盔甲、狼毛）不動。"""
    rgb = a[..., :3].astype(int)
    spread = rgb.max(-1) - rgb.min(-1)
    val = rgb.max(-1)
    grey = (spread < 30) & (val > 55) & (val < 235)
    for _ in range(passes):
        thick = ndimage.binary_opening(mask, np.ones((3, 3)))
        kill = mask & ~thick & grey
        if not kill.any():
            break
        mask = mask & ~kill
    return mask


def drop_aura(a, mask, hue=(190, 300), min_sat=12, grow=3, boxes=None):
    """挖掉整片帶棋盤格的光芒（例如紫色星空）：先找這段色相，再把夾在裡面、旁邊的灰格一起挖掉。光芒改用程式畫。"""
    hsv = np.array(Image.fromarray(a[..., :3]).convert("HSV")).astype(int)
    h = hsv[..., 0] * 360 // 256
    rgb = a[..., :3].astype(int)
    spread = rgb.max(-1) - rgb.min(-1)
    val = rgb.max(-1)
    aura = mask & (h >= hue[0]) & (h < hue[1]) & (hsv[..., 1] >= min_sat) & (val > 60)
    zone = ndimage.binary_dilation(aura, np.ones((3, 3)), iterations=grow)
    grey = mask & zone & (spread < 34) & (val > 90)
    if boxes:                                   # 只在光芒所在的範圍內挖，角色本身（狼臉、法杖）不碰
        keep = np.zeros_like(mask)
        for x0, y0, x1, y1 in boxes:
            keep[y0:y1, x0:x1] = True
        aura &= keep; grey &= keep
    return mask & ~aura & ~grey


def shift_hue(img, lo, hi, by, darken=1.0, min_sat=40):
    """把某段色相（0–360 度）整段轉到別的顏色，例如粉紅轉成紫紅；金髮、膚色不在範圍內就不動。"""
    hsv = np.array(img.convert("HSV")).astype(int)
    h = hsv[..., 0] * 360 // 256
    sel = ((h >= lo) | (h < hi)) if lo > hi else ((h >= lo) & (h < hi))
    sel &= hsv[..., 1] >= min_sat
    hsv[..., 0] = np.where(sel, ((h + by) % 360) * 256 // 360, hsv[..., 0])
    hsv[..., 2] = np.where(sel, (hsv[..., 2] * darken).clip(0, 255), hsv[..., 2])
    return Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")


def polish(path, colors=96, sat=1.12, contrast=1.06, holes=False, hue=None, debris=False, aura=None, min_bit=8):
    im = Image.open(path).convert("RGBA")
    a = np.array(im)
    mask = a[..., 3] > 0
    if aura:
        mask = drop_aura(a, mask, **aura)
    mask = peel_grey(a, mask)
    if holes:
        mask = drop_grey_holes(a, mask)
    if debris:
        mask = drop_debris(a, mask)
    lab, n = ndimage.label(mask)
    if n:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        for i, s in enumerate(sizes, 1):
            if s < min_bit:
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
    # 馴獸師：原圖滿是棋盤格細線殘渣；第 5 階段背後的紫色星空混了棋盤格，整片挖掉改用程式畫光暈
    "beast_tamer": {"sat": 1.15, "contrast": 1.08, "debris": True, "min_bit": 24,
                    "stages": {5: {"aura": {"hue": (150, 300), "boxes": [(0, 45, 51, 122), (51, 34, 57, 94), (118, 12, 200, 62), (122, 62, 192, 74), (88, 14, 118, 30)]}}}},
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
            tune = dict(TUNE.get(sp, {}))
            tune.update(tune.pop("stages", {}).get(i, {}))   # 個別階段的設定
            out = polish(src_png, **tune)
            out.save(chars / sp / f"{i}.png", optimize=True)
            st["w"], st["h"] = out.width, out.height
        print(f"{sp}: 完成 {len(entry['stages'])} 張")
    (chars / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
