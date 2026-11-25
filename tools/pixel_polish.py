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


def drop_tint(a, mask, protect=(), max_sat=70, vmin=70, vmax=222, hue=None):
    """光芒裡帶顏色的棋盤格：保護角色本體的範圍，範圍外「不太鮮豔、不太亮」的像素挖掉。
    很亮的白（翅膀、光環）、很暗的描邊、鮮豔的寶石保留。hue 可以再限制色相範圍。"""
    hsv = np.array(Image.fromarray(a[..., :3]).convert("HSV")).astype(int)
    h = hsv[..., 0] * 360 // 256
    sel = mask & (hsv[..., 1] <= max_sat) & (hsv[..., 2] >= vmin) & (hsv[..., 2] <= vmax)
    if hue:
        lo, hi = hue
        sel &= ((h >= lo) | (h < hi)) if lo > hi else ((h >= lo) & (h < hi))
    for x0, y0, x1, y1 in protect:
        sel[y0:y1, x0:x1] = False
    return mask & ~sel


def smooth_tint(a, mask, protect=(), max_sat=90, vmin=70, vmax=228, hue=None, sigma=5.0, lift=1.0, boxes=None,
                glow=None, mix=0.0, alpha=255, min_sat=0):
    """光芒和棋盤格交錯得太密、挖不乾淨時：不挖，改成把那一片「抹平」成柔和的光（棋盤格紋路消失，形狀保留）。"""
    hsv = np.array(Image.fromarray(a[..., :3]).convert("HSV")).astype(int)
    h = hsv[..., 0] * 360 // 256
    sel = mask & (hsv[..., 1] <= max_sat) & (hsv[..., 1] >= min_sat) & (hsv[..., 2] >= vmin) & (hsv[..., 2] <= vmax)
    if hue:
        lo, hi = hue
        sel &= ((h >= lo) | (h < hi)) if lo > hi else ((h >= lo) & (h < hi))
    if boxes:
        keep = np.zeros_like(sel)
        for x0, y0, x1, y1 in boxes:
            keep[y0:y1, x0:x1] = True
        sel &= keep
    for x0, y0, x1, y1 in protect:
        sel[y0:y1, x0:x1] = False
    w = ndimage.gaussian_filter(sel.astype(float), sigma)
    out = a.copy()
    for c in range(3):
        sm = ndimage.gaussian_filter(a[..., c].astype(float) * sel, sigma) / np.maximum(w, 1e-6)
        if glow:                                   # 再往發光的顏色靠（例如金白色）
            sm = sm * (1 - mix) + glow[c] * mix
        out[..., c] = np.where(sel, np.clip(sm * lift, 0, 255), a[..., c])
    return out, sel, alpha


def drop_outside(a, mask, dark=80, grow=1, max_sat=255, protect=(), boxes=None):
    """從圖片外面往內淹：碰到深色描邊就停。淹得到、又不鮮豔的像素就是背景殘渣（光芒裡的棋盤格），挖掉。
    角色有完整描邊的部位淹不進去，所以不會被挖。"""
    hsv = np.array(Image.fromarray(a[..., :3]).convert("HSV")).astype(int)
    wall = mask & (hsv[..., 2] < dark)
    wall = ndimage.binary_dilation(wall, CROSS, iterations=grow) & mask
    open_ = ~wall
    for x0, y0, x1, y1 in protect:
        open_[y0:y1, x0:x1] = False
    lab, _ = ndimage.label(open_, structure=CROSS)
    H, W = mask.shape
    seeds = set(np.unique(lab[~mask])) | set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    seeds.discard(0)
    reach = np.isin(lab, list(seeds)) & mask & (hsv[..., 1] <= max_sat)
    if boxes:
        keep = np.zeros_like(reach)
        for x0, y0, x1, y1 in boxes:
            keep[y0:y1, x0:x1] = True
        reach &= keep
    return mask & ~reach


