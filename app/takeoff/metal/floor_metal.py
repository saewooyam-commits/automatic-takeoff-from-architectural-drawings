"""금속 ① 트렌치·재료분리대 (v25, 2026-11-25).

도면에 그려진 대로만 낸다. 길이를 모르거나 문을 못 가린 것은 수량 칸을 비우거나(None) '(확인 필요)' 줄로 보낸다.
산출 기준:
  - 트렌치: 종류·폭별 줄, 단위 m. 토목·조경 측구는 넣지 않는다.
  - 재료분리대: 길이 = 문틀 안쪽 폭 − 플로어힌지 길이(하나당 rules '플로어힌지_길이_mm', 임시 307) × 힌지 수.
    방화문에는 없다(그려져 있어도 세지 않고 표시만). 플로어힌지도 방화문도 아닌 문은 안쪽 폭 그대로 + 확인 필요.

도면 읽기는 dxf_source 의 옆길(Sheet.layered: 레이어 이름이 트렌치·재료분리대인 선, 무소음트렌치 블록)과 도곽 글자를 쓴다.
같은 것이 여러 도곽(평면도·확대평면도·창호평면도, 개정본)에 반복되므로 같은 층 도곽끼리 겹침을 확인해 한 번만 센다.
"""
import math
import re
from collections import Counter, defaultdict

from ..tags import floors_from_title, floor_sort_key

TRENCH_LAYER = re.compile(r"트렌치|트랜치|TRENCH", re.I)
DIV_LAYER = re.compile(r"재료\s*분리대", re.I)
SYMBOL_BLOCK = re.compile(r"트[렌랜]치|TRENCH", re.I)          # 덮개·단면 기호 블록: 그 안의 선은 길이에 넣지 않는다
PLAN_EXCL = re.compile(r"단면|일람|계단|배치|조경|오.?우수|구조|설비|전기|배관|전등|방화구획|피난|단열|천장|천정|가설|비계|구적|기초|산출|"
                       r"배연|장애인|대지|교통|광고|도로|입면|전개")
FH_RE = re.compile(r"플로어\s*힌지|후로아\s*힌지|FLOOR\s*HINGE", re.I)
FH_N_RE = re.compile(r"(?:플로어\s*힌지|후로아\s*힌지|FLOOR\s*HINGE)\s*(?:\([^)]*\))?\s*(\d{1,2})\s*(?:조|개|EA|SET)", re.I)
SLIDE_RE = re.compile(r"미닫이|자동\s*문|슬라이딩|행거|SLIDING|AUTO", re.I)
SINGLE_RE = re.compile(r"외\s*여닫이|편개|외짝|외\s*개", re.I)
DOUBLE_RE = re.compile(r"양\s*여닫이|쌍\s*여닫이|양개|쌍문|쌍\s*개", re.I)
GENERIC_HW = re.compile(r"^\s*(부속\s*철물\s*일체|철물\s*일체|일체|-|)\s*$")

TR_TXT = re.compile(r"트[렌랜]치|TRENCH", re.I)
CIVIL_TXT = re.compile(r"측구|토목|U\s*형|조경", re.I)
DIV_TXT = re.compile(r"재료\s*분리대", re.I)
NOTE_TXT = re.compile(r"설치|상이|이질|만나는|사용|시공", re.I)
W_RE = re.compile(r"W\s*[=:：]?\s*(\d{2,4})(?!\d)", re.I)


def _fmt(v):
    return f"{v:,.0f}"


def _m(v_mm):
    return round(v_mm / 1000.0, 2)


def _flabel(fl):
    """층 튜플 → 사람이 읽는 이름 ('B1' → 지하1층, ('4F'…'17F') → 4~17층)."""
    def one(x):
        if x.startswith("B"):
            return f"지하{x[1:]}층"
        if x.endswith("F") and x[:-1].isdigit():
            return f"{x[:-1]}층"
        return {"RF": "지붕·옥탑", "PIT": "PIT층", "ATTIC": "다락"}.get(x, x)
    if not fl:
        return "층 미상"
    if len(fl) == 1:
        return one(fl[0])
    return f"{one(fl[0]).rstrip('층')}~{one(fl[-1])}"


def _ftitle(f):
    t = (getattr(f, "title", "") or "").strip()
    return f"{t or '(제목 없음)'}(도곽 {getattr(f, 'index', '?')})"


def _scale(f):
    """도곽의 치수값/실제 거리 비율 (mm/도면단위). 믿을 만하지 않으면 (1.0, False)."""
    rs = Counter()
    for d in getattr(f, "dims", []) or []:
        g = d["span"][1] - d["span"][0]
        if g > 1e-6 and d.get("val", 0) > 0:
            rs[round(d["val"] / g, 2)] += 1
    if not rs:
        return 1.0, False
    v, n = rs.most_common(1)[0]
    if n >= 5 and n >= 0.5 * sum(rs.values()) and v > 0:
        return (1.0 if abs(v - 1) < 0.02 else v), True
    return 1.0, False


def _is_metal_plan(f, classify=None):
    t = re.sub(r"\s", "", getattr(f, "title", "") or "")
    if not t:
        return False
    if PLAN_EXCL.search(t):
        return False
    if "평면" in t:
        return True
    if classify is not None:
        try:
            return classify(f) == "tagplan"
        except Exception:
            return False
    return bool(re.search(r"창호도|안내도|부호도", t))


def _candidate_frames(frames, plan_frames):
    """금속 표시를 볼 평면 종류 도곽. 소제목으로 나눈 창호평면도 조각(plan_frames 에만 있음)은 부모 대신 쓴다."""
    try:
        from ..frames import classify
    except Exception:
        classify = None
    ids = {id(f) for f in frames}
    subs = [f for f, _, _ in plan_frames if id(f) not in ids]
    split_parents = {getattr(s, "index", None) for s in subs}
    out = [f for f in frames if _is_metal_plan(f, classify) and getattr(f, "index", None) not in split_parents]
    seen = set()
    for s in subs:
        if id(s) not in seen:
            seen.add(id(s))
            out.append(s)
    return out


# ---------------------------------------------------------------------------------------------------------
# 도형 정리
def _plen(pts, closed=False):
    ring = pts + [pts[0]] if closed else pts
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(ring, ring[1:]))


def _rect_info(pts, axis_only=False, tol=0.6):
    """닫힌 4점이 직사각형이면 (긴 변, 짧은 변, 중심, 긴 변 방향 단위벡터, 축과 나란한지). 아니면 None."""
    if len(pts) != 4:
        return None
    v = [(pts[(i + 1) % 4][0] - pts[i][0], pts[(i + 1) % 4][1] - pts[i][1]) for i in range(4)]
    L = [math.hypot(*a) for a in v]
    if min(L) < 1e-6:
        return None
    for i in range(4):
        a, b = v[i], v[(i + 1) % 4]
        if abs(a[0] * b[0] + a[1] * b[1]) > 0.01 * L[i] * L[(i + 1) % 4]:
            return None
    if abs(L[0] - L[2]) > tol or abs(L[1] - L[3]) > tol:
        return None
    long_i = 0 if L[0] >= L[1] else 1
    lv = (v[long_i][0] / L[long_i], v[long_i][1] / L[long_i])
    axis = abs(lv[0]) < 1e-3 or abs(lv[1]) < 1e-3
    if axis_only and not axis:
        return None
    cx = sum(p[0] for p in pts) / 4
    cy = sum(p[1] for p in pts) / 4
    return max(L[0], L[1]), min(L[0], L[1]), (cx, cy), lv, axis


def _in_symbol_block(r):
    return any(SYMBOL_BLOCK.search(n or "") for n in r.get("chain", ()))


