"""부호 태그 기준 범용 창호일람표 파서.

사무소마다 일람표 모양은 달라도 각 칸에는 '번호 위 / 종류 아래' 부호 태그가 하나씩 있다.
1) 태그를 찾고  2) 같은 줄의 표 칸에서 수량("7개소", "/ 14개소")과 사양 글자를 읽고
3) 입면도가 태그 위에 있는지 아래에 있는지(사무소별 배치)를 판정해 그 영역의 치수로 W×H를 정한다.
층별 수량표('계' 열)가 있는 일람표는 기존 schedule.parse_schedule_sheet 결과를 우선한다.
"""
import re
import statistics
from collections import Counter

from .schedule import ScheduleItem, normalize_code, _num, NUM_RE, MARGIN_MAX, MIN_SIZE, NUM1

TYPE_RE = re.compile(r"^[A-Z]{1,5}$")
NUMB_RE = re.compile(r"^%s(?:\s*[,/]\s*%s)*$" % (NUM1, NUM1))   # 묶음 표기 1,3 / 2/4 포함
STATED_ROW = True
EDGE_COVER = True
NARROW_CELL_H = 8
FAR_PITCH = 0
FAR_X = 1
RIGHT_EDGE = 0.85
HARDWARE_RE = re.compile(r"힌지|손잡이|부속|철물|도어록|도어락|클로저|스토퍼|경첩|잠금|정첩|핸들|렌치|실린더|고무|가스켓|몰딩|방충망")
QTY_RE = re.compile(r"(\d+)\s*(개소|EA|ea|SET|틀)(?![가-힣A-Za-z])|(?<![가-힣\w])(\d+)\s*개(?![가-힣A-Za-z])")
# '3EA X 4F = 12EA', '2개 × 3층 = 6개', '3EA X 4F' : 층당 개수 × 층수 (= 합계)
QTY_FORMULA_RE = re.compile(r"(?<![\d,.])(\d{1,3})\s*(EA|ea|개소|개)?\s*[xX×*]\s*(\d{1,2})\s*(F|f|층|개층)?\s*(?:=\s*(\d{1,4})\s*(?:EA|ea|개소|개)?)?")
DOOR_TYPES = re.compile(r"(D|ST|SH|S)$|D[A-Z]?$|^(ST|SH|FSH|OHD|ATD|AD)")
SINGLE_TEXT = True
SINGLE_RE = re.compile(r"^\s*([A-Z]{2,5})\s*-\s*(\d{1,3}[A-Za-z]?'?)\s*$")
SINGLE_PLAN_RE = re.compile(r"^\s*([A-Z]{1,5})\s*-?\s*(\d{1,3}[A-Za-z]?'?)\s*$")
SINGLE_IGNORE = {"THK", "TYPE", "NO", "REV", "DWG", "STS", "SUS", "SST", "STL", "PVC", "ALC", "RC", "SRC", "KS", "ISO", "FL", "EL", "GL", "SL"}
SINGLE_TYPES = set()          # 이번 실행에서 일람표가 한 덩어리 글자 부호로 읽힌 종류 (pipeline 이 시작할 때 비운다)
IGNORE_TYPES = {"FL", "SL", "CL", "GL", "EL", "UP", "DN", "N", "S", "E", "W", "A", "B", "C", "X", "Y",
                "F", "H", "L", "R", "EV", "PS", "DA", "TV", "MDF", "NONE"}


STACKED = {}      # (파일, 도면명) → 같은 자리에 겹쳐 그려져 하나로 센 부호 글자 수


def _unstack(texts):
    """같은 글자가 정확히 같은 자리에 두 번 그려진 것(복사가 겹친 것)은 하나로 본다. 자리가 글자 높이의 1/10 안에서 같을 때만."""
    seen, out = {}, []
    for t in texts:
        h = max(t.y1 - t.y0, 1e-9)
        q = max(h * 0.1, 1e-6)
        k = (t.text.strip(), round(t.x0 / q), round(t.y0 / q))
        near = [(k[0], k[1] + i, k[2] + j) for i in (-1, 0, 1) for j in (-1, 0, 1)]
        if any(n in seen for n in near):
            continue
        seen[k] = 1
        out.append(t)
    return out


def find_tags(sheet, types=None):
    """번호(위)+종류(아래) 태그 목록 [(code, type, number, type_text, number_text)]."""
    tags, used = [], set()
    texts0 = [t for t in sheet.texts if t.angle == 0]
    texts = _unstack(texts0)
    if types is not None and len(texts) < len(texts0):
        kept = {id(t) for t in texts}
        n = sum(1 for t in texts0 if id(t) not in kept and (NUMB_RE.match(t.text) or SINGLE_PLAN_RE.match(t.text)))
        if n:
            STACKED[(getattr(sheet, "file", ""), sheet.title)] = n
    nums = [t for t in texts if NUMB_RE.match(t.text)]
    for t in texts:
        if not TYPE_RE.match(t.text):
            continue
        if types is not None and t.text not in types:
            continue
        if types is None and t.text in IGNORE_TYPES:
            continue
        h = max(t.y1 - t.y0, 1e-9)
        best = None
        for u in nums:
            if id(u) in used:
                continue
            dx, dy = u.cx - t.cx, t.cy - u.cy      # u가 위에 있으면 dy>0
            # 번호는 종류 글자 바로 위(분수형 태그는 가운데 선 때문에 3배 높이까지 떨어짐)
            if abs(dx) <= h * 1.3 and h * 0.6 <= dy <= h * 3.4:
                d = abs(dx) + dy
                if best is None or d < best[0]:
                    best = (d, u)
        if best:
            used.add(id(best[1]))
            for num in re.split(r"\s*[,/]\s*", best[1].text):
                tags.append((normalize_code(t.text, num), t.text, num, t, best[1]))
    # 한 덩어리 글자 부호('FSD-01', 'AW-1'): 일람표(types 없음)에서는 붙임표가 있는 것만,
    # 평면도(types 있음)에서는 일람표가 한 덩어리 글자로 적힌 종류(SINGLE_TYPES)만 'FSD5'·'SSD08c' 처럼 붙임표 없이도 받는다.
    if SINGLE_TEXT:
        paired = {id(tg[3]) for tg in tags} | {id(tg[4]) for tg in tags}
        have = {tg[0] for tg in tags}
        for t in texts:
            if id(t) in paired:
                continue
            m = (SINGLE_PLAN_RE if types is not None else SINGLE_RE).match(t.text)
            if not m:
                continue
            typ, num = m.group(1), m.group(2)
            if types is not None:
                if typ not in types or typ not in SINGLE_TYPES:
                    continue
            elif typ in IGNORE_TYPES or typ in SINGLE_IGNORE:
                continue
            code = normalize_code(typ, num)
            if types is None and code in have:
                continue                      # 일람표: 같은 부호의 짝 태그가 이미 있으면 그쪽이 그림 태그
            tags.append((code, typ, num, t, t))
    return tags


def _vertical_chains(texts):
    ts = [t for t in texts if NUM_RE.match(t.text) and t.angle in (90, 270)]
    groups = []
    for t in sorted(ts, key=lambda t: t.cx):
        for g in groups:
            if abs(g[0] - t.cx) <= (t.x1 - t.x0) * 0.6:
                g[1].append(t)
                break
        else:
            groups.append([t.cx, [t]])
    return [sorted(g, key=lambda t: t.cy) for _, g in groups]


def _horizontal_chains(texts):
    ts = [t for t in texts if NUM_RE.match(t.text) and t.angle == 0]
    groups = []
    for t in sorted(ts, key=lambda t: t.cy):
        for g in groups:
            if abs(g[0] - t.cy) <= (t.y1 - t.y0) * 0.6:
                g[1].append(t)
                break
        else:
            groups.append([t.cy, [t]])
    return [sorted(g, key=lambda t: t.cx) for _, g in groups]


def _pick(chains, drop_bottom=False):
    """chains: 치수선별 글자 목록. 양옆 여유치수(≤30) 괄호 우선, 없으면 최대.
    drop_bottom: 창(窓)의 경우 맨 아래(바닥에 닿는) 치수는 창대높이로 보고 제외."""
    if not chains:
        return None, [], False
    lowest = max((t.cy for ch in chains for t in ch), default=None)
    bracketed, allv, dropped = [], [], False
    for ch in chains:
        vals = [_num(t.text) for t in ch]
        for i, (v, t) in enumerate(zip(vals, ch)):
            if v is None or v < MIN_SIZE:
                continue
            big = [x for x in vals if x and x >= MIN_SIZE]
            if drop_bottom and len(big) >= 2 and i == len(ch) - 1 and abs(t.cy - lowest) < (t.x1 - t.x0) * 0.6:
                dropped = True
                continue
            allv.append(v)
            left = vals[i - 1] if i > 0 else None
            right = vals[i + 1] if i + 1 < len(vals) else None
            if left is not None and right is not None and left <= MARGIN_MAX and right <= MARGIN_MAX:
                bracketed.append(v)
    if bracketed:
        return int(max(bracketed)), sorted(set(allv)), dropped
    if allv:
        return int(max(allv)), sorted(set(allv)), dropped
    return None, [], dropped


CHAIN_GAP_MM = 120
LEVEL_RE = re.compile(r"(?:\d{1,2}\s*층|옥탑|지붕|PH|RF|B\s*\d)\s*(?:층)?\s*/?\s*[SFG]\.?L\b", re.I)


CHAIN_LINE_TOL = 60       # 이 안에서 어긋난 치수선은 같은 줄로 본다 (나란한 두 치수선은 보통 수백 mm 떨어져 있다)


