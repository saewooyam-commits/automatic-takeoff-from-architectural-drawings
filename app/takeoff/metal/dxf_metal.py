"""DWG/DXF 도면에서 나오는 금속 물량 (1차: 계단 핸드레일).

계단 단면도의 디딤판·챌판 지그재그 선을 찾아 판(flight)마다 높이·경사길이를 재고,
계단실별 총높이 × 경사계수 + 계단참 꺾임 으로 핸드레일 길이를 낸다. 글자(단높이 표기)에 기대지 않으므로
단면에 계단만 그려져 있으면 어느 사무소 도면이든 같은 식으로 읽힌다.

- 도곽 고르기: 제목에 '계단'과 '단면'이 있는 도곽 우선, 없으면 일반 단면도 중 계단 높이가 가장 큰 것(계단을 온전히 자른 단면)
- 한 도곽 안의 계단실 구분: 소제목(…계단실-1 단면…)이 여럿이면 가장 가까운 소제목으로 묶고, 소제목 하나에 절단 위치가 여럿이면 가장 높은 것 하나만
  (같은 계단을 두 방향으로 자른 그림을 두 번 세지 않도록)
- 길이: 계단 윗선(디딤판 코 선)을 아래층 바닥에서 위층 바닥까지 이어 잰 길이 = Σ판 경사길이 + Σ계단참 깊이(계단 옆 가로 치수 사슬에서 읽음).
  rules['계단난간']: 측수(1 편측/2 양측), 꺾임길이_m(계단참 치수를 못 읽었을 때 계단참 1개소당 길이)
"""
import math
from collections import Counter
import re
import statistics
from collections import defaultdict

STAIR_TITLE = re.compile(r"계단")
SECTION_TITLE = re.compile(r"단면")
NOT_SECTION = re.compile(r"대지|배관|오.?우수|단열|상세도-?\d*$")


def stair_chains(segs, riser=(130, 230), tread=(220, 340), tol=3.0):
    """챌판(세로 선) 사이의 계단꼴 이어짐으로 판을 찾는다: 챌판 i 의 위 끝에서 디딤판 폭만큼 옆으로 간 자리에
    다음 챌판 j 의 아래 끝이 있으면 i→j. 디딤판 선이 다른 선에 잘려 있어도 되고, 앞판·뒷판이 겹쳐도 된다.
    반환: [{nV, riser, tread, x:(x0,x1), y:(y0,y1)}]"""
    V = []
    for x0, y0, x1, y1 in segs:
        if abs(x0 - x1) <= tol and riser[0] <= abs(y1 - y0) <= riser[1]:
            V.append((x0, min(y0, y1), max(y0, y1)))
    bybot = defaultdict(list)
    for i, v in enumerate(V):
        bybot[round(v[1] / tol)].append(i)
    adj = defaultdict(list)
    for i, (x, y0, y1) in enumerate(V):
        kk = round(y1 / tol)
        for k in (kk - 1, kk, kk + 1):
            for j in bybot.get(k, []):
                if tread[0] <= abs(V[j][0] - x) <= tread[1] and abs(V[j][1] - y1) <= tol:
                    adj[i].append(j)
                    adj[j].append(i)
    seen, chains = set(), []
    for i in range(len(V)):
        if i in seen:
            continue
        stack, comp = [i], []
        while stack:
            j = stack.pop()
            if j in seen:
                continue
            seen.add(j)
            comp.append(j)
            stack.extend(adj[j])
        if len(comp) >= 3:
            xs = [V[j][0] for j in comp]
            ys = [V[j][1] for j in comp] + [V[j][2] for j in comp]
            rh = statistics.median(V[j][2] - V[j][1] for j in comp)
            xsorted = sorted(set(round(x) for x in xs))
            steps = [b - a for a, b in zip(xsorted, xsorted[1:]) if tread[0] <= b - a <= tread[1]]
            tw = statistics.median(steps) if steps else (tread[0] + tread[1]) / 2
            # 오르는 방향: 챌판 x 가 y 와 함께 커지면 오른쪽으로 오르는 판(+1), 작아지면 왼쪽(-1)
            pts = sorted((V[j][1], V[j][0]) for j in comp)
            d = 1 if pts[-1][1] >= pts[0][1] else -1
            chains.append(dict(nV=len(comp), riser=rh, tread=tw, x=(min(xs), max(xs)), y=(min(ys), max(ys)), dir=d))
    return chains


def _columns(chains, gap=1500):
    """x 범위가 겹치는(또는 gap 안의) 사슬끼리 = 절단 위치 하나의 계단"""
    cols = []
    for c in sorted(chains, key=lambda c: c["x"][0]):
        for col in cols:
            if c["x"][0] <= col["x"][1] + gap and c["x"][1] >= col["x"][0] - gap:
                col["chains"].append(c)
                col["x"] = (min(col["x"][0], c["x"][0]), max(col["x"][1], c["x"][1]))
                break
        else:
            cols.append({"x": c["x"], "chains": [c]})
    return cols


def _bands(chains):
    """같은 높이 띠의 사슬(마감선/구조선, 앞뒤 판)은 판 하나로"""
    out = []
    for c in sorted(chains, key=lambda c: c["y"][0]):
        for b in out:
            ov = min(b["y"][1], c["y"][1]) - max(b["y"][0], c["y"][0])
            if ov > 0.6 * min(b["y"][1] - b["y"][0], c["y"][1] - c["y"][0]):
                b["chains"].append(c)
                b["y"] = (min(b["y"][0], c["y"][0]), max(b["y"][1], c["y"][1]))
                break
        else:
            out.append({"y": c["y"], "chains": [c]})
    return out


def _floor_lines(frame, x0, x1, min_len=4000):
    """계단 x 범위(양옆 3m 여유)에 걸치는 긴 가로선의 y 값들 = 바닥(슬래브) 선 후보"""
    ys = set()
    for a, b, c, d in frame.segments:
        if abs(b - d) <= 2 and abs(c - a) >= min_len and min(a, c) < x1 + 3000 and max(a, c) > x0 - 3000:
            ys.add(round(b))
    return sorted(ys)