def _frame_metal(f, s):
    """도곽 하나의 금속 도형을 종류별로 나눈다. 길이는 도면 단위(×s 하면 mm)."""
    out = {"tr_open": [], "tr_cutclosed": [], "tr_rect": [], "tr_bent": [], "tr_ins": [], "tr_ins_small": [],
           "tr_inblk": 0, "dv_rect": [], "dv_other": [], "dv_cut": 0, "small_tr": 0, "small_dv": 0}
    seen_ins = set()
    for r in getattr(f, "layered", None) or []:
        try:
            if r["k"] == "poly" and abs(r.get("sc", 1.0) - 1) > 0.05:
                # 축소·확대 삽입한 그림(키플랜 등) 속의 선: 실제 크기가 아니므로 세지 않는다
                if TRENCH_LAYER.search(r.get("layer", "")):
                    out["small_tr"] += 1
                elif DIV_LAYER.search(r.get("layer", "")):
                    out["small_dv"] += 1
                continue
            if r["k"] == "ins":
                key = (round(r["cx"]), round(r["cy"]), round(r["w"]), round(r["h"]))
                if key in seen_ins:
                    out.setdefault("ins_dup", 0)
                    out["ins_dup"] += 1          # 같은 자리에 겹쳐 그린 같은 블록: 한 번만
                    continue
                seen_ins.add(key)
                big = max(r["w"], r["h"]) * s
                real = abs(abs(r.get("sx", 1)) - 1) < 0.05 and abs(abs(r.get("sy", 1)) - 1) < 0.05 and big >= 200
                (out["tr_ins"] if real else out["tr_ins_small"]).append(r)
                continue
            lay = r.get("layer", "")
            if TRENCH_LAYER.search(lay):
                if _in_symbol_block(r):
                    out["tr_inblk"] += 1
                    continue
                if r["closed"]:
                    ri = _rect_info(r["pts"], axis_only=True)
                    if ri:
                        out["tr_rect"].append((r, ri))
                    else:
                        out["tr_bent"].append(r)
                elif r.get("was_closed"):
                    out["tr_cutclosed"].append(r)
                else:
                    out["tr_open"].append(r)
            elif DIV_LAYER.search(lay):
                if r["closed"]:
                    ri = _rect_info(r["pts"])
                    if ri and ri[1] * s <= 300 and ri[0] >= 3 * ri[1]:
                        out["dv_rect"].append((r, ri))
                        continue
                if r.get("cut"):
                    out["dv_cut"] += 1
                out["dv_other"].append(r)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------------------------------------
# 같은 층 도곽끼리 겹침 (평면도와 확대평면도, 개정본): 90도 단위 회전·평행 이동으로 한쪽이 다른 쪽에 다 들어가면 같은 것
def _segs_of(polys):
    out = []
    for r in polys:
        pts = r["pts"]
        ring = pts + [pts[0]] if r.get("closed") else pts
        out += [(a[0], a[1], b[0], b[1]) for a, b in zip(ring, ring[1:]) if (a[0], a[1]) != (b[0], b[1])]
    return out


def _uniq_texts(f, boiler=()):
    c = Counter(t.text for t in f.texts)
    return {t.text: t for t in f.texts if c[t.text] == 1 and len(t.text) >= 2 and t.text not in boiler
            and not re.fullmatch(r"[\d,.\s\-+=×xX*/()]+", t.text)}


ROTS = ((1, 0), (0, 1), (-1, 0), (0, -1))          # 회전 (cos, sin): 0·90·180·270도. 확대평면도는 평면도를 돌려 그리는 일이 있다


def _tf(T, x, y):
    """T = (cos, sin, dx, dy): 회전 뒤 평행 이동."""
    c, s, dx, dy = T
    return c * x - s * y + dx, s * x + c * y + dy


def _tf_inv(T, x, y):
    c, s, dx, dy = T
    x, y = x - dx, y - dy
    return c * x + s * y, -s * x + c * y


def _offsets(fa, fb, ga, gb, boiler=()):
    """fa 좌표 → fb 좌표 변환 후보 [((cos, sin, dx, dy), 표 수)] (많이 맞는 순). 90도 단위 회전 + 평행 이동.
    boiler: 표제란처럼 거의 모든 도곽에 같은 자리로 있는 글자(도곽끼리의 이동만 알려 주므로 뺀다)."""
    votes = Counter()

    def vote(rot, ax, ay, bx, by, w=1):
        c, s = rot
        X, Y = c * ax - s * ay, s * ax + c * ay
        votes[(c, s, round((bx - X) / 5) * 5, round((by - Y) / 5) * 5)] += w
    ua, ub = _uniq_texts(fa, boiler), _uniq_texts(fb, boiler)
    for k, ta in ua.items():
        tb = ub.get(k)
        if tb is not None:
            for rot in ROTS:
                vote(rot, ta.cx, ta.cy, tb.cx, tb.cy)
    sa = _segs_of([r for r in ga["tr_open"] + ga["tr_cutclosed"] + ga["tr_bent"]] + [r for r, _ in ga["tr_rect"]])
    sb = _segs_of([r for r in gb["tr_open"] + gb["tr_cutclosed"] + gb["tr_bent"]] + [r for r, _ in gb["tr_rect"]])
    byl = defaultdict(list)
    for t in sb:
        byl[round(math.hypot(t[2] - t[0], t[3] - t[1]))].append(t)
    for sg in sorted(sa, key=lambda q: -math.hypot(q[2] - q[0], q[3] - q[1]))[:150]:
        L = round(math.hypot(sg[2] - sg[0], sg[3] - sg[1]))
        if L < 300:
            continue
        vx, vy = sg[2] - sg[0], sg[3] - sg[1]
        for t in byl.get(L, [])[:60]:
            wx, wy = t[2] - t[0], t[3] - t[1]
            for rot in ROTS:
                c, s = rot
                rx, ry = c * vx - s * vy, s * vx + c * vy
                if abs(rx - wx) < 1 and abs(ry - wy) < 1:
                    vote(rot, sg[0], sg[1], t[0], t[1], 2)
                elif abs(rx + wx) < 1 and abs(ry + wy) < 1:
                    vote(rot, sg[0], sg[1], t[2], t[3], 2)
    for key in ("dv_rect", "tr_rect"):
        for ra, ia in ga[key]:
            for rb, ib in gb[key]:
                if abs(ia[0] - ib[0]) < 2 and abs(ia[1] - ib[1]) < 2:
                    for rot in ROTS:
                        c, s = rot
                        lx, ly = c * ia[3][0] - s * ia[3][1], s * ia[3][0] + c * ia[3][1]
                        if abs(abs(lx * ib[3][0] + ly * ib[3][1]) - 1) < 1e-3:
                            vote(rot, ia[2][0], ia[2][1], ib[2][0], ib[2][1])
    for ra in ga["tr_ins"]:
        for rb in gb["tr_ins"]:
            if abs(ra["w"] - rb["w"]) < 2 and abs(ra["h"] - rb["h"]) < 2:
                for rot in ROTS:
                    vote(rot, ra["cx"], ra["cy"], rb["cx"], rb["cy"])
    # 5 단위 반올림 경계에 걸린 표를 이웃끼리 합친다
    merged = Counter()
    for (c, s, x, y), n in votes.items():
        merged[(c, s, x, y)] = sum(votes.get((c, s, x + dx, y + dy), 0) for dx in (-5, 0, 5) for dy in (-5, 0, 5))
    return [(k, n) for k, n in merged.most_common(8) if n >= 3]