def chain_sums(ds):
    """치수선별(가로치수는 ty, 세로치수는 치수선 x 가 같은 것끼리) 끝과 끝이 이어진 치수들의 합 목록 (예: 6,960+170+9,500)."""
    if not ds:
        return []
    # 같은 치수선: 치수선 위치가 CHAIN_LINE_TOL(mm) 안에서 이어지는 것끼리 (손으로 이어 그리다 수십 mm 어긋난 줄도 한 줄.
    # 칸으로 나눠 묶으면 22mm 차이가 경계에 걸려 두 줄로 갈린다 — Q현장 CW-4 54,300 을 25,690 으로 읽었던 원인)
    rows = []
    for o in ("h", "v"):
        pos = sorted(((d["ty"] if o == "h" else d.get("px", d["tx"])), i) for i, d in enumerate(ds) if d["o"] == o)
        cur, last = [], None
        for p_, i in pos:
            if last is not None and p_ - last > CHAIN_LINE_TOL:
                rows.append(cur)
                cur = []
            cur.append(ds[i])
            last = p_
        if cur:
            rows.append(cur)
    out = []
    for lst in rows:
        lst = sorted(lst, key=lambda d: d["span"][0])
        tol = max(max(d["val"] for d in lst) * 0.003, 3)
        gap = max(tol, CHAIN_GAP_MM)          # 나란한 두 틀 사이 이음매(≤120mm)는 이어진 것으로 본다
        cur, end, best = 0.0, None, 0.0
        for d in lst:
            if end is not None and -tol <= d["span"][0] - end <= gap:
                cur += d["val"]
            else:
                cur = d["val"]
            end = d["span"][1]
            best = max(best, cur)
        out.append(best)
    return out


def chain_total(hs):
    """이어진 치수 합의 최댓값."""
    s = chain_sums(hs)
    return max(s) if s else None


def _chain_or_max(ds, cur, tol):
    """단일 최대치수 cur 와 치수 사슬 합을 비교해 최종값 결정.
    후보 치수선: 최대치수와 '같은 쪽'(측정점 py/px 가 가까운 쪽)에 있는 치수선만.
    - 그 치수선들 가운데 끝이 이어진 치수들의 합이 cur 보다 크면 그 합 (총치수가 안 적힌 6,960+170+9,500, 소계만 적힌 5,100 위 900+2,550+2,550)
    - 반대쪽(창 아래 그리드 간격선 등)의 사슬 합은 보지 않는다 (H현장 CW-1: 위 총치수 26,400 vs 아래 그리드 합 26,500)"""
    if not ds:
        return cur, None
    dmax = max(ds, key=lambda d: d["val"])
    side = (lambda d: d.get("py", d["ty"])) if dmax["o"] == "h" else (lambda d: d.get("px", d["tx"]))
    near = max(dmax["val"] * 0.25, tol * 10)
    same = [d for d in ds if d["o"] == dmax["o"] and abs(side(d) - side(dmax)) <= near]
    sums = chain_sums(same)
    ch = max(sums) if sums else 0
    if ch > cur + tol:
        return int(round(ch)), ch
    return cur, None


def _abut(a, b, tol):
    return abs(a - b) <= tol


def development_width(dims, h):
    """모서리를 돌아가는 긴 진열창을 펼쳐 그린 그림(전개도)의 폭.
    한 칸에 그림이 여러 줄·여러 조각으로 나뉘어 있고, 줄마다 맨 위 치수선(조각별 총치수)이 있다:
      윗줄  2,400 | 4,600 |90°| 15,700        아랫줄(이어짐)  20,500
    → 줄마다 맨 위 치수선의 조각(서로 겹치지 않는 것)을 모두 더하고, 줄끼리도 더한다.
    치수가 적히지 않은 틈(기둥·벽을 X 로 지운 곳)은 더하지 않는다.
    반환: (폭, [줄별 조각 목록]) — 치수선이 없으면 None"""
    hs = [d for d in dims if d["o"] == "h" and d["val"] > 0]
    if not hs:
        return None
    lines_, cur, last = [], [], None
    for d in sorted(hs, key=lambda d: d["ty"]):
        if last is not None and d["ty"] - last > CHAIN_LINE_TOL:
            lines_.append(cur)
            cur = []
        cur.append(d)
        last = d["ty"]
    lines_.append(cur)
    rows, prev_y = [], None
    for ln in lines_:                       # 위에서 아래로: 바로 위 치수선과 가까우면(세부 치수 줄) 같은 그림 줄
        y = min(d["ty"] for d in ln)
        if prev_y is not None and y - prev_y <= h * 8:
            prev_y = y
            continue
        prev_y = y
        segs = []
        for d in sorted(ln, key=lambda d: (d["span"][0], -d["val"])):
            if segs and d["span"][0] < segs[-1]["span"][1] - 5:
                if d["val"] > segs[-1]["val"]:
                    segs[-1] = d
                continue
            segs.append(d)
        rows.append([round(d["val"]) for d in segs])
    return sum(sum(r) for r in rows), rows


def _frame_extent(lines, x_lo, x_hi, y_lo, y_hi):
    """창틀 가로 틀선의 맨 아래·맨 위 y. 틀선 = 폭 [x_lo,x_hi] 의 90% 이상을 덮고, 폭 밖으로 5% 넘게 나가지 않는 가로선.
    (층 바닥선 FL 은 레벨 기호까지 길게 나가므로 빠진다.) 없으면 None"""
    W = x_hi - x_lo
    if W <= 0:
        return None
    m = max(W * 0.05, lines.tol * 4)
    pad = (y_hi - y_lo) * 0.02 + lines.tol * 4
    lv = {}
    out_of = set()
    for y, a, b in lines.h:
        if not (y_lo - pad <= y <= y_hi + pad) or b <= x_lo or a >= x_hi:
            continue
        key = round(y / (lines.tol * 2))
        if a < x_lo - m or b > x_hi + m:
            out_of.add(key)
            continue
        lv.setdefault(key, []).append((max(a, x_lo), min(b, x_hi), y))
    ys = []
    for key, lst in lv.items():
        if key in out_of or (key - 1) in out_of or (key + 1) in out_of:
            continue
        lst.sort()
        cov, ca, cb = 0.0, None, None
        for a, b, _ in lst:
            if cb is None or a > cb:
                if cb is not None:
                    cov += cb - ca
                ca, cb = a, b
            else:
                cb = max(cb, b)
        if cb is not None:
            cov += cb - ca
        if cov >= 0.9 * W:
            ys.append(sum(y for _, _, y in lst) / len(lst))
    if len(ys) < 2:
        return None
    return min(ys), max(ys)


def _end_gaps(lines, x_lo, x_hi, y0, y1):
    """x_lo~x_hi 띠 안의 세로선이 [y0,y1] 의 양 끝에서 얼마나 모자라는지 (아래쪽 틈, 위쪽 틈). 세로선이 없으면 구간 전체."""
    lo, hi = None, None
    for x, a, b in lines.v:
        if x_lo <= x <= x_hi and b > y0 and a < y1:
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    if lo is None:
        return (y1 - y0, y1 - y0)
    return (max(lo - y0, 0), max(y1 - hi, 0))


