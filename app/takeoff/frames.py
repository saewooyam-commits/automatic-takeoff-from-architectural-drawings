"""CAD 모델공간을 도곽(도면 테두리) 단위 Sheet로 나눈다.

도곽 후보: 가로세로비 1.25~1.65의 사각형(A판 비율) 중 충분히 큰 것.
겉테두리/안테두리가 겹치면 안쪽을, 여러 도곽을 감싸는 초대형 사각형은 버린다.
"""
import re
import statistics
from bisect import bisect_left, bisect_right

from .model import Sheet
from .tags import floors_from_title
from .dxf_source import _lay_center

TITLE_KW = re.compile(r"(평면도|일람표|입면도|단면도|상세도|창호도|안내도|부호도|배치도|전개도|계획도|목록|개요|구적도|산출)")


def _contains(a, b, tol=0.0):
    return a[0] - tol <= b[0] and a[1] - tol <= b[1] and b[2] <= a[2] + tol and b[3] <= a[3] + tol


def _rects_from_lines(sheet, th):
    """선 4개(또는 토막난 선)로 그린 도곽: 같은 x 범위의 긴 가로선 한 쌍 + 그 양 끝에 세로선이 서 있으면 사각형으로 본다."""
    from collections import defaultdict
    tol = max(th * 0.5, 1.0)
    H, V = defaultdict(list), defaultdict(list)
    for x0, y0, x1, y1 in sheet.segments:
        if abs(y0 - y1) <= tol and abs(x1 - x0) >= th * 40:
            H[(round(min(x0, x1) / tol), round(max(x0, x1) / tol))].append(y0)
        elif abs(x0 - x1) <= tol and abs(y1 - y0) >= th * 5:
            V[round(x0 / tol)].append((min(y0, y1), max(y0, y1)))

    def covered(x, ya, yb):
        """x 자리에 서 있는 세로선들이 ya~yb 를 90% 이상 덮는가"""
        segs = []
        for k in (round(x / tol) - 1, round(x / tol), round(x / tol) + 1):
            segs += V.get(k, [])
        segs = sorted(s for s in segs if s[1] > ya and s[0] < yb)
        cov, cur = 0.0, ya
        for a, b in segs:
            a = max(a, cur)
            if b > a:
                cov += b - a
                cur = b
        return cov >= 0.9 * (yb - ya)
    def touch(x, ya, yb):
        """x 자리에 ya~yb 구간과 닿는 세로선이 하나라도 있는가 (모서리 토막)"""
        for k in (round(x / tol) - 1, round(x / tol), round(x / tol) + 1):
            for a, b in V.get(k, []):
                if b > ya - tol and a < yb + tol:
                    return True
        return False
    labs = [(t.cx, t.cy) for t in sheet.texts if TITLE_LABEL.search(t.text) and len(t.text) <= 40]
    out, weak = [], []
    for (kx0, kx1), ys in H.items():
        if len(ys) < 2:
            continue
        x0, x1 = kx0 * tol, kx1 * tol
        w = x1 - x0
        ys = sorted(set(round(y / tol) * tol for y in ys))
        for i, ya in enumerate(ys):
            for yb in ys[i + 1:]:
                h = yb - ya
                if h < th * 40 or not (1.25 <= max(w, h) / min(w, h) <= 1.65):
                    continue
                c0, c1 = covered(x0, ya, yb), covered(x1, ya, yb)
                if c0 and c1:
                    out.append((x0, ya, x1, yb))
                elif sum(1 for lx, ly in labs if x0 <= lx <= x1 and ya <= ly <= yb) == 1:
                    # 세로 테두리가 없는 도곽(변환에서 빠졌거나 두꺼운 선·이미지로 그린 양식, N현장):
                    # 같은 폭의 가로선 한 쌍이 표제란 라벨을 품고 있으면 인정
                    weak.append((x0, ya, x1, yb))
    # 세로선 없이 잡은 후보끼리는 같은 폭에서 서로 품으면 바깥 것만 (표 괘선 쌍이 만든 안쪽 사각형은 도곽이 아니다)
    weak = [r for r in weak if not any(o is not r and o[0] == r[0] and o[2] == r[2] and o[1] <= r[1] and o[3] >= r[3] for o in weak)]
    # 같은 폭으로 위아래 이어 붙은 도곽끼리 살짝(10% 미만) 겹치면(표제란 선을 테두리로 잘못 짝지은 경우) 뒤 것을 앞 것 끝에서 시작
    weak.sort(key=lambda r: (r[0], r[2], r[1]))
    fixed = []
    for r in weak:
        if fixed and fixed[-1][0] == r[0] and fixed[-1][2] == r[2] and fixed[-1][3] > r[1] and \
                (fixed[-1][3] - r[1]) < 0.1 * (r[3] - r[1]):
            r = (r[0], fixed[-1][3], r[2], r[3])
            w_, h_ = r[2] - r[0], r[3] - r[1]
            if not (1.25 <= max(w_, h_) / min(w_, h_) <= 1.65):
                continue
        fixed.append(r)
    sheet._weak_rects = set(fixed)
    out += fixed
    return out