def _covered_frac(sa, sb, T, tol=3.0):
    """sa 선분들(T 로 옮김)이 sb 선분 위에 놓인 길이 비율."""
    if not sa:
        return 1.0
    G = 2000.0
    grid = defaultdict(list)
    for s in sb:
        x0, x1 = sorted((s[0], s[2])); y0, y1 = sorted((s[1], s[3]))
        for gx in range(int(x0 // G), int(x1 // G) + 1):
            for gy in range(int(y0 // G), int(y1 // G) + 1):
                grid[(gx, gy)].append(s)
    tot = cov = 0.0
    for s in sa:
        ax, ay = _tf(T, s[0], s[1])
        bx, by = _tf(T, s[2], s[3])
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-6:
            continue
        tot += L
        ux, uy = (bx - ax) / L, (by - ay) / L
        cand = set()
        x0, x1 = sorted((ax, bx)); y0, y1 = sorted((ay, by))
        for gx in range(int((x0 - tol) // G), int((x1 + tol) // G) + 1):
            for gy in range(int((y0 - tol) // G), int((y1 + tol) // G) + 1):
                for t in grid.get((gx, gy), ()):
                    cand.add(t)
        ivs = []
        for t in cand:
            # t 의 두 끝이 s 의 직선 위(tol 안)에 있어야 같은 선
            d0 = abs((t[0] - ax) * uy - (t[1] - ay) * ux)
            d1 = abs((t[2] - ax) * uy - (t[3] - ay) * ux)
            if d0 > tol or d1 > tol:
                continue
            p0 = (t[0] - ax) * ux + (t[1] - ay) * uy
            p1 = (t[2] - ax) * ux + (t[3] - ay) * uy
            lo, hi = max(0.0, min(p0, p1) - tol), min(L, max(p0, p1) + tol)
            if hi > lo:
                ivs.append((lo, hi))
        ivs.sort()
        c, cur = 0.0, None
        for lo, hi in ivs:
            if cur is None or lo > cur[1]:
                if cur:
                    c += cur[1] - cur[0]
                cur = [lo, hi]
            else:
                cur[1] = max(cur[1], hi)
        if cur:
            c += cur[1] - cur[0]
        cov += min(c, L)
    return cov / tot if tot else 1.0


def _contained(ga, gb, fam, T):
    """가족 fam 의 ga 도형이 T 로 옮긴 뒤 gb 안에 다 있는가."""
    if fam == "tr_line":
        sa = _segs_of(ga["tr_open"] + ga["tr_cutclosed"])
        sb = _segs_of(gb["tr_open"] + gb["tr_cutclosed"] + gb["tr_bent"] + [r for r, _ in gb["tr_rect"]])
        return _covered_frac(sa, sb, T) >= 0.98
    if fam == "tr_closed":
        sa = _segs_of(ga["tr_bent"] + [r for r, _ in ga["tr_rect"]])
        sb = _segs_of(gb["tr_bent"] + [r for r, _ in gb["tr_rect"]] + gb["tr_open"] + gb["tr_cutclosed"])
        return _covered_frac(sa, sb, T) >= 0.98
    if fam == "dv_rect":
        for ra, ia in ga[fam]:
            X, Y = _tf(T, *ia[2])
            if not any(abs(ia[0] - ib[0]) < 2 and abs(ia[1] - ib[1]) < 2 and math.hypot(ib[2][0] - X, ib[2][1] - Y) < 5 for rb, ib in gb[fam]):
                return False
        return True
    if fam == "tr_ins":
        for ra in ga[fam]:
            X, Y = _tf(T, ra["cx"], ra["cy"])
            if not any(abs(ra["w"] - rb["w"]) < 2 and abs(ra["h"] - rb["h"]) < 2 and math.hypot(rb["cx"] - X, rb["cy"] - Y) < 5 for rb in gb[fam]):
                return False
        return True
    return False


def _local_ok(fa, fb, T, pt, R):
    """fa 의 점 pt 둘레(반경 R)의 일반 선이 T 로 옮긴 뒤 fb 의 선 위에 대부분(80%) 놓이는가 (문틀·벽 선으로 같은 자리인지 확인)."""
    x, y = pt
    sa = [s for s in fa.segments if abs((s[0] + s[2]) / 2 - x) < R and abs((s[1] + s[3]) / 2 - y) < R]
    sa = [s for s in sa if math.hypot(s[2] - s[0], s[3] - s[1]) > 1]
    if len(sa) < 8:
        return False
    X, Y = _tf(T, x, y)
    sb = [s for s in fb.segments if abs((s[0] + s[2]) / 2 - X) < R * 1.5 and abs((s[1] + s[3]) / 2 - Y) < R * 1.5]
    if _covered_frac(sa[:600], sb, T) >= 0.8:
        return True
    # 한쪽(확대평면도)이 더 자세하면 반대 방향으로 본다: fb 의 둘레 선이 fa 에 다 있는가
    sb2 = [s for s in sb if abs((s[0] + s[2]) / 2 - X) < R and abs((s[1] + s[3]) / 2 - Y) < R and math.hypot(s[2] - s[0], s[3] - s[1]) > 1]
    if len(sb2) < 8:
        return False
    c, s_, dx, dy = T
    Ti = (c, -s_) + tuple(_tf_inv(T, 0, 0))
    sa2 = [s for s in fa.segments if abs((s[0] + s[2]) / 2 - x) < R * 1.5 and abs((s[1] + s[3]) / 2 - y) < R * 1.5]
    return _covered_frac(sb2[:600], sa2, Ti) >= 0.8


def _fam_size(g, fam):
    if fam == "tr_line":
        return sum(_plen(r["pts"]) for r in g["tr_open"] + g["tr_cutclosed"])
    if fam == "tr_closed":
        return sum(_plen(r["pts"], True) for r in g["tr_bent"]) + sum(_plen(r["pts"], True) for r, _ in g["tr_rect"])
    return len(g[fam])


class _Ctx:
    """현장 한 곳의 금속 도곽 정리 결과 (두 함수가 같이 쓴다)."""
    def __init__(self, frames, plan_frames):
        self.plan_frames = list(plan_frames or [])
        self.plan_ids = {id(f) for f, _, _ in self.plan_frames}
        self.frames = _candidate_frames(frames, self.plan_frames)
        self.info = {}
        for f in self.frames:
            s, ok = _scale(f)
            self.info[id(f)] = {"f": f, "s": s, "s_ok": ok, "fl": tuple(floors_from_title(getattr(f, "title", "") or "")),
                                "g": _frame_metal(f, s)}
        self._off_cache = {}
        self._cont_cache = {}
        # 표제란 글자: 금속 도곽의 40% 이상에 나오는 글자
        cnt = Counter()
        for f in self.frames:
            cnt.update({t.text for t in f.texts})
        n = len(self.frames)
        self.boiler = {k for k, v in cnt.items() if n >= 4 and v >= 0.4 * n}

    def offsets(self, fa, fb):
        k = (id(fa), id(fb))
        if k not in self._off_cache:
            try:
                self._off_cache[k] = _offsets(fa, fb, self.info[id(fa)]["g"], self.info[id(fb)]["g"], self.boiler)
            except Exception:
                self._off_cache[k] = []
        return self._off_cache[k]

    def contained_in(self, fa, fb, fam):
        """fa 의 fam 도형이 fb 에 다 들어가면 그 변환(T), 아니면 None.
        후보 변환: 글자·도형 표가 3 이상인 것 + 도곽 위치 차이(같은 그림을 같은 자리에 둔 개정본).
        재료분리대처럼 도형이 한두 개뿐이면 우연히 맞을 수 있으므로, 표가 모자란 변환은 사각형 둘레의 일반 선까지 맞는지 본다."""
        key = (id(fa), id(fb), fam)
        if key in self._cont_cache:
            return self._cont_cache[key]
        ia, ib = self.info[id(fa)], self.info[id(fb)]
        res = None
        if abs(ia["s"] - ib["s"]) <= 1e-6:
            cands = [(T, n) for T, n in self.offsets(fa, fb)]
            try:
                fT = (1, 0, round(fb.bounds[0] - fa.bounds[0]), round(fb.bounds[1] - fa.bounds[1]))
                if all(abs(fT[2] - T[2]) > 5 or abs(fT[3] - T[3]) > 5 or T[:2] != (1, 0) for T, _ in cands):
                    cands.append((fT, 0))
            except Exception:
                pass
            for T, n in cands:
                if not _contained(ia["g"], ib["g"], fam, T):
                    continue
                if fam == "dv_rect" and (n < 3 + len(ia["g"]["dv_rect"])):
                    # 표가 사각형 자신에게서만 나왔을 수 있다 → 사각형 둘레의 일반 선이 같은지 확인
                    if not all(_local_ok(fa, fb, T, ri[2], 1500.0 / ia["s"]) for _, ri in ia["g"]["dv_rect"]):
                        continue
                res = T
                break
            if res is None and fam == "dv_rect" and ia["g"]["dv_rect"]:
                # 사각형끼리만 맞춰 본 변환(표가 적어 후보에 없던 것)도 둘레 선이 같으면 쓴다
                r0 = ia["g"]["dv_rect"][0][1]
                for _, ib0 in ib["g"]["dv_rect"]:
                    if abs(r0[0] - ib0[0]) >= 2 or abs(r0[1] - ib0[1]) >= 2:
                        continue
                    for c, s_ in ROTS:
                        lx, ly = c * r0[3][0] - s_ * r0[3][1], s_ * r0[3][0] + c * r0[3][1]
                        if abs(abs(lx * ib0[3][0] + ly * ib0[3][1]) - 1) > 1e-3:
                            continue
                        X, Y = c * r0[2][0] - s_ * r0[2][1], s_ * r0[2][0] + c * r0[2][1]
                        T = (c, s_, ib0[2][0] - X, ib0[2][1] - Y)
                        if _contained(ia["g"], ib["g"], fam, T) and \
                                all(_local_ok(fa, fb, T, ri[2], 1500.0 / ia["s"]) for _, ri in ia["g"]["dv_rect"]):
                            res = T
                            break
                    if res is not None:
                        break
        self._cont_cache[key] = res
        return res

    def pick(self, fam):
        """가족 fam 이 있는 도곽을 층별로 묶어 한 번만 셀 도곽을 고른다.
        반환: (kept: [(층, [도곽…])], dups: {id(도곽): (같은 것으로 본 도곽, 같은 것인지 일부인지)}, nofloor: [도곽])"""
        groups = defaultdict(list)
        nofloor = []
        for f in self.frames:
            inf = self.info[id(f)]
            if _fam_size(inf["g"], fam) <= 0:
                continue
            (groups[inf["fl"]] if inf["fl"] else nofloor).append(f)
        kept, dups = [], {}
        for fl, fs in groups.items():
            fs.sort(key=lambda f: (0 if id(f) in self.plan_ids else 1, -_fam_size(self.info[id(f)]["g"], fam)))
            keep = []
            for f in fs:
                hit = None
                for k in keep:
                    if self.contained_in(f, k, fam) is not None:
                        hit = k
                        break
                if hit is not None:
                    dups[id(f)] = hit
                else:
                    keep.append(f)
            # 먼저 고른 도곽이 나중 도곽에 다 들어가면(앞 도곽이 일부만 그림) 앞 도곽을 뺀다
            changed = True
            while changed and len(keep) > 1:
                changed = False
                for a in list(keep):
                    for b in keep:
                        if a is not b and self.contained_in(a, b, fam) is not None:
                            keep.remove(a)
                            dups[id(a)] = b
                            for k_, v_ in list(dups.items()):
                                if v_ is a:
                                    dups[k_] = b
                            changed = True
                            break
                    if changed:
                        break
            kept.append((fl, keep))
        kept.sort(key=lambda x: floor_sort_key(x[0][0]))
        return kept, dups, nofloor

    def tags_for(self, f):
        """도곽 f 의 문 부호 태그 [(부호, x, y)]. f 가 창호평면도가 아니면 같은 층 창호평면도의 태그를 평행 이동해 가져온다."""
        for pf, fl, tg in self.plan_frames:
            if pf is f:
                return [(t[0], t[3].cx, t[3].cy) for t in tg], None
        fl = self.info[id(f)]["fl"]
        for pf, pfl, tg in self.plan_frames:
            if tuple(floors_from_title(getattr(pf, "title", "") or "")) != fl or id(pf) not in self.info:
                continue
            T = self.contained_in(f, pf, "dv_rect")
            if T is not None:
                return [(t[0],) + tuple(_tf_inv(T, t[3].cx, t[3].cy)) for t in tg], pf
        return [], None


# ---------------------------------------------------------------------------------------------------------
# 하드웨어 일람표 (Q현장식): 문 목록(DOOR TYPE … H/W SET) → SET 표(SET NO. | Q'TY | CODE | DESCRIPTION)의 FLOOR HINGE 개수
HW_TITLE = re.compile(r"하드웨어|HARDWARE|도어\s*일람|DOOR\s*SCHEDULE", re.I)
SET_NO = re.compile(r"^\s*(\d{3}[A-Z]{0,3})\s*$")
SET_HDR = re.compile(r"^\s*SET\s*NO\.?\s*$", re.I)
QTY_HDR = re.compile(r"^\s*Q\s*['`’]?\s*TY\s*$", re.I)


def _code_key(c):
    """부호 비교용: 띄어쓰기·하이픈 없애고 숫자 앞 0 을 뗀다 (일람표 'FSD-05' = 평면도 'FSD-5')."""
    k = re.sub(r"[\s\-]", "", c or "").upper()
    return re.sub(r"(?<=[A-Z])0+(?=\d)", "", k)


def _read_set_table(f):
    """SET 표 도곽 → {SET 번호: FLOOR HINGE 개수 합(없으면 0) 또는 None(못 읽음)}.
    SET 칸의 경계는 표의 가로선으로 정한다(번호 글자는 칸 가운데쯤에 있고 품목 줄은 그 위아래로 퍼져 있다)."""
    out = {}
    hdrs = sorted([t for t in f.texts if SET_HDR.match(t.text)], key=lambda t: t.cx)
    if not hdrs:
        return out
    qtys = [t for t in f.texts if QTY_HDR.match(t.text)]
    hsegs = [(min(x0, x1), max(x0, x1), (y0 + y1) / 2) for x0, y0, x1, y1 in f.segments if abs(y1 - y0) < 1 and abs(x1 - x0) > 1]
    for i, h in enumerate(hdrs):
        nx = hdrs[i + 1].cx if i + 1 < len(hdrs) else f.bounds[2]
        q = [t for t in qtys if h.cx < t.cx < nx and abs(t.cy - h.cy) < 3 * max(h.y1 - h.y0, 1)]
        if not q:
            continue
        qx = min(q, key=lambda t: t.cx - h.cx).cx
        lo = h.cx - (qx - h.cx)
        ys = sorted({round(y) for x0, x1, y in hsegs if x0 <= h.cx and x1 >= qx and y > h.cy})
        if len(ys) < 2:
            continue
        col = [t for t in f.texts if lo <= t.cx < nx - (qx - h.cx) and t.cy > h.cy]
        for t in col:
            m = SET_NO.match(t.text)
            if not m or abs(t.cx - h.cx) > abs(qx - h.cx) * 0.8:
                continue
            up = [y for y in ys if y < t.cy]
            dn = [y for y in ys if y > t.cy]
            if not up or not dn:
                continue
            y0, y1 = max(up), min(dn)
            rows = [u for u in col if y0 < u.cy < y1]
            n = 0
            for u in rows:
                if FH_RE.search(u.text):
                    hh = max(u.y1 - u.y0, 1)
                    qq = [v for v in rows if abs(v.cy - u.cy) < hh * 0.6 and abs(v.cx - qx) < (qx - h.cx) * 0.6 and re.fullmatch(r"\s*\d{1,2}\s*", v.text)]
                    if len(qq) != 1:
                        n = None
                        break
                    n += int(qq[0].text)
            sno = m.group(1).upper()
            out[sno] = None if (sno in out and out[sno] != n) else n
    return out


def hardware_floor_hinges(frames, codes):
    """하드웨어 일람표에서 {문 부호: (SET 번호들, 플로어힌지 개수, 근거)}.
    끝까지 연결된 부호만 돌려준다: 문 목록의 그 부호 줄마다 H/W SET 번호 → SET 표에서 그 번호 칸의 FLOOR HINGE 줄과 개수.
    한 부호의 줄들이 서로 다른 개수의 SET 을 가리키면 쓰지 않는다. 문 번호로 적는 양식(R현장 A62→A63)은 하지 않는다."""
    out = {}
    hw = [f for f in frames if HW_TITLE.search(re.sub(r"\s", "", getattr(f, "title", "") or ""))
          or re.search(r"하드웨어|HARDWARE", getattr(f, "file", "") or "", re.I)]
    if not hw:
        return out, "하드웨어 일람표 도곽 없음"
    norm = {_code_key(c): c for c in codes}
    sets = {}
    links = defaultdict(list)          # 부호 → [SET 번호] (문 목록 줄마다)
    door_w = defaultdict(set)          # 부호 → {문 목록 DOOR SIZE W}
    for f in hw:
        try:
            for k, v in _read_set_table(f).items():
                sets[k] = None if (k in sets and sets[k] != v) else v
        except Exception:
            pass
        # 문 목록: DOOR TYPE 칸의 부호(한 칸에 'SSD-12 / SSD-12a /' 처럼 여러 줄로 적히기도 함) → 같은 줄의 H/W SET 칸
        set_cols = sorted(t.cx for t in f.texts if re.fullmatch(r"\s*SET\s*", t.text, re.I))
        if not set_cols:
            continue
        lines = []
        for t in f.texts:
            parts = [x for x in re.split(r"[/,]", t.text) if x.strip()]
            cs = [norm.get(_code_key(x)) for x in parts]
            if parts and all(cs):
                lines.append((t, cs))
        lines.sort(key=lambda a: (round(a[0].cx), a[0].cy))
        cells = []
        for t, cs in lines:
            h = max(t.y1 - t.y0, 1)
            c = cells[-1] if cells else None
            if c and abs(c["x"] - t.cx) < h and t.cy - c["y1"] <= 1.6 * h and c["open"]:
                c["codes"] += cs; c["y1"] = t.cy; c["open"] = t.text.rstrip().endswith(("/", ","))
            else:
                cells.append({"x": t.cx, "y0": t.cy, "y1": t.cy, "h": h, "codes": list(cs), "open": t.text.rstrip().endswith(("/", ","))})
        w_cols = sorted(t.cx for t in f.texts if re.fullmatch(r"\s*W\s*", t.text))
        for c in cells:
            right = [x for x in set_cols if x > c["x"]]
            if not right:
                continue
            sx = min(right)
            wr = [x for x in w_cols if c["x"] < x < sx]
            if wr:
                wx = min(wr)
                ww = [u for u in f.texts if abs(u.cx - wx) < (wx - c["x"]) * 0.5 and c["y0"] - 0.6 * c["h"] <= u.cy <= c["y1"] + 0.6 * c["h"]
                      and re.fullmatch(r"\s*\d{1,2},?\d{3}\s*", u.text)]
                if len(ww) == 1:
                    for code in c["codes"]:
                        door_w[code].add(int(ww[0].text.replace(",", "")))
            w = (sx - c["x"]) * 0.3
            cand = [u for u in f.texts if abs(u.cx - sx) < w and c["y0"] - 0.6 * c["h"] <= u.cy <= c["y1"] + 0.6 * c["h"] and SET_NO.match(u.text)]
            if len(cand) == 1:
                for code in c["codes"]:
                    links[code].append(SET_NO.match(cand[0].text).group(1).upper())
    for c, ss in links.items():
        ns = {sets.get(s, "x") for s in ss}
        if len(ns) != 1:
            continue
        n = next(iter(ns))
        if n is None or n == "x":
            continue
        su = sorted(set(ss))
        out[c] = (su, n, f"{c} → SET {'/'.join(su)} → FLOOR HINGE {n}개" if n else f"{c} → SET {'/'.join(su)} (FLOOR HINGE 없음)")
    hardware_floor_hinges.door_w = {k: sorted(v) for k, v in door_w.items()}
    return out, (f"하드웨어 일람표 {len(hw)}장에서 SET {len(sets)}개(그중 못 읽음 {sum(1 for v in sets.values() if v is None)}), "
                 f"문 목록에서 부호→SET 연결 {len(links)}종, 끝까지 연결된 부호 {len(out)}종")


# ---------------------------------------------------------------------------------------------------------
def _hinge_count(it):
    """일람표 글자에서 플로어힌지 수. (개수 또는 None, 설명)"""
    hw = " ".join(x for x in (getattr(it, "hardware", ""), getattr(it, "spec_text", "")) if x)
    m = FH_N_RE.search(hw)
    if m:
        return int(m.group(1)), f"일람표 '{m.group(0).strip()}'"
    txt = " ".join(x for x in (getattr(it, "name", ""), hw) if x)
    if SINGLE_RE.search(txt) and not DOUBLE_RE.search(txt):
        return 1, "일람표 외여닫이 표기"
    if DOUBLE_RE.search(txt) and not SINGLE_RE.search(txt):
        return 2, "일람표 양여닫이 표기"
    return None, "힌지 수를 일람표에서 못 정함"


def _is_fire(it):
    """방화문인가: 종류가 FSD 계열이거나 명칭에 방화. (철물 설명의 '방화용도어체크' 같은 글자는 보지 않는다)"""
    return bool(re.match(r"^F\w*SD", it.type or "") or re.match(r"^FSD", it.code or "") or "방화" in (it.name or ""))


def divider_rows(frames, plan_counts, items, rules):
    """재료분리대 줄. [{구분, 항목, 규격, 단위, 수량, 근거}]"""
    R = (rules or {}).get("재료분리대", {}) or {}
    HL = R.get("플로어힌지_길이_mm", 307)
    plan_frames = getattr(plan_counts, "plan_frames", []) or []
    ctx = _Ctx(frames, plan_frames)
    rows = []
    by_code = {it.code: it for it in items}
    kept, dups, nofloor = ctx.pick("dv_rect")
    hwmap, hwnote, door_w = ({}, "", {})
    try:
        hardware_floor_hinges.door_w = {}
        hwmap, hwnote = hardware_floor_hinges(frames, list(by_code))
        door_w = getattr(hardware_floor_hinges, "door_w", {}) or {}
    except Exception as ex:
        hwnote = f"하드웨어 일람표 읽기 실패: {ex}"
    fh_grp, chk_grp, fire_grp = defaultdict(int), defaultdict(int), defaultdict(int)   # (층, 부호, 길이, 힌지수/사유) → 개수
    fh_tot = chk_tot = 0.0
    fire_n = 0
    frame_notes = []
    mult_note = []
    for fl, fs in kept:
        n_fl = len(fl)
        flb = _flabel(fl)
        if len(fs) > 1:
            frame_notes.append(f"{flb}: 재료분리대 사각형이 든 도곽 {len(fs)}장의 구성이 서로 달라 모두 더함(한 층을 나눠 그린 것인지 겹침 확인 필요): "
                               + ", ".join(_ftitle(f) for f in fs))
        for f in fs:
            inf = ctx.info[id(f)]
            s = inf["s"]
            rects = inf["g"]["dv_rect"]
            tags, via = ctx.tags_for(f)
            dd = [_ftitle(g["f"]) for g in ctx.info.values() if dups.get(id(g["f"])) is f]
            frame_notes.append(f"{flb} {_ftitle(f)}: 사각형 {len(rects)}개" + (f" ×{n_fl}층" if n_fl > 1 else "")
                               + (f" (문 부호는 {_ftitle(via)} 에서 옮겨 씀)" if via is not None else "")
                               + (f" / 같은 것으로 보고 세지 않은 도곽: {', '.join(dd)}" if dd else ""))
            if not inf["s_ok"]:
                frame_notes.append(f"  {_ftitle(f)}: 치수 비율을 못 읽어 도면 단위를 mm 로 봄")
            # 1) 사각형마다 가까운 문 부호
            lim = 3000.0 / s
            best = []
            for r, ri in rects:
                cx, cy = ri[2]
                cand = sorted(((math.hypot(x - cx, y - cy), c, (x, y)) for c, x, y in tags), key=lambda a: a[0])
                cand = [a for a in cand if a[0] <= lim]
                best.append(cand)
            claim = Counter(c[0][2] for c in best if c)
            for (r, ri), cand in zip(rects, best):
                L = ri[0] * s
                key_fl = flb
                reason, code, it = None, None, None
                if not cand:
                    reason = "문 못 가림(3m 안에 문 부호 없음)"
                else:
                    d1, c1, p1 = cand[0]
                    other = [a for a in cand[1:] if a[1] != c1]
                    if other and other[0][0] < 1.3 * d1:
                        # 두 후보가 모두 방화문이면 어느 쪽이든 방화문 자리다 → 세지 않는 쪽으로 (부호는 둘 다 적는다)
                        i1, i2 = by_code.get(c1), by_code.get(other[0][1])
                        if i1 is not None and i2 is not None and _is_fire(i1) and _is_fire(i2):
                            fire_grp[(key_fl, n_fl, f"{c1} 또는 {other[0][1]}(둘 다 방화문)", round(L))] += 1
                            fire_n += n_fl
                            continue
                        reason = f"문 못 가림(가까운 부호 {c1} {d1 * s:,.0f} / {other[0][1]} {other[0][0] * s:,.0f})"
                    elif claim[p1] > 1:
                        reason = f"문 못 가림(부호 {c1} 하나를 사각형 {claim[p1]}개가 가리킴)"
                    else:
                        code = c1
                        it = by_code.get(c1)
                        if it is None:
                            reason = f"문 대응 확인 필요(가까운 부호 {c1} 가 일람표에 없음)"
                wsrc = ""
                if reason is None:
                    ws = [w for w in [it.width] + list(getattr(it, "width_candidates", []) or []) if w]
                    dw = list(door_w.get(code, []))
                    ok = [w for w in ws if 55 <= w - L <= 105]
                    okd = [w for w in dw if 55 <= w - L <= 105]
                    if ok:
                        wsrc = f"일람표 폭 {_fmt(ok[0])}"
                    elif okd:
                        wsrc = f"하드웨어 일람표 문 목록 DOOR SIZE W {_fmt(okd[0])}, 창호일람표 전체 폭 {'/'.join(_fmt(w) for w in sorted(set(ws))) or '-'}"
                    else:
                        reason = (f"문 대응 확인 필요(사각형 {_fmt(L)} / 가까운 부호 {code} 일람표 "
                                  + ("/".join(_fmt(w) for w in sorted(set(ws))) if ws else "폭 못 읽음")
                                  + (f", 문 목록 DOOR SIZE W {'/'.join(_fmt(w) for w in dw)}" if dw else "")
                                  + (" — 일람표 폭이 옆 고정창을 포함한 전체 폭일 수 있음" if ws and min(ws) - L > 105 and not dw else "") + ")")
                if reason is not None:
                    chk_grp[(key_fl, n_fl, code or "-", round(L), reason)] += 1
                    chk_tot += L * n_fl
                    continue
                # 2) 문이 확정된 사각형
                fire = _is_fire(it)
                if fire:
                    fire_grp[(key_fl, n_fl, code, round(L))] += 1
                    fire_n += n_fl
                    continue
                hwtxt = " ".join(x for x in (getattr(it, "hardware", ""), getattr(it, "spec_text", "")) if x)
                has_fh, src, nh = False, "", None
                if FH_RE.search(hwtxt):
                    has_fh = True
                    nh, src = _hinge_count(it)
                elif code in hwmap:
                    sno, n, why = hwmap[code]
                    if n > 0:
                        has_fh, nh, src = True, n, f"하드웨어 일람표 SET {'/'.join(sno)} FLOOR HINGE {n}개"
                    else:
                        chk_grp[(key_fl, n_fl, code, round(L), f"하드웨어 일람표 SET {'/'.join(sno)} 에 FLOOR HINGE 없음·방화문 아님 → 안쪽 폭 그대로")] += 1
                        chk_tot += L * n_fl
                        continue
                if has_fh and SLIDE_RE.search(" ".join(x for x in (it.name, hwtxt) if x)):
                    chk_grp[(key_fl, n_fl, code, round(L), "미닫이·자동문인데 플로어힌지 표기(일람표 충돌)")] += 1
                    chk_tot += L * n_fl
                    continue
                if has_fh and nh:
                    v = L - HL * nh
                    if v <= 0:
                        chk_grp[(key_fl, n_fl, code, round(L), f"힌지 {nh}개를 빼면 0 이하")] += 1
                        chk_tot += L * n_fl
                        continue
                    fh_grp[(key_fl, n_fl, code, round(L), nh, src + (f"; 문 폭 대조: {wsrc}" if wsrc else ""))] += 1
                    fh_tot += v * n_fl
                    continue
                if has_fh:
                    chk_grp[(key_fl, n_fl, code, round(L), f"플로어힌지 있음, {src}")] += 1
                elif re.search(r"피봇|피벗|PIVOT|정첩|경첩|BUTT|힌지|HINGE", getattr(it, "hardware", "") or "", re.I):
                    chk_grp[(key_fl, n_fl, code, round(L), "일람표 철물에 다른 힌지만 있음(플로어힌지·방화문 아님) → 안쪽 폭 그대로")] += 1
                else:
                    chk_grp[(key_fl, n_fl, code, round(L), "플로어힌지 여부 못 가림(일람표에 '부속철물일체' 등만 있음"
                             + (", 하드웨어 일람표에서 이 부호를 끝까지 연결 못 함)" if "하드웨어 일람표 도곽 없음" not in hwnote else ")"))] += 1
                chk_tot += L * n_fl
    for f in nofloor:
        g = ctx.info[id(f)]["g"]
        frame_notes.append(f"층 미상 {_ftitle(f)}: 사각형 {len(g['dv_rect'])}개 (층을 못 읽어 합계에 넣지 않음)")

    def _key(k):
        return (floor_sort_key(_floor_code(k[0])), str(k[2]))
    if fh_grp:
        ev = []
        for k in sorted(fh_grp, key=_key):
            flb, n_fl, code, L, nh, src = k
            n = fh_grp[k]
            ev.append(f"{flb} {code} {_fmt(L)}−{HL}×{nh}={_fmt(L - HL * nh)} ×{n}" + (f"×{n_fl}층" if n_fl > 1 else "") + f" ({src})")
        rows.append({"구분": "재료분리대", "항목": "재료분리대 (플로어힌지 문)", "규격": "레이어 사각형(문틀 안쪽 폭)−플로어힌지", "단위": "M",
                     "수량": _m(fh_tot), "근거": "; ".join(ev) + f". 힌지 하나당 {HL}mm(rules.json 재료분리대, 임시값)"})
    if chk_grp:
        ev = []
        for k in sorted(chk_grp, key=_key):
            flb, n_fl, code, L, why = k
            n = chk_grp[k]
            ev.append(f"{flb} {code} {_fmt(L)} ×{n}" + (f"×{n_fl}층" if n_fl > 1 else "") + f" [{why}]")
        rows.append({"구분": "재료분리대", "항목": "재료분리대 (확인 필요)", "규격": "레이어 사각형(문틀 안쪽 폭 그대로)", "단위": "M",
                     "수량": _m(chk_tot), "근거": "안쪽 폭을 그대로 셈: " + "; ".join(ev)})
    if fire_grp:
        ev = [f"{k[0]} {k[2]} {_fmt(k[3])} ×{fire_grp[k]}" + (f"×{k[1]}층" if k[1] > 1 else "") for k in sorted(fire_grp, key=_key)]
        rows.append({"구분": "재료분리대", "항목": "방화문 밑 재료분리대 표시(세지 않음)", "규격": "레이어 사각형", "단위": "M", "수량": None,
                     "근거": f"방화문에는 재료분리대를 넣지 않음(회사 기준 5-2). 도면에 그려진 것 {fire_n}개: " + "; ".join(ev)})
    if rows:
        rows[-1]["근거"] += " / 도곽: " + " | ".join(frame_notes) + (f" / {hwnote}" if hwnote else "")
    # 글자·기호 (N현장 글자, L현장·R현장 '재' 기호)
    try:
        trow = _divider_text_row(ctx, has_rects=bool(fh_grp or chk_grp or fire_grp))
        if trow:
            rows.append(trow)
    except Exception as ex:
        rows.append({"구분": "재료분리대", "항목": "재료분리대 글자·기호 (읽기 실패, 확인 필요)", "규격": "", "단위": "", "수량": None, "근거": str(ex)})
    if not rows:
        rows.append({"구분": "재료분리대", "항목": "재료분리대 (평면도에서 표시 못 찾음, 확인 필요)", "규격": "", "단위": "M", "수량": None,
                     "근거": f"평면 종류 도곽 {len(ctx.frames)}장에서 재료분리대 레이어 사각형·글자·'재' 기호를 못 찾음 (도면에 없다는 뜻은 아님)"})
    return rows


def _floor_code(flb):
    m = re.match(r"지하(\d+)", flb)
    if m:
        return f"B{m.group(1)}"
    m = re.match(r"(\d+)", flb)
    if m:
        return f"{m.group(1)}F"
    return "RF" if "지붕" in flb else "1F"


def _text_dups(ctx, fa, fb, pick):
    """같은 층 두 도곽에서 같은 자리(글자로 찾은 평행 이동 후)의 대상 글자 수."""
    offs = ctx.offsets(fa, fb)
    if not offs or offs[0][1] < 4:
        return 0
    T = offs[0][0]                   # 가장 많이 맞는 변환 하나만 (엉뚱한 이동으로 우연히 맞는 것을 막는다)
    ta, tb = pick(fa), pick(fb)
    n = 0
    for a in ta:
        h = max(min(a.y1 - a.y0, a.x1 - a.x0), 1)
        X, Y = _tf(T, a.cx, a.cy)
        if any(abs(b.cx - X) < 2 * h and abs(b.cy - Y) < 2 * h and b.text == a.text for b in tb):
            n += 1
    return n


def _div_texts(f):
    """도곽 f 의 재료분리대 글자·기호 분류: {'pointer': [...], 'symbol': [...], 'legend': [...], 'note': [...], 'granite': [...], 'other': [...]}"""
    out = defaultdict(list)
    granite_legend = any(DIV_TXT.search(t.text) and "화강석" in t.text for t in f.texts)
    used = set()                       # 범례 기호 옆 설명 글자(': 재료분리대')는 범례 한 개로만 센다
    for t in f.texts:
        if t.text.strip() != "재":
            continue
        h = max(t.y1 - t.y0, 1)
        right = [u for u in f.texts if u is not t and abs(u.cy - t.cy) < h and 0 < u.x0 - t.x1 < 12 * h and DIV_TXT.search(u.text)]
        if right:
            out["legend"].append(t)
            used.update(id(u) for u in right)
        elif granite_legend:
            out["granite_sym"].append(t)
        else:
            out["symbol"].append(t)
    for t in f.texts:
        s = t.text.strip()
        if s == "재" or id(t) in used or not DIV_TXT.search(s):
            continue
        if re.match(r"^\s*[:：]", s):
            out["legend"].append(t)
        elif "화강석" in s:
            out["granite"].append(t)
        elif "상세" in s:
            out["other"].append(t)
        elif NOTE_TXT.search(s):
            out["note"].append(t)
        else:
            out["pointer"].append(t)
    return out


def _divider_text_row(ctx, has_rects):
    per = []
    tot = Counter()
    for f in ctx.frames:
        d = _div_texts(f)
        if not any(d.values()):
            continue
        per.append((f, d))
        for k, v in d.items():
            tot[k] += len(v)
    if not per:
        return None
    if has_rects and not (tot["pointer"] or tot["symbol"]):
        return None              # 레이어 사각형이 있는 현장의 일반 주기는 따로 줄을 만들지 않는다
    ev = []
    dup_tot = 0
    byfl = defaultdict(list)
    for f, d in per:
        byfl[ctx.info[id(f)]["fl"]].append((f, d))
    for fl in sorted(byfl, key=lambda x: floor_sort_key(x[0]) if x else (" ", -999)):
        lst = byfl[fl]
        for i, (f, d) in enumerate(lst):
            parts = []
            if d["pointer"]:
                parts.append(f"문 위치를 가리키는 글자 {len(d['pointer'])}회")
            if d["symbol"]:
                parts.append(f"'재' 기호 {len(d['symbol'])}회")
            if d["legend"]:
                parts.append(f"범례 {len(d['legend'])}")
            if d["note"]:
                parts.append(f"일반 주기 {len(d['note'])}")
            if d["granite"] or d["granite_sym"]:
                parts.append(f"화강석 {len(d['granite']) + len(d['granite_sym'])}회(금속 아님으로 보고 세지 않음, 확인 필요)")
            if d["other"]:
                parts.append(f"상세 제목 {len(d['other'])}(세지 않음)")
            dn = ""
            if fl and (d["pointer"] or d["symbol"]):
                for g, dg in lst[:i]:
                    if not (dg["pointer"] or dg["symbol"]):
                        continue
                    n = _text_dups(ctx, f, g, lambda x: _div_texts(x)["pointer"] + _div_texts(x)["symbol"])
                    if n:
                        dn = f" (그중 {n}회는 {_ftitle(g)} 와 같은 자리 = 중복 확인)"
                        dup_tot += n
                        break
            ev.append(f"{_flabel(fl)} {_ftitle(f)}: " + ", ".join(parts) + dn)
    head = (f"길이가 그려져 있지 않아 길이를 내지 않음. 문 위치 표시 글자 {tot['pointer']}회, '재' 기호 {tot['symbol']}회"
            f" (도곽별 합, 중복 확인 전)" + (f", 그중 같은 자리 중복 확인 {dup_tot}회" if dup_tot else "")
            + ". 확대도 -1/-2 가 겹치면 같은 문을 두 번 가리킬 수 있음. 범례·일반 주기는 위치 표시가 아니라 따로 적음")
    return {"구분": "재료분리대", "항목": "재료분리대 표시(길이 미산출, 확인 필요)", "규격": "글자·기호", "단위": "M", "수량": None,
            "근거": head + ": " + "; ".join(ev)}


# ---------------------------------------------------------------------------------------------------------
def _trench_kind(s):
    if re.search(r"무소음", s):
        return "무소음트렌치"
    if re.search(r"오픈|OPEN", s, re.I):
        return "오픈트렌치"
    if re.search(r"커버|COVER|그레이[팅칭]|GRATING", s, re.I):
        return "커버·그레이팅 트렌치"
    return "트렌치(종류 없음)"


def _trench_texts(f):
    """도곽 f 의 트렌치 글자: ([(종류, W 또는 None, 글자)], 토목·조경 수, 상세 참조 수)"""
    out, civil, det = [], 0, 0
    for t in f.texts:
        s = t.text.strip()
        if not TR_TXT.search(s):
            continue
        if CIVIL_TXT.search(s):
            civil += 1
            continue
        if "상세" in s:
            det += 1
            continue
        m = W_RE.search(s)
        w = int(m.group(1)) if m else None
        if w is None:
            h = max(t.y1 - t.y0, 1)
            near = [u for u in f.texts if u is not t and abs(u.cy - t.cy) < 2.5 * h and not TR_TXT.search(u.text)
                    and min(u.x1, t.x1) - max(u.x0, t.x0) > -h and W_RE.search(u.text)]
            if near:
                w = int(W_RE.search(min(near, key=lambda u: abs(u.cy - t.cy)).text).group(1))
        out.append((_trench_kind(s), w, s))
    return out, civil, det


def trench_rows(frames, plan_counts, rules):
    """트렌치 줄. [{구분, 항목, 규격, 단위, 수량, 근거}]"""
    plan_frames = getattr(plan_counts, "plan_frames", []) or []
    ctx = _Ctx(frames, plan_frames)
    rows = []

    def dup_note(f, dups):
        dd = [_ftitle(g["f"]) for g in ctx.info.values() if dups.get(id(g["f"])) is f]
        return f" (같은 선으로 보고 세지 않은 도곽: {', '.join(dd)})" if dd else ""

    # 1) 전용 레이어의 열린 선
    try:
        kept, dups, nofloor = ctx.pick("tr_line")
        if kept or nofloor:
            tot, ev, warn = 0.0, [], []
            for fl, fs in kept:
                if len(fs) > 1:
                    warn.append(f"{_flabel(fl)} 도곽 {len(fs)}장의 선 구성이 달라 모두 더함(겹침 확인 필요)")
                for f in fs:
                    inf = ctx.info[id(f)]
                    g = inf["g"]
                    L = sum(_plen(r["pts"]) for r in g["tr_open"]) * inf["s"]
                    Lc = sum(_plen(r["pts"]) for r in g["tr_cutclosed"]) * inf["s"]
                    tot += L * len(fl)
                    ev.append(f"{_flabel(fl)} {_ftitle(f)} {_fmt(L)}" + (f"×{len(fl)}층" if len(fl) > 1 else "")
                              + (f" (블록 안 선 {g['tr_inblk']}개 제외)" if g["tr_inblk"] else "")
                              + (f" (클립에 잘린 폐합선 조각 {_fmt(Lc)} 은 넣지 않음)" if Lc else "") + dup_note(f, dups))
            for f in nofloor:
                inf = ctx.info[id(f)]
                ev.append(f"층 미상 {_ftitle(f)} {_fmt(sum(_plen(r['pts']) for r in inf['g']['tr_open']) * inf['s'])} (합계에 넣지 않음)")
            if tot > 0:
                lays = sorted({r["layer"] for inf in ctx.info.values() for r in inf["g"]["tr_open"]})
                rows.append({"구분": "트렌치", "항목": "트렌치 (레이어 선, 종류·폭 확인 필요)", "규격": "레이어 " + ", ".join(lays[:3]),
                             "단위": "M", "수량": _m(tot),
                             "근거": "전용 레이어 선의 그려진 길이(기둥을 피해 꺾인 턱 포함, 중심선인지 테두리인지 미확인): " + "; ".join(ev)
                                     + ("; " + "; ".join(warn) if warn else "")})
    except Exception as ex:
        rows.append({"구분": "트렌치", "항목": "트렌치 레이어 선 (읽기 실패, 확인 필요)", "규격": "", "단위": "", "수량": None, "근거": str(ex)})

    # 2) 무소음트렌치 블록 (실제 크기 삽입만)
    try:
        kept, dups, nofloor = ctx.pick("tr_ins")
        grp = defaultdict(lambda: [0.0, []])      # (폭, 길이) → [합 mm, 근거]
        small = []
        for fl, fs in kept:
            for f in fs:
                inf = ctx.info[id(f)]
                c = Counter()
                for r in inf["g"]["tr_ins"]:
                    w, h = sorted((r["w"] * inf["s"], r["h"] * inf["s"]))
                    c[(round(w), round(h), r["name"])] += 1
                for (w, h, nm), n in sorted(c.items()):
                    grp[(w, h)][0] += n * h * len(fl)
                    grp[(w, h)][1].append(f"{_flabel(fl)} {_ftitle(f)} {n}장" + (f"×{len(fl)}층" if len(fl) > 1 else "")
                                          + (" (겹침 확인 필요)" if len(fs) > 1 else "") + dup_note(f, dups))
        for f in ctx.frames:
            sm = ctx.info[id(f)]["g"]["tr_ins_small"]
            if sm:
                small.append(f"{_ftitle(f)} {len(sm)}장")
        for f in nofloor:
            grp_n = len(ctx.info[id(f)]["g"]["tr_ins"])
            small.append(f"층 미상 {_ftitle(f)} 실제 크기 {grp_n}장(합계에 넣지 않음)")
        for (w, h), (tot, ev) in sorted(grp.items()):
            rows.append({"구분": "트렌치", "항목": f"무소음트렌치 W{w} (덮개 블록 장수×길이, 확인 필요)", "규격": f"블록 {h}×{w}", "단위": "M",
                         "수량": _m(tot), "근거": f"블록 '무소음트렌치' 실제 크기 삽입 장수 × 긴 변 {h}: " + "; ".join(ev)
                                                 + (f" / 축소 삽입 등(세지 않음): {', '.join(small)}" if small else "")})
        if not grp and small:
            rows.append({"구분": "트렌치", "항목": "무소음트렌치 블록 (실제 크기 삽입 없음, 확인 필요)", "규격": "", "단위": "M", "수량": None,
                         "근거": "축소 삽입 등: " + ", ".join(small)})
    except Exception as ex:
        rows.append({"구분": "트렌치", "항목": "무소음트렌치 블록 (읽기 실패, 확인 필요)", "규격": "", "단위": "", "수량": None, "근거": str(ex)})

    # 3) 전용 레이어의 폐합선 (축과 나란한 사각형 = 긴 변, 꺾인 폐합선은 환산하지 않음)
    try:
        kept, dups, nofloor = ctx.pick("tr_closed")
        if kept or nofloor:
            byw = defaultdict(lambda: [0.0, []])
            bent_n, bent_len, bent_ev = 0, 0.0, []
            for fl, fs in kept:
                for f in fs:
                    inf = ctx.info[id(f)]
                    s = inf["s"]
                    c = Counter()
                    for r, ri in inf["g"]["tr_rect"]:
                        c[(round(ri[1] * s), round(ri[0] * s))] += 1
                    for (w, L), n in sorted(c.items()):
                        byw[w][0] += n * L * len(fl)
                        byw[w][1].append(f"{_flabel(fl)} {_ftitle(f)} {_fmt(L)}×{w} {n}개" + (f"×{len(fl)}층" if len(fl) > 1 else "")
                                         + (" (겹침 확인 필요)" if len(fs) > 1 else "") + dup_note(f, dups))
                    b = inf["g"]["tr_bent"]
                    if b:
                        bl = sum(_plen(r["pts"], True) for r in b) * s
                        bent_n += len(b) * len(fl)
                        bent_len += bl * len(fl)
                        bent_ev.append(f"{_flabel(fl)} {_ftitle(f)} 꺾인 폐합선 {len(b)}개 둘레 합 {_fmt(bl)}" + (f"×{len(fl)}층" if len(fl) > 1 else "")
                                       + dup_note(f, dups))
            for f in nofloor:
                g = ctx.info[id(f)]["g"]
                bent_ev.append(f"층 미상 {_ftitle(f)} 사각형 {len(g['tr_rect'])}개, 꺾인 폐합선 {len(g['tr_bent'])}개 (합계에 넣지 않음)")
            part = " 부분합: 같은 레이어의 꺾인 폐합선은 길이 미산출" if bent_n else ""
            for w, (tot, ev) in sorted(byw.items()):
                rows.append({"구분": "트렌치", "항목": f"트렌치 W{w} (레이어 사각형 긴 변, 종류 확인 필요" + (", 부분합)" if bent_n else ")"),
                             "규격": f"폭 {w}(사각형 짧은 변)", "단위": "M", "수량": _m(tot),
                             "근거": "전용 레이어의 축과 나란한 닫힌 사각형의 긴 변: " + "; ".join(ev) + ("." + part if part else "")})
            if bent_ev:
                rows.append({"구분": "트렌치", "항목": "트렌치 (레이어 꺾인 폐합선, 길이 미산출, 확인 필요)", "규격": "", "단위": "M", "수량": None,
                             "근거": f"꺾인 폐합선 {bent_n}개 둘레 합 {_fmt(bent_len)} (길이 미산출): " + "; ".join(bent_ev)})
    except Exception as ex:
        rows.append({"구분": "트렌치", "항목": "트렌치 레이어 폐합선 (읽기 실패, 확인 필요)", "규격": "", "단위": "", "수량": None, "근거": str(ex)})

    # 4) 글자
    try:
        per, civil_tot, kinds = [], 0, Counter()
        byfl = defaultdict(list)
        for f in ctx.frames:
            got, civil, det = _trench_texts(f)
            if got or civil or det:
                byfl[ctx.info[id(f)]["fl"]].append((f, got, civil, det))
        ev = []
        dup_tot = 0
        for fl in sorted(byfl, key=lambda x: floor_sort_key(x[0]) if x else (" ", -999)):
            lst = byfl[fl]
            for i, (f, got, civil, det) in enumerate(lst):
                c = Counter((k, w) for k, w, _ in got)
                for k_, n in c.items():
                    kinds[k_] += n
                civil_tot += civil
                parts = [f"{k}" + (f" W={w}" if w else " (W 없음)") + f" {n}회" for (k, w), n in sorted(c.items(), key=lambda a: (a[0][0], a[0][1] or 0))]
                if civil:
                    parts.append(f"토목·조경 {civil}회 제외")
                if det:
                    parts.append(f"상세 참조 글자 {det}회(세지 않음)")
                dn = ""
                if fl and got:
                    for g, gg, _, _ in lst[:i]:
                        if not gg:
                            continue
                        n = _text_dups(ctx, f, g, lambda x: [t for t in x.texts if TR_TXT.search(t.text) and not CIVIL_TXT.search(t.text)])
                        if n:
                            dn = f" (그중 {n}회는 {_ftitle(g)} 와 같은 자리 = 중복 확인)"
                            dup_tot += n
                            break
                ev.append(f"{_flabel(fl)} {_ftitle(f)}: " + ", ".join(parts) + dn)
        if ev:
            head = ("글자와 지시선만으로는 길이를 내지 않음(지시선 끝 선 찾기는 아직 안 함). 종류·폭은 사람이 정할 단서: "
                    + ", ".join(f"{k}" + (f" W={w}" if w else "") + f" {n}회" for (k, w), n in sorted(kinds.items(), key=lambda a: (a[0][0], a[0][1] or 0)))
                    + (f" (도곽별 합, 같은 자리 중복 확인 {dup_tot}회)" if dup_tot else " (도곽별 합, 중복 확인 전)"))
            rows.append({"구분": "트렌치", "항목": "트렌치 글자 (길이 미산출, 확인 필요)", "규격": "글자", "단위": "M", "수량": None,
                         "근거": head + ": " + "; ".join(ev)})
    except Exception as ex:
        rows.append({"구분": "트렌치", "항목": "트렌치 글자 (읽기 실패, 확인 필요)", "규격": "", "단위": "", "수량": None, "근거": str(ex)})
    if not rows:
        rows.append({"구분": "트렌치", "항목": "트렌치 (평면도에서 표시 못 찾음, 확인 필요)", "규격": "", "단위": "M", "수량": None,
                     "근거": f"평면 종류 도곽 {len(ctx.frames)}장에서 트렌치 레이어·무소음트렌치 블록·트렌치 글자를 못 찾음 (도면에 없다는 뜻은 아님)"})
    return rows
