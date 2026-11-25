"""창호도(일람표) 모양을 입면도에서 찾아 위치와 개수를 센다.

CAD에서는 일람표 입면과 건물 입면이 모두 실제 치수(mm)로 그려지므로,
일람표 칸의 창 그림(외곽 + 멀리언/무창살 + 열림 표시 사선)을 '형판'으로 떼어
입면도 위에 대보고 선이 얼마나 겹치는지(길이 비율)로 판정한다.
좌우 대칭(열림 방향 반대) 형판도 함께 시험한다.
"""
import math
from collections import defaultdict


def _merge(intervals, gap):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


class SegIndex:
    """선분을 가로/세로/사선으로 나눠 격자 색인."""

    def __init__(self, segments, tol):
        self.tol = tol
        self.h = defaultdict(list)   # key(y) -> [(x0,x1,y)]
        self.v = defaultdict(list)   # key(x) -> [(y0,y1,x)]
        self.d = defaultdict(list)   # key(x,y of first end) -> [(x0,y0,x1,y1)]
        self.hlist = []
        for x0, y0, x1, y1 in segments:
            if abs(y0 - y1) <= tol * 0.5 and abs(x1 - x0) > tol:
                y = (y0 + y1) / 2
                a, b = min(x0, x1), max(x0, x1)
                self.h[round(y / tol)].append((a, b, y))
                self.hlist.append((a, b, y))
            elif abs(x0 - x1) <= tol * 0.5 and abs(y1 - y0) > tol:
                x = (x0 + x1) / 2
                self.v[round(x / tol)].append((min(y0, y1), max(y0, y1), x))
            elif math.hypot(x1 - x0, y1 - y0) > tol * 3:
                for (ax, ay) in ((x0, y0), (x1, y1)):
                    self.d[(round(ax / tol), round(ay / tol))].append((x0, y0, x1, y1))

    def _cover(self, table, key_val, a, b):
        k = round(key_val / self.tol)
        iv = []
        for kk in (k - 1, k, k + 1):
            for s0, s1, c in table.get(kk, ()):
                if abs(c - key_val) <= self.tol and s1 > a and s0 < b:
                    iv.append((max(s0, a), min(s1, b)))
        return sum(q - p for p, q in _merge(iv, self.tol))

    def h_cover(self, y, a, b):
        return self._cover(self.h, y, a, b)

    def v_cover(self, x, a, b):
        return self._cover(self.v, x, a, b)

    def has_diag(self, x0, y0, x1, y1):
        k = (round(x0 / self.tol), round(y0 / self.tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for s in self.d.get((k[0] + dx, k[1] + dy), ()):
                    for (p, q) in (((s[0], s[1]), (s[2], s[3])), ((s[2], s[3]), (s[0], s[1]))):
                        if abs(p[0] - x0) <= self.tol * 1.5 and abs(p[1] - y0) <= self.tol * 1.5 and \
                                abs(q[0] - x1) <= self.tol * 1.5 and abs(q[1] - y1) <= self.tol * 1.5:
                            return True
        return False


def make_template(item, tol):
    """일람표 칸에서 창 그림 형판 추출 → (W, H, [(kind, 상대좌표...)])."""
    sh = getattr(item, "region_sheet", None)
    reg = getattr(item, "region", None)
    W, H = item.width, item.height
    if sh is None or reg is None or not W or not H:
        return None
    l, t, r, b = reg
    m = max(tol * 10, (b - t) * 0.04)
    l, t, r, b = l - m, t - m, r + m, b + m
    segs = [s for s in sh.segments if l <= min(s[0], s[2]) and max(s[0], s[2]) <= r and
            t <= min(s[1], s[3]) and max(s[1], s[3]) <= b]
    if not segs:
        return None
    ix = SegIndex(segs, tol)
    # 외곽 찾기: 길이≈W 가로선 + 그 끝에서 길이≈H 세로선
    tw, th = max(tol * 2, W * 0.012), max(tol * 2, H * 0.012)
    best = None
    for a, b_, y in ix.hlist:
        for (xa, xb) in ((a, b_),):
            if abs((xb - xa) - W) > tw:
                continue
            for y0 in (y, y - H):      # 이 선이 윗변 / 아랫변
                lc = ix.v_cover(xa, y0, y0 + H)
                rc = ix.v_cover(xa + W, y0, y0 + H)
                sc = (lc + rc) / (2 * H)
                if sc > 0.7 and (best is None or sc > best[0]):
                    best = (sc, xa, y0)
    if not best:
        return None
    _, x0, y0 = best
    parts = []
    for s in segs:
        sx0, sy0, sx1, sy1 = s
        if not (x0 - tol <= min(sx0, sx1) and max(sx0, sx1) <= x0 + W + tol and
                y0 - tol <= min(sy0, sy1) and max(sy0, sy1) <= y0 + H + tol):
            continue
        L = math.hypot(sx1 - sx0, sy1 - sy0)
        if L < min(W, H) * 0.04:
            continue
        if abs(sy0 - sy1) <= tol * 0.5:
            parts.append(("h", (sy0 + sy1) / 2 - y0, min(sx0, sx1) - x0, max(sx0, sx1) - x0))
        elif abs(sx0 - sx1) <= tol * 0.5:
            parts.append(("v", (sx0 + sx1) / 2 - x0, min(sy0, sy1) - y0, max(sy0, sy1) - y0))
        else:
            parts.append(("d", sx0 - x0, sy0 - y0, sx1 - x0, sy1 - y0))
    # 같은 선 중복 제거
    uniq = {}
    for p in parts:
        uniq[tuple(round(v / tol) if isinstance(v, float) else v for v in p)] = p
    return W, H, list(uniq.values())


def _mirror(parts, W):
    out = []
    for p in parts:
        if p[0] == "h":
            out.append(("h", p[1], W - p[3], W - p[2]))
        elif p[0] == "v":
            out.append(("v", W - p[1], p[2], p[3]))
        else:
            out.append(("d", W - p[1], p[2], W - p[3], p[4]))
    return out


def _score(ix, parts, x0, y0):
    tot = got = 0.0
    for p in parts:
        if p[0] == "h":
            L = p[3] - p[2]
            tot += L
            got += ix.h_cover(y0 + p[1], x0 + p[2], x0 + p[3])
        elif p[0] == "v":
            L = p[3] - p[2]
            tot += L
            got += ix.v_cover(x0 + p[1], y0 + p[2], y0 + p[3])
        else:
            L = math.hypot(p[3] - p[1], p[4] - p[2])
            tot += L
            if ix.has_diag(x0 + p[1], y0 + p[2], x0 + p[3], y0 + p[4]):
                got += L
    return got / tot if tot else 0.0


def _precision(ix, parts, x0, y0, W, H):
    """입면도에서 이 상자 안에 있는 가로/세로 선 길이 중 형판 선과 겹치는 비율."""
    tix = SegIndex([], ix.tol)
    for p in parts:
        if p[0] == "h":
            tix.h[round((y0 + p[1]) / ix.tol)].append((x0 + p[2], x0 + p[3], y0 + p[1]))
        elif p[0] == "v":
            tix.v[round((x0 + p[1]) / ix.tol)].append((y0 + p[2], y0 + p[3], x0 + p[1]))
    tot = got = 0.0
    m = ix.tol * 2
    for ky in range(round((y0 + m) / ix.tol), round((y0 + H - m) / ix.tol) + 1):
        for a, b, y in ix.h.get(ky, ()):
            a2, b2 = max(a, x0), min(b, x0 + W)
            if b2 - a2 > ix.tol and y0 + m <= y <= y0 + H - m:
                tot += b2 - a2
                got += tix.h_cover(y, a2, b2)
    for kx in range(round((x0 + m) / ix.tol), round((x0 + W - m) / ix.tol) + 1):
        for a, b, x in ix.v.get(kx, ()):
            a2, b2 = max(a, y0), min(b, y0 + H)
            if b2 - a2 > ix.tol and x0 + m <= x <= x0 + W - m:
                tot += b2 - a2
                got += tix.v_cover(x, a2, b2)
    return got / tot if tot else 1.0


def accept(rec, prec, min_score):
    """재현율이 아주 높거나, 재현율·정밀도가 둘 다 충분하면 채택."""
    return rec >= min_score or (rec >= 0.65 and prec >= 0.6)


def find_in_sheet(ix, tpl, min_score=0.8):
    W, H, parts = tpl
    variants = [parts, _mirror(parts, W)]
    tw = max(ix.tol * 2, W * 0.012)
    hits = []
    # 외곽 윗변/아랫변 후보: 길이≈W 가로선 (끊긴 선은 이어 붙여서)
    rows = defaultdict(list)
    for a, b, y in ix.hlist:
        rows[round(y / ix.tol)].append((a, b, y))
    cands = set()
    for k, lst in rows.items():
        y = sum(v[2] for v in lst) / len(lst)
        for a, b in _merge([(v[0], v[1]) for v in lst], ix.tol):
            if b - a + tw >= W:
                # 이어진 긴 선 위의 모든 시작점 후보(세로선 위치)를 시도
                xs = {a}
                for kk in range(round(a / ix.tol) - 1, round(b / ix.tol) + 2):
                    for s0, s1, x in ix.v.get(kk, ()):
                        if a - ix.tol <= x <= b - W + tw and s0 - ix.tol <= y <= s1 + ix.tol:
                            xs.add(x)
                for x in xs:
                    if x + W <= b + tw:
                        cands.add((round(x, 0), round(y, 0)))
    for x, y in cands:
        for y0 in (y, y - H):
            # 빠른 거르기: 좌우 세로 외곽선
            if ix.v_cover(x, y0, y0 + H) < H * 0.6 or ix.v_cover(x + W, y0, y0 + H) < H * 0.6:
                continue
            best = None
            for v in variants:
                rec = _score(ix, v, x, y0)
                if rec < 0.6:
                    continue
                prec = _precision(ix, v, x, y0, W, H)
                if accept(rec, prec, min_score):
                    sc = (rec + prec) / 2 if rec < min_score else max(rec, (rec + prec) / 2)
                    if best is None or sc > best:
                        best = sc
            if best is not None:
                hits.append((best, x, y0, W, H))
    return hits


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a[1], a[2], a[1] + a[3], a[2] + a[4]
    bx0, by0, bx1, by1 = b[1], b[2], b[1] + b[3], b[2] + b[4]
    iw, ih = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))


