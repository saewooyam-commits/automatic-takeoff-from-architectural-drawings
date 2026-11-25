"""DXF(DWG 변환본)를 Sheet로 변환.

- 모델공간의 TEXT/MTEXT/DIMENSION 글자와 블록(INSERT) 안의 글자·속성을 모두 펼친다.
- CAD 좌표(y 위쪽 증가)를 화면 좌표(y 아래쪽 증가)로 뒤집는다.
- 한 파일에 여러 도면이 타일처럼 그려진 경우가 대부분이라, 도곽(테두리) 단위로 나누는
  split_frames()를 제공한다.
"""
import math
import re

from .model import Sheet, Text

CJK = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣一-鿿]")
MTEXT_CODES = re.compile(r"\\[A-Za-z][^;\\]*;|\\[PpNn~]|[{}]|\\[LlOoKk]")


def _text_width(s, h):
    w = 0.0
    for ch in s:
        w += h * (1.0 if CJK.match(ch) else 0.62)
    return w


def _angle_from_rotation(rot):
    """CAD 회전각(반시계, 도) → 화면 기준 각도 규칙(0=가로, 270=아래→위)."""
    r = round((rot or 0) % 360 / 90) % 4
    return {0: 0, 1: 270, 2: 0, 3: 90}[r]


def _bbox(x, y, w, h, rot, halign="left", valign="baseline"):
    """삽입점·정렬·회전으로 글자 외곽(화면좌표) 근사."""
    dx = {"left": 0, "center": -w / 2, "right": -w}.get(halign, 0)
    dy = {"baseline": 0, "bottom": 0, "middle": -h / 2, "top": -h}.get(valign, 0)
    pts = [(dx, dy), (dx + w, dy), (dx + w, dy + h), (dx, dy + h)]
    c, s = math.cos(math.radians(rot or 0)), math.sin(math.radians(rot or 0))
    xs, ys = [], []
    for px, py in pts:
        xs.append(x + px * c - py * s)
        ys.append(-(y + px * s + py * c))      # y 뒤집기
    return min(xs), min(ys), max(xs), max(ys)


H_ALIGN = {0: "left", 1: "center", 2: "right", 3: "left", 4: "center", 5: "left"}
V_ALIGN = {0: "baseline", 1: "bottom", 2: "middle", 3: "top"}
MT_ATTACH = {  # MTEXT attachment_point → (h, v)
    1: ("left", "top"), 2: ("center", "top"), 3: ("right", "top"),
    4: ("left", "middle"), 5: ("center", "middle"), 6: ("right", "middle"),
    7: ("left", "bottom"), 8: ("center", "bottom"), 9: ("right", "bottom"),
}


def _clean(s):
    s = MTEXT_CODES.sub("", s or "")
    s = s.replace("%%c", "Ø").replace("%%C", "Ø").replace("%%d", "°").replace("%%p", "±")
    return s.strip()


SKIPPED = {"text": 0, "dim": 0, "insert": 0}   # 읽다 실패해 건너뛴 객체 수 (load_dxf가 Sheet.warnings에 기록)


def _emit_text(e, out):
    t = e.dxftype()
    try:
        if t in ("TEXT", "ATTRIB"):
            s = _clean(e.dxf.text)
            if not s:
                return
            h = e.dxf.height or 1
            ha, va = H_ALIGN.get(e.dxf.get("halign", 0), "left"), V_ALIGN.get(e.dxf.get("valign", 0), "baseline")
            p = e.dxf.align_point if (ha != "left" or va != "baseline") and e.dxf.hasattr("align_point") else e.dxf.insert
            rot = e.dxf.get("rotation", 0)
            w = _text_width(s, h) * (e.dxf.get("width", 1) or 1)
            out.append(Text(*_bbox(p[0], p[1], w, h, rot, ha, va), s, _angle_from_rotation(rot), h))
        elif t == "MTEXT":
            raw = e.plain_text() if hasattr(e, "plain_text") else e.text
            lines = [ln.strip() for ln in re.split(r"\n|\\P", raw) if ln.strip()]
            if not lines:
                return
            h = e.dxf.get("char_height", 1) or 1
            rot = e.dxf.get("rotation", 0)
            if e.dxf.hasattr("text_direction"):
                d = e.dxf.text_direction
                rot = math.degrees(math.atan2(d[1], d[0]))
            ha, va = MT_ATTACH.get(e.dxf.get("attachment_point", 1), ("left", "top"))
            x, y = e.dxf.insert[0], e.dxf.insert[1]
            n = len(lines)
            step = h * 1.67
            # 여러 줄 MTEXT는 줄마다 따로 만든다
            top_off = {"top": 0, "middle": (n - 1) * step / 2 + h / 2, "bottom": (n - 1) * step + h}[va]
            c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            for i, ln in enumerate(lines):
                off = top_off - i * step - h   # 줄 기준선까지의 수직 오프셋 (CAD 좌표)
                lx = x - s_ * off
                ly = y + c * off
                w = _text_width(ln, h)
                out.append(Text(*_bbox(lx, ly, w, h, rot, ha, "baseline"), _clean(ln),
                                _angle_from_rotation(rot), h))
    except Exception:
        SKIPPED["text"] += 1