def colorize(img, hue, sat=90, max_sat=70, darken=1.0):
    """把灰黑的盔甲染色（例如黑甲染成暗紅甲）：不鮮豔的像素換成指定色相，亮度不變；原本就鮮豔的火光、寶石不動。"""
    hsv = np.array(img.convert("HSV")).astype(int)
    sel = hsv[..., 1] <= max_sat
    hsv[..., 0] = np.where(sel, hue * 256 // 360, hsv[..., 0])
    hsv[..., 1] = np.where(sel, np.maximum(hsv[..., 1], sat), hsv[..., 1])
    hsv[..., 2] = np.where(sel, (hsv[..., 2] * darken).clip(0, 255), hsv[..., 2])
    return Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")


def drop_grey_blobs(a, mask, grey_ratio=0.7, fill=0.6):
    """飄在角色旁邊的灰色方塊（棋盤格碎片）：不是最大塊、幾乎都是灰色、形狀方方正正的，就挖掉。"""
    rgb = a[..., :3].astype(int)
    grey = (rgb.max(-1) - rgb.min(-1)) < 24
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    if n < 2:
        return mask
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    big = int(np.argmax(sizes)) + 1
    for i, box in enumerate(ndimage.find_objects(lab), 1):
        if i == big or box is None:
            continue
        comp = lab[box] == i
        area = comp.sum()
        if area > sizes[big - 1] * 0.12:
            continue
        if grey[box][comp].mean() >= grey_ratio and area / comp.size >= fill:
            mask[box] &= ~comp
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


def polish(path, colors=96, sat=1.12, contrast=1.06, holes=False, hue=None, debris=False, aura=None, min_bit=8, tint=None, smooth=None, outside=None, recolor=None, peel=8, blobs=False):
    im = Image.open(path).convert("RGBA")
    a = np.array(im)
    mask = a[..., 3] > 0
    soft = []                                      # 抹平後要半透明的區域
    if smooth:
        for sm in (smooth if isinstance(smooth, list) else [smooth]):
            a, sel, al = smooth_tint(a, mask, **sm)
            if al < 255:
                soft.append((sel, al))
    if aura:
        mask = drop_aura(a, mask, **aura)
    if tint:
        mask = drop_tint(a, mask, **tint)
    if outside:
        mask = drop_outside(a, mask, **outside)
    if peel:                                       # 劍刃、鎧甲是淺灰色的角色要少剝或不剝
        mask = peel_grey(a, mask, passes=peel)
    if holes:
        mask = drop_grey_holes(a, mask)
    if debris:
        mask = drop_debris(a, mask)
    if blobs:
        mask = drop_grey_blobs(a, mask)
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
    if recolor:
        img = colorize(img, **recolor)
    arr = np.array(img)
    fill = arr[mask]
    arr[~mask] = np.median(fill, axis=0).astype(np.uint8) if len(fill) else 0
    q = np.array(Image.fromarray(arr).quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).convert("RGB"))
    alpha = np.where(mask, 255, 0).astype(np.uint8)
    for sel, al in soft:
        alpha[sel & mask] = al
        body &= ~sel                               # 半透明的光不加墨線
    out = np.dstack([q, alpha])
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
    # 精靈使：第 3、4 階段的精靈旁邊夾著棋盤格（保護人和精靈，範圍外的暗灰格挖掉）；
    # 第 5 階段的光芒和棋盤格交錯太密 → 換新外觀：抹平成半透明的金白柔光，翅膀和光環的亮線保留
    "spirit": {"sat": 1.15, "contrast": 1.08, "debris": True, "min_bit": 24, "holes": True, "stages": {
        3: {"tint": {"max_sat": 80, "vmin": 40, "vmax": 215, "protect": [(38, 35, 86, 162), (0, 28, 22, 105), (98, 28, 128, 74)]}},
        4: {"tint": {"hue": (0, 70), "max_sat": 95, "vmin": 40, "vmax": 205, "protect": [(52, 28, 122, 155), (30, 18, 62, 150)]}},
        5: {"holes": False, "smooth": {"protect": [(86, 44, 104, 70), (66, 72, 120, 160)], "max_sat": 95, "vmax": 224, "sigma": 3,
                                       "glow": (255, 236, 190), "mix": 0.55, "alpha": 120}}}},
    # 死靈法師：第 3 階段紫水晶裡的棋盤格、第 5 階段紫色煙霧裡的棋盤格 → 抹平成半透明紫色靈氣（白色幽靈保留）
    "necromancer": {"sat": 1.1, "contrast": 1.1, "debris": True, "min_bit": 24, "holes": True, "stages": {
        3: {"smooth": [{"hue": (225, 320), "max_sat": 200, "vmin": 50, "vmax": 225, "boxes": [(98, 2, 131, 48)], "sigma": 2,
                        "glow": (150, 90, 230), "mix": 0.3, "alpha": 190},
                       {"max_sat": 45, "vmin": 80, "vmax": 220, "boxes": [(98, 2, 131, 48)], "sigma": 2,
                        "glow": (150, 90, 230), "mix": 0.5, "alpha": 150}]},
        5: {"holes": False, "smooth": [
            {"hue": (225, 320), "min_sat": 38, "vmin": 35, "vmax": 228, "sigma": 4, "glow": (140, 85, 220), "mix": 0.4, "alpha": 150},
            {"max_sat": 45, "vmin": 80, "vmax": 215, "protect": [(62, 20, 132, 173), (28, 5, 58, 173), (125, 88, 196, 173)],
             "sigma": 3, "glow": (150, 90, 230), "mix": 0.5, "alpha": 130}]}}},
    # 魔像：第 5 階段綠色光芒裡的棋盤格 → 身體描邊清楚，直接挖乾淨（保護鬍子），光芒改用程式畫
    "golem": {"sat": 1.15, "contrast": 1.08, "debris": True, "min_bit": 24, "holes": True, "stages": {
        5: {"tint": {"hue": (80, 190), "max_sat": 95, "vmin": 85, "vmax": 240, "protect": [(68, 50, 102, 100)]}}}},
    # 惡魔：第 3 階段整片紅色棋盤格蓋在身上、修不回來 → 換新外觀：用第 4 階段的騎士，黑甲染成暗紅甲（地獄領主）
    "demon": {"sat": 1.12, "contrast": 1.1, "debris": True, "min_bit": 24, "holes": True, "stages": {
        3: {"source": 4, "contrast": 1.12, "recolor": {"hue": 354, "sat": 165, "max_sat": 70, "darken": 0.82}}}},
    # 獸人、哥布林、聖騎士：劍、斧、鎧甲是淺灰色，不能挖灰色破洞也不能多剝 → 只剝 1 層，飄在旁邊的灰色方塊另外清
    "orc": {"sat": 1.12, "contrast": 1.08, "min_bit": 30, "peel": 1, "blobs": True},
    "goblin": {"sat": 1.12, "contrast": 1.08, "min_bit": 30, "peel": 1, "blobs": True},
    "paladin": {"sat": 1.12, "contrast": 1.08, "min_bit": 30, "peel": 1, "blobs": True},
    # 天海神龍：雲是灰白色，只剝 2 層灰邊；火龍：沒有灰色部位，照一般方式剝
    "sea_dragon": {"sat": 1.15, "contrast": 1.08, "min_bit": 30, "peel": 2, "blobs": True},
    "flame_dragon": {"sat": 1.15, "contrast": 1.08, "min_bit": 30, "blobs": True},
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
            if "source" in tune:                             # 換新外觀：借用別的階段的原圖（再染色）
                src_png = orig / sp / f"{tune.pop('source')}.png"
            out = polish(src_png, **tune)
            out.save(chars / sp / f"{i}.png", optimize=True)
            st["w"], st["h"] = out.width, out.height
        print(f"{sp}: 完成 {len(entry['stages'])} 張")
    (chars / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