def size_from_dims(dims, is_door, lines=None):
    """치수 객체(측정 구간 포함)로 W×H 결정.
    가로: 양끝이 30mm 이하 여유치수와 맞닿은 치수 우선, 없으면 최대.
    세로: 창은 바닥(FL, 가장 낮은 끝점)에 닿는 치수(창대·바닥~창상단)를 제외하고 최대."""
    hs = [d for d in dims if d["o"] == "h" and d["val"] >= 1]
    vs = [d for d in dims if d["o"] == "v" and d["val"] >= 1]
    w = h = None
    notes = []
    big = [d for d in hs if d["val"] >= MIN_SIZE]
    if big:
        tol = max(max(d["val"] for d in big) * 0.003, 2)
        small = [d for d in hs if d["val"] <= MARGIN_MAX]
        br = [d for d in big if any(_abut(s["span"][1], d["span"][0], tol) for s in small)
              and any(_abut(s["span"][0], d["span"][1], tol) for s in small)]
        w = int(round(max(d["val"] for d in (br or big))))
        if not br:
            w2, ch = _chain_or_max(hs, w, tol)
            if ch:
                notes.append(f"이어진 가로 치수 합 {ch:.0f} 사용 (최대 단일 치수 {w})")
            w = w2
    vb = [d for d in vs if d["val"] >= MIN_SIZE]
    if vb and lines is not None and big:
        # 창틀 세로선이 실제로 덮는 치수만 창 높이 후보 (창대·벽 구간 제외)
        wd = max(big, key=lambda d: d["val"])
        x_lo, x_hi = wd["span"][0] - lines.tol * 2, wd["span"][1] + lines.tol * 2
        # 창틀의 바깥 세로선은 폭 치수의 양 끝에 있다. 가운데를 지나는 지시선(주석 인출선)이 창 위 빈 구간을 덮어도 창틀로 치지 않도록
        # 양 끝 띠(폭의 6%)에서만 덮임을 본다. (K현장 PPW-9: 창 위 500 구간을 '배연창' 인출선이 덮어 1100+500 으로 읽던 것)
        e = max(lines.tol * 6, 0.06 * (x_hi - x_lo)) if EDGE_COVER else (x_hi - x_lo)
        cov = [(d, max(lines.v_coverage(x_lo, min(x_lo + e, x_hi), d["span"][0], d["span"][1]),
                       lines.v_coverage(max(x_hi - e, x_lo), x_hi, d["span"][0], d["span"][1]))) for d in vb]
        good = [d for d, c in cov if c >= 0.85]
        if good:
            h = int(round(max(d["val"] for d in good)))
            if len(good) < len(vb) and max(d["val"] for d in vb) > h:
                notes.append("창틀 선이 없는 구간(창대·벽)의 치수는 제외하고 높이 산정")
            h2, ch = _chain_or_max(good, h, max(h * 0.003, 2))
            if ch:
                notes.append(f"이어진 세로 치수 합 {ch:.0f} 사용 (최대 단일 치수 {h})")
            h = h2
            # 창틀의 위·아래 끝(가로 틀선)에 꼭 맞는 치수가 따로 있으면 그것이 창 높이다.
            # 커튼월 그림에는 층 바닥선(FL)까지 잰 치수(층고 합)와 창틀만 잰 치수가 같이 있다: L현장 ACW-1 은 틀 6,925 / 층고 7,600
            # 폭이 이어진 치수 합일 때는 그 전체 구간이 창틀의 폭이다
            fx_lo = min(d["span"][0] for d in big) - lines.tol * 2
            fx_hi = max(d["span"][1] for d in big) + lines.tol * 2
            ext = _frame_extent(lines, fx_lo, fx_hi, min(d["span"][0] for d in vb), max(d["span"][1] for d in vb))
            fit = []
            fe = max(lines.tol * 6, 0.06 * (fx_hi - fx_lo))
            def _gaps(d):
                return min((_end_gaps(lines, a_, b_, d["span"][0], d["span"][1]) for a_, b_ in
                            ((fx_lo, fx_lo + fe), (fx_hi - fe, fx_hi))), key=lambda g: g[0] + g[1])
            if ext:
                # 가로 틀선의 위·아래 끝에 맞는 치수. 다만 문처럼 아래 틀선이 없는 그림에서는 틀선 범위가 창 일부일 뿐이므로(B현장 AW-5),
                # 지금 읽은 높이의 치수가 창틀 세로선 밖으로 실제로 나가 있을 때만 바꾼다
                cur_ = [d for d in vb if abs(d["val"] - h) <= max(30, h * 0.002)]
                def _gaps_any(d):      # 한쪽 끝 띠라도 세로 틀선이 모자라면 창틀 밖으로 나간 치수다
                    return max((_end_gaps(lines, a_, b_, d["span"][0], d["span"][1]) for a_, b_ in
                                ((fx_lo, fx_lo + fe), (fx_hi - fe, fx_hi))), key=lambda g: g[0] + g[1])
                over = (not cur_) or all(max(_gaps_any(d)) > max(30, d["val"] * 0.003) for d in cur_)
                fit = [d for d in vb if d["val"] >= 0.8 * h and over and max(_gaps(d)) <= max(30, d["val"] * 0.003)
                       and abs(d["span"][0] - ext[0]) <= max(30, d["val"] * 0.002) and abs(d["span"][1] - ext[1]) <= max(30, d["val"] * 0.002)]
            if not fit:
                # 틀선으로 못 가리면(맨 아래 틀선이 층 바닥선과 겹치는 등) 창틀 바깥 세로선이 치수 양 끝까지 닿는지로 가린다
                full = [d for d in vb if d["val"] >= 0.8 * h and max(_gaps(d)) <= max(30, d["val"] * 0.003)]
                top = max(full, key=lambda d: d["val"]) if full else None
                if top and h - top["val"] > max(30, h * 0.002) \
                        and not any(abs(d["val"] - h) <= max(30, h * 0.002) for d in full):
                    fit = [top]
                elif not full:
                    # 양 끝이 모두 맞는 치수가 없다: 창틀 선에 가장 가까운 치수를 쓰되 확인 표시 (L현장 ACW-5: 치수 11,085, 틀선은 10,725 구간)
                    cur = [d for d in vb if abs(d["val"] - h) <= max(30, h * 0.002)]
                    near = min((d for d in vb if d["val"] >= 0.8 * h), key=lambda d: sum(_gaps(d)), default=None)
                    if cur and near and sum(_gaps(near)) < sum(_gaps(cur[0])) - 30 and h - near["val"] > 30:
                        g = _gaps(near)
                        notes.append(f"창틀 선은 {near['val'] - sum(g):.0f} 구간만 덮는데 맞는 치수가 없음: 가장 가까운 치수 {near['val']:.0f} 를 씀 (틈 {g[0]:.0f}/{g[1]:.0f}) → 높이 확인")
                        h = int(round(near["val"]))
            # 차이가 200 미만이면 바꾸지 않는다(H현장 CW-3: 32,100 과 32,000 — 사람이 읽은 값은 32,100)
            if fit and abs(fit[0]["val"] - h) >= 200:
                hv = int(round(fit[0]["val"]))
                notes = [n for n in notes if "이어진 세로 치수 합" not in n]
                notes.append(f"창틀 위·아래 끝에 맞는 치수 {hv} 를 높이로 씀 (층 바닥선까지 잰 {h} 는 창틀 밖 구간 포함)")
                h = hv
            return w, h, notes
    if vb:
        fl = max(d["span"][1] for d in vs)
        tol = max(max(d["val"] for d in vb) * 0.003, 2)
        cand = vb
        if not is_door:
            off = [d for d in vb if not _abut(d["span"][1], fl, tol)]
            if off and len(off) < len(vb):
                cand = off
                notes.append("바닥(FL)에 닿는 치수(창대/전체높이)는 제외하고 창 높이 산정")
        h = int(round(max(d["val"] for d in cand)))
    return w, h, notes


def _rows(tags, tol):
    ys = sorted({round(t[3].cy / tol) for t in tags})
    return ys


TABLE_CODE_RE = re.compile(r"^\s*([A-Z]{2,4})\s*-?\s*(\d{1,2}[A-Z]?)\s*$")


def _table_rows(sheet, have, h):
    """표로 된 일람(부호 | TYPE | W X H | 개소 | 위치 — 그릴·셔터 목록에 흔함): 한 줄에 'AG1' 과 '800 X 700' 이 나란히 있으면 한 부호.
    개소는 머리글 '개소' 열 아래의 숫자."""
    out = []
    sizes = [(t, m) for t in sheet.texts if t.angle == 0
             for m in [re.fullmatch(r"\s*(\d{1,2},?\d{3}|\d{3})\s*[xX×]\s*(\d{1,2},?\d{3}|\d{3})\s*", t.text)] if m]
    # W 와 H 가 따로 칸에 적힌 표(부호 | 층 | 수량 | 명칭 | 위치 | … | 6,600 | 3,250 | …): 같은 줄에 크기 숫자가 꼭 둘이면 앞이 W, 뒤가 H
    NUMSZ = re.compile(r"\s*(\d{1,2},\d{3}|\d{3,5})\s*")
    for t in sheet.texts:
        m = TABLE_CODE_RE.match(t.text)
        if not m or t.angle != 0 or m.group(1) in IGNORE_TYPES or f"{m.group(1)}-{m.group(2)}" in have:
            continue
        hh_ = max(t.size, t.y1 - t.y0, 1)
        line = sorted([u for u in sheet.texts if u.angle == 0 and u is not t and abs(u.cy - t.cy) <= hh_ * 0.8 and 0 < u.cx - t.cx < hh_ * 150],
                      key=lambda u: u.cx)
        if any(re.search(r"\d\s*[xX×]\s*\d", u.text) for u in line):
            continue
        nums = [u for u in line if NUMSZ.fullmatch(u.text) and int(u.text.replace(",", "")) >= 300]
        if len(nums) != 2 or len(line) < 3:
            continue
        if any(TABLE_CODE_RE.match(u.text) and u.cx < nums[0].cx for u in line):
            continue                  # 이 글자와 숫자 사이에 다른 부호가 있으면 그 부호의 줄이다 (그림 옆 큰 부호 글자가 작은 표의 줄과 나란한 경우)
        w, hg = (int(u.text.replace(",", "")) for u in nums)
        if not (300 <= w <= 60000 and 300 <= hg <= 20000):
            continue
        qty = None
        for u in line:
            q = QTY_RE.search(u.text)
            if q and not HARDWARE_RE.search(u.text):
                qty = float(q.group(1) or q.group(3))
                break
        code = f"{m.group(1)}-{m.group(2)}"
        it = ScheduleItem(code=code, type=m.group(1), number=m.group(2), name="", location="",
                          width=w, height=hg, qty=qty, frame="", glass="", hardware="",
                          sheet=f"{sheet.title} (도곽 {sheet.index})", width_candidates=[w], height_candidates=[hg])
        it.notes.append(f"표로 된 일람에서 읽음 (W {nums[0].text.strip()}, H {nums[1].text.strip()} 칸)")
        it.spec_text = " ".join(u.text for u in line)[:300]
        it.layout = "table"
        it.region = (t.x0, t.y0, nums[1].x1, t.y1)
        it.tag_box = (t.x0, t.y0, t.x1, t.y1)
        it.region_sheet = sheet
        have.add(code)
        out.append(it)
    if not sizes:
        return out
    heads = [t for t in sheet.texts if re.sub(r"\s", "", t.text) in ("개소", "수량", "EA")]
    for t in sheet.texts:
        m = TABLE_CODE_RE.match(t.text)
        if not m or t.angle != 0 or m.group(1) in IGNORE_TYPES:
            continue
        code = f"{m.group(1)}-{m.group(2)}"
        if code in have:
            continue
        hh_ = max(t.size, 1)
        row = [(s_, sm) for s_, sm in sizes if abs(s_.cy - t.cy) <= hh_ * 0.8 and 0 < s_.cx - t.cx < hh_ * 60]
        if not row:
            continue
        s_, sm = min(row, key=lambda x: x[0].cx)
        w, hg = int(sm.group(1).replace(",", "")), int(sm.group(2).replace(",", ""))
        qty = None
        hd = [x for x in heads if x.cy < t.cy and x.cx > s_.cx]
        if hd:
            hx = min(hd, key=lambda x: (t.cy - x.cy, x.cx)).cx
            nums = [x for x in sheet.texts if abs(x.cy - t.cy) <= hh_ * 0.8 and abs(x.cx - hx) <= hh_ * 6 and re.fullmatch(r"\s*\d{1,3}\s*", x.text)]
            if nums:
                qty = float(min(nums, key=lambda x: abs(x.cx - hx)).text)
        it = ScheduleItem(code=code, type=m.group(1), number=m.group(2), name="", location="",
                          width=w, height=hg, qty=qty, frame="", glass="", hardware="",
                          sheet=f"{sheet.title} (도곽 {sheet.index})", width_candidates=[w], height_candidates=[hg])
        it.notes.append(f"표로 된 일람에서 읽음 ('{s_.text.strip()}')")
        it.spec_text = ""
        it.layout = "table"
        it.region = (t.x0, t.y0, s_.x1, t.y1)
        it.tag_box = (t.x0, t.y0, t.x1, t.y1)
        it.region_sheet = sheet
        have.add(code)
        out.append(it)
    return out