def _overlap_frac(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / max(min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])), 1e-9)


def _label_points(sheet, th):
    labels = [t for t in sheet.texts if TITLE_LABEL.search(t.text) and len(t.text) <= 40]
    pts = []
    for t in sorted(labels, key=lambda t: (t.cy, t.cx)):
        if not any(abs(t.cx - x) < th * 40 and abs(t.cy - y) < th * 15 for x, y in pts):
            pts.append((t.cx, t.cy))
    return pts


def _area(r):
    return (r[2] - r[0]) * (r[3] - r[1])


def _is_container(r, allc, lab_pts, th, geometric=True):
    """여러 도곽을 한꺼번에 감싸는 사각형인가.
    라벨(DRAWING TITLE/도면명) 묶음이 2개 이상 들어 있고 서로 멀리(폭·높이의 절반 이상) 떨어져 있으면 그렇다.
    geometric: 서로 겹치지 않는 큰 후보(면적 25% 이상)를 2개 이상 품어도 그렇다 (표 상자는 한 도곽 안에 여럿 있어도 작다)."""
    pts = [(x, y) for x, y in lab_pts if r[0] <= x <= r[2] and r[1] <= y <= r[3]]
    if len(pts) >= 2:
        xs_, ys_ = [p_[0] for p_ in pts], [p_[1] for p_ in pts]
        if max(xs_) - min(xs_) > 0.5 * (r[2] - r[0]) or max(ys_) - min(ys_) > 0.5 * (r[3] - r[1]):
            return True
    if not geometric:
        return False
    # 서로 겹치지 않는 안쪽 후보들이 면적의 85% 이상을 채우면(두 장이 꼭 맞게 들어 있으면) 여러 장을 감싼 테두리.
    # 한 장 안의 표 상자들은 서로 겹치고 면적도 그만큼 못 채운다
    inside = [q for q in allc if q is not r and _contains(r, q, th * 0.1) and _area(q) >= 0.2 * _area(r)]
    if len(inside) < 2:
        return False
    # 서로 겹치지 않는 안쪽 후보 묶음 중 면적 합이 가장 큰 것 (첫 후보를 바꿔 가며 큰 순서로 채움)
    best = 0.0
    inside.sort(key=lambda q: -_area(q))
    for first in inside[:40]:
        disjoint = [first]
        for q in inside:
            if q is not first and all(_overlap_frac(q, d) < 0.05 for d in disjoint):
                disjoint.append(q)
        if len(disjoint) >= 2:
            best = max(best, sum(_area(q) for q in disjoint))
    return best >= 0.85 * _area(r)


def _covered_by(r, q):
    """r 의 면적 중 q 와 겹치는 비율 (r 안에 든 작은 상자는 r 을 깎지 않는다)"""
    ix = max(0.0, min(r[2], q[2]) - max(r[0], q[0]))
    iy = max(0.0, min(r[3], q[3]) - max(r[1], q[1]))
    return ix * iy / max(_area(r), 1e-9)


def _text_count(sheet, r):
    xs = getattr(sheet, "_xs_sorted", None)
    if xs is None:
        xs = sorted((t.cx, i) for i, t in enumerate(sheet.texts))
        sheet._xs_sorted = xs
        sheet._xk = [x for x, _ in xs]
    lo, hi = bisect_left(sheet._xk, r[0]), bisect_right(sheet._xk, r[2])
    return sum(1 for _, i in xs[lo:hi] if r[1] <= sheet.texts[i].cy <= r[3])


def _poly_frames(sheet, th, poly):
    """닫힌 사각형(폴리라인) 도곽: 예전 규칙 그대로 — 겹겹이면 안쪽(25% 넘는 안쪽 사각형이 없는 것)을 쓴다"""
    keep = []
    for r in poly:
        inner = [o for o in poly if o is not r and _contains(r, o, th * 0.1) and _area(o) > 0.25 * _area(r)]
        if not inner:
            keep.append(r)
    # 도곽은 서로 비스듬히 겹치지 않는다. 같은 사각형(배경 도면 블록의 테두리 등)이 조금씩 어긋나 여러 번 겹쳐 놓인 묶음은
    # 표제란 라벨(도면명/DRAWING TITLE)을 품은 것만 남기고, 라벨이 하나도 없는 3개 이상 묶음은 도곽이 아니라고 본다.
    if len(keep) >= 3:
        lab_pts = _label_points(sheet, th)
        def has_lab(r):
            return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for x, y in lab_pts)
        order = sorted(range(len(keep)), key=lambda i: keep[i][0])
        over = {i: [] for i in range(len(keep))}
        for a_, i in enumerate(order):
            ri = keep[i]
            for j in order[a_ + 1:]:
                rj = keep[j]
                if rj[0] >= ri[2]:
                    break
                if min(_covered_by(ri, rj), _covered_by(rj, ri)) > 0.05:
                    over[i].append(j)
                    over[j].append(i)
        drop = set()
        for i, lst in over.items():
            if not lst or has_lab(keep[i]):
                continue
            if any(has_lab(keep[j]) for j in lst) or len(lst) >= 2:
                drop.add(i)
        if drop:
            keep = [r for i, r in enumerate(keep) if i not in drop]
            sheet._overlap_dropped = len(drop)
    return keep


