"""Sheet 일부를 PNG로 그려보기 (검토·디버깅용)."""
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def render(sheet, box, out, px=1800):
    x0, y0, x1, y1 = box
    s = px / max(x1 - x0, 1e-9)
    W, H = px, int((y1 - y0) * s) + 1
    img = Image.new("RGB", (W, max(H, 1)), "white")
    d = ImageDraw.Draw(img)
    for a, b, c, e in sheet.segments:
        if max(a, c) < x0 or min(a, c) > x1 or max(b, e) < y0 or min(b, e) > y1:
            continue
        d.line([((a - x0) * s, (b - y0) * s), ((c - x0) * s, (e - y0) * s)], fill=(150, 150, 150))
    fonts = {}
    for t in sheet.texts:
        if not (x0 <= t.cx <= x1 and y0 <= t.cy <= y1):
            continue
        h = max(int(min(t.y1 - t.y0, t.x1 - t.x0) * s * 0.9), 6)
        f = fonts.get(h) or fonts.setdefault(h, ImageFont.truetype(FONT, h))
        if t.angle == 0:
            d.text(((t.x0 - x0) * s, (t.y0 - y0) * s), t.text, fill=(0, 0, 200), font=f)
        else:
            tw = int(f.getlength(t.text)) + 2
            tmp = Image.new("RGBA", (tw, h + 4), (255, 255, 255, 0))
            ImageDraw.Draw(tmp).text((0, 0), t.text, fill=(200, 0, 0), font=f)
            tmp = tmp.rotate(90 if t.angle == 270 else -90, expand=True)
            img.paste(tmp, (int((t.x0 - x0) * s), int((t.y0 - y0) * s)), tmp)
    img.save(out)
    return out