def _landing_depths(frame, x0, x1, ymin, ymax):
    """계단 옆의 가로 치수 사슬에서 계단참 깊이를 읽는다.
    판(디딤판 전체 길이)에 해당하는 치수 = 계단 x 범위와 절반 이상 겹치는 것, 그 양옆에 맞닿은 치수 = 왼쪽/오른쪽 계단참 깊이.
    반환: [(ty, 왼쪽깊이 or None, 오른쪽깊이 or None)] (치수 사슬이 있는 높이마다)"""
    hd = [d for d in frame.dims if d.get("o") == "h" and d.get("span") and d.get("val")
          and x0 - 4000 <= d["tx"] <= x1 + 4000 and ymin - 4000 <= d["ty"] <= ymax + 4000]
    out = []
    for d in hd:
        a, b = d["span"]
        ov = min(b, x1) - max(a, x0)
        if ov < 0.5 * (b - a) or (b - a) < 1000:
            continue
        same = [e for e in hd if abs(e["ty"] - d["ty"]) <= 300 and e is not d]      # 가운데 치수 글자가 조금 비켜 적히기도 함
        left = [e for e in same if abs(e["span"][1] - a) <= 40]
        right = [e for e in same if abs(e["span"][0] - b) <= 40]
        L = left[0]["val"] if left and 500 <= left[0]["val"] <= 3500 else None
        Rr = right[0]["val"] if right and 500 <= right[0]["val"] <= 3500 else None
        if L or Rr:
            out.append((d["ty"], L, Rr))
    return out


def analyze_stairs(frame):
    """도곽 하나 → 절단 위치별 [{x, flights, H(m), riser, tread, risers, slope(m), k, ymin, ymax, coverage, ...}]
    계단 높이 H: 가장 아래 디딤판 바로 아래 바닥선 ~ 가장 위 디딤판 바로 위 바닥선 (긴 가로선 = 슬래브 선).
    바닥선을 못 찾으면 디딤판 범위(span). 뒷판이 난간만 그려져 챌판이 없는 도면은 Σ판높이 < span 이 되는데(coverage),
    그런 경우에도 바닥선 기준 H 는 맞는다."""
    res = []
    for col in _columns(stair_chains(frame.segments)):
        bs = _bands(col["chains"])
        if len(bs) < 2:
            continue
        ymin, ymax = min(b["y"][0] for b in bs), max(b["y"][1] for b in bs)
        span = (ymax - ymin) / 1000
        sumH = sum(b["y"][1] - b["y"][0] for b in bs) / 1000
        if span < 1.5:
            continue
        cov = sumH / span
        lines = _floor_lines(frame, col["x"][0], col["x"][1])
        below = [y for y in lines if ymin - 1500 <= y <= ymin + 150]
        above = [y for y in lines if ymax - 150 <= y <= ymax + 1500]
        lo = min(below, key=lambda y: abs(y - ymin)) if below else None
        hi = min(above, key=lambda y: abs(y - ymax)) if above else None
        floor_based = lo is not None and hi is not None and (hi - lo) / 1000 >= span * 0.95
        H = (hi - lo) / 1000 if floor_based else span
        r = statistics.median(c["riser"] for c in col["chains"])
        t = statistics.median(c["tread"] for c in col["chains"])
        med_band = statistics.median(b["y"][1] - b["y"][0] for b in bs)
        # 판 수: 챌판이 다 읽혔으면 읽힌 띠 수. 뒷판이 안 그려졌거나 챌판 일부가 빠진 도면은 높이 ÷ 읽힌 판 높이(중앙값)로 추정하되
        # 읽힌 판 수 이상, 그 두 배 이하로 둔다 (추정이므로 근거에 '확인' 표시)
        if cov >= 0.9 and H <= span * 1.05:
            flights = len(bs)
        else:
            flights = min(max(len(bs), round(H * 1000 / med_band)), 2 * len(bs))
        n = max(2, round(H * 1000 / r / flights))          # 판 하나의 챌판 수
        slope_one = math.hypot(t * (n - 1), r * n) / 1000
        slope = slope_one * flights
        boxes = [(min(c["x"][0] for c in b["chains"]), b["y"][0], max(c["x"][1] for c in b["chains"]), b["y"][1]) for b in bs]
        # 계단참: 판마다 끝나는 쪽(오른쪽으로 오르면 오른쪽) 계단참 깊이를 가장 가까운 치수 사슬에서 읽는다
        chains_d = _landing_depths(frame, col["x"][0], col["x"][1], ymin, ymax)
        landings, measured = [], 0
        for b in bs:
            d = 1 if sum(c.get("dir", 1) for c in b["chains"]) >= 0 else -1
            ytop = b["y"][1]
            if chains_d:
                ty, L, Rr = min(chains_d, key=lambda c: abs(c[0] - ytop))
                v = (Rr if d > 0 else L) or (L if d > 0 else Rr)
                if v:
                    landings.append(v / 1000)
                    measured += 1
                    continue
            landings.append(None)
        if measured and len(landings) < flights:          # 읽지 못한 판(뒷판)은 읽힌 값의 중앙값으로
            landings += [None] * (flights - len(landings))
        fill = statistics.median([v for v in landings if v]) if measured else None
        land_list = [v if v else fill for v in landings[:flights]] if measured else []
        res.append(dict(x=col["x"], flights=flights, H=H, span=span, floor_based=floor_based, riser=r, tread=t,
                        risers=n * flights, slope=slope, k=slope / H, ymin=lo if floor_based else ymin,
                        ymax=hi if floor_based else ymax, coverage=cov, read_flights=len(bs), bands=boxes,
                        landings=land_list, landing_measured=measured))
    return res