def _line_frames(sheet, th, line_rects, taken):
    """선 4개로 그린 도곽 후보 중 이미 잡힌 도곽(taken)과 겹치지 않는 것들을 고른다.
    표 괘선 쌍도 사각형으로 잡혀 서로 겹치는 후보가 많으므로,
      1) 라벨(DRAWING TITLE) 묶음을 멀리 떨어져 2개 이상 품거나, 서로 겹치지 않는 안쪽 후보가 85% 이상 채우는 것(여러 장을 감싼 테두리)은 뺀다
      2) 겹겹이 둘러싼 테두리는 안쪽을 쓰되, 안쪽 바깥 띠에 표제란이 있으면(표제란 띠가 옆에 붙은 양식) 바깥을 쓴다
      3) 표제란(라벨·도면명 글자)이 가장자리 띠에 있는 후보만 남기고,
      4) 서로 겹치지 않으면서 (라벨 하나를 품은 것에 가중치를 주어) 덮는 면적이 가장 큰 집합을 고른다"""
    allc = [r for r in line_rects if not any(_covered_by(r, q) > 0.2 or _covered_by(q, r) > 0.2 for q in taken)]
    if not allc:
        return []
    lab_pts = _label_points(sheet, th)
    allc_full = list(allc)
    allc = [r for r in allc if not _is_container(r, allc_full, lab_pts, th)]
    title_pts = [(t.cx, t.cy) for t in sheet.texts if TITLE_KW.search(t.text) and len(t.text) <= 40]
    drop = set()
    for r in allc:
        inner = [o for o in allc if o is not r and _contains(r, o, th * 0.1) and _area(o) > 0.25 * _area(r)]
        if not inner:
            continue
        o = max(inner, key=_area)
        if any(not _contains(o, q, th * 0.1) for q in inner if q is not o):
            continue
        others = [q for q in allc if q is not r and q is not o and not _contains(q, r, th * 0.1) and _overlap_frac(q, o) < 0.05]

        def in_strip(x, y):
            if not (r[0] <= x <= r[2] and r[1] <= y <= r[3]) or (o[0] <= x <= o[2] and o[1] <= y <= o[3]):
                return False
            return not any(q[0] <= x <= q[2] and q[1] <= y <= q[3] for q in others)
        has_title = any(in_strip(x, y) for x, y in lab_pts) or any(in_strip(x, y) for x, y in title_pts)
        if has_title and ((r[2] - r[0]) >= (o[2] - o[0]) * 1.08 or (r[3] - r[1]) >= (o[3] - o[1]) * 1.08):
            drop.add(id(o))
        else:
            drop.add(id(r))
    allc = [r for r in allc if id(r) not in drop]
    all_title = lab_pts + title_pts

    def near_edge(r, pts):
        W, H = r[2] - r[0], r[3] - r[1]
        return [(x, y) for x, y in pts if r[0] <= x <= r[2] and r[1] <= y <= r[3] and
                (x - r[0] < W * 0.15 or r[2] - x < W * 0.15 or y - r[1] < H * 0.15 or r[3] - y < H * 0.15)]
    if all_title:
        with_title = [r for r in allc if near_edge(r, all_title)]
        if with_title:
            allc = with_title

    def conflict(a, b):
        return _covered_by(a, b) > 0.02 or _covered_by(b, a) > 0.02

    def weight(r):
        n = sum(1 for x, y in lab_pts if r[0] <= x <= r[2] and r[1] <= y <= r[3])
        return _area(r) * (2.0 if n == 1 else 1.0)
    kept = []
    for r in sorted(allc, key=lambda r: -weight(r)):
        if not any(conflict(r, q) for q in kept):
            kept.append(r)
    changed = True
    while changed:
        changed = False
        for r in list(kept):
            others = [q for q in allc if q is not r and not any(conflict(q, k) for k in kept if k is not r) and conflict(q, r)]
            if not others:
                continue
            pick = []
            for q in sorted(others, key=lambda q: -weight(q)):
                if not any(conflict(q, p_) for p_ in pick):
                    pick.append(q)
            if sum(weight(q) for q in pick) > weight(r) * 1.02 or (len(pick) >= 2 and sum(_area(q) for q in pick) >= 0.9 * _area(r)):
                kept.remove(r)
                kept += pick
                changed = True
                break
    return kept


