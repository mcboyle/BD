#!/usr/bin/env python3
"""Tile every nav tab / drill-in / in-page subtab into contact sheets, per theme.
Each cell = a 380px-wide top-crop thumbnail + a label bar. Output 4 PNGs:
  montage_{light,dark}_navtabs.png  and  montage_{light,dark}_subtabs.png
"""
import os, json
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
CAP = os.environ.get("BD_CAPTURE_DIR", os.path.join(ROOT, "reports", "capture"))
OUT = "/mnt/user-data/outputs"
man = json.load(open(f"{CAP}/manifest.json"))

CW, CH = 380, 300          # cell thumbnail w/h (top-crop)
LBL = 30                   # label bar height
PAD = 14
COLS = 4
F  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
FS = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
FT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)

TH = {"light": {"bg": (244,245,247), "card": (255,255,255), "ink": (28,32,36),
                "ink3": (107,114,128), "line": (227,230,234), "accent": (91,91,214)},
      "dark":  {"bg": (21,23,28), "card": (29,32,38), "ink": (232,234,237),
                "ink3": (135,142,152), "line": (45,50,58), "accent": (139,139,240)}}

def thumb(relpath, theme):
    p = os.path.join(CAP, relpath)
    if not os.path.exists(p):
        return None
    im = Image.open(p).convert("RGB")
    w = CW
    im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    im = im.crop((0, 0, CW, min(CH, im.height)))
    if im.height < CH:  # pad short ones with the PAGE bg (what the live page shows), not white
        bg = Image.new("RGB", (CW, CH), TH[theme]["bg"])
        bg.paste(im, (0,0)); im = bg
    return im

def sheet(entries, theme, title, fname):
    t = TH[theme]
    rows = (len(entries) + COLS - 1) // COLS
    cellW, cellH = CW + PAD, CH + LBL + PAD
    W = COLS * cellW + PAD
    TITLE_H = 64
    H = TITLE_H + rows * cellH + PAD
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    d.text((PAD+6, 20), title, font=FT, fill=t["ink"])
    d.text((W - 300, 30), f"v3.66.363 · {theme}", font=FS, fill=t["ink3"])
    for idx, (route, label, th_file) in enumerate(entries):
        r, c = divmod(idx, COLS)
        x = PAD + c * cellW
        y = TITLE_H + r * cellH
        # card
        d.rounded_rectangle([x, y, x+CW, y+LBL+CH], radius=10, fill=t["card"], outline=t["line"], width=1)
        # label bar
        d.text((x+10, y+7), label[:38], font=F, fill=t["ink"])
        rt = route if route.startswith("/") else ""
        if rt:
            tw = d.textlength(rt, font=FS)
            d.text((x+CW-tw-10, y+9), rt, font=FS, fill=t["accent"])
        # thumb
        im = thumb(th_file, theme)
        if im:
            img.paste(im, (x, y+LBL))
            d.rectangle([x, y+LBL, x+CW, y+LBL+CH], outline=t["line"], width=1)
        else:
            d.text((x+12, y+LBL+12), "(no capture)", font=FS, fill=t["ink3"])
    path = f"{OUT}/{fname}"
    img.save(path, optimize=True)
    print(f"  {fname}: {len(entries)} cells  {W}x{H}")
    return path

# group entries
def collect(cat, theme):
    out = []
    for m in man:
        if m["cat"] == cat and m["theme"] == theme and m.get("file"):
            out.append((m["route"], m["label"], m["file"]))
    return out

paths = []
for theme in ("light", "dark"):
    nav = collect("nav", theme)
    sub = collect("drillin", theme) + collect("subtab", theme)
    paths.append(sheet(nav, theme, f"Nav tabs — {len(nav)} routes", f"montage_{theme}_navtabs.png"))
    paths.append(sheet(sub, theme, f"Site drill-ins + in-page subtabs — {len(sub)} views", f"montage_{theme}_subtabs.png"))

print("PATHS:", "|".join(paths))