def _subtitles(frame):
    """도곽 안 계단 단면 소제목(가장 큰 글자 크기의 '계단…단면' 글자). 없으면 []"""
    subs = [t for t in frame.texts if STAIR_TITLE.search(t.text) and SECTION_TITLE.search(t.text) and t.angle == 0]
    if not subs:
        return []
    big = max(t.size for t in subs)
    subs = [t for t in subs if t.size >= big * 0.8]
    # 같은 글이 표제란에도 한 번 더 있으면(도면명) 그림 쪽 것만: 글자 위치가 다른 것끼리 묶어 x 순
    out, seen = [], set()
    for t in sorted(subs, key=lambda t: t.cx):
        k = t.text.replace(" ", "")
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


EL_RE = re.compile(r"EL\.?\s*([+\-±]?)\s*(\d[\d,]*(?:\.\d+)?)")
STAIR_NAME_RE = re.compile(r"계단실?\s*[-#]?\s*(\d{1,2}|[A-Z])(?![\d층FfMm])")
LAST_WARNINGS = []


def _levels(frame):
    """도곽 안의 레벨 표기 [(x, y, EL m)]"""
    out = []
    for t in frame.texts:
        m = EL_RE.search(t.text)
        if not m or "TEL" in t.text.upper():
            continue
        try:
            v = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if v >= 1000:
            v /= 1000.0                       # mm 로 적은 도면
        if m.group(1) == "-":
            v = -v
        if -60 <= v <= 400:
            out.append((t.cx, t.cy, v))
    return out


FLOOR_MARK_RES = [(re.compile(r"B\s*(\d{1,2})\s*(?:ST|ND|RD|TH|F)?\s*\.?\s*F?FL", re.I), -1),
                  (re.compile(r"지하\s*(\d{1,2})\s*층.*FL", re.I), -1),
                  (re.compile(r"(?<![B\d])(\d{1,2})\s*(?:ST|ND|RD|TH|F)\s*\.?\s*F?FL", re.I), 1),
                  (re.compile(r"지상\s*(\d{1,2})\s*층.*FL", re.I), 1)]
REPEAT_RE = re.compile(r"(\d{1,2},?\d{3})\s*[xX×*]\s*(\d{1,2})\s*개\s*층")


def _floor_marks(frame):
    """층 이름 표기 [(x, y, 층 번호)] — 지하는 음수 ('B5TH FFL' → -5, '지상10층 FL.' → 10)"""
    out = []
    for t in frame.texts:
        for rx, sign in FLOOR_MARK_RES:
            m = rx.search(t.text)
            if m:
                out.append((t.cx, t.cy, sign * int(m.group(1))))
                break
    return out


def _is_continuation(levels):
    """같은 레벨 값이 한 장 안에서 서로 다른 높이(2m 넘게)에 적혀 있으면: 한 계단을 아래·가운데·위로 나눠 옆으로 늘어놓은 장"""
    by = defaultdict(list)
    for x, y, v in levels:
        by[round(v, 1)].append(y)
    return any(max(ys) - min(ys) > 2000 for ys in by.values())


def _el_ranges(res, levels):
    """절단 위치마다 그 옆에 적힌 레벨 범위 (lo, hi) 또는 None. 레벨 표기가 그림 왼쪽에 있는지 오른쪽에 있는지는
    모든 절단 위치가 레벨을 가장 많이 갖게 되는 쪽으로 정한다."""
    cols = sorted(res, key=lambda r: r["x"][0])
    best = None
    for side in ("left", "right"):
        out = []
        for i, r in enumerate(cols):
            if side == "left":
                a = cols[i - 1]["x"][1] if i else r["x"][0] - 25000
                b = r["x"][0]
            else:
                a = r["x"][1]
                b = cols[i + 1]["x"][0] if i + 1 < len(cols) else r["x"][1] + 25000
            ys = sorted((r["ymin"], r["ymax"]))
            vs = [v for x, y, v in levels if a <= x <= b and ys[0] - 1500 <= y <= ys[1] + 1500]
            out.append((min(vs), max(vs)) if len(vs) >= 2 and max(vs) - min(vs) >= 1 else None)
        score = sum(1 for o in out if o)
        if best is None or score > best[0]:
            best = (score, out)
    return {id(r): rg for r, rg in zip(cols, best[1])}


def _sum_parts(parts):
    """이어지는 부분들을 한 계단으로 합친다"""
    parts = sorted(parts, key=lambda r: r["x"][0])
    H = sum(r["H"] for r in parts)
    slope = sum(r["slope"] for r in parts)
    flights = sum(r["flights"] for r in parts)
    read = sum(r["read_flights"] for r in parts)
    lands = [v for r in parts for v in (r["landings"] or [])] if all(r["landings"] for r in parts) else []
    big = max(parts, key=lambda r: r["H"])
    return dict(big, H=H, span=sum(r["span"] for r in parts), slope=slope, k=slope / H, flights=flights, read_flights=read,
                risers=sum(r["risers"] for r in parts), coverage=sum(r["coverage"] * r["H"] for r in parts) / H,
                floor_based=all(r["floor_based"] for r in parts), bands=[b for r in parts for b in r["bands"]],
                landings=lands, landing_measured=sum(r["landing_measured"] for r in parts), parts=parts)


def _clip_pieces(frame, r):
    """이 절단 위치가 잘라 넣은 블록(XCLIP) 안에 있으면 (블록 이름, 블록 안 x 중심, [블록 안 y 구간…]).
    한 줄기가 위아래 두 조각(중간 층 생략)으로 이어 붙여져 있으면 구간이 둘이다."""
    cx = (r["x"][0] + r["x"][1]) / 2
    ys = sorted((r["ymin"], r["ymax"]))
    span = max(ys[1] - ys[0], 1e-9)
    by_name = {}
    for c in getattr(frame, "clips", None) or []:
        sx0, sy0, sx1, sy1 = c["screen"]
        if not (sx0 <= cx <= sx1) or sx1 - sx0 < 1 or sy1 - sy0 < 1:
            continue
        a, b = max(ys[0], sy0), min(ys[1], sy1)
        if b - a < 1000:
            continue
        bx0, by0, bx1, by1 = c["block"]
        kx, ky = (bx1 - bx0) / (sx1 - sx0), (by1 - by0) / (sy1 - sy0)
        if not (0.9 <= kx <= 1.1 and 0.9 <= ky <= 1.1):
            continue                      # 축척을 바꿔 넣은 블록은 다루지 않는다
        f = lambda sy: by1 - (sy - sy0) * ky
        d = by_name.setdefault(c["name"], {"x": bx0 + (cx - sx0) * kx, "iv": [], "cov": 0.0})
        d["iv"].append(tuple(sorted((f(a), f(b)))))
        d["cov"] += b - a
    best = max(by_name.items(), key=lambda kv: kv[1]["cov"], default=None)
    if best is None or best[1]["cov"] < 0.8 * span:
        return None
    return (best[0], best[1]["x"], sorted(best[1]["iv"]))


