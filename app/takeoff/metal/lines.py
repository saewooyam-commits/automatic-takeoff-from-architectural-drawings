"""PDF/DXF 선을 굵기·색·점선 모양별로 묶고, 쪼개진 점선을 한 줄로 복원한다."""
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass

import pymupdf


@dataclass
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    color: tuple

    @property
    def length(self):
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass
class Run:
    """점선 조각들을 이은 한 줄."""
    orient: str            # 'h' | 'v'
    c: float               # 가로선이면 y, 세로선이면 x
    a: float               # 시작
    b: float               # 끝
    width: float
    color: tuple
    n: int                 # 조각 수
    dash: float            # 평균 조각 길이
    gap: float             # 평균 간격
    inked: float           # 실제 칠해진 길이

    @property
    def length(self):
        return self.b - self.a

    @property
    def style(self):
        kind = "solid" if self.n == 1 or self.gap < 0.3 else "dash"
        return (round(self.width, 2), tuple(round(v, 2) for v in self.color), kind,
                round(self.dash, 0) if kind == "dash" else 0)


def page_segments(page):
    m = page.rotation_matrix
    out = []
    for d in page.get_drawings():
        w = d.get("width") or 0.0
        col = tuple(d.get("color") or ())
        for it in d["items"]:
            if it[0] == "l":
                p0, p1 = it[1] * m, it[2] * m
                out.append(Seg(p0.x, p0.y, p1.x, p1.y, w, col))
            elif it[0] == "re":
                r = it[1] * m
                r.normalize()
                for a, b in (((r.x0, r.y0), (r.x1, r.y0)), ((r.x1, r.y0), (r.x1, r.y1)),
                             ((r.x1, r.y1), (r.x0, r.y1)), ((r.x0, r.y1), (r.x0, r.y0))):
                    out.append(Seg(a[0], a[1], b[0], b[1], w, col))
    return out


def build_runs(segs, max_gap=4.0, tol=0.25):
    """같은 굵기·색·직선 위의 조각을 간격 max_gap(pt) 이하면 이어 붙인다."""
    groups = defaultdict(list)
    for s in segs:
        if s.length < 0.05:
            continue
        key = (round(s.width, 2), tuple(round(v, 2) for v in s.color))
        if abs(s.y1 - s.y0) <= tol:
            groups[key + ("h", round((s.y0 + s.y1) / 2 / tol))].append((min(s.x0, s.x1), max(s.x0, s.x1), (s.y0 + s.y1) / 2))
        elif abs(s.x1 - s.x0) <= tol:
            groups[key + ("v", round((s.x0 + s.x1) / 2 / tol))].append((min(s.y0, s.y1), max(s.y0, s.y1), (s.x0 + s.x1) / 2))
    runs = []
    for (w, col, o, _), lst in groups.items():
        lst.sort()
        cur = None
        for a, b, c in lst:
            if cur and a - cur["b"] <= max_gap:
                if a > cur["b"]:
                    cur["gaps"].append(a - cur["b"])
                cur["b"] = max(cur["b"], b)
                cur["dashes"].append(b - a)
            else:
                if cur:
                    runs.append(cur)
                cur = {"o": o, "c": c, "a": a, "b": b, "w": w, "col": col, "dashes": [b - a], "gaps": []}
        if cur:
            runs.append(cur)
    out = []
    for r in runs:
        out.append(Run(r["o"], r["c"], r["a"], r["b"], r["w"], r["col"], len(r["dashes"]),
                       statistics.median(r["dashes"]), statistics.median(r["gaps"]) if r["gaps"] else 0.0,
                       sum(r["dashes"])))
    return out


NUM = re.compile(r"^\d{1,3}(,\d{3})+$|^\d{3,6}$")


