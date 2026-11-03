# 拼 before/after 对比：同一区域裁剪、等比并排，顶部标注
from PIL import Image, ImageDraw
import sys

pairs = [("n0-life.png", "n11-life.png", "改前", "改后")]
BOX = (300, 240, 900, 760)   # 树所在区域，按 1280x900 的视口坐标写，下面按设备倍率放大
SCALE = 1.0
DPR = 2                      # 截图是 2 倍设备像素

tiles = []
for a, b, la, lb in pairs:
    for f, lab in ((a, la), (b, lb)):
        im = Image.open(f".shots/{f}").convert("RGB").crop(tuple(v * DPR for v in BOX))
        w, h = int(im.width * SCALE), int(im.height * SCALE)
        im = im.resize((w, h), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h + 34), (10, 12, 14))
        canvas.paste(im, (0, 34))
        d = ImageDraw.Draw(canvas)
        d.text((10, 10), lab, fill=(150, 165, 175))
        tiles.append(canvas)

gap = 10
W = sum(t.width for t in tiles) + gap * (len(tiles) - 1)
H = max(t.height for t in tiles)
out = Image.new("RGB", (W, H), (10, 12, 14))
x = 0
for t in tiles:
    out.paste(t, (x, 0))
    x += t.width + gap
out.save(".shots/before-after-2.png")
print("ok", out.size)