def _union_len(ivs):
    tot, end = 0.0, None
    for lo, hi in sorted(ivs):
        if end is None or lo > end:
            tot += hi - lo
            end = hi
        elif hi > end:
            tot += hi - end
            end = hi
    return tot


def _union_by_clip(frame, res):
    """같은 블록의 같은 계단을 구간별로 잘라 늘어놓은 것끼리 묶어, 블록 안에서 실제로 덮는 높이만큼만 센다(겹쳐 보여 준 부분은 한 번).
    한 줄기 안에서 조각 사이가 떠 있으면(기준층 생략) 블록에는 그 층들이 다 그려져 있으므로 맨 아래부터 맨 위까지를 센다.
    반환: 묶은 뒤의 절단 위치 목록"""
    groups, rest = {}, []
    for r in res:
        c = _clip_pieces(frame, r)
        if c is None:
            rest.append(r)
            continue
        key = next((k for k in groups if k[0] == c[0] and abs(k[1] - c[1]) <= 3000), None)
        if key is None:
            key = (c[0], c[1])
            groups[key] = []
        groups[key].append((r, c[2]))
    out = list(rest)
    for key, lst in groups.items():
        stacked = any(len(iv) >= 2 for _, iv in lst)
        if len(lst) == 1 and not stacked:
            out.append(lst[0][0])
            continue
        ivs = [x for _, iv in lst for x in iv]
        union = _union_len(ivs) / 1000
        hull = (max(hi for _, hi in ivs) - min(lo for lo, _ in ivs)) / 1000
        H = hull if stacked else union
        m = _sum_parts([r for r, _ in lst]) if len(lst) > 1 else dict(lst[0][0], parts=[lst[0][0]])
        k_ = H / max(m["H"], 0.1)
        fl = max(1, round(m["flights"] * k_))
        lands = list(m["landings"] or [])
        if lands:
            lands = lands[:fl] + [statistics.median(lands)] * max(0, fl - len(lands))
        own = "+".join(f"{r['H']:.1f}" for r, _ in lst)
        m.update(H=H, slope=m["slope"] * k_, flights=fl, risers=round(m["risers"] * k_), landings=lands, hull=stacked,
                 read_flights=min(m["read_flights"], fl))
        m["k"] = m["slope"] / H
        m["note"] = (f"한 계단을 {len(lst)}구간으로 잘라 늘어놓은 장(블록 '{key[0]}') → 그림 높이 {own}m, 블록 안에서 실제로 덮는 높이 {union:.1f}m"
                     + (f", 사이에 생략된 층까지 {hull:.1f}m" if stacked else ""))
        out.append(m)
    return out