def estimate_scale(texts, segs, region=None):
    """치수 글자(예: 18,600)와 바로 아래/위 가로 치수선 길이로 mm/pt를 추정 (중앙값)."""
    ratios = []
    hs = [s for s in segs if abs(s.y1 - s.y0) < 0.2 and s.length > 5 and s.width < 0.2]
    for t in texts:
        if t.angle != 0 or not NUM.match(t.text):
            continue
        if region and not (region[0] <= t.cx <= region[2] and region[1] <= t.cy <= region[3]):
            continue
        v = float(t.text.replace(",", ""))
        if v < 500:
            continue
        best = None
        for s in hs:
            y = (s.y0 + s.y1) / 2
            if 0 <= y - t.y1 <= 3.0 or 0 <= t.y0 - y <= 3.0:
                mx = (s.x0 + s.x1) / 2
                if abs(mx - t.cx) <= max(s.length * 0.1, 2):
                    if best is None or abs(mx - t.cx) < best[0]:
                        best = (abs(mx - t.cx), s)
        if best:
            ratios.append(v / best[1].length)
    if not ratios:
        return None, 0
    ratios.sort()
    med = ratios[len(ratios) // 2]
    good = [r for r in ratios if abs(r - med) / med < 0.02]
    return statistics.median(good), len(good)


def estimate_scale_chain(texts, region=None, angle=0):
    """이어진 치수 글자 간격으로 축척(mm/pt) 투표.
    같은 줄의 이웃 치수 V1, V2 글자 중심 간격 = (V1+V2)/2 / 축척."""
    ts = [t for t in texts if t.angle == angle and NUM.match(t.text)]
    if region:
        ts = [t for t in ts if region[0] <= t.cx <= region[2] and region[1] <= t.cy <= region[3]]
    rows = defaultdict(list)
    for t in ts:
        key = round((t.cy if angle == 0 else t.cx) / 1.0)
        rows[key].append(t)
    ratios = []
    for lst in rows.values():
        lst.sort(key=lambda t: t.cx if angle == 0 else t.cy)
        for a, b in zip(lst, lst[1:]):
            va, vb = float(a.text.replace(",", "")), float(b.text.replace(",", ""))
            d = (b.cx - a.cx) if angle == 0 else (b.cy - a.cy)
            if d > 3 and va >= 300 and vb >= 300:
                ratios.append((va + vb) / 2 / d)
    if not ratios:
        return None, 0
    # 2% 안에 가장 많이 모이는 값
    ratios.sort()
    best, bestn = None, 0
    for r in ratios:
        n = sum(1 for q in ratios if abs(q - r) / r <= 0.02)
        if n > bestn:
            best, bestn = r, n
    near = [q for q in ratios if abs(q - best) / best <= 0.02]
    return statistics.median(near), len(near)


def estimate_scale_any(texts, region=None):
    r1 = estimate_scale_chain(texts, region, 0)
    r2 = estimate_scale_chain(texts, region, 270)
    r3 = estimate_scale_chain(texts, region, 90)
    cands = [r for r in (r1, r2, r3) if r[0]]
    if not cands:
        return None, 0
    return max(cands, key=lambda r: r[1])


def merge_pairs(runs, max_sep):
    """각관처럼 양쪽 면이 평행한 두 줄로 그려진 부재를 한 줄로 합친다.
    같은 방향·간격 max_sep(pt) 이하·구간이 절반 이상 겹치면 하나로 본다."""
    out = []
    by = defaultdict(list)
    for r in runs:
        by[r.orient].append(r)
    for o, lst in by.items():
        lst.sort(key=lambda r: (r.c, r.a))
        used = [False] * len(lst)
        for i, r in enumerate(lst):
            if used[i]:
                continue
            used[i] = True
            a, b = r.a, r.b
            for j in range(i + 1, len(lst)):
                q = lst[j]
                if q.c - r.c > max_sep:
                    break
                if used[j]:
                    continue
                ov = min(b, q.b) - max(a, q.a)
                if ov >= 0.5 * min(b - a, q.b - q.a):
                    used[j] = True
                    a, b = min(a, q.a), max(b, q.b)
            out.append(Run(o, r.c, a, b, r.width, r.color, r.n, r.dash, r.gap, r.inked))
    return out