def _candidate_rects(sheet, th):
    """도곽 후보: 닫힌 사각형(폴리라인) 도곽을 먼저 잡고(예전 규칙), 그와 겹치지 않는 곳에서만 선 4개로 그린 도곽을 찾는다."""
    def ok(r):
        w, h = r[2] - r[0], r[3] - r[1]
        return w > 0 and h > 0 and 1.25 <= max(w, h) / min(w, h) <= 1.65 and min(w, h) >= th * 40
    poly = [tuple(r) for r in (getattr(sheet, "rects", []) or []) if ok(r)]
    poly = list({tuple(round(v, 1) for v in r): r for r in poly}.values())
    poly = [r for r in poly if _text_count(sheet, r) >= 20]
    frames = _poly_frames(sheet, th, poly)
    if sheet.segments:
        line_rects = [r for r in _rects_from_lines(sheet, th) if ok(r) and _text_count(sheet, r) >= 20]
        frames += _line_frames(sheet, th, line_rects, frames)
    return frames


def find_frames(sheet):
    hs = sorted(t.size for t in sheet.texts if t.size > 0)
    th = hs[len(hs) // 2] if hs else 1
    out = list(_candidate_rects(sheet, th))
    out.sort(key=lambda r: (round(r[1] / max(th * 100, 1)), r[0]))
    weak = getattr(sheet, "_weak_rects", set())
    nweak = sum(1 for r in out if tuple(r) in weak)
    if nweak:
        sheet.frame_method = f"세로 테두리 없는 도곽 {nweak}장을 가로선+표제란 라벨로 잡음"
    return out


LABEL_WORDS = re.compile(r"^(SCALE|DATE|DRAWN(\s*BY)?|CHECKED(\s*BY)?|APPROVED(\s*BY)?|DESIGNED(\s*BY)?|PROJECT(\s*(NO|TITLE)\.?)?|DRAWING(\s*NO)?\.?|"
                         r"SHEET(\s*NO)?\.?|REV(ISION)?\.?|NO\.?|DWG\.?(\s*NO)?\.?|RELEASE\s*DATE|JOB\s*NO\.?|"
                         r"축\s*척|도\s*면\s*번\s*호|일\s*자|설\s*계|승\s*인|검\s*토|작\s*성|공\s*사\s*명|도\s*면\s*명)(\s*[:：].*)?$", re.I)
TITLE_LABEL = re.compile(r"(DRAWING\s*TITLE|DRAWING\s*NAME|NAME\s*OF\s*DRAWING|DWG\.?\s*TITLE|도\s*면\s*명|도면\s*제목)", re.I)


def _title_from_label(sub):
    labs = [t for t in sub.texts if TITLE_LABEL.search(t.text) and len(t.text) <= 40]
    best = None
    for lab in labs:
        h = max(lab.size, 1)
        for t in sub.texts:
            if t is lab or TITLE_LABEL.search(t.text) or not re.search(r"[가-힣A-Za-z]", t.text):
                continue
            dx, dy = t.cx - lab.cx, t.cy - lab.cy
            if LABEL_WORDS.match(t.text.strip()):
                continue                       # 'SCALE', 'DRAWING NO.' 같은 표제란 항목 이름은 제목이 아니다
            # 라벨 오른쪽(같은 줄), 아래쪽, 또는 위쪽(항목 이름 줄이 맨 아래에 있고 값이 그 위에 적히는 양식)
            if (abs(dy) < h * 1.5 and 0 < dx < h * 60) or (abs(dy) < h * 15 and abs(dx) < h * 60):
                # 도면명 낱말이 없는 글자끼리는 라벨 아래·오른쪽 것을 먼저 (라벨 위에는 앞 항목인 공사명 값이 있기 쉽다)
                score = (TITLE_KW.search(t.text) is not None, dy > -h * 0.5, t.size, -(abs(dx) + abs(dy)))
                if best is None or score > best[0]:
                    best = (score, t)
    return best[1].text if best else ""


def guess_title(sub):
    t = _title_from_label(sub)
    if t:
        # 라벨 옆 글자가 도면 이름답지 않으면(사무소 이름 'ABC' 처럼, 표제란 값이 비어 있을 때) 도곽 안 제목 글자를 쓴다
        if not TITLE_KW.search(t) and not re.search(r"[가-힣]{2,}", t):
            alt = _guess_title_kw(sub)
            if alt:
                return alt
        return t
    return _guess_title_kw(sub)


def _guess_title_kw(sub):
    """도곽 안 제목: 표제란(우측/하단 25%) 안의 도면명 키워드 글자 우선, 없으면 가장 큰 키워드 글자."""
    x0, y0, x1, y1 = sub.bounds
    W, H = x1 - x0, y1 - y0
    kw = [t for t in sub.texts if TITLE_KW.search(t.text) and len(t.text) <= 40
          and not re.search(r"참조|참고|^\s*\d+[.)]|^\s*[※*]|바람|것$|함\.?$", t.text)]      # '2. 문의 개폐방향은 평면도 참조' 같은 주석은 제외
    if not kw:
        return ""
    tb = [t for t in kw if t.cx > x0 + W * 0.75 or t.cy > y0 + H * 0.80]
    pool = tb or kw
    big = max(t.size for t in kw)
    bigs = {t.text for t in kw if t.size >= big * 0.95}
    if len(bigs) >= 2:
        # 가장 큰 도면명 글자가 여럿(한 도곽에 그림이 둘: '지상3층', '지상4층') → 그보다 작은 표제란 글자('지상3, 4층 창호평면도')를 제목으로
        smaller = [t for t in pool if t.size < big * 0.95]
        if smaller:
            return max(smaller, key=lambda t: (t.size, -len(t.text))).text
    # 표제란 안에서는 '평면도'처럼 짧고 분명한 이름을 선호
    return max(pool, key=lambda t: (t.size, -len(t.text))).text


def frames_from_titles(sheet):
    """도곽 사각형이 없을 때의 대안: 표제란 라벨(DRAWING TITLE/도면명) 위치로 도곽을 추정한다.
    라벨은 도곽 우하단에 있으므로, 이웃 라벨 간격으로 도곽 크기를 잡고 라벨 기준 좌상 방향으로 상자를 만든다."""
    labs = [t for t in sheet.texts if TITLE_LABEL.search(t.text) and len(t.text) <= 40]
    if len(labs) < 2:
        return []
    # 같은 도곽의 라벨(DRAWING NO./TITLE 등)이 여럿이면 하나로 묶기
    th = max(statistics.median(t.size for t in labs), 1)
    pts = []
    for t in sorted(labs, key=lambda t: (t.cy, t.cx)):
        if not any(abs(t.cx - x) < th * 40 and abs(t.cy - y) < th * 15 for x, y in pts):
            pts.append((t.cx, t.cy))
    if len(pts) < 2:
        return []
    xs = sorted({round(x / th) * th for x, _ in pts})
    ys = sorted({round(y / th) * th for _, y in pts})
    dxs = [b - a for a, b in zip(xs, xs[1:]) if b - a > th * 50]
    dys = [b - a for a, b in zip(ys, ys[1:]) if b - a > th * 50]
    W = statistics.median(dxs) if dxs else None
    H = statistics.median(dys) if dys else None
    if W is None and H is None:
        return []
    if W is None:
        W = H * 1.414
    if H is None:
        H = W / 1.414
    out = []
    for x, y in pts:
        # 라벨은 우하단 표제란 안: 오른쪽 여백 ~3%, 아래 여백 ~4%
        x1, y1 = x + W * 0.08, y + H * 0.06
        out.append((x1 - W, y1 - H, x1, y1))
    return out


def _phase_edge(vals, pitch, lo, hi):
    """pitch 간격으로 반복되는 도곽의 경계 위치: 값들을 pitch 로 접었을 때 가장 비어 있는 자리."""
    import math
    if not vals or pitch <= 0:
        return lo
    nb = 60
    bins = [0] * nb
    for v in vals:
        bins[int(((v - lo) % pitch) / pitch * nb) % nb] += 1
    # 빈 구간(연속으로 0 또는 최소)의 가운데
    m = min(bins)
    best, cur, bi, bl = None, 0, 0, 0
    for i in range(2 * nb):
        if bins[i % nb] <= m:
            if cur == 0:
                bi = i
            cur += 1
            if cur > bl:
                bl, best = cur, bi
        else:
            cur = 0
    k = (best + (bl - 1) / 2) % nb if best is not None else 0
    return lo + (k + 0.5) / nb * pitch


def frames_from_title_texts(sheet):
    """도곽 사각형도 표제란 라벨도 없을 때(도곽이 외부참조 xref 라 파일에 없는 경우): 도면명 글자로 도곽을 추정한다.
    같은 줄(y)에 같은 간격으로 늘어선 도면명 글자들 → 간격이 도곽 폭, 높이는 A열 비율(1:1.414).
    도곽 경계 위치는 글자·선 좌표를 폭으로 접어 가장 비어 있는 자리로 잡는다."""
    cands = [t for t in sheet.texts if TITLE_KW.search(t.text) and len(t.text) <= 40
             and not re.search(r"참조|참고|^\s*[:\d]|상세도$", t.text)]
    if len(cands) < 2:
        return []
    rows = []
    for t in sorted(cands, key=lambda t: (t.cy, t.cx)):
        for r in rows:
            if abs(r[0] - t.cy) <= t.size * 0.6 and abs(r[1] - t.size) <= t.size * 0.2:
                r[2].append(t)
                break
        else:
            rows.append([t.cy, t.size, [t]])
    out = []
    xs_all = [t.cx for t in sheet.texts] + [s[0] for s in sheet.segments] + [s[2] for s in sheet.segments]
    rows = [r for r in rows if len(r[2]) >= 2]
    if any(len(r[2]) >= 3 for r in rows):
        rows = [r for r in rows if len(r[2]) >= 3]        # 둘뿐인 줄은 간격을 믿기 어렵다 (셋 이상인 줄이 있으면 뺀다)
    for cy, size, ts in rows:
        ts.sort(key=lambda t: t.cx)
        dxs = sorted(b.cx - a.cx for a, b in zip(ts, ts[1:]) if b.cx - a.cx > size * 10)
        if not dxs:
            continue
        W = statistics.median(dxs)
        # 간격의 정수배로 떨어진 것만 같은 줄의 도곽 (한 줄에 다른 크기 도곽이 섞이면 뺀다)
        ts = [t for t in ts if abs(((t.cx - ts[0].cx) / W) - round((t.cx - ts[0].cx) / W)) < 0.12]
        if len(ts) < 2:
            continue
        H = W / 1.414
        band = [t.cx for t in sheet.texts if abs(t.cy - cy) < H * 1.2]
        x_edge = _phase_edge(band or xs_all, W, ts[0].cx - W, ts[0].cx)
        ys = [u.cy for u in sheet.texts if ts[0].cx - W <= u.cx <= ts[-1].cx + W and abs(u.cy - cy) < H * 1.5]
        y_edge = _phase_edge(ys, H, cy - H, cy + H) if ys else cy - H * 0.9
        for t in ts:
            k = (t.cx - x_edge) // W
            x0 = x_edge + k * W
            k2 = (t.cy - y_edge) // H
            y0 = y_edge + k2 * H
            out.append((x0, y0, x0 + W, y0 + H))
    # 겹치는 상자(도면명 글자와 표제란 글자가 둘 다 잡힌 경우) 중복 제거: 글자가 많이 든 것부터 남긴다
    out.sort(key=lambda r: -_text_count(sheet, r))
    uniq = []
    for r in out:
        if not any(_overlap_frac(r, q) > 0.5 for q in uniq):
            uniq.append(r)
    uniq.sort(key=lambda r: (round(r[1] / max(r[3] - r[1], 1)), r[0]))
    return uniq


class FrameList(list):
    """도곽 목록 (+ method: 도곽을 어떻게 나눴는지)"""
    method = ""


def split_frames(sheet):
    # 외부참조(xref)로 합쳐 들어온 선·사각형은 도곽 찾기에서 뺀다 (배경 도면의 자기 도곽이 겹쳐 들어오므로).
    # 글자·선 내용물은 그대로 쓴다.
    det = sheet
    nn = getattr(sheet, "native_n_segs", None)
    if nn is not None and nn < len(sheet.segments):
        det = Sheet(source=sheet.source, index=sheet.index, width=sheet.width, height=sheet.height,
                    texts=[t for t in sheet.texts if not getattr(t, "xref", False)], segments=sheet.segments[:nn])
        det.bounds = sheet.bounds
        det.rects = getattr(sheet, "native_rects", [])
        det.dims = getattr(sheet, "dims", [])
    frames = find_frames(det)
    sheet.frame_method = getattr(det, "frame_method", "") if det is not sheet else getattr(sheet, "frame_method", "")
    one_big = len(frames) == 1 and _text_count(det, frames[0]) >= 0.6 * max(len(det.texts), 1)
    if len(frames) <= 1 and not one_big:        # 도곽 하나가 글자 대부분을 품고 있으면 그게 진짜 도곽(한 장에 그림 여럿) → 그대로 둔다
        alt = frames_from_titles(det)
        if len(alt) > len(frames):
            frames = alt
            sheet.frame_method = "표제란 글자 위치로 추정"
        else:
            alt = frames_from_title_texts(det)
            if len(alt) > len(frames):
                frames = alt
                sheet.frame_method = "도곽 선·표제란이 없어(외부참조?) 도면명 글자 간격으로 도곽 추정"
    xs = sorted(range(len(sheet.texts)), key=lambda i: sheet.texts[i].cx)
    xk = [sheet.texts[i].cx for i in xs]
    seg = sorted(sheet.segments, key=lambda s: min(s[0], s[2]))
    sk = [min(s[0], s[2]) for s in seg]
    out = []
    # 금속 옆길(layered): 도곽이 정해진 뒤 원본 시트에서 중심 위치로 배분한다. 어느 도곽에도 안 드는 것(모형공간 한쪽의 xref 원본)은 버린다.
    lay_all = getattr(sheet, "layered", None) or []
    lay_c = []
    for q in lay_all:
        try:
            lay_c.append((_lay_center(q), q))
        except Exception:
            pass
    for n, r in enumerate(frames, 1):
        lo, hi = bisect_left(xk, r[0]), bisect_right(xk, r[2])
        texts = [sheet.texts[i] for i in xs[lo:hi] if r[1] <= sheet.texts[i].cy <= r[3]]
        lo, hi = bisect_left(sk, r[0]), bisect_right(sk, r[2])
        segs = [s for s in seg[lo:hi] if max(s[0], s[2]) <= r[2] and
                r[1] <= min(s[1], s[3]) and max(s[1], s[3]) <= r[3]]
        sub = Sheet(source=sheet.source, index=n, width=r[2] - r[0], height=r[3] - r[1],
                    texts=texts, segments=segs)
        sub.bounds = r
        sub.rects = [q for q in getattr(sheet, "rects", []) if _contains(r, q)]
        sub.dims = [d for d in getattr(sheet, "dims", []) if r[0] <= d["tx"] <= r[2] and r[1] <= d["ty"] <= r[3]]
        sub.clips = [c for c in getattr(sheet, "clips", []) if r[0] <= (c["screen"][0] + c["screen"][2]) / 2 <= r[2]
                     and r[1] <= (c["screen"][1] + c["screen"][3]) / 2 <= r[3]]
        sub.layered = [q for (cx, cy), q in lay_c if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]]
        sub.title = guess_title(sub)
        out.append(sub)
    # 제목이 없는 도곽: 표제란 띠가 도곽 사각형 바깥(오른쪽/아래)에 붙은 양식 → 바깥 띠의 글자에서 제목을 찾는다
    for sub in out:
        if sub.title:
            continue
        x0, y0, x1, y1 = sub.bounds
        W, H = x1 - x0, y1 - y0
        others = [o.bounds for o in out if o is not sub]
        strip = [t for t in sheet.texts
                 if ((x1 <= t.cx <= x1 + W * 0.3 and y0 - H * 0.1 <= t.cy <= y1 + H * 0.1) or
                     (x0 - W * 0.1 <= t.cx <= x1 + W * 0.1 and (y1 <= t.cy <= y1 + H * 0.25 or y0 - H * 0.25 <= t.cy <= y0)))
                 and not any(b[0] <= t.cx <= b[2] and b[1] <= t.cy <= b[3] for b in others)]
        if strip:
            tmp = Sheet(source=sheet.source, index=sub.index, width=W, height=H, texts=strip, segments=[])
            tmp.bounds = sub.bounds
            t = _title_from_label(tmp)
            if t:
                sub.title = t
                sub.title_outside = True
    out = FrameList(out)
    out.method = getattr(sheet, "frame_method", "")
    return out