def _dim_record(e, dims):
    """선형 치수의 실제 측정 구간(화면좌표)과 값."""
    try:
        dt = e.dimtype & 0x0F if hasattr(e, "dimtype") else e.dxf.dimtype & 0x0F
        if dt not in (0, 1):
            return
        p2, p3 = e.dxf.defpoint2, e.dxf.defpoint3
        ang = e.dxf.get("angle", 0) if dt == 0 else math.degrees(math.atan2(p3[1] - p2[1], p3[0] - p2[0]))
        a = round((ang % 180) / 90) % 2           # 0=가로, 1=세로
        val = e.get_measurement()
        val = float(val) if not hasattr(val, "magnitude") else float(val.magnitude)
        txt = (e.dxf.get("text", "") or "").strip()
        if txt not in ("", "<>"):
            m = re.search(r"(?<![\d,])(?:\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:\.\d+)?", _clean(txt).replace("<>", f"{val:.0f}"))
            if not m:
                return                      # 'W', 'H', 'A' 같은 글자로 덮어쓴 치수: 실제 크기가 아니다
            if m:
                try:
                    val = float(m.group(0).replace(",", ""))
                except ValueError:
                    pass
        tm = e.dxf.get("text_midpoint", None)
        tx, ty = (tm[0], -tm[1]) if tm is not None else ((p2[0] + p3[0]) / 2, -(p2[1] + p3[1]) / 2)
        if a == 0:
            span = (min(p2[0], p3[0]), max(p2[0], p3[0]))
        else:
            span = (min(-p2[1], -p3[1]), max(-p2[1], -p3[1]))
        # 측정점 위치: 세로치수는 측정점 x, 가로치수는 측정점 y (어느 창의 치수인지 판단용)
        px = (p2[0] + p3[0]) / 2
        py = -(p2[1] + p3[1]) / 2
        dims.append({"o": "h" if a == 0 else "v", "val": val, "span": span, "tx": tx, "ty": ty, "px": px, "py": py})
    except Exception:
        SKIPPED["dim"] += 1


CLIPPED = {"n": 0}
CUR_CLIPS = []
CLIP_INFO = {}          # DXF 경로 → [{name, screen(x0,y0,x1,y1), block(x0,y0,x1,y1)}]


def _clip_polygon(e):
    """XCLIP 으로 일부만 보이게 한 블록(INSERT)의 보이는 범위(화면좌표 다각형). 없으면 None.
    큰 단면·평면을 블록 하나로 두고 장마다 다른 부분만 잘라 보여 주는 도면에서, 자르지 않으면 같은 그림이 여러 번 겹쳐 읽힌다."""
    try:
        if not e.has_extension_dict:
            return None
        d = e.get_extension_dict().dictionary
        if "ACAD_FILTER" not in d:
            return None
        sp = d["ACAD_FILTER"].get("SPATIAL")
        if sp is None or sp.dxftype() != "SPATIAL_FILTER" or not sp.dxf.get("is_clipping_enabled", 1):
            return None
        b = [(v[0], v[1]) for v in sp.boundary_vertices]
        if len(b) < 2:
            return None
        if len(b) == 2:
            b = [b[0], (b[1][0], b[0][1]), b[1], (b[0][0], b[1][1])]
        tmi = sp.transform_matrix
        tmi.inverse()
        im = sp.inverse_insert_matrix
        M = e.matrix44()
        out, blk = [], []
        for x, y in b:
            q = im.transform(tmi.transform((x, y, 0)))        # 블록 안 좌표
            w = M.transform(q)
            blk.append((q[0], q[1]))
            out.append((w[0], -w[1]))
        # 어느 블록의 어느 부분을 어디에 보여 주는지 기록 (같은 그림을 구간별로 잘라 늘어놓은 장을 알아보는 데 쓴다)
        CUR_CLIPS.append({"name": e.dxf.name,
                          "screen": (min(p[0] for p in out), min(p[1] for p in out), max(p[0] for p in out), max(p[1] for p in out)),
                          "block": (min(p[0] for p in blk), min(p[1] for p in blk), max(p[0] for p in blk), max(p[1] for p in blk))})
        return out
    except Exception:
        return None