def _rotated(sheet):
    """도곽 전체를 90° 돌려 그린 일람표(글자가 모두 세로)를 바로 세운 사본. (x, y) → (-y, x)"""
    import copy
    r = copy.copy(sheet)
    r.texts = []
    for t in sheet.texts:
        u = copy.copy(t)
        u.x0, u.x1, u.y0, u.y1 = -t.y1, -t.y0, t.x0, t.x1
        u.angle = (t.angle + 90) % 360
        r.texts.append(u)
    r.segments = [(-sg[1], sg[0], -sg[3], sg[2]) for sg in sheet.segments]
    r.dims = []
    for d in getattr(sheet, "dims", None) or []:
        a, b = d["span"]
        r.dims.append({"o": "v" if d["o"] == "h" else "h", "val": d["val"], "span": (a, b) if d["o"] == "h" else (-b, -a),
                       "tx": -d["ty"], "ty": d["tx"], "px": -d.get("py", d["ty"]), "py": d.get("px", d["tx"])})
    x0, y0, x1, y1 = getattr(sheet, "bounds", (0, 0, sheet.width, sheet.height))
    r.bounds = (-y1, x0, -y0, x1)
    r.width, r.height = sheet.height, sheet.width
    return r


DOOR_NO_HEAD = re.compile(r"^(문번호|도어번호|DOORNO\.?|DOORNUMBER|문NO\.?)$", re.I)
DOOR_NO_RE = re.compile(r"^\s*([A-Z]{0,2})\s*-?\s*(\d{2,4}[A-Z]?)\s*$")


def _door_list(sheet):
    """문 번호 목록형 일람표(층별 | 문 번호 | 적용실 명 | 문 크기 W x H | … | 비고): 한 줄이 문 하나.
    수량은 1(비고에 'N개소' 가 있으면 그 수), 층별 칸이 '지상 2~17층' 처럼 여러 층이면 층수만큼 곱한다.
    크기가 없는 줄('창호입면도참조 (ASD-1)')은 창호 일람표 쪽 부호이므로 넣지 않는다."""
    heads = sorted([t for t in sheet.texts if t.angle == 0 and DOOR_NO_HEAD.match(re.sub(r"\s", "", t.text))], key=lambda t: t.cx)
    if not heads:
        return []
    SZ = r"\s*(\d{1,2},?\d{3}|\d{3})\s*[xX×*]\s*(\d{1,2},?\d{3}|\d{3})\s*"
    sizes = [(t, m) for t in sheet.texts if t.angle == 0 for m in [re.fullmatch(SZ, t.text)] if m]
    x1 = getattr(sheet, "bounds", (0, 0, sheet.width, sheet.height))[2]
    out, seen = [], set()
    for hi, hd in enumerate(heads):
        hh = max(hd.y1 - hd.y0, 1e-9)
        prev_x = heads[hi - 1].cx if hi else None
        next_x = min([o.cx for o in heads if o.cx > hd.cx + hh * 10], default=x1)
        # 이 묶음의 '층별' 칸: 문 번호 머리글 왼쪽 (앞 묶음 머리글 오른쪽)
        fl_head = [t for t in sheet.texts if re.sub(r"\s", "", t.text) in ("층별", "층", "FLOOR") and abs(t.cy - hd.cy) <= hh * 2
                   and t.cx < hd.cx and (prev_x is None or t.cx > prev_x)]
        fl_x = max(fl_head, key=lambda t: t.cx).cx if fl_head else None
        right = next_x - ((hd.cx - fl_x) if fl_x is not None else hh * 4) - hh * 2
        floors = sorted([t for t in sheet.texts if fl_x is not None and t.angle == 0 and abs(t.cx - fl_x) <= (hd.cx - fl_x) * 0.6
                         and t.cy > hd.cy + hh and re.search(r"층|[BP]\d|RF|PH", t.text)], key=lambda t: t.cy)
        for t in sheet.texts:
            m = DOOR_NO_RE.match(t.text)
            if not m or t.angle != 0 or t.cy <= hd.cy + hh or abs(t.cx - hd.cx) > hh * 5:
                continue
            row = [(s_, sm) for s_, sm in sizes if abs(s_.cy - t.cy) <= hh * 1.2 and t.cx < s_.cx < right]
            if not row:
                continue
            s_, sm = min(row, key=lambda x: x[0].cx)
            w, hg = int(sm.group(1).replace(",", "")), int(sm.group(2).replace(",", ""))
            typ = m.group(1) or "D"
            code = f"{typ}-{m.group(2)}"
            if code in seen:
                continue
            seen.add(code)
            line = [u for u in sheet.texts if u.angle == 0 and u is not t and abs(u.cy - t.cy) <= hh * 1.2 and t.cx < u.cx < right]
            qty, notes = 1.0, [f"문 번호 목록에서 읽음 ('{t.text.strip()}', {s_.text.strip()})"]
            for u in line:
                q = QTY_RE.search(u.text)
                if q:
                    qty = float(q.group(1) or q.group(3))
                    notes.append(f"비고 '{u.text.strip()}' → {qty:g}개소")
                    break
            fl = [f_ for f_ in floors if f_.cy <= t.cy + hh * 1.2]
            fl_txt = fl[-1].text.strip() if fl else ""
            fm = re.search(r"(\d{1,2})\s*[~∼-]\s*(\d{1,2})\s*층", fl_txt)
            if fm and int(fm.group(2)) > int(fm.group(1)):
                n = int(fm.group(2)) - int(fm.group(1)) + 1
                notes.append(f"층별 '{fl_txt}' → {n}개 층 × {qty:g}")
                qty *= n
            name = min([u for u in line if u.cx < s_.cx and re.search(r"[가-힣A-Za-z]", u.text)], key=lambda u: u.cx, default=None)
            it = ScheduleItem(code=code, type=typ, number=m.group(2), name="문", location=(fl_txt + " " + (name.text.strip() if name else "")).strip(),
                              width=w, height=hg, qty=qty, frame="", glass="", hardware="",
                              sheet=f"{sheet.title} (도곽 {sheet.index})", width_candidates=[w], height_candidates=[hg])
            it.notes += notes
            it.spec_text = " ".join(u.text for u in sorted(line, key=lambda u: u.cx))[:300]
            # 열 머리글(문 형태 / 재료 / HDWE SET NO / 비고)에 맞춰 그 줄의 값을 읽는다 → 종류별 묶음 표, 철물 집계에 씀
            hcols = [h_ for h_ in sheet.texts if h_.angle == 0 and abs(h_.cy - hd.cy) <= hh * 2.5 and hd.cx - hh * 2 <= h_.cx < next_x
                     and re.search(r"[가-힣A-Za-z]", h_.text)]
            cols = {}
            for u in line:
                if not hcols:
                    break
                hname = re.sub(r"\s", "", min(hcols, key=lambda h_: abs(h_.cx - u.cx)).text)
                key = ("형태" if "형태" in hname or "TYPE" in hname.upper() else "재료" if "재료" in hname else
                       "철물" if "HDWE" in hname.upper() or "SETNO" in hname.upper() else "비고" if "비고" in hname else
                       "개폐" if "개폐" in hname else None)
                if key:
                    cols.setdefault(key, []).append(u.text.strip())
            it.door_cols = {k: " ".join(v) for k, v in cols.items()}
            it.layout = "doorlist"
            it.region = (t.x0, t.y0, s_.x1, t.y1)
            it.tag_box = (t.x0, t.y0, t.x1, t.y1)
            it.region_sheet = sheet
            out.append(it)
    return out


def parse_tag_schedule(sheet):
    dl = _door_list(sheet)
    if len(dl) >= 5:
        return dl
    if not find_tags(sheet) and sum(1 for t in sheet.texts if t.angle == 270) > 0.5 * len(sheet.texts):
        rot = _rotated(sheet)
        if find_tags(rot):
            items = _parse_tag_schedule(rot)
            for it in items:
                for nm in ("region", "tag_box"):
                    b = getattr(it, nm, None)
                    if b:
                        setattr(it, nm, (b[1], -b[2], b[3], -b[0]))
                it.region_sheet = sheet
                it.notes.append("도곽을 90° 돌려 그린 일람표 → 바로 세워 읽음")
            return items
    return _parse_tag_schedule(sheet)