class _Result(defaultdict):
    """code -> hits, 그리고 .skipped(건너뛴 입면도 안내)"""
    def __init__(self):
        super().__init__(list)
        self.skipped = []


MAX_SEGS = 80000        # 이보다 선이 많은 입면도는 짧은 선(해칭)을 걸러낸 뒤에도 많으면 건너뜀
TIME_BUDGET_S = 90      # 입면도 한 장당 비교 시간 상한


def match_elevations(items, elev_sheets, tol, min_score=0.8, log=None):
    """반환: {code: [(도곽번호, 점수, x, y, W, H)]} (겹치는 후보는 점수 높은 부호 하나만)."""
    import time
    tpls = {}
    for it in items:
        tp = make_template(it, tol)
        if tp and len(tp[2]) >= 4:
            tpls[it.code] = tp
    result = _Result()
    result_skipped = result.skipped
    min_dim = min([min(W, H) for W, H, _ in tpls.values()] or [300])
    for sh in elev_sheets:
        segs = sh.segments
        if len(segs) > MAX_SEGS:
            # 형판의 가장 짧은 변의 4% 미만인 선(해칭·질감)은 모양 판정에 기여하지 않으므로 제외
            lim = min_dim * 0.04
            segs = [s for s in segs if math.hypot(s[2] - s[0], s[3] - s[1]) >= lim]
            if len(segs) > MAX_SEGS * 2:
                result_skipped.append(f"도곽 {sh.index} {sh.title}: 선 {len(sh.segments):,}개(걸러낸 뒤 {len(segs):,}개)로 너무 많아 모양 비교 생략")
                continue
        ix = SegIndex(segs, tol)
        allhits = []
        t0 = time.time()
        timed_out = False
        for code, tp in tpls.items():
            if time.time() - t0 > TIME_BUDGET_S:
                timed_out = True
                break
            for h in find_in_sheet(ix, tp, min_score):
                allhits.append(h + (code,))
        if timed_out:
            result_skipped.append(f"도곽 {sh.index} {sh.title}: {TIME_BUDGET_S}초 초과로 일부 부호만 비교")
        # 큰 창·높은 점수 우선으로 겹침 제거 (작은 창이 큰 창 안의 일부로 잡히는 것 방지)
        allhits.sort(key=lambda h: (-h[0] * 0 - h[3] * h[4] * h[0], -h[0]))
        kept = []
        for h in allhits:
            if all(_iou(h, k) < 0.5 for k in kept):
                kept.append(h)
        for sc, x, y, W, H, code in kept:
            result[code].append((sh.index, round(sc, 2), x, y, W, H))
    if log:
        for m in result_skipped:
            log("주의: " + m)
    return result, tpls