def _combine(frame, res):
    """한 계단(소제목 하나)에 딸린 절단 위치들 → 계단 하나의 기하.
    보통은 같은 계단을 여러 방향으로 자른 그림이므로 가장 높은 것 하나. 한 계단을 층 구간별로 나눠 그린 장이면 구간을 더한다:
      ① 같은 블록을 잘라(XCLIP) 늘어놓은 것 → 블록 안에서 실제로 덮는 높이
      ② 옆에 적힌 레벨(EL)·층 이름이 구간마다 다른 것 → 구간 합
    기준층을 생략하고 '2,900X14개층' 이라 적은 구간에는 생략된 층 높이를 더한다."""
    res = [dict(r) for r in res if r["H"] >= 1.5]
    if not res:
        return None
    fm = _floor_marks(frame)
    merged = _union_by_clip(frame, res) if len(res) > 1 or (getattr(frame, "clips", None) and res) else res
    frng = _el_ranges(merged, fm) if fm else {}
    for t in frame.texts:
        m = REPEAT_RE.search(t.text)
        if not m:
            continue
        fh, n = float(m.group(1).replace(",", "")) / 1000, int(m.group(2))
        near = [r for r in merged if min(r["ymin"], r["ymax"]) - 500 <= t.cy <= max(r["ymin"], r["ymax"]) + 500
                and not r.get("repeat") and not r.get("hull")]          # 블록에서 생략 구간까지 이미 센 것은 다시 더하지 않는다
        if not near or n < 2 or not (2.0 <= fh <= 6.0) or any(r.get("hull") for r in merged):
            continue
        wide = [r for r in near if frng.get(id(r)) and frng[id(r)][1] - frng[id(r)][0] >= n - 1]
        r = min(wide or near, key=lambda r: abs((r["x"][0] + r["x"][1]) / 2 - t.cx))
        add = fh * (n - 1)
        k_ = (r["H"] + add) / r["H"]
        per = r["flights"] / max(r["H"], 0.1)
        r.update(H=r["H"] + add, slope=r["slope"] * k_, flights=round(r["flights"] + per * add), risers=round(r["risers"] * k_),
                 repeat=f"기준층 생략 표기 '{t.text.strip()}' → {fh:.2f}m × {n - 1}개 층({add:.1f}m)을 더함")
        if r["landings"]:
            med = statistics.median(r["landings"])
            r["landings"] = list(r["landings"]) + [med] * max(0, r["flights"] - len(r["landings"]))
    if len(merged) == 1:
        r = merged[0]
        if r.get("repeat"):
            r["note"] = ((r.get("note") + "; ") if r.get("note") else "") + r["repeat"]
        return r
    if any(r.get("parts") for r in merged):
        return max(merged, key=lambda r: r["H"])      # 블록 기준으로 이미 묶였다: 나머지는 다른 방향 그림
    res = merged
    lv = _levels(frame)
    by_el = _is_continuation(lv)
    if not by_el:
        byf = defaultdict(list)
        for x, y, v in fm:
            byf[v].append(y)
        if not any(max(ys) - min(ys) > 2000 for ys in byf.values()):
            return max(res, key=lambda r: r["H"])
        lv = fm
    rng = _el_ranges(res, lv)
    if by_el and sum(1 for v in rng.values() if v) < 2:
        rng2 = _el_ranges(res, fm)                # 레벨 값은 한두 군데뿐이고 층 이름으로 적은 도면
        if sum(1 for v in rng2.values() if v) >= 2:
            rng, by_el = rng2, False
    known = sorted([r for r in res if rng.get(id(r))], key=lambda r: rng[id(r)][0])
    if len(known) < 2:
        return max(res, key=lambda r: r["H"])
    groups = []                       # 레벨 범위가 절반 넘게 겹치면 같은 구간을 다른 방향으로 자른 것 → 그중 높은 것
    for r in known:
        lo, hi = rng[id(r)]
        for g in groups:
            glo, ghi = g["rng"]
            ov = min(hi, ghi) - max(lo, glo)
            if ov > 0.5 * min(hi - lo, ghi - glo):
                g["rs"].append(r)
                g["rng"] = (min(lo, glo), max(hi, ghi))
                break
        else:
            groups.append({"rng": (lo, hi), "rs": [r]})
    parts = []
    for g in groups:
        r = max(g["rs"], key=lambda r: r["H"])
        span_el = g["rng"][1] - g["rng"][0]
        # 잘라 놓은 구간은 위아래로 이웃 구간과 조금씩 겹쳐 그려진다 → 그 구간에 적힌 레벨 범위까지만 센다
        if by_el and not r.get("repeat") and r["H"] > span_el + 0.3:
            k_ = span_el / r["H"]
            fl = max(1, round(r["flights"] * k_))
            r = dict(r, H=span_el, slope=r["slope"] * k_, flights=fl, risers=round(r["risers"] * k_),
                     read_flights=min(r["read_flights"], fl), landings=(r["landings"] or [])[:fl])
        parts.append(r)
    if len(parts) == 1:
        return parts[0]
    out = _sum_parts(parts)
    if by_el:
        out["el"] = (min(g["rng"][0] for g in groups), max(g["rng"][1] for g in groups))
    out["note"] = (f"한 계단을 {len(parts)}구간으로 나눠 그린 장 → 구간 높이 " + "+".join(f"{r['H']:.1f}" for r in parts) + "m 를 더함 (" +
                   (f"레벨 EL {min(g['rng'][0] for g in groups):+.1f} ~ {max(g['rng'][1] for g in groups):+.1f}" if by_el else
                    f"층 표기 {min(g['rng'][0] for g in groups):g} ~ {max(g['rng'][1] for g in groups):g}층") + ")" +
                   "".join("; " + r["repeat"] for r in parts if r.get("repeat")))
    return out


def _stair_key(name):
    m = STAIR_NAME_RE.search(name.replace(" ", ""))
    return f"계단실#{m.group(1)}" if m else None


def _frame_stair_name(f):
    """도곽 안 글자에 계단실 이름이 한 가지만(두 번 이상) 적혀 있으면 그 이름. 여러 가지가 섞였거나 없으면 None"""
    ks = Counter(k for k in (_stair_key(t.text) for t in f.texts if len(t.text) <= 40) if k)
    if len(ks) == 1 and sum(ks.values()) >= 2:
        return next(iter(ks))
    return None


def _rail_pairs(f):
    """도곽 안에서 20~50° 기울고 600mm 이상인 선 중 25~95mm 간격으로 나란한 짝(손스침 두 줄). 반환 [(길이, ymin, ymax)]"""
    import math
    sl = []
    for a, b, c, d in f.segments:
        dx, dy = c - a, d - b
        L = math.hypot(dx, dy)
        if L < 600:
            continue
        ang = abs(math.degrees(math.atan2(dy, dx))) % 180
        ang = min(ang, 180 - ang)
        if 20 <= ang <= 50:
            sl.append((a, b, c, d, L, math.atan2(dy, dx) % math.pi))
    sl.sort(key=lambda s_: s_[0])
    out, used = [], set()
    for i, s_ in enumerate(sl):
        if i in used:
            continue
        nx, ny, ux, uy = -math.sin(s_[5]), math.cos(s_[5]), math.cos(s_[5]), math.sin(s_[5])
        for j in range(i + 1, len(sl)):
            t = sl[j]
            if t[0] - s_[0] > s_[4] + 200:
                break
            if j in used or abs(s_[5] - t[5]) > 0.02:
                continue
            dist = abs((t[0] - s_[0]) * nx + (t[1] - s_[1]) * ny)
            if not 25 <= dist <= 95:
                continue
            q = sorted([(t[0] - s_[0]) * ux + (t[1] - s_[1]) * uy, (t[2] - s_[0]) * ux + (t[3] - s_[1]) * uy])
            ov = min(s_[4], q[1]) - max(0, q[0])
            if ov >= 0.7 * min(s_[4], t[4]):
                used.add(i)
                used.add(j)
                out.append((max(s_[4], t[4]), min(s_[1], s_[3]), max(s_[1], s_[3])))
                break
    return out