ELEV_KW = re.compile(r"(정면도|배면도|좌측면도|우측면도|측면도|입면도)")
SCHED_LABELS = re.compile(r"^(형\s*태|위\s*치|프\s*레\s*임|부\s*호|부속\s*철물|철\s*물|유\s*리|재료\s*및\s*(유리|종류)|"
                          r"마감\s*및\s*유리|문\s*/\s*유리|위치\s*및\s*(수량|개소)|개소\s*및\s*위치|수\s*량|규\s*격|명\s*칭|"
                          r"문틀\s*/\s*마감|문틀\s*및\s*창틀|유\s*리\s*,\s*마\s*감|명칭\s*및\s*위치\s*/\s*개소|개\s*구\s*부|명칭\s*,\s*위치|마\s*감|부\s*속|비\s*고|재\s*질|재\s*료|치\s*수|창호\s*(종류|명)|종\s*류)$")
EXCLUDE_PLAN = re.compile(r"(구조|배관|전등|전기|설비|배치도|단열|피난|배연|방화구획|조경|주차|오.?우수|천장|천정|가설|비계|산출|구적|기초)")


def classify(sub, tag_types=None):
    t = re.sub(r"\s", "", sub.title)
    labels = sum(1 for x in sub.texts if SCHED_LABELS.match(x.text.strip()))
    n_shape = sum(1 for x in sub.texts if re.sub(r"\s", "", x.text) in ("형태", "형태및치수"))
    if ("일람표" in t and ("창호" in t or re.search(r"^(문|창|도어|DOOR|WINDOW|셔터|샷시|그릴|갤러리)|문\(DOOR\)|창\(WINDOW\)", t, re.I))
            and not re.search(r"하드웨어|철물|HARDWARE|유리|마감|재료", t, re.I)) or ("일람표" not in t and "평면" not in t and "단면" not in t and
                                          ((labels >= 6 and n_shape >= 2) or (not t and labels >= 4 and n_shape >= 1))):
        return "schedule"
    if re.search(r"창호도|부호도", t) and "평면" not in t and not floors_from_title(t) and labels >= 3:
        return "schedule"          # '창호도-1'/'창호 부호도-2'(표제란 오기) : 층 이름 없이 일람표 행 라벨(명칭·위치·부속철물…)이 있으면 창호 일람표
    if "창호" in t and re.search(r"평면|창호도|부호도|안내도|배치도|키플랜|KEY", t, re.I):
        return "tagplan"           # 창호평면도 / 창호 안내도(층별 키플랜) : 부호 태그가 찍힌 평면
    if "평면도" in t and not EXCLUDE_PLAN.search(t):
        return "plan"
    if "목록" not in t and (ELEV_KW.search(t) or (not TITLE_KW.search(t) and
                                                 any(ELEV_KW.search(x.text) and len(x.text) < 12 for x in sub.texts))):
        return "elevation"
    return "other"