def _inside(poly, x, y):
    n, c = len(poly), False
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            c = not c
    return c


def _clip_seg_rect(sg, r):
    """선분을 축에 나란한 사각형으로 자른다 (Liang–Barsky). 밖이면 None"""
    x0, y0, x1, y1 = sg
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p_, q_ in ((-dx, x0 - r[0]), (dx, r[2] - x0), (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if p_ == 0:
            if q_ < 0:
                return None
            continue
        t = q_ / p_
        if p_ < 0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)
    if t0 == 0.0 and t1 == 1.0:
        return sg
    return (x0 + t0 * dx, y0 + t0 * dy, x0 + t1 * dx, y0 + t1 * dy)


def _apply_clip(poly, texts, segs, rects, dims):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    r = (min(xs), min(ys), max(xs), max(ys))
    is_rect = len(poly) == 4 and all(abs(p[0] - r[0]) < 1e-6 * max(1, abs(r[2] - r[0])) or abs(p[0] - r[2]) < 1e-6 * max(1, abs(r[2] - r[0])) for p in poly) \
        and all(abs(p[1] - r[1]) < 1e-6 * max(1, abs(r[3] - r[1])) or abs(p[1] - r[3]) < 1e-6 * max(1, abs(r[3] - r[1])) for p in poly)
    ins = (lambda x, y: r[0] <= x <= r[2] and r[1] <= y <= r[3]) if is_rect else (lambda x, y: _inside(poly, x, y))
    texts[:] = [t for t in texts if ins(t.cx, t.cy)]
    if is_rect:
        segs[:] = [c for c in (_clip_seg_rect(sg, r) for sg in segs) if c is not None]
    else:
        segs[:] = [sg for sg in segs if ins((sg[0] + sg[2]) / 2, (sg[1] + sg[3]) / 2)]
    if rects is not None:
        rects[:] = [q for q in rects if ins(q[0], q[1]) and ins(q[2], q[3])]
    if dims is not None:
        dims[:] = [d for d in dims if ins(d["tx"], d["ty"])]


# ---- 금속(트렌치·재료분리대) 옆길: 레이어·블록 이름이 필요한 도형만 따로 모은다 (v25) -----------------------
# 기존 texts/segments/rects/dims 의 내용·순서는 바꾸지 않는다. 모은 것은 Sheet.layered 에 둔다.
#   {"k": "poly", "layer", "pts": [(x, y)…] 화면좌표, "closed", "chain": (블록 이름…), "cut": 클립 경계에 잘림}
#   {"k": "ins", "name", "layer", "chain", "cx", "cy" 화면좌표 중심, "w", "h" 실제 크기(축척 반영, 블록 축 기준), "sx", "sy"}
METAL_LAYER = re.compile(r"트렌치|트랜치|TRENCH|재료\s*분리대", re.I)
METAL_BLOCK = re.compile(r"무소음\s*트[렌랜]치", re.I)
LAY_SKIPPED = {"n": 0}          # 금속 옆길에서 읽다 실패해 건너뛴 객체 수 (기존 순회에는 영향 없음)
_LAYER_HIT = {}
_BLOCK_EXT = {}


def _metal_layer(name):
    v = _LAYER_HIT.get(name)
    if v is None:
        v = _LAYER_HIT[name] = bool(METAL_LAYER.search(name or ""))
    return v


def _lay_poly(e, t, L, chain, lay, sc=1.0):
    """대상 레이어의 LINE/LWPOLYLINE/POLYLINE 한 개 → lay 에 기록 (실패는 여기서 삼킨다)."""
    try:
        if t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            pts, closed = [(s[0], -s[1]), (en[0], -en[1])], False
        elif t == "LWPOLYLINE":
            pts, closed = [(p[0], -p[1]) for p in e.get_points("xy")], bool(e.closed)
        else:
            pts, closed = [(v.dxf.location[0], -v.dxf.location[1]) for v in e.vertices], bool(e.is_closed)
        if len(pts) >= 2 and closed and pts[0] == pts[-1]:
            pts = pts[:-1]
        elif len(pts) >= 4 and pts[0] == pts[-1]:
            pts, closed = pts[:-1], True
        if len(pts) >= 2:
            lay.append({"k": "poly", "layer": L, "pts": pts, "closed": closed, "chain": tuple(chain), "cut": False, "sc": sc})
    except Exception:
        LAY_SKIPPED["n"] += 1


def _block_extent(e):
    """블록 정의 안 선들의 범위 (블록 좌표). 이름별로 한 번만 잰다."""
    key = (id(getattr(e, "doc", None)), e.dxf.name)
    if key in _BLOCK_EXT:
        return _BLOCK_EXT[key]
    xs, ys = [], []
    try:
        for b in e.block():
            bt = b.dxftype()
            try:
                if bt == "LINE":
                    for p in (b.dxf.start, b.dxf.end):
                        xs.append(p[0]); ys.append(p[1])
                elif bt == "LWPOLYLINE":
                    for p in b.get_points("xy"):
                        xs.append(p[0]); ys.append(p[1])
                elif bt == "POLYLINE":
                    for v in b.vertices:
                        xs.append(v.dxf.location[0]); ys.append(v.dxf.location[1])
            except Exception:
                continue
    except Exception:
        pass
    ext = (min(xs), min(ys), max(xs), max(ys)) if xs else None
    _BLOCK_EXT[key] = ext
    return ext


def _lay_insert(e, L, chain, lay):
    """무소음트렌치 같은 이름의 블록 삽입 → lay 에 기록 (실패는 여기서 삼킨다)."""
    try:
        ext = _block_extent(e)
        sx, sy = e.dxf.get("xscale", 1) or 1, e.dxf.get("yscale", 1) or 1
        if ext:
            M = e.matrix44()
            c = M.transform(((ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2, 0))
            w, h = (ext[2] - ext[0]) * abs(sx), (ext[3] - ext[1]) * abs(sy)
        else:
            p = e.dxf.insert
            c, w, h = (p[0], p[1]), 0.0, 0.0
        lay.append({"k": "ins", "name": e.dxf.name, "layer": L, "chain": tuple(chain), "cx": c[0], "cy": -c[1],
                    "w": w, "h": h, "sx": sx, "sy": sy})
    except Exception:
        LAY_SKIPPED["n"] += 1


def _lay_center(r):
    if r["k"] == "ins":
        return r["cx"], r["cy"]
    xs, ys = [p[0] for p in r["pts"]], [p[1] for p in r["pts"]]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def _clip_layered(poly, lay):
    """금속 옆길의 클립: 축과 나란한 사각형이면 선을 실제로 자르고, 그 밖 다각형이면 선분 중점으로 남기거나 버린다.
    일부만 남은 선은 열린 조각(cut=True)으로 바꾼다 → 잘린 폐합선을 사각형으로 세지 않는다. 블록은 중심이 안에 있으면 남긴다."""
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    r = (min(xs), min(ys), max(xs), max(ys))
    is_rect = len(poly) == 4 and all(abs(p[0] - r[0]) < 1e-6 * max(1, abs(r[2] - r[0])) or abs(p[0] - r[2]) < 1e-6 * max(1, abs(r[2] - r[0])) for p in poly) \
        and all(abs(p[1] - r[1]) < 1e-6 * max(1, abs(r[3] - r[1])) or abs(p[1] - r[3]) < 1e-6 * max(1, abs(r[3] - r[1])) for p in poly)
    ins = (lambda x, y: r[0] <= x <= r[2] and r[1] <= y <= r[3]) if is_rect else (lambda x, y: _inside(poly, x, y))
    out = []
    for it in lay:
        try:
            if it["k"] == "ins":
                if ins(it["cx"], it["cy"]):
                    out.append(it)
                continue
            pts = it["pts"]
            ring = pts + [pts[0]] if it["closed"] else pts
            pieces, cur, whole = [], [], True
            for a, b in zip(ring, ring[1:]):
                sg = (a[0], a[1], b[0], b[1])
                if is_rect:
                    c = _clip_seg_rect(sg, r)
                else:
                    c = sg if ins((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) else None
                if c is None:
                    whole = False
                    if cur:
                        pieces.append(cur); cur = []
                    continue
                if c != sg:
                    whole = False
                p0, p1 = (c[0], c[1]), (c[2], c[3])
                if cur and abs(cur[-1][0] - p0[0]) < 1e-9 and abs(cur[-1][1] - p0[1]) < 1e-9:
                    cur.append(p1)
                else:
                    if cur:
                        pieces.append(cur)
                    cur = [p0, p1]
                if abs(p1[0] - b[0]) > 1e-9 or abs(p1[1] - b[1]) > 1e-9:      # 끝이 잘렸으면 조각을 닫는다
                    pieces.append(cur); cur = []
            if cur:
                pieces.append(cur)
            if whole:
                out.append(it)
                continue
            for pc in pieces:
                if len(pc) >= 2:
                    out.append(dict(it, pts=pc, closed=False, cut=True, was_closed=bool(it["closed"] or it.get("was_closed"))))
        except Exception:
            LAY_SKIPPED["n"] += 1
    lay[:] = out


def _walk(entities, texts, segs, depth=0, rects=None, dims=None, lay=None, play="0", chain=(), sc=1.0):
    for e in entities:
        t = e.dxftype()
        if lay is not None and t in ("LINE", "LWPOLYLINE", "POLYLINE", "INSERT"):
            try:
                L = e.dxf.get("layer", "0") or "0"
                if L == "0":
                    L = play                       # 블록 안 레이어 0 은 삽입 레이어를 물려받는다
                if t == "INSERT":
                    if METAL_BLOCK.search(e.dxf.name or ""):
                        _lay_insert(e, L, chain, lay)
                elif _metal_layer(L):
                    _lay_poly(e, t, L, chain, lay, sc)
            except Exception:
                LAY_SKIPPED["n"] += 1
        if t == "DIMENSION" and dims is not None:
            _dim_record(e, dims)
        if t in ("TEXT", "MTEXT", "ATTRIB"):
            _emit_text(e, texts)
        elif t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            segs.append((s[0], -s[1], en[0], -en[1]))
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points("xy")]
            if rects is not None and len(pts) in (4, 5) and (e.closed or pts[0] == pts[-1]):
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                if len({round(x, 3) for x in xs}) == 2 and len({round(y, 3) for y in ys}) == 2:
                    rects.append((min(xs), -max(ys), max(xs), -min(ys)))
            if e.closed and pts:
                pts.append(pts[0])
            segs += [(a[0], -a[1], b[0], -b[1]) for a, b in zip(pts, pts[1:])]
        elif t in ("INSERT", "DIMENSION") and depth < 6:
            # 금속 옆길: 블록 안으로 물려줄 레이어·블록 이름 (실패해도 기존 순회는 그대로)
            c_lay, c_play, c_chain, c_sc = None, play, chain, sc
            if lay is not None and t == "INSERT":
                try:
                    c_play = e.dxf.get("layer", "0") or "0"
                    if c_play == "0":
                        c_play = play
                    c_chain = chain + (e.dxf.name,)
                    # 블록 안 도형의 누적 축척 (가상 삽입의 축척은 이미 바깥 축척이 곱해진 값). 축소 삽입한 키플랜 등을 가려내는 데 쓴다
                    c_sc = math.sqrt(abs((e.dxf.get("xscale", 1) or 1) * (e.dxf.get("yscale", 1) or 1)))
                    c_lay = lay
                except Exception:
                    LAY_SKIPPED["n"] += 1
                    c_lay = None
            try:
                if t == "INSERT":
                    for a in e.attribs:
                        _emit_text(a, texts)
                poly = _clip_polygon(e) if t == "INSERT" else None
                if poly:
                    t_, s_, r_, d_ = [], [], ([] if rects is not None else None), ([] if dims is not None else None)
                    l_ = [] if c_lay is not None else None
                    _walk(e.virtual_entities(), t_, s_, depth + 1, r_, d_, l_, c_play, c_chain, c_sc)
                    _apply_clip(poly, t_, s_, r_, d_)
                    CLIPPED["n"] += 1
                    texts += t_
                    segs += s_
                    if rects is not None:
                        rects += r_
                    if dims is not None:
                        dims += d_
                    if l_:
                        try:
                            _clip_layered(poly, l_)
                            c_lay += l_
                        except Exception:
                            LAY_SKIPPED["n"] += 1
                    continue
                _walk(e.virtual_entities(), texts, segs if t == 'INSERT' else [], depth + 1, rects, dims if t == 'INSERT' else None,  # 치수선은 도형에서 제외
                      c_lay, c_play, c_chain, c_sc)
            except Exception:
                SKIPPED["insert"] += 1


def _tolerant_ezdxf():
    """변환기가 만든 DXF 가 손상돼 있어도(중복 핸들, 깨진 DIMSTYLE 참조) 읽을 수 있게 ezdxf 의 2차 적재를 관대하게 바꾼다.
    실패하는 개체 하나 때문에 도면 전체를 버리지 않고 그 개체만 건너뛴다."""
    import ezdxf.document as d
    if getattr(d.Drawing, "_takeoff_patched", False):
        return
    def _2nd(self):
        db = self.entitydb
        for entity in list(db.values()):
            try:
                cmd = entity.post_load_hook(self)
            except Exception:
                continue
            if cmd is not None:
                self._post_init_commands.append(cmd)
    d.Drawing._2nd_loading_stage = _2nd
    d.Drawing._takeoff_patched = True


def _ensure_eof(path):
    """끝에 EOF 태그가 없는 DXF(변환기가 중간에 끊긴 것)는 EOF 를 붙인 사본을 만들어 읽는다."""
    import os
    with open(path, "rb") as f:
        f.seek(0, 2)
        n = f.tell()
        f.seek(max(0, n - 4096))
        tail = f.read()
    if b"EOF" in tail:
        return path
    fixed = os.path.splitext(path)[0] + "._eof.dxf"
    with open(path, "rb") as src, open(fixed, "wb") as dst:
        body = src.read()
        dst.write(body)
        if not body.endswith((b"\n", b"\r")):
            dst.write(b"\r\n")
        dst.write(b"  0\r\nEOF\r\n")
    return fixed


# 외부참조(xref) 해결: run.py 가 현장 폴더들과 DWG→DXF 변환 함수를 넣어 준다.
XREF_DIRS = []          # xref 파일을 찾을 폴더(현장 폴더, 입력 파일의 폴더)
XREF_CONVERT = None     # convert_dwg(src, out_dir) -> dxf 경로
XREF_LOG = None
_XREF_CACHE = {}


def _norm_name(s):
    import os
    return re.sub(r"[^0-9A-Za-z가-힣]", "", os.path.splitext(os.path.basename(s))[0]).lower()


def _find_xref_file(xref_path, depth=3):
    """xref 경로('.\\X-REF\\X-PLAN-2F.dwg')의 파일을 현장 폴더 안에서 이름으로 찾는다(대소문자·기호 무시)."""
    import os
    want = _norm_name(xref_path.replace("\\", "/"))
    if not want:
        return None
    for ri, root in enumerate(XREF_DIRS):
        if not root or not os.path.isdir(root):
            continue
        # 첫 폴더(입력 폴더)는 3단계까지, 그 위 폴더는 2단계까지만(옆 폴더 X-REF 정도): 다른 현장의 같은 이름 파일을 집어오지 않게
        lim = depth if ri == 0 else 2
        root_n = os.path.normpath(root)
        base_depth = root_n.count(os.sep)
        for dp, dns, fns in os.walk(root_n):
            if os.path.normpath(dp).count(os.sep) - base_depth >= lim - 1:
                dns[:] = []
            for fn in fns:
                if fn.lower().endswith((".dwg", ".dxf")) and _norm_name(fn) == want:
                    return os.path.join(dp, fn)
    return None


def _xref_sheet(xref_path, out_dir):
    """xref 파일을 (한 번만) 변환해 Sheet 로 읽는다. 못 찾으면 None."""
    import os
    key = _norm_name(xref_path)
    if key in _XREF_CACHE:
        return _XREF_CACHE[key]
    src = _find_xref_file(xref_path)
    sh = None
    if src:
        try:
            import os as _os
            _os.makedirs(out_dir, exist_ok=True)
            dxf = src if src.lower().endswith(".dxf") else (XREF_CONVERT(src, out_dir) if XREF_CONVERT else None)
            if dxf:
                sh = load_dxf(dxf, _xref_depth=1)
                sh.xref_file = src
        except Exception as ex:
            if XREF_LOG:
                XREF_LOG(f"xref {os.path.basename(src)} 읽기 실패: {ex}")
            sh = None
    _XREF_CACHE[key] = sh
    return sh


def _merge_xref(dst_texts, dst_segs, dst_rects, dst_dims, sh, ins, base, sx, sy, rot):
    """xref Sheet(화면좌표)를 INSERT 변환(삽입점·축척·회전)으로 옮겨 합친다."""
    c, s_ = math.cos(math.radians(rot or 0)), math.sin(math.radians(rot or 0))
    def tf(x, ys):
        # 화면좌표 → CAD(y 뒤집기) → 블록 기준점 빼고 축척·회전 → 삽입점 더하기 → 화면좌표
        y = -ys
        dx, dy = (x - base[0]) * sx, (y - base[1]) * sy
        X = ins[0] + dx * c - dy * s_
        Y = ins[1] + dx * s_ + dy * c
        return X, -Y
    for t in sh.texts:
        pts = [tf(t.x0, t.y0), tf(t.x1, t.y0), tf(t.x1, t.y1), tf(t.x0, t.y1)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        nt = Text(min(xs), min(ys), max(xs), max(ys), t.text, t.angle, t.size * abs(sx))
        nt.xref = True
        dst_texts.append(nt)
    for a0, b0, a1, b1 in sh.segments:
        X0, Y0 = tf(a0, b0); X1, Y1 = tf(a1, b1)
        dst_segs.append((X0, Y0, X1, Y1))
    for r in getattr(sh, "rects", []):
        pts = [tf(r[0], r[1]), tf(r[2], r[1]), tf(r[2], r[3]), tf(r[0], r[3])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        dst_rects.append((min(xs), min(ys), max(xs), max(ys)))
    for d in getattr(sh, "dims", []):
        if (rot or 0) % 180 != 0:
            continue                       # 회전된 xref 의 치수 방향은 바뀌므로 단순화: 건너뜀
        tx, ty = tf(d["tx"], d["ty"]); px, py = tf(d["px"], d["py"])
        if d["o"] == "h":
            sp = (tf(d["span"][0], 0)[0], tf(d["span"][1], 0)[0])
        else:
            sp = (tf(0, d["span"][0])[1], tf(0, d["span"][1])[1])
        dst_dims.append({"o": d["o"], "val": d["val"] * abs(sx), "span": (min(sp), max(sp)), "tx": tx, "ty": ty, "px": px, "py": py})


def _merge_layered(src, ins, base, sx, sy, rot, xname):
    """xref Sheet 의 금속 옆길(layered, 참조 원본 화면좌표)을 삽입 변환으로 옮긴 **새 목록**. 원본(캐시)은 건드리지 않는다."""
    c, s_ = math.cos(math.radians(rot or 0)), math.sin(math.radians(rot or 0))

    def tf(x, ys):
        y = -ys
        dx, dy = (x - base[0]) * sx, (y - base[1]) * sy
        return ins[0] + dx * c - dy * s_, -(ins[1] + dx * s_ + dy * c)
    out = []
    for r in src:
        if r["k"] == "ins":
            X, Y = tf(r["cx"], r["cy"])
            out.append(dict(r, cx=X, cy=Y, w=r["w"] * abs(sx), h=r["h"] * abs(sy), sx=r["sx"] * sx, sy=r["sy"] * sy,
                            chain=(xname,) + tuple(r["chain"])))
        else:
            out.append(dict(r, pts=[tf(x, y) for x, y in r["pts"]], chain=(xname,) + tuple(r["chain"]),
                            sc=r.get("sc", 1.0) * math.sqrt(abs(sx * sy))))
    return out


def load_dxf(path, _xref_depth=0):
    import ezdxf, os
    from ezdxf import recover
    _tolerant_ezdxf()
    path = _ensure_eof(path)
    try:
        doc, _ = recover.readfile(path)
    except Exception:
        doc = ezdxf.readfile(path)
    texts, segs, rects, dims = [], [], [], []
    layered = []
    for k_ in SKIPPED:
        SKIPPED[k_] = 0
    LAY_SKIPPED["n"] = 0
    if _xref_depth == 0:
        del CUR_CLIPS[:]
    _walk(doc.modelspace(), texts, segs, 0, rects, dims, layered)
    lay_skip = LAY_SKIPPED["n"]
    # 외부참조(xref): 블록 정의가 비어 있고 xref 경로가 있으면 현장 폴더에서 그 파일을 찾아 삽입 위치에 합친다
    xref_notes = []
    native_n_segs, native_rects = len(segs), list(rects)
    if _xref_depth == 0 and XREF_DIRS:
        for e in doc.modelspace():
            if e.dxftype() != "INSERT":
                continue
            try:
                blk = doc.blocks.get(e.dxf.name)
                if blk is None or not blk.block.is_xref:
                    continue
                xp = blk.block.dxf.get("xref_path", "") or e.dxf.name
                sh = _xref_sheet(xp, os.path.join(os.path.dirname(path), "_xref"))
                if sh is None:
                    xref_notes.append(f"xref '{e.dxf.name}' 파일을 찾지 못함 → 그 부분은 비어 있음")
                    continue
                ins = (e.dxf.insert[0], e.dxf.insert[1])
                base = (blk.block.dxf.base_point[0], blk.block.dxf.base_point[1])
                poly = _clip_polygon(e)
                if poly:
                    # 잘라서(XCLIP) 넣은 xref: 보이는 범위만 합친다
                    t_, s_, r_, d_ = [], [], [], []
                    _merge_xref(t_, s_, r_, d_, sh, ins, base, e.dxf.xscale or 1.0, e.dxf.yscale or 1.0, e.dxf.rotation or 0.0)
                    _apply_clip(poly, t_, s_, r_, d_)
                    texts += t_; segs += s_; rects += r_; dims += d_
                    CLIPPED["n"] += 1
                else:
                    _merge_xref(texts, segs, rects, dims, sh, ins, base, e.dxf.xscale or 1.0, e.dxf.yscale or 1.0, e.dxf.rotation or 0.0)
                # 금속 옆길: 캐시된 xref Sheet 의 layered 는 참조 원본 좌표 그대로 두고, 삽입마다 복사해서 옮긴다
                try:
                    if getattr(sh, "layered", None):
                        l_ = _merge_layered(sh.layered, ins, base, e.dxf.xscale or 1.0, e.dxf.yscale or 1.0, e.dxf.rotation or 0.0, e.dxf.name)
                        if poly:
                            _clip_layered(poly, l_)
                        layered += l_
                except Exception:
                    lay_skip += 1
                xref_notes.append(f"xref '{e.dxf.name}' ← {os.path.basename(getattr(sh, 'xref_file', ''))} 합침")
            except Exception as ex:
                xref_notes.append(f"xref '{getattr(e.dxf, 'name', '?')}' 처리 실패: {ex}")
    if texts:
        x0 = min(t.x0 for t in texts); y0 = min(t.y0 for t in texts)
        x1 = max(t.x1 for t in texts); y1 = max(t.y1 for t in texts)
    else:
        x0 = y0 = 0; x1 = y1 = 1
    sh = Sheet(source=path, index=1, width=x1 - x0, height=y1 - y0, texts=texts, segments=segs)
    sh.bounds = (x0, y0, x1, y1)
    sh.rects = rects
    sh.dims = dims
    sh.native_n_segs = native_n_segs
    if _xref_depth == 0:
        CLIP_INFO[path] = list(CUR_CLIPS)
        sh.clips = list(CUR_CLIPS)
    sh.native_rects = native_rects
    sh.layered = layered
    sh.layered_skipped = lay_skip
    sh.warnings =[f"읽기 실패로 건너뜀: 글자 {SKIPPED['text']}, 치수 {SKIPPED['dim']}, 블록 {SKIPPED['insert']}"] \
        if any(SKIPPED.values()) else []
    sh.warnings += sorted(set(xref_notes))
    return sh
