"""벡터 PDF 페이지를 Sheet로 변환."""
import re
import pymupdf

from .model import Text, Sheet

TITLE_RE = re.compile(r"\b(A\d{2})\s*-\s*(\d{3})\b")


def _angle(direction, rot):
    dx, dy = direction
    # 원본 좌표계에서의 글자 방향 각도(시계방향, y-down)
    if abs(dx) >= abs(dy):
        a = 0 if dx > 0 else 180
    else:
        a = 90 if dy > 0 else 270
    a = (a + rot) % 360
    # 화면 기준: 0=가로, 270=아래에서 위로 읽는 세로글자
    return a


def load_page(doc, i, path=""):
    page = doc[i]
    rot = page.rotation
    m = page.rotation_matrix
    r = page.rect  # 회전 반영된 표시 크기
    sheet = Sheet(source=path, index=i + 1, width=r.width, height=r.height)
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            ang = _angle(line["dir"], rot)
            for s in line["spans"]:
                txt = s["text"].strip()
                if not txt:
                    continue
                rect = pymupdf.Rect(s["bbox"]) * m
                rect.normalize()
                sheet.texts.append(Text(rect.x0, rect.y0, rect.x1, rect.y1,
                                        txt, ang, s["size"]))
    sheet.title, sheet.number = guess_title(sheet)
    return sheet


def load_segments(doc, i, sheet):
    page = doc[i]
    m = page.rotation_matrix
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                p0, p1 = it[1] * m, it[2] * m
                sheet.segments.append((p0.x, p0.y, p1.x, p1.y))
            elif it[0] == "re":
                rr = it[1] * m
                rr.normalize()
                sheet.segments += [(rr.x0, rr.y0, rr.x1, rr.y0), (rr.x1, rr.y0, rr.x1, rr.y1),
                                   (rr.x1, rr.y1, rr.x0, rr.y1), (rr.x0, rr.y1, rr.x0, rr.y0)]
    return sheet


def guess_title(sheet):
    """표제란(우하단)에서 도면명과 번호를 찾는다."""
    W, H = sheet.width, sheet.height
    box = [t for t in sheet.texts if t.cx > W * 0.80 and t.cy > H * 0.88]
    joined = " ".join(t.text for t in sorted(box, key=lambda t: (round(t.cy), t.cx)))
    m = TITLE_RE.search(joined.replace(" ", " "))
    number = f"{m.group(1)}-{m.group(2)}" if m else ""
    # 도면명: 표제란에서 가장 큰 글자
    title = ""
    cand = [t for t in box if t.angle == 0 and not TITLE_RE.search(t.text)
            and re.search(r"[가-힣]", t.text)]
    if cand:
        title = max(cand, key=lambda t: t.size).text
    return title, number


def load_pdf(path, pages=None, with_segments=False):
    doc = pymupdf.open(path)
    idx = range(len(doc)) if pages is None else [p - 1 for p in pages]
    out = []
    for i in idx:
        sh = load_page(doc, i, path)
        if with_segments:
            load_segments(doc, i, sh)
        out.append(sh)
    return out