def stair_handrail_dxf(frames, rules=None, n_floors=None):
    """도곽 목록 → 계단 핸드레일 행 [{항목, 규격, 단위, 수량, 근거, 도곽}]. 경고는 LAST_WARNINGS 에."""
    LAST_WARNINGS.clear()
    R = (rules or {}).get("계단난간", {})
    turn = R.get("꺾임길이_m", 0.4)
    sides = R.get("측수", 1)
    cand = []
    for f in frames:
        t = (f.title or "").replace(" ", "")
        is_stair_sheet = STAIR_TITLE.search(t) and re.search(r"단면|상세", t) and "평면" not in t      # 계단단면도, 계단상세도
        if not is_stair_sheet and (not SECTION_TITLE.search(t) or NOT_SECTION.search(t)):
            # 제목을 못 읽은 도곽이라도 계단실 이름이 한 가지만 적혀 있고 같은 파일의 다른 도곽이 단면도이면 계단 단면 후보
            if not (not t and _frame_stair_name(f) and any(SECTION_TITLE.search((g.title or "").replace(" ", "")) for g in frames
                                                      if g is not f and getattr(g, "file", None) == getattr(f, "file", None))):
                continue
        cand.append(f)
    def _select(cand):
        """단면 도곽 묶음 → [(이름, 도곽, 기하)]"""
        stair_frames = [f for f in cand if STAIR_TITLE.search(f.title or "") or _frame_stair_name(f)]
        stairs = []
        if stair_frames:
            for f in stair_frames:
                res = analyze_stairs(f)
                if not res:
                    continue
                nm = None if STAIR_TITLE.search(f.title or "") else _frame_stair_name(f)
                if nm:
                    # 제목에는 계단이 없지만('코어 확대 단면도-2') 도곽 안에 계단실 이름이 하나만 적힌 단면도 → 그 계단실의 단면
                    r = _combine(f, res)
                    if r:
                        stairs.append((f"{(f.title or '').strip()}({nm})".strip(), f, r))
                    continue
                subs = _subtitles(f)
                if len(subs) >= 2:
                    # 절단 위치를 가장 가까운 소제목에 붙이고, 소제목마다 가장 높은 것 하나
                    by_sub = {}
                    for r in res:
                        cx = (r["x"][0] + r["x"][1]) / 2
                        s_ = min(subs, key=lambda t: abs(t.cx - cx))
                        by_sub.setdefault(s_.text.strip(), []).append(r)
                    for k, rs in by_sub.items():
                        r = _combine(f, rs)
                        if r:
                            stairs.append((k, f, r))
                else:
                    r = _combine(f, res)
                    if r:
                        stairs.append((f.title.strip(), f, r))
            # 계단상세도가 일부 층만 자른 부분 상세이면(일반 단면도의 계단이 훨씬 높으면) 일반 단면도의 계단을 쓴다
            if len(stairs) == 1:
                best = None
                for f in cand:
                    if f in stair_frames:
                        continue
                    for r in analyze_stairs(f):
                        if best is None or r["H"] > best[1]["H"]:
                            best = (f, r)
                if best and best[1]["H"] > stairs[0][2]["H"] * 1.15:
                    stairs = [(f"{best[0].title.strip()} 속 계단", best[0], best[1])]
        else:
            # 계단 단면도가 없으면 일반 단면도 중 계단 높이가 가장 큰 것 하나 (계단을 온전히 자른 단면)
            best = None
            for f in cand:
                for r in analyze_stairs(f):
                    if best is None or r["H"] > best[1]["H"]:
                        best = (f, r)
            if best:
                stairs.append((f"{best[0].title.strip()} 속 계단", best[0], best[1]))
        return stairs

    # 여러 동이면 동마다 따로 고른다 ((1동) 주단면도 / (2동) 주단면도 …)
    from ..buildings import building_of
    groups = {}
    for f in cand:
        groups.setdefault(building_of(f.title), []).append(f)
    stairs = []
    if len([g for g in groups if g]) >= 2:
        for b, fs in sorted(groups.items(), key=lambda kv: (kv[0] is None, str(kv[0]))):
            for name, f, r in _select(fs):
                stairs.append((f"{b} {name}" if b and not building_of(name) else name, f, r))
    else:
        stairs = _select(cand)
    # 같은 계단 단면이 여러 파일에 들어 있으면(폴더 입력에서 같은 장이 중복) 한 번만 센다
    uniq, dup_notes = [], []
    for name, f, r in stairs:
        same = next((u for u in uniq if u[0].replace(" ", "") == name.replace(" ", "") and abs(u[2]["H"] - r["H"]) <= 0.01 * max(r["H"], 0.1)), None)
        if same:
            dup_notes.append(f"도곽 {f.index}{' ' + f.file if getattr(f, 'file', '') else ''}은 도곽 {same[1].index}와 같은 계단 단면 → 한 번만 셈")
            continue
        uniq.append((name, f, r))
    stairs = uniq
    # 같은 계단실 이름이 여러 장에 나오면 한 계단으로 묶는다: 레벨 범위가 이어지면(겹침 없음) 더하고, 아니면 가장 높은 장 하나만
    grouped, order = {}, []
    for name, f, r in stairs:
        k = _stair_key(name) or name.replace(" ", "")
        if k not in grouped:
            grouped[k] = []
            order.append(k)
        grouped[k].append((name, f, r))
    stairs = []
    for k in order:
        g = grouped[k]
        if len(g) == 1:
            stairs.append(g[0])
            continue
        rngs = []
        for name, f, r in g:
            if r.get("el"):
                rngs.append(r["el"])
            else:
                lv = [v for x, y, v in _levels(f)]
                rngs.append((min(lv), max(lv)) if len(lv) >= 2 else None)
        disjoint = all(rngs) and all(min(a[1], b[1]) - max(a[0], b[0]) <= 0.25 * min(a[1] - a[0], b[1] - b[0])
                                     for i, a in enumerate(rngs) for b in rngs[i + 1:])
        if disjoint:
            parts = [dict(r, _frame=f_) for _, f_, r in g]
            m = _sum_parts(parts)
            m["note"] = (f"{k} 가 {len(g)}장(도곽 {', '.join(str(f.index) for _, f, _ in g)})에 층 구간별로 나뉘어 있음 → 높이 " +
                         "+".join(f"{r['H']:.1f}" for r in parts) + "m 를 더함")
            stairs.append((g[0][0], g[0][1], m))
        else:
            name, f, r = max(g, key=lambda x: x[2]["H"])
            r = dict(r)
            r["note"] = ((r.get("note") + "; ") if r.get("note") else "") + \
                f"{k} 가 {len(g)}장(도곽 {', '.join(str(f_.index) for _, f_, _ in g)})에 그려져 있음 → 가장 높은 장(도곽 {f.index}) 하나만 셈"
            stairs.append((name, f, r))
    # 상식 검사 1: 계단 높이가 건물 높이를 넘으면 잘못 읽은 것 → 버리고 경고
    all_lv = [v for f in cand for x, y, v in _levels(f)]
    bld_h = (max(all_lv) - min(all_lv)) if len(all_lv) >= 4 and max(all_lv) - min(all_lv) >= 3 else (n_floors * 4.5 if n_floors else None)
    bad = []
    if bld_h:
        keep_ = []
        for name, f, r in stairs:
            if r["H"] > bld_h * 1.5 + 3.0:
                bad.append(name)
                LAST_WARNINGS.append(f"계단 핸드레일: '{name}'(도곽 {f.index}) 계단 높이 {r['H']:.1f}m 가 도면에 적힌 레벨 범위 {bld_h:.1f}m 의 1.5배를 넘음 → 잘못 읽은 것으로 보고 뺌 (계단 단면도 확인)")
            else:
                if r["H"] > bld_h * 1.25 + 2.0:
                    LAST_WARNINGS.append(f"계단 핸드레일: '{name}'(도곽 {f.index}) 계단 높이 {r['H']:.1f}m 가 도면에 적힌 레벨 범위 {bld_h:.1f}m 보다 꽤 큼 → 계단 단면도 확인")
                keep_.append((name, f, r))
        stairs = keep_
    rows = []
    for name, f, r in stairs:
        if r["landings"]:
            land = sum(r["landings"])
            land_txt = (f"계단참 {land:.1f}m(도면 치수 " + "+".join(f"{v:.2f}" for v in r["landings"][:4]) +
                        ("…" if len(r["landings"]) > 4 else "") + f", {len(r['landings'])}개소)")
        else:
            land = r["flights"] * turn
            land_txt = f"계단참 {turn}m×{r['flights']}개소(치수 없어 규칙값)"
        L = (r["slope"] + land) * sides
        warn = (f" ※ 판 {r['read_flights']}개만 챌판이 읽힘(디딤판 범위의 {r['coverage']:.0%}); 판 수는 높이÷판높이로 추정"
                if r["coverage"] < 0.9 else "")
        basis = "바닥선 기준" if r["floor_based"] else "디딤판 범위"
        rows.append({"항목": "계단 핸드레일", "규격": name, "단위": "M", "수량": round(L, 1),
                     "근거": f"도곽 {f.index} {f.title}: 계단 윗선 따라 — 경사 {r['slope']:.1f}m(판 {r['flights']}개, 챌판 약 {r['risers']}단 "
                             f"{r['riser']:.0f}×{r['tread']:.0f}) + {land_txt}" + (f" × {sides}측" if sides != 1 else "") +
                             f"; 계단 높이 {r['H']:.2f}m({basis})" + warn + (f" / {r['note']}" if r.get("note") else ""),
                     "도곽": f.index, "기하": r, "frame": f})
    if rows and dup_notes:
        rows[-1]["근거"] += " / " + "; ".join(dup_notes)
    # 핸드레일이 실제로 그려져 있는지: 손스침은 단면도에 '나란한 두 줄 사선'으로 그려진다. 도곽마다 그 길이를 재서 계단 경사 길이와 견준다.
    # (아직은 표시만 한다. 길이는 디딤판을 따라 잰 값 그대로 — 손스침 선 인식이 검증되면 그려진 곳만 재도록 바꾼다)
    by_frame = {}
    for r in rows:
        if r.get("frame") is not None and r.get("기하"):
            by_frame.setdefault(id(r["frame"]), []).append(r)
    for rs_ in by_frame.values():
        f = rs_[0]["frame"]
        try:
            rail = sum(L for L, _, _ in _rail_pairs(f)) / 1000.0
        except Exception:
            continue
        slope = sum(r["기하"]["slope"] for r in rs_)
        ratio = rail / slope if slope else 0
        for r in rs_:
            r["손스침선"] = round(rail, 1)
            r["근거"] += f" / 이 도곽에 그려진 손스침 선(나란한 두 줄 사선) 합 {rail:.1f}m, 계단 경사 길이 {slope:.1f}m 의 {ratio:.0%}"
        if ratio < 0.3:
            LAST_WARNINGS.append(f"계단 핸드레일: '{rs_[0]['규격']}'(도곽 {f.index}) 은 손스침 선이 {'전혀 안 보임' if rail < 0.5 else f'경사 길이의 {ratio:.0%} 만 보임'}"
                                 f" → 핸드레일이 안 그려진 계단일 수 있음. 길이 {sum(r['수량'] or 0 for r in rs_):.1f}m 는 디딤판 기준이라 확신 못 함 → 단면도 확인")
    # 상식 검사 2: 도면에 적힌 계단실 이름 수와 읽은 계단 수
    names = set()
    for f in frames:
        for txt in [f.title or ""] + [t.text for t in f.texts if "계단" in t.text and len(t.text) <= 30]:
            k = _stair_key(txt)
            if k:
                names.add(k)
    got = {_stair_key(r["규격"]) for r in rows if r["수량"] is not None} - {None}
    n_rows = sum(1 for r in rows if r["수량"] is not None)
    # 한 단면 그림에 계단실 이름이 둘 함께 적혀 있으면(R현장 '계단실 #1' 바로 옆에 '계단실 #2') 그 그림이 두 계단실을 함께 나타낸다
    if len(names) >= 2:
        for mname in sorted(names - got):
            src = None
            for f in frames:
                mt = [t for t in f.texts if _stair_key(t.text) == mname and len(t.text) <= 30]
                for r in rows:
                    if r.get("frame") is not f or r["수량"] is None or _stair_key(r["규격"]) is None:
                        continue
                    ot = [t for t in f.texts if _stair_key(t.text) == _stair_key(r["규격"]) and len(t.text) <= 30]
                    pairs = sum(1 for a_ in mt if any(abs(a_.cx - b_.cx) + abs(a_.cy - b_.cy) <= 5 * max(a_.y1 - a_.y0, b_.y1 - b_.y0, 1) for b_ in ot))
                    if pairs and pairs == len(mt):
                        src = r
                        break
                if src:
                    break
            if src:
                rows.append({"항목": "계단 핸드레일", "규격": f"{mname} ({src['규격']} 그림에 이름이 함께 적힘)", "단위": "M", "수량": src["수량"],
                             "근거": f"도곽 {src['도곽']}: '{mname}' 단면도가 따로 없고 {_stair_key(src['규격'])} 단면 그림의 이름표 옆에 '{mname}' 가 함께 적혀 있음 → 같은 그림이 두 계단실을 나타내는 것으로 보고 같은 길이를 한 번 더 셈 (확신 못 함 → 확인)",
                             "도곽": src["도곽"], "기하": src.get("기하"), "frame": src.get("frame")})
                got.add(mname)
                n_rows += 1
                LAST_WARNINGS.append(f"계단 핸드레일: '{mname}' 는 단면도가 따로 없고 {_stair_key(src['규격'])} 그림에 이름이 함께 적혀 있어 같은 길이({src['수량']}m)로 한 번 더 셈 → 확인")
    if len(names) >= 2:
        miss = sorted(names - got)
        if miss and len(got) == n_rows:       # 읽은 계단 이름이 모두 '계단실#N' 꼴일 때만 어느 것이 빠졌는지 말할 수 있다
            LAST_WARNINGS.append(f"계단 핸드레일: 도면에 계단실 이름이 {len(names)}개({', '.join(sorted(names))}) 있는데 단면에서 읽은 것은 {len(got)}개 → 못 읽은 계단실: {', '.join(miss)}")
        elif n_rows < len(names) and len(got) != n_rows:
            LAST_WARNINGS.append(f"계단 핸드레일: 도면에 계단실 이름이 {len(names)}개({', '.join(sorted(names))}) 있는데 단면에서 읽은 계단은 {n_rows}개")
    # 상식 검사 3: 계단 하나의 핸드레일 길이는 한쪽 기준으로 계단 높이의 1.5~4배쯤이다
    for r in rows:
        g = r.get("기하")
        if g and r["수량"] is not None and not (1.3 <= r["수량"] / sides / g["H"] <= 4.5):
            LAST_WARNINGS.append(f"계단 핸드레일: '{r['규격']}' 길이 {r['수량']}m 가 계단 높이 {g['H']:.1f}m 에 비해 이상함(높이의 {r['수량'] / sides / g['H']:.1f}배) → 확인")
    if not rows and cand:
        rows.append({"항목": "계단 핸드레일", "규격": "", "단위": "M", "수량": None,
                     "근거": "단면도 " + ", ".join(f.title for f in cand[:4]) + " 에서 계단 윤곽(디딤판·챌판 지그재그)을 찾지 못함", "도곽": None})
    return rows