def _gap_cut(segments, lo, hi, y0, y1, n=400):
    """x가 lo~hi, y가 y0~y1 인 띠에서 선분이 없는 가장 넓은 빈 구간의 중앙 x.
    두 도면을 함께 가로지르는 긴 선(테두리·그리드선·치수선)은 무시한다. 빈 구간이 없으면 None."""
    step = (hi - lo) / n
    if step <= 0:
        return None
    cov = [False] * n
    for a, b, c, d in segments:
        sx0, sx1 = min(a, c), max(a, c)
        if sx1 < lo or sx0 > hi or sx1 - sx0 > (hi - lo) * 0.3:
            continue
        if not (y0 <= min(b, d) and max(b, d) <= y1):
            continue
        i0, i1 = max(0, int((sx0 - lo) / step)), min(n - 1, int((sx1 - lo) / step))
        for i in range(i0, i1 + 1):
            cov[i] = True
    best, i = (0, None), 0
    while i < n:
        if cov[i]:
            i += 1
            continue
        j = i
        while j < n and not cov[j]:
            j += 1
        if j - i > best[0]:
            best = (j - i, (i + j) / 2)
        i = j
    return lo + best[1] * step if best[1] is not None else None


def split_by_subtitles(sheet, want):
    """한 도곽 안에 여러 도면(예: 층별 창호평면도 4개)이 있을 때 소제목으로 나눈다.
    want(text) -> bool 로 소제목을 고르고, 소제목은 각 그림 '아래'에 있다고 본다.
    가로 경계는 이웃 소제목 사이에서 선이 없는 가장 넓은 틈(도면 사이 여백)으로 잡는다
    (소제목이 그림 가운데가 아니라 왼쪽에 붙어 있어도 옆 도면을 침범하지 않도록).
    반환: [(소제목, Sheet)] / 소제목이 1개 이하이면 []."""
    subs = [t for t in sheet.texts if want(t.text) and t.angle == 0]
    if len(subs) < 2:
        return []
    big = max(t.size for t in subs)
    subs = [t for t in subs if t.size >= big * 0.8]          # 가장 큰 소제목 크기만(범례 글자 제외)
    x0, y0, x1, y1 = sheet.bounds
    h = big

    def cluster(vals, tol):
        out = []
        for v in sorted(vals):
            if out and v - out[-1][-1] <= tol:
                out[-1].append(v)
            else:
                out.append([v])
        return [sum(c) / len(c) for c in out]
    rows = cluster([t.cy for t in subs], h * 4)
    ycut = [y0] + [r + h * 1.5 for r in rows]                # 소제목 바로 아래까지가 그 그림
    out = []
    for ri, ry in enumerate(rows):
        ts = sorted([t for t in subs if abs(t.cy - ry) <= h * 4], key=lambda t: t.cx)
        by0, by1 = ycut[ri], ycut[ri + 1]
        xcut = [x0]
        for a, b in zip(ts, ts[1:]):
            g = _gap_cut(sheet.segments, a.cx, b.cx, by0, by1)
            xcut.append(g if g is not None else (a.cx + b.cx) / 2)
        xcut.append(x1)
        for ci, t in enumerate(ts):
            bx0, bx1 = xcut[ci], xcut[ci + 1]
            sub = Sheet(source=sheet.source, index=sheet.index, width=bx1 - bx0, height=by1 - by0,
                        texts=[u for u in sheet.texts if bx0 <= u.cx <= bx1 and by0 <= u.cy <= by1],
                        segments=[s for s in sheet.segments if bx0 <= min(s[0], s[2]) and max(s[0], s[2]) <= bx1
                                  and by0 <= min(s[1], s[3]) and max(s[1], s[3]) <= by1])
            sub.bounds = (bx0, by0, bx1, by1)
            sub.layered = []
            for q in getattr(sheet, "layered", None) or []:
                try:
                    cx, cy = _lay_center(q)
                except Exception:
                    continue
                if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                    sub.layered.append(q)
            sub.title = t.text
            sub.parent_title = sheet.title
            sub.file = getattr(sheet, "file", "")
            out.append((t.text, sub))
    return out