def _parse_tag_schedule(sheet):
    tags = find_tags(sheet)
    if not tags:
        return _table_rows(sheet, set(), 1)
    # 한 그림을 같이 쓰는 덩어리 글자 부호가 위아래로 붙은 양식(FSD-07 바로 아래 FSD-07a): 번호가 같은 것끼리만 묶어
    # 접미 글자 없는 쪽을 그림 태그로 쓰고, 나머지는 끝에서 같은 크기(+'(07a : 600)' 같은 단서)로 덧붙인다
    stacked = []
    singles = [t for t in tags if t[3] is t[4]]
    for b in singles:
        hb = max(b[3].y1 - b[3].y0, 1e-9)
        for a in singles:
            if a is b or a[1] != b[1] or len(a[2]) >= len(b[2]):
                continue
            if re.sub(r"\D", "", a[2]).lstrip("0") == re.sub(r"\D", "", b[2]).lstrip("0") and \
                    abs(a[3].x0 - b[3].x0) <= hb * 1.5 and 0 < abs(b[3].cy - a[3].cy) <= hb * 8:
                stacked.append((a, b))
                break
    if stacked:
        subs = {id(b) for _, b in stacked}
        mains = {id(a): b for a, b in stacked}
        # 그림 태그는 두 글자를 합친 상자(위 글자 = 번호 자리, 아래 글자 = 종류 자리)로 본다: 칸 찾기가 두 줄을 건너뛰도록
        tags = [(t[:3] + tuple(sorted((t[3], mains[id(t)][3]), key=lambda x: -x.cy))) if id(t) in mains else t
                for t in tags if id(t) not in subs]
        stacked = [(next(t for t in tags if t[0] == a[0]), b) for a, b in stacked]
    h = statistics.median([t[3].y1 - t[3].y0 for t in tags])
    x0, y0, x1, y1 = getattr(sheet, "bounds", (0, 0, sheet.width, sheet.height))
    # 같은 줄 태그끼리 묶기
    rows = []
    for tg in sorted(tags, key=lambda t: t[3].cy):
        for r in rows:
            if abs(r[0] - tg[3].cy) <= h * 1.5:
                r[1].append(tg)
                break
        else:
            rows.append([tg[3].cy, [tg]])
    row_y = [r[0] for r in rows]
    pitch = statistics.median([b - a for a, b in zip(row_y, row_y[1:])]) if len(row_y) > 1 else (y1 - y0) * 0.6
    xs_all = sorted(t[3].cx for t in tags)
    col_w = statistics.median([b - a for a, b in zip(xs_all, xs_all[1:]) if b - a > h * 5] or [(x1 - x0) / 4])

    def col_bounds(tg, row):
        same = sorted(t[3].x0 for t in row[1])
        nxt = [x for x in same if x > tg[3].x0 + h]
        left = tg[3].x0 - h * 1.5
        right = (min(nxt) - h * 0.5) if nxt else tg[3].x0 + col_w - h * 0.5
        return left, right

    def dims_in(left, right, top, bottom):
        return [t for t in sheet.texts if left <= t.cx <= right and top <= t.cy <= bottom and NUM_RE.match(t.text)]

    lines = None
    cells = {}
    if sheet.segments:
        from .cells import Lines, shape_cell
        lines = Lines(sheet.segments, tol=h * 0.15)
        votes = {"above": 0, "below": 0}
        for tg in tags:
            tb = (min(tg[3].x0, tg[4].x0), tg[4].y0, max(tg[3].x1, tg[4].x1), tg[3].y1)
            got = {}
            for dirn in ("above", "below"):
                c = shape_cell(lines, tb, dirn, h, x0, x1, y0, y1)
                nd = 0
                if c:
                    nd = sum(1 for t in sheet.texts if c[0] <= t.cx <= c[2] and c[1] <= t.cy <= c[3] and NUM_RE.match(t.text))
                    # 칸의 반대쪽 가장자리에 다른 태그가 붙어 있으면 그 부호의 그림 칸이다 (옆 줄 부호의 칸을 집어오지 않도록)
                    far = c[1] if tg[3].cy > c[3] else c[3]
                    near = max(h * 1.5, min(pitch * FAR_PITCH, h * 8)) if FAR_PITCH else h * 1.5
                    if any(o is not tg and c[0] - h * FAR_X <= o[3].cx <= c[2] + h * FAR_X and abs(o[3].cy - far) <= near
                           and not (c[1] <= o[3].cy <= c[3]) for o in tags):
                        c, nd = None, 0
                    # 태그가 칸의 오른쪽 끝(85% 밖)에 있으면 옆 열의 넓은 칸(자동문 등)이 이쪽까지 걸친 것 → 내 칸이 아니다
                    elif RIGHT_EDGE and (c[2] - c[0]) > h * 12 and tg[3].cx > c[0] + RIGHT_EDGE * (c[2] - c[0]):
                        c, nd = None, 0
                got[dirn] = (c, nd)
            cells[id(tg)] = got
            a, b = got["above"][1] > 0, got["below"][1] > 0
            if a and not b:
                votes["above"] += 1
            elif b and not a:
                votes["below"] += 1
        cell_layout = "above" if votes["above"] >= votes["below"] else "below"
    # 입면도 위치 판정: 맨 윗줄 태그 위 영역 vs 맨 아랫줄 태그 아래 영역
    top_row, bot_row = rows[0], rows[-1]
    up = sum(len(dims_in(*col_bounds(tg, top_row), top_row[0] - pitch, top_row[0] - h)) for tg in top_row[1])
    down = sum(len(dims_in(*col_bounds(tg, bot_row), bot_row[0] + h * 4, bot_row[0] + pitch)) for tg in bot_row[1])
    layout = "above" if up >= down else "below"
    if cells and (votes["above"] or votes["below"]):
        layout = cell_layout

    items = []
    sheet_layout = layout
    for ri, row in enumerate(rows):
        for tg in row[1]:
            code, typ, num, tt, nt = tg
            grouped = bool(re.search(r"[,/]", nt.text))      # '1,3' 같은 묶음 태그: 사양 행 표시용, 크기는 안 잼
            # 태그별 방향: 한쪽에만 치수가 든 입면 칸이 있으면 그쪽, 아니면 시트 전체 판정
            got = cells.get(id(tg), {}) if cells else {}
            na, nb = got.get("above", (None, 0))[1], got.get("below", (None, 0))[1]
            layout = "above" if (na and not nb) else "below" if (nb and not na) else sheet_layout
            left, right = col_bounds(tg, row)
            # 같은 열의 이전/다음 태그 행으로 영역 제한
            if layout == "above":
                prev = [r[0] for r in rows[:ri] if any(col_bounds(o, r)[0] <= tt.cx <= col_bounds(o, r)[1] or
                                                        left <= o[3].cx <= right for o in r[1])]
                top = (max(prev) + h * 6) if prev else y0
                bottom = tt.cy - h * 1.2
            else:
                nxt = [r[0] for r in rows[ri + 1:] if any(left <= o[3].cx <= right for o in r[1])]
                top = tt.cy + h * 1.2
                bottom = (min(nxt) - h * 1.2) if nxt else min(y1, tt.cy + pitch)
            c = cells.get(id(tg), {}).get(layout, (None, 0))[0] if cells else None
            dleft = left
            vright = right + h * 0.5  # 세로 치수선 측정점 x 의 오른쪽 한계 (이웃 부호 경계 바로 너머까지만: 옆 칸 치수 혼입 방지)
            same = [o[3].x0 for o in row[1] if o[3].x0 > tt.x0 + h]
            hright = right + h
            if NARROW_CELL_H and c and (c[2] - c[0]) <= h * NARROW_CELL_H:
                c = None              # 태그 바로 옆의 작은 글상자(사양 칸)를 그림 칸으로 잘못 잡은 것 → 부호 간격으로 대신
            if c and (right - left) > h * 12 and (c[2] - c[0]) < 0.35 * (right - left) \
                    and not any(o is not tg and abs(o[3].cx - tt.cx) <= h * 12 and abs(o[3].cy - tt.cy) <= h * 12 for o in tags) \
                    and any(d["o"] == "v" and c[0] <= d["tx"] <= c[2] and c[1] <= d["ty"] <= c[3] for d in sheet.dims) \
                    and not any(d["o"] == "h" and c[0] <= d["tx"] <= c[2] and c[1] <= d["ty"] <= c[3] for d in sheet.dims) \
                    and any(d["o"] == "h" and c[2] < d["tx"] <= right and c[1] - h * 4 <= d["ty"] <= c[3] + h * 4 for d in sheet.dims):
                # 좁은 띠 안에는 세로 치수만 있고 가로 치수는 그 바로 오른쪽(같은 부호 간격 안)에 있을 때만
                # 칸이 부호 간격의 1/3 도 안 된다: 그림의 창틀 세로선을 칸 벽으로 잘못 본 것(R현장 2021 AW-17: 칸 벽과 창틀 사이 좁은 띠만 잡힘)
                c = None
            if c and c[3] - c[1] > h * 8:
                # 가로: 이웃 부호 간격(left,right)과 표 칸의 교집합, 세로: 표 칸
                prev = [o[3].x0 for o in row[1] if o[3].x0 < tt.x0 - h]
                if not same:          # 오른쪽에 다른 부호가 없으면 표 칸 끝까지 (넓은 커튼월 등)
                    right = c[2] - lines.tol
                left, right = max(left, c[0] + lines.tol), min(right, c[2] - lines.tol)
                dleft = (c[0] + lines.tol) if not prev else left
                vright = right + h * 0.5
                hright = right + h
                top, bottom = c[1] + lines.tol, c[3] - lines.tol
                # 행 사이 괘선이 없어 칸이 다음 부호 줄까지 이어진 양식: 칸 안(같은 열)에 다른 부호가 있으면 그 줄 앞에서 끊는다
                def _over(o):
                    if grouped or o[0] == code:
                        return False          # 묶음 태그(20/21) 칸 안의 개별 그림 태그, 같은 부호의 두 번째 태그는 경계가 아니다
                    return _over0(o)

                def _over0(o):
                    """다른 줄의 부호 o 가 이 칸의 x 범위에 걸치는지: 범위 안에 있거나, 밖에 있어도 그 줄에서는 이 칸의 옆벽(세로 괘선)이 끊겨 있으면."""
                    if left - h <= o[3].cx <= right + h:
                        return True
                    if c[0] - h <= o[3].cx <= c[2] + h:
                        return False          # 같은 칸 안의 옆 열 부호(한 칸에 부호 여럿)
                    wall = c[0] if o[3].cx < c[0] else c[2]
                    segs_ = [(a_, b_) for vx, a_, b_ in lines.v if abs(vx - wall) <= lines.tol * 2 and b_ - a_ > h * 8
                             and a_ - lines.tol <= tt.cy <= b_ + lines.tol]
                    if not segs_:
                        return False          # 옆벽이 긴 괘선이 아니면(그림 선 조각 등) 판단하지 않는다
                    return not lines.has_v(wall, o[3].cy)      # 줄마다 끊어 그린 괘선도 있으므로 그 줄에 벽이 있는지만 본다
                if layout == "below":
                    inner = [o[4].cy for o in tags if o is not tg and tt.cy + h * 12 < o[4].cy < c[3] - h and _over(o)]
                    if inner:
                        bottom = min(bottom, min(inner) - h * 1.2)
                        # 옆벽(세로 괘선)이 그보다 먼저 끝나면 거기가 칸의 끝 (다음 줄 그림의 치수가 부호 글자보다 위에 적힌 양식)
                        for wall in (c[0], c[2]):
                            ws_ = sorted((a_, b_) for vx, a_, b_ in lines.v if abs(vx - wall) <= lines.tol * 2)
                            end = None
                            for a_, b_ in ws_:             # 태그 줄에서 아래로 끊김 없이 이어지는 벽의 끝
                                if end is None:
                                    if a_ - lines.tol <= tt.cy <= b_ + lines.tol and b_ - a_ > h * 8:
                                        end = b_
                                elif a_ <= end + lines.tol * 2:
                                    end = max(end, b_)
                            if end is not None and tt.cy + h * 8 < end < bottom:
                                bottom = end
                else:
                    inner = [o[3].cy for o in tags if o is not tg and c[1] + h < o[3].cy < nt.cy - h * 12 and _over(o)]
                    if inner:
                        top = max(top, max(inner) + h * 1.2)
            region = [] if grouped else [t for t in sheet.texts if left <= t.cx <= right and top <= t.cy <= bottom]
            # 세로 치수 글자는 그림 왼쪽(칸 왼쪽 끝까지)에 적히므로 범위를 넓힌다
            region_v = [] if grouped else [t for t in sheet.texts if dleft - h <= t.cx <= right and top <= t.cy <= bottom]
            is_door = bool(re.search(r"D", typ)) or typ in ("ST", "SH", "FSH", "OHD")
            w, wc, _ = _pick(_horizontal_chains(region))
            hgt, hc, dropped = _pick(_vertical_chains(region_v), drop_bottom=not is_door)
            dnotes = []
            gsplit = None
            if grouped and getattr(sheet, "dims", None):
                # 묶음 태그('20/21'): 한 칸에 그림이 번호 수만큼 나란히 있으면 왼쪽부터 번호 순서대로 치수를 나눠 준다
                nums_g = re.split(r"\s*[,/]\s*", nt.text)
                k_idx = nums_g.index(num) if num in nums_g else -1
                hs_all = [d for d in sheet.dims if d["o"] == "h" and d["val"] >= MIN_SIZE and
                          left - h <= d["tx"] <= right + h and (top - 4 * h) <= d["ty"] <= (bottom + h)]
                clusters = []
                for d in sorted(hs_all, key=lambda d: d["span"][0]):
                    if clusters and d["span"][0] < clusters[-1][1] - h * 0.5:
                        clusters[-1][1] = max(clusters[-1][1], d["span"][1])
                    else:
                        clusters.append([d["span"][0], d["span"][1]])
                if k_idx >= 0 and len(clusters) == len(nums_g):
                    gsplit = clusters[k_idx]
            if getattr(sheet, "dims", None) and (not grouped or gsplit):
                gl, gr = (gsplit[0] - h, gsplit[1] + h) if gsplit else (None, None)

                def _mine(d):
                    """이 칸의 치수인지: 측정 구간이 칸 범위와 절반 이상 겹치고, 치수선 위치가 칸 안(약간 바깥 허용).
                    묶음 태그의 분할(gsplit)이 있으면 그 그림의 x 범위 안의 치수만."""
                    a, b = d["span"]
                    if d["o"] == "h":
                        if gsplit and not (gl <= a and b <= gr):
                            return False
                        ov = min(b, hright) - max(a, left - h)
                        return ov >= 0.5 * (b - a) and (top - 4 * h) <= d["ty"] <= (bottom + h)
                    ov = min(b, bottom + h) - max(a, top - h)
                    x = d.get("px", d["tx"])
                    if gsplit:
                        return ov >= 0.5 * (b - a) and (gl - 3 * h <= x <= gr or gl - 3 * h <= d["tx"] <= gr)
                    return ov >= 0.5 * (b - a) and dleft - 2 * h <= x <= vright     # 측정점 기준 (글자 위치는 옆 칸 경계에 걸치기도 함)
                rd = [d for d in sheet.dims if _mine(d)]
                if rd and not same:
                    # 줄의 마지막 칸: 그림이 표 칸 경계를 넘어 그려진 경우(B현장 AW-15) — 칸 안 치수와 '이어진' 가로 치수만 따라가며 더한다
                    # (옆에 다른 그림이 있어도 그 치수는 끝이 맞닿지 않으므로 섞이지 않음)
                    band = [d for d in sheet.dims if d["o"] == "h" and d not in rd and (top - 4 * h) <= d["ty"] <= (bottom + h)
                            and d["span"][0] >= left - h]
                    changed = True
                    while changed and band:
                        changed = False
                        for d in list(band):
                            a, b = d["span"]
                            for e in rd:
                                if e["o"] != "h" or abs(e["ty"] - d["ty"]) > h * 1.5:
                                    continue
                                ea, eb = e["span"]
                                tol_ = max(e["val"], d["val"]) * 0.003 + 3
                                if abs(a - eb) <= CHAIN_GAP_MM or abs(ea - b) <= CHAIN_GAP_MM or abs(a - ea) <= tol_ or abs(b - eb) <= tol_:
                                    rd.append(d); band.remove(d); changed = True
                                    break
                if rd:
                    dw, dh, dnotes = size_from_dims(rd, is_door, lines)
                    # 전개도(모서리 각도 표시 '90°' 가 있는 긴 진열창): 줄·조각으로 나뉜 총치수를 모두 더한다
                    if dw and not grouped:
                        # 이 칸 안에 그려진 치수선·각도 표시만 본다 (옆 칸·윗 칸 것이 섞이면 남의 창을 더하게 된다: R현장 AW-9)
                        dev = development_width([d for d in rd if top <= d["ty"] <= bottom and left <= d["tx"] <= hright], h)
                        corner = any(re.fullmatch(r"\s*\d{2,3}\s*°\s*", t.text) for t in sheet.texts
                                     if left <= t.cx <= hright and top <= t.cy <= bottom)
                        if dev and len(dev[1]) > 1:
                            # 작은 줄(상세 치수 줄)은 그림 조각이 아니다: 가장 긴 줄의 20% 이상이고 2m 이상인 줄만 더한다
                            big = max(sum(r) for r in dev[1])
                            keep = [r for r in dev[1] if sum(r) >= max(0.2 * big, 2000)]
                            dev = (sum(sum(r) for r in keep), keep)
                        if dev and not corner:
                            # 각도 표시가 없으면 조심해서: 지금 읽은 폭이 들어 있는 한 줄 안에서, 같은 총치수선 위에 떨어져 놓인 조각만 더한다
                            drow = next((r for r in dev[1] if len(r) >= 2 and any(abs(v - dw) <= 10 for v in r) and min(r) >= 500), None)
                            dev = (sum(drow), [drow]) if drow else None
                        if dev and dev[0] > dw + 10:
                            dnotes = [n for n in dnotes if "이어진 가로 치수 합" not in n] + [
                                ("펼쳐 그린 그림(모서리 각도 표시 있음)" if corner else "한 칸에 조각으로 나눠 그린 그림") + ": 줄·조각별 총치수 " +
                                " / ".join("+".join(str(v) for v in r) for r in dev[1]) + f" = {dev[0]} (한 조각만 읽으면 {dw}; 치수 없는 틈은 더하지 않음 → 확인)"]
                            dw = dev[0]
                    # 치수 객체가 이 칸에 있으면 그 값만 쓴다 (글자 기반 추정은 옆 칸 치수 글자를 집어올 수 있음)
                    w, hgt, dropped = dw, dh, False
                    wc = sorted({round(d["val"]) for d in rd if d["o"] == "h" and d["val"] >= MIN_SIZE})
                    hc = sorted({round(d["val"]) for d in rd if d["o"] == "v" and d["val"] >= MIN_SIZE})
            # 수량·사양: 태그 줄 근처 글자 (번호 글자 위로 2.5줄 ~ 종류 글자 아래로 6줄: '위치 및 수량' 행이 번호 위에 있는 양식도 포함)
            lo_y, hi_y = min(tt.cy, nt.cy) - h * 2.5, tt.cy + h * 6
            if layout == "below" and bottom > tt.cy:
                # 부호 아래에 그림, 그 아래에 명칭/개소·문틀·유리·철물 행이 오는 양식: 그림 칸 아래 ~ 다음 부호 줄 직전까지 본다
                hi_y = max(hi_y, min(bottom + h * 10, tt.cy + pitch - h * 1.5))
            band = [t for t in sheet.texts if t.angle == 0 and left <= t.cx <= right + h * 2 and
                    lo_y <= t.cy <= hi_y and t is not tt and t is not nt]
            band.sort(key=lambda t: (round(t.cy / h), t.cx))
            qty = None
            formulas = [m for t in band for m in [QTY_FORMULA_RE.search(t.text)] if m and (m.group(2) or m.group(4) or m.group(5))]
            if formulas:
                # 층당 개수 × 층수 줄이 여럿이면(층마다 다른 수) 모두 더한다. '='가 있으면 그 합계를 믿는다 (크기 글자 '1,500 X 2,200'은 단위가 없어 제외)
                qty = float(sum(int(m.group(5)) if m.group(5) else int(m.group(1)) * int(m.group(3)) for m in formulas))
            else:
                for t in band:
                    if HARDWARE_RE.search(t.text):
                        continue              # '플로어 힌지 2조, 손잡이 일식 2개소' 같은 철물 개수는 창호 수량이 아니다
                    m = QTY_RE.search(t.text)
                    if m:
                        qty = float(m.group(1) or m.group(3))
                        break
            spec = " ".join(t.text for t in band if not QTY_RE.fullmatch(t.text.strip()))
            # 글로 적힌 규격('1900X2100 재료동일' : 다른 부호의 그림을 같이 쓰며 폭만 다름)이 있으면 그림 치수보다 그 글을 믿는다
            written = None
            for t in band:
                if not (left <= t.cx <= right):            # 자기 칸 안의 글만 (옆 칸 규격 글을 끌어오지 않도록)
                    continue
                m = re.search(r"(?<![\d.])(\d{1,2},?\d{3})\s*[xX×＊*]\s*(\d{1,2},?\d{3})(?![\d])", t.text)
                if m and not QTY_FORMULA_RE.search(t.text):
                    # 이 글에 가장 가까운 태그가 다른 부호면(한 칸 안에 작은 태그+규격 목록을 끼워 넣은 양식) 남의 규격이다
                    def _d(tg_):
                        return min(((tg_[3].cx - t.cx) ** 2 + (tg_[3].cy - t.cy) ** 2) ** 0.5,
                                   ((tg_[4].cx - t.cx) ** 2 + (tg_[4].cy - t.cy) ** 2) ** 0.5)
                    nearest = min(tags, key=_d)
                    if nearest[0] != code:
                        continue
                    ww, hh = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
                    if 300 <= ww <= 15000 and 300 <= hh <= 8000:
                        written = (ww, hh, t.text.strip())
                        break
            # '개구부' 행처럼 규격만 따로 적은 글('2,020 x 2,110')과 그림 칸 구석의 '* 22개소': 그림 칸 위끝 ~ 태그 줄 사이, 자기 열 안에서 찾는다.
            # 글자 전체가 규격/개소뿐인 것만(주석 문장 제외), 가장 가까운 태그가 이 부호일 때만.
            if STATED_ROW:
                ylo, yhi = min(top, lo_y - h * 2), max(bottom, hi_y)
                own = [t for t in sheet.texts if t.angle == 0 and left - h <= t.cx <= right and ylo <= t.cy <= yhi]
                def _mine(t):
                    def _d(tg_):
                        return min(((tg_[3].cx - t.cx) ** 2 + (tg_[3].cy - t.cy) ** 2) ** 0.5,
                                   ((tg_[4].cx - t.cx) ** 2 + (tg_[4].cy - t.cy) ** 2) ** 0.5)
                    return min(tags, key=_d)[0] == code
                SZ = r"\s*(\d{1,2},?\d{3}|\d{3})\s*[xX×]\s*(\d{1,2},?\d{3}|\d{3})\s*"
                stated_t = None
                if not written:
                    st = [(abs(t.cy - nt.cy), t, m) for t in own for m in [re.fullmatch(SZ, t.text)] if m and _mine(t)]
                    if st:
                        _, t, m = min(st, key=lambda x: x[0])
                        ww, hh = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
                        if 300 <= ww <= 60000 and 300 <= hh <= 40000:
                            written = (ww, hh, t.text.strip())
                            stated_t = t
                if qty is None:
                    QT = r"\s*[*※]?\s*(\d{1,3})\s*(개소|EA|ea|개)\s*"
                    if stated_t is not None:
                        # 규격 글 오른쪽 ~ 같은 줄의 다음 규격 글 사이(칸이 옆 열까지 넓은 경우 포함), 규격 글 바로 위아래 3줄 안
                        nxt = min([o.cx for o in sheet.texts if o is not stated_t and abs(o.cy - stated_t.cy) <= h and o.cx > stated_t.cx + h
                                   and re.fullmatch(SZ, o.text)], default=float("inf"))
                        sq_ = [(abs(t.cx - tt.cx) + abs(t.cy - tt.cy), t, m) for t in sheet.texts if t.angle == 0
                               and stated_t.cx - h * 2 < t.cx < nxt - h and abs(t.cy - stated_t.cy) <= h * 4
                               for m in [re.fullmatch(QT, t.text)] if m]
                    else:
                        sq_ = [(abs(t.cx - tt.cx) + abs(t.cy - tt.cy), t, m) for t in own for m in [re.fullmatch(QT, t.text)] if m and t.cx <= right]
                    if sq_:
                        # 한 칸에 그림이 둘이면 개소 글도 둘: 태그에 가까운 쪽
                        qty = float(min(sq_, key=lambda x: x[0])[2].group(1))
            size_note = None
            if written and (w, hgt) != (written[0], written[1]):
                size_note = f"글로 적힌 규격 {written[0]}×{written[1]} 사용 (그림 치수 {w or '-'}×{hgt or '-'}; '{written[2][:30]}')"
                if w and hgt and (abs(w - written[0]) > 0.05 * written[0] or abs(hgt - written[1]) > 0.05 * written[1]):
                    size_note += " → 글과 그림이 5% 넘게 다름, 확인 필요"
                w, hgt = written[0], written[1]
            it = ScheduleItem(code=code, type=typ, number=num, name="", location="",
                              width=w, height=hgt, qty=qty, frame="", glass="", hardware="",
                              sheet=f"{sheet.title} (도곽 {sheet.index})",
                              width_candidates=wc, height_candidates=hc)
            if size_note:
                it.notes.append(size_note)
            if tt is nt:
                SINGLE_TYPES.add(typ)
            # 셔터: 개구부 높이 위에 셔터 박스 치수(500 안팎)가 이어져 있으면 박스는 창호 크기에 넣지 않는다
            if hgt and hc and any("이어진 세로 치수 합" in n for n in dnotes):
                main_h = max(hc)
                box = hgt - main_h
                if 0 < box <= 800 and any(abs(v - box) <= 2 for v in hc) and any(
                        re.search(r"S[H]?UTTER\s*BOX|셔터\s*박스|샤[타터]\s*박스|셔터\s*BOX", t.text, re.I) for t in sheet.texts
                        if left - h * 4 <= t.cx <= right + h and top - h <= t.cy <= bottom + h):
                    dnotes = [n for n in dnotes if "이어진 세로 치수 합" not in n] + [f"셔터 박스 {box:g} 는 높이에서 뺌 (개구부 높이 {main_h}, 박스 포함 {hgt})"]
                    it.notes = [n for n in it.notes if "이어진 세로 치수 합" not in n]
                    hgt = it.height = main_h
            # 그림 위에 크게 'VOID'(삭제) 라고 덮어쓴 칸
            if any(re.fullmatch(r"\s*(VOID|삭\s*제|DELETE[D]?)\s*", t.text, re.I) and (t.y1 - t.y0) >= h * 1.5
                   and left - h <= t.cx <= right + h and top - h <= t.cy <= bottom + h for t in sheet.texts if t.angle == 0):
                it.void = True
            it.spec_text = spec[:300]
            it._band = band                       # 개소 행의 맨숫자 보정용 (아래)
            it.layout = layout
            it.region = (left, top, right, bottom)
            it.tag_box = (min(tt.x0, nt.x0), nt.y0, max(tt.x1, nt.x1), tt.y1)
            it.region_sheet = sheet
            if dropped:
                it.notes.append("맨 아래 치수를 창대 높이로 보고 제외")
            it.notes += dnotes
            if w and not hgt and not grouped:
                # 세로 치수가 없는 칸: 그림(창틀 선)의 가로세로 비례로 높이 추정 (일람표 그림은 축척대로 그려짐)
                # 칸 경계를 조금 넘는 창틀 선(아래 끝이 칸 선 밖으로 1줄쯤 나간 것)도 포함: x 는 칸 안, y 는 칸과 절반 이상 겹치면
                segs = [sg for sg in sheet.segments if left <= min(sg[0], sg[2]) and max(sg[0], sg[2]) <= right
                        and min(bottom, max(sg[1], sg[3])) - max(top, min(sg[1], sg[3])) >= 0.5 * max(abs(sg[3] - sg[1]), 1e-9)
                        and min(sg[1], sg[3]) >= top - h * 2 and max(sg[1], sg[3]) <= bottom + h * 2]
                if len(segs) >= 4 and lines:
                    # 창틀 양쪽 세로선: 위아래 끝이 같은 세로선들 중 가장 멀리 떨어진 한 쌍 → 그 간격이 창틀 폭, 길이가 창틀 높이
                    # (표 괘선·FL선처럼 창틀보다 긴 가로선에 끌려가지 않는다)
                    vs_ = [(round(min(sg[1], sg[3]) / lines.tol), round(max(sg[1], sg[3]) / lines.tol), sg[0], abs(sg[3] - sg[1]))
                           for sg in segs if abs(sg[2] - sg[0]) <= lines.tol and abs(sg[3] - sg[1]) > h * 2]
                    groups = {}
                    for ya, yb, x, ln in vs_:
                        groups.setdefault((ya, yb), []).append((x, ln))
                    bw = bh = 0
                    for lst in groups.values():
                        if len(lst) >= 2:
                            spread = max(x for x, _ in lst) - min(x for x, _ in lst)
                            if spread > bw:
                                bw, bh = spread, max(ln for _, ln in lst)
                    if bw > 0 and bh > 0 and 0.05 <= bh / bw <= 20:
                        hgt = int(round(w * bh / bw / 10.0)) * 10
                        it.height = hgt
                        it.notes.append(f"세로 치수 없음: 그림 비례로 높이 {hgt} 추정 (확인 필요)")
            # 층 레벨 표기(2층/SL, 옥탑/SL …)가 칸 안에 3개 이상이면 여러 층에 걸친 창. 표기가 가로로 늘어서 있으면 눕혀 그린 그림 → W/H 교환
            lv = [t for t in sheet.texts if left - h <= t.cx <= right + h and top - h <= t.cy <= bottom + 3 * h and LEVEL_RE.search(t.text)]
            if len(lv) >= 3:
                it.levels = len(lv)
                xs_, ys_ = [t.cx for t in lv], [t.cy for t in lv]
                if (max(xs_) - min(xs_)) > (max(ys_) - min(ys_)) and it.width and it.height and it.width > it.height:
                    it.width, it.height = it.height, it.width
                    it.width_candidates, it.height_candidates = it.height_candidates, it.width_candidates
                    it.notes.append(f"층 레벨 표기 {len(lv)}개가 가로로 늘어서 있어 90° 눕혀 그린 그림으로 판단 → 폭/높이 교환")
            if grouped and gsplit:
                it.notes.append(f"묶음 태그({nt.text}): 칸 안 그림을 왼쪽부터 번호 순서로 나눠 치수 산정")
            elif grouped:
                it.notes.append(f"묶음 태그({nt.text})의 사양 행 - 크기는 개별 그림 태그에서")
            elif not w or not hgt:
                it.notes.append("치수 자동인식 실패")
            items.append(it)
    # 개소 행에 단위 없이 숫자만 적힌 것('9'): 다른 부호의 '3EA X 4F = 12EA' 같은 수량 글자와 같은 행(y)에 있는 맨숫자를 수량으로
    qty_rows = {round(t.cy / h) for it in items for t in getattr(it, "_band", []) if QTY_FORMULA_RE.search(t.text) and
                (lambda m: m.group(2) or m.group(4) or m.group(5))(QTY_FORMULA_RE.search(t.text))}
    for it in items:
        band = getattr(it, "_band", [])
        if it.qty is None and qty_rows:
            bare = [t for t in band if round(t.cy / h) in qty_rows and re.fullmatch(r"\d{1,3}", t.text.strip())]
            if len(bare) == 1:
                it.qty = float(bare[0].text)
                it.notes.append(f"개소 행의 숫자 '{bare[0].text}' 를 수량으로 읽음 (단위 표기 없음)")
        if hasattr(it, "_band"):
            del it._band
    # 크기 상식 범위: 그림 비례로 추정한 높이가 6m를 넘으면 버린다(칸을 잘못 잡은 것). 끝자리가 5mm 단위가 아니면 치수 합이 어긋난 것일 수 있다.
    for it in items:
        if it.height and it.height > 6000 and any("비례로 높이" in n for n in it.notes):
            it.notes = [n for n in it.notes if "비례로 높이" not in n] + [f"그림 비례로 추정한 높이 {it.height}는 상식 범위를 넘어 버림 → 크기 확인"]
            it.height = None
        for nm, v in (("폭", it.width), ("높이", it.height)):
            if v and v % 5 and not any("글로 적힌 규격" in n for n in it.notes):
                it.notes.append(f"{nm} {v}: 끝자리가 5mm 단위가 아님 → 치수 확인")
    voids = [it.code for it in items if getattr(it, "void", False)]
    if voids:
        sheet.void_codes = sorted(set(getattr(sheet, "void_codes", [])) | set(voids))
        items = [it for it in items if not getattr(it, "void", False)]
    items += _table_rows(sheet, {it.code for it in items if it.width and it.height}, h)
    # 같은 부호 태그가 한 장에 두 번(그림 위 + '위치및 개소' 행) 있는 양식: 크기 있는 쪽을 남기고 수량은 합친다
    # (둘 다 크기가 있고 서로 다르면 둘 다 남겨 pipeline 의 중복 진단에 맡긴다)
    by = {}
    merged = []
    for it in items:
        prev = by.get(it.code)
        if prev is None:
            by[it.code] = it
            merged.append(it)
            continue
        a_full, b_full = bool(prev.width and prev.height), bool(it.width and it.height)
        if a_full and b_full and (prev.width, prev.height) != (it.width, it.height):
            merged.append(it)                 # 진짜 다른 크기 → 그대로 둠
            continue
        keep, drop = (prev, it) if (a_full or not b_full) else (it, prev)
        if keep.qty is None and drop.qty is not None:
            keep.qty = drop.qty
        if not keep.spec_text and getattr(drop, "spec_text", ""):
            keep.spec_text = drop.spec_text
        if keep is it:
            merged[merged.index(prev)] = it
            by[it.code] = it
    items = merged
    # 한 그림에 태그가 둘 붙은 양식('FSD 1' 과 'FSD 1-1' 이 나란히, 그림은 하나): 크기를 못 읽은 쪽이 바로 옆 태그의 크기를 같이 쓴다
    for it in items:
        if it.width and it.height:
            continue
        tb = getattr(it, "tag_box", None)
        if not tb:
            continue
        cx, cy = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
        near = [o for o in items if o is not it and o.width and o.height and getattr(o, "tag_box", None)
                and abs((o.tag_box[1] + o.tag_box[3]) / 2 - cy) < h * 1.5
                and 0 < abs((o.tag_box[0] + o.tag_box[2]) / 2 - cx) < h * 7]
        if len(near) == 1:
            o = near[0]
            # 옆 태그의 높이가 '그림 비례 추정'이고 이쪽에는 세로 치수가 실제로 있으면(치수선이 이쪽 태그 밑에 그려짐) 그 치수가 두 부호의 높이다
            own_h = it.height if (it.height and it.height_candidates and not it.width) else None
            if own_h and any("비례로 높이" in n for n in o.notes):
                o.notes = [n for n in o.notes if "비례로 높이" not in n] + [f"세로 치수는 옆 태그 {it.code} 밑에 그려진 {own_h} 를 씀 (그림은 하나)"]
                o.height, o.height_candidates = own_h, list(it.height_candidates)
            it.width, it.height = o.width, o.height
            it.width_candidates, it.height_candidates = list(o.width_candidates), list(o.height_candidates)
            it.notes = [n for n in it.notes if n != "치수 자동인식 실패"]
            it.notes.append(f"옆 태그 {o.code} 와 그림을 같이 씀 → 같은 크기로 봄 (확인 필요)")
    by = {}
    for it in items:
        by.setdefault(it.code, it)

    def _line_qty(t, reach):
        c = [(u.cx, m) for u in sheet.texts if u.angle == 0 and u is not t and abs(u.cy - t.cy) <= h * 0.8 and 0 < u.cx - t.cx <= reach
             and not HARDWARE_RE.search(u.text) for m in [QTY_RE.search(u.text)] if m]
        return float(min(c)[1].group(1) or min(c)[1].group(3)) if c else None

    for a, b in stacked:
        main = by.get(a[0])
        if main is None or b[0] in by:
            continue
        a_txt = a[4] if a[3] is b[3] else a[3]
        q = _line_qty(a_txt, col_w)
        if q is not None:
            main.qty = q
        it = ScheduleItem(code=b[0], type=b[1], number=b[2], name="", location="", width=main.width, height=main.height,
                          qty=_line_qty(b[3], col_w), frame="", glass="", hardware="", sheet=main.sheet,
                          width_candidates=list(main.width_candidates), height_candidates=list(main.height_candidates))
        it.spec_text = getattr(main, "spec_text", "")
        it.layout, it.region, it.region_sheet = main.layout, main.region, sheet
        it.tag_box = (b[3].x0, b[3].y0, b[3].x1, b[3].y1)
        it.notes.append(f"{main.code} 와 그림을 같이 씀 → 같은 크기로 봄 (확인 필요)")
        items.append(it)
        by[it.code] = it
    # 변형 부호 단서: 그림 옆 '(07a : 600)'(가로 글 = 폭, 세로 글 = 높이), 작은 표 'AW03a : 500' + 같은 줄 '5개소'
    def _near(cands, t):
        return min(cands, key=lambda it: abs((it.tag_box[0] + it.tag_box[2]) / 2 - t.cx) + abs((it.tag_box[1] + it.tag_box[3]) / 2 - t.cy))
    tabled = []
    for t in sheet.texts:
        m = re.fullmatch(r"\s*\(\s*(\d{1,3}[A-Za-z]?)\s*:\s*(\d{3,5})\s*\)\s*", t.text)
        if m:
            cands = [it for it in items if getattr(it, "tag_box", None) and normalize_code(it.type, m.group(1)) == it.code]
            if cands:
                it, v = _near(cands, t), int(m.group(2))
                if t.angle == 0:
                    it.width, it.width_candidates = v, [v]
                else:
                    it.height, it.height_candidates = v, [v]
                it.notes = [n for n in it.notes if "같은 크기로 봄" not in n and n != "치수 자동인식 실패"] + [f"단서 '{t.text.strip()}' 로 {'폭' if t.angle == 0 else '높이'} {v}"]
            continue
        m = re.fullmatch(r"\s*([A-Z]{1,5})\s*-?\s*(\d{1,3}[A-Za-z]?)\s*:\s*(\d{3,5})\s*", t.text) if t.angle == 0 else None
        if m:
            tabled.append((normalize_code(m.group(1), m.group(2)), int(m.group(3)), t, m.group(1)))
    # 작은 표의 숫자가 폭인지 높이인지: 표에 적힌 값이 그림 치수와 같은 부호(기준 부호)를 보고 정한다
    if tabled:
        nw = sum(1 for c, v, _, _ in tabled if c in by and by[c].width == v)
        nh = sum(1 for c, v, _, _ in tabled if c in by and by[c].height == v)
        dim = "height" if nh > nw else "width"
        rows_ = []
        for c, v, t, typ in tabled:
            if c not in by:
                # 표에만 있는 변형 부호(AW21a … AW21r): 접미 글자를 뗀 기준 부호의 그림을 같이 쓰고 표의 값만 바꾼다
                base = by.get(re.sub(r"[A-Z]'?$", "", c))
                if base is None or not re.search(r"\d[A-Z]'?$", c):
                    continue
                it = ScheduleItem(code=c, type=typ, number=c.split("-", 1)[1], name="", location="", width=base.width, height=base.height,
                                  qty=None, frame="", glass="", hardware="", sheet=base.sheet,
                                  width_candidates=list(base.width_candidates), height_candidates=list(base.height_candidates))
                it.spec_text = getattr(base, "spec_text", "")
                it.layout, it.region, it.region_sheet = base.layout, base.region, sheet
                it.tag_box = (t.x0, t.y0, t.x1, t.y1)
                it.notes.append(f"{base.code} 의 변형(옆 표에만 있음): 그림은 {base.code} 것")
                items.append(it)
                by[c] = it
            rows_.append((by[c], v, t))
        for it, v, t in rows_:
            if getattr(it, dim) != v:
                setattr(it, dim, v)
                setattr(it, dim + "_candidates", [v])
                it.notes = [n for n in it.notes if "같은 크기로 봄" not in n and n != "치수 자동인식 실패"] + [f"옆 표 '{t.text.strip()}' 로 {'폭' if dim == 'width' else '높이'} {v}"]
            q = _line_qty(t, h * 14)
            if q is not None and q != it.qty:
                it.notes.append(f"옆 표의 개소 {q:g} 사용" + (f" (부호 줄에는 {it.qty:g})" if it.qty is not None else ""))
                it.qty = q
    return items