# ---- 글자 기반 금속 항목 집계 (참고용) -------------------------------------------------------------
# 평면도에 이름이 적히는 금속 품목. 도면마다 표기가 달라 '글자 수'일 뿐이므로 근거에 도곽·층별 개수를 적어 사람이 확인하게 한다.
#   종류 "층합": 층마다 따로 있는 것 → 사용한 층 평면도의 글자 수를 층수 곱해서 더함
#   종류 "최대": 한 줄기가 여러 층을 지나는 것(선홈통) → 평면도 가운데 글자가 가장 많은 한 장의 수
INVENTORY = [
    ("옥상·점검 사다리", r"사다리", "EA", "층합", r"사다리"),
    ("집수정", r"집수정", "EA", "층합", r"집수정"),
    ("우편함", r"우편함", "EA", "층합", r"우편함"),
    ("카스토퍼", r"카\s*스\s*토\s*퍼|카스타퍼|CAR\s*STOP", "EA", "층합", r"카스토퍼|카스타퍼"),
    ("청소용 고리", r"청소\s*용?\s*고리", "EA", "층합", r"청소\s*용?\s*고리"),
    ("점검구", r"점검구", "EA", "층합", r"점검구"),
    ("상자홈통", r"상자\s*홈통", "EA", "최대", r"상자\s*홈통"),
    ("선홈통(개소)", r"선\s*홈통|(?<![A-Z])R\.?D\.?(?![A-Z])", "EA", "최대", r"선\s*홈통"),
]


def keyword_inventory(plan_frames, rules=None):
    """plan_frames: [(도곽, 층 튜플, 태그)] (창호 집계에 쓴 층별 평면도). → [{항목, 규격, 단위, 수량, 근거}]"""
    rows = []
    for name, pat, unit, mode, est_pat in INVENTORY:
        rx = re.compile(pat, re.I)
        per = []            # (도곽 제목, 층수, 글자 수)
        for f, fl, _ in plan_frames:
            n = sum(1 for t in f.texts if rx.search(t.text))
            if n:
                per.append((f.title, len(fl), n))
        if not per:
            continue
        if mode == "층합":
            q = sum(n * m for _, m, n in per)
        else:
            q = max(n for _, _, n in per)
        ev = ", ".join(f"{ti} {n}" + (f"×{m}층" if m > 1 else "") for ti, m, n in per)
        rows.append({"항목": name, "규격": "글자 수(참고)", "단위": unit, "수량": q,
                     "근거": f"평면도 글자 '{name.split('(')[0]}' {'층별 합' if mode == '층합' else '최다 한 장'}: {ev}",
                     "견적패턴": est_pat})
    return rows
