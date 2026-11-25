"""파생 물량: 창호 1개에서 따라 나오는 유리·코킹·충전·철물 물량.

규칙은 RULES(아래 기본값)로 정의하고, 회사별로 rules.json으로 덮어쓸 수 있다.
  - 창호주위 코킹/충전(m) = Σ 둘레 × 면수 × 수량   (대상 종류 필터)
  - 유리(m²)             = Σ W×H × 수량  을 유리 사양(두께·종류)별로 묶음
  - 유리주위 코킹(m)      = Σ 유리 칸 둘레 × 면수 × 수량  (형판에서 유리 칸 분할)
  - 철물(조)             = 일람표 철물 글자에서 품목·개수 읽기 × 수량
"""
import json
import os
import re
from collections import defaultdict

# 규칙표. "_출처" 는 값의 근거: "일반" = 업계 통상 기준, "현장맞춤" = 한 현장의 견적서 숫자에 맞춰 정한 값(다른 현장 검증 전).
RULES = {
    "창호주위코킹": {"면수": 1, "제외종류": ["WD", "PD", "ABS", "SH", "ST"], "단위": "m",
                 "_출처": "일반. 회사마다 기준이 다를 수 있으니 확인해서 고쳐 쓴다"},
    "창호주위충전": {"면수": 1, "제외종류": ["WD", "PD", "ABS", "SH", "ST"], "단위": "m", "_출처": "일반"},
    "유리주위코킹": {"면수": 2, "최소유리폭": 150, "단위": "m", "_출처": "일반(유리 양면)"},
    "유리": {"제외종류": ["WD", "PD", "ABS", "SH", "ST", "SD", "FSD"], "단위": "m2", "_출처": "일반"},
    "다층창호": {"높이기준_mm": 4500, "항상종류": ["ACW", "CW", "SCW"], "항상종류_최소높이_mm": 3000,
              "_출처": "현장맞춤. 이 높이 이상 창이 여러 층 평면에 나오면 한 덩어리로 봄. 커튼월 종류라도 3m 미만 작은 창은 보통 창(E현장 ACW-5)"},
    "트렌치": {"손잡이_개당": 2, "마구리앵글_면수": 2,
             "_출처": "현장맞춤. 한 현장 값에 맞춘 것이라 확인 필요"},
    "계단난간": {"꺾임길이_m": 1.3, "측수": 1, "_출처": "계단참 깊이를 도면 치수에서 못 읽을 때 1개소당 길이. 측수 1=한 줄 2=양쪽"},
    "철물": {"제외종류": ["WD", "PD", "ABS"], "피봇힌지배수": 1,
             "문짝당기본": {"도어클로저": 1, "피봇힌지": 1, "도어스톱": 1},
             "기본적용종류": ["FSD", "SD"], "양개문폭": 1500,
             "_출처": "현장맞춤. 여닫이문 문짝마다 기본 철물. 피봇힌지를 문짝당 2조로 보는 회사도 있음"},
}

# 견적서 항목 찾기용 (파생 항목 → 견적서 품목명 정규식)
EST_MATCH = {
    "창호주위코킹 (m)": r"창호\s*주위.*코킹|창호.*방수\s*코킹|외벽\s*창호.*코킹",
    "창호주위충전 (m)": r"우레탄|충전",
    "유리주위코킹 (m)": r"유리\s*주위.*코킹|유리\s*코킹",
    "도어클로저 (조)": r"도어\s*클로[저져]|도어\s*체크|도아\s*체크",
    "피봇힌지 (조)": r"피[봇벗]\s*힌지",
    "플로어힌지 (조)": r"플로어\s*힌지|후로아\s*힌지",
    "도어록 (조)": r"(?<!디지털)(?<!디지탈)도어\s*[록락]|도아\s*록|도어\s*핸들",
    "디지털도어록 (조)": r"디지[털탈]\s*도어\s*록",
    "도어스톱 (조)": r"도어\s*스[톱탑]",
}

HW = [  # (표준 품목명, 정규식)
    ("도어클로저", r"도어\s*클로[저져]|도어\s*체크|도아\s*체크|도어첵"),
    ("피봇힌지", r"피[봇벗]\s*힌지"),
    ("플로어힌지", r"플로어\s*힌지|후로아\s*힌지"),
    ("일반힌지", r"(?<![봇벗어아])\s*힌지"),
    ("도어록", r"도어\s*[록락]|도아\s*록|레버\s*록|실린더\s*록"),
    ("디지털도어록", r"디지[털탈]\s*도어\s*록"),
    ("도어스톱", r"도어\s*스[톱탑]|말굽"),
    ("손잡이", r"손잡이|핸들|풀\s*바|PULL"),
    ("패닉바", r"패닉\s*바|PANIC"),
]
NUM_AFTER = r"\s*[:x×*]?\s*(\d+)\s*(조|개|EA|ea|SET|set)"


def load_rules(path=None):
    r = json.loads(json.dumps(RULES))
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if isinstance(v, dict):
                    r.setdefault(k, {}).update(v)
                else:
                    r[k] = v
    return r


def glass_label(text):
    """유리 사양 글자 → '24T 로이복층' 같은 묶음 이름."""
    t = text or ""
    th = None
    # 두께 후보를 모두 모아, 3mm 이상인 첫 값을 유리 두께로 본다
    # (같은 글에 '50X150X1.2t'(틀 두께)와 '24(일반6,Ar12,로이6)'(유리)가 함께 있는 일이 흔하다)
    cands = []
    for pat in (r"(\d{1,2}(?:\.\d+)?)\s*(?:T|t|mm|MM|㎜)(?![a-zA-Z])",
                r"THK\s*(\d{1,2}(?:\.\d+)?)",
                r"(?<![A-Za-z])T\s*=?\s*(\d{1,2}(?:\.\d+)?)(?!\d)",
                r"(?<![\d.])(\d{2})\s*\((?=[^)]*(?:Ar|AR|Air|일반|로이|LE|CL))"):      # '24(일반6, Ar12, 로이6)' : 괄호 앞 총두께
        for m in re.finditer(pat, t, re.I):
            if float(m.group(1)) >= 3:          # 3mm 미만은 유리가 아니라 틀 두께(T.1.2, 1.6T 등)
                cands.append((m.start(), m.group(1)))
    if cands:
        th = min(cands)[1]                      # 글에서 먼저 나오는 것(고정창 24 / 여닫이 28 이면 24)
    else:
        m = re.search(r"\(?\s*(\d{1,2})\s*(?:LE|CL|C)?\s*\+\s*(\d{1,2})\s*(?:A|AR|AIR)?\s*\+\s*(\d{1,2})", t, re.I)
        if m:
            th = str(sum(int(x) for x in m.groups()))
    kind = []
    for k, pat in (("삼중", r"삼중|트리플"), ("복층", r"복층|페어|\d+\s*A\b|\+\s*\d+\s*A"), ("로이", r"로이|LOW-?E|LE\b"),
                   ("강화", r"강화|TEMP"), ("접합", r"접합|PVB"), ("반강화", r"반강화|배강도|H/S")):
        if re.search(pat, t, re.I):
            kind.append(k)
    if "삼중" in kind and "복층" in kind:
        kind.remove("복층")
    if not th and not kind:
        return None
    return f"{th + 'T ' if th else ''}{''.join(kind) or '유리'}".strip()


def hardware_counts(text):
    """'도어록1조, 피봇힌지 2조, 방화용도어체크1조' → {'도어록':1,'피봇힌지':2,'도어클로저':1}"""
    out = defaultdict(float)
    t = text or ""
    taken = []
    for name, pat in HW:
        for m in re.finditer(pat, t, re.I):
            if any(a <= m.start() < b for a, b in taken):
                continue
            n = re.match(NUM_AFTER, t[m.end():m.end() + 8])
            out[name] += float(n.group(1)) if n else 1.0
            taken.append((m.start(), m.end()))
    if "디지털도어록" in out and "도어록" in out:
        out["도어록"] = max(0, out["도어록"] - out["디지털도어록"])
        if not out["도어록"]:
            del out["도어록"]
    return dict(out)


def glass_layers(item):
    """이중창(외부/내부 유리 따로, '이중창', '사중 미서기')이면 2, 아니면 1."""
    txt = " ".join(x for x in (item.name, item.glass, getattr(item, "spec_text", "")) if x)
    if (re.search(r"외부\s*[:：]", txt) and re.search(r"내부\s*[:：]", txt)) or re.search(r"이중\s*창|사중|4중", txt):
        return 2
    return 1


def leaves(item, double_w=1500):
    """문짝 수: 폭이 양개문폭 이상이거나 '쌍/양개/플러쉬볼트' 표기가 있으면 2."""
    txt = " ".join(x for x in (item.name, item.hardware, getattr(item, "spec_text", "")) if x)
    if re.search(r"쌍여닫|양개|쌍문|플러쉬\s*볼트|FLUSH", txt, re.I):
        return 2
    return 2 if (item.width or 0) >= double_w else 1


def pane_perimeter(item, tol=15, min_w=150):
    """형판(일람표 입면 그림)에서 유리 칸을 찾아 칸 둘레 합(m, 1개당).

    모든 가로·세로 선 위치로 격자를 만들고, 창틀처럼 얇은 칸(min_w 미만)은 벽으로 본다.
    이웃 칸 사이에 선이 없으면 같은 유리로 합친다(합집합). 합쳐진 덩어리 하나 = 유리 한 장.
    """
    try:
        from .elevmatch import make_template
        tp = make_template(item, tol)
    except Exception:
        tp = None
    if not tp:
        return None, 0
    W, H, parts = tp

    def uniq(vals):
        out = []
        for v in sorted(vals):
            if not out or v - out[-1] > tol:
                out.append(v)
        return out
    xs = uniq([0.0, float(W)] + [p[1] for p in parts if p[0] == "v" and (p[3] - p[2]) >= 50])
    ys = uniq([0.0, float(H)] + [p[1] for p in parts if p[0] == "h" and (p[3] - p[2]) >= 50])
    if len(xs) * len(ys) > 40000:
        return None, 0

    def cover(kind, c, a, b):
        tot = []
        for p in parts:
            if p[0] == kind and abs(p[1] - c) <= tol * 1.5 and p[3] > a and p[2] < b:
                tot.append((max(p[2], a), min(p[3], b)))
        tot.sort()
        s_, e_, acc = None, None, 0.0
        for x, y in tot:
            if e_ is None or x > e_:
                if e_ is not None:
                    acc += e_ - s_
                s_, e_ = x, y
            else:
                e_ = max(e_, y)
        if e_ is not None:
            acc += e_ - s_
        return acc / max(b - a, 1e-9)

    nx, ny = len(xs) - 1, len(ys) - 1
    # 얇은 칸(창틀)은 합친 다음 덩어리 크기로 판단한다 (격자 간격만 보고 버리면 큰 유리가 잘림)
    ok = [[True for j in range(ny)] for i in range(nx)]
    parent = {}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for i in range(nx):
        for j in range(ny):
            if ok[i][j]:
                parent[(i, j)] = (i, j)
    for i in range(nx):
        for j in range(ny):
            if not ok[i][j]:
                continue
            if i + 1 < nx and ok[i + 1][j] and cover("v", xs[i + 1], ys[j], ys[j + 1]) < 0.9:
                union((i, j), (i + 1, j))
            if j + 1 < ny and ok[i][j + 1] and cover("h", ys[j + 1], xs[i], xs[i + 1]) < 0.9:
                union((i, j), (i, j + 1))
    regions = defaultdict(list)
    for c in parent:
        regions[find(c)].append(c)
    total, n = 0.0, 0
    for cells in regions.values():
        i0 = min(c[0] for c in cells); i1 = max(c[0] for c in cells)
        j0 = min(c[1] for c in cells); j1 = max(c[1] for c in cells)
        # 덩어리가 바깥 테두리를 뚫고 나가면(선이 없는 빈 공간) 유리로 보지 않음
        if (i1 - i0 + 1) * (j1 - j0 + 1) != len(cells):
            continue
        w, h = xs[i1 + 1] - xs[i0], ys[j1 + 1] - ys[j0]
        if w < min_w or h < min_w:
            continue
        if min(cover("v", xs[i0], ys[j0], ys[j1 + 1]), cover("v", xs[i1 + 1], ys[j0], ys[j1 + 1]),
               cover("h", ys[j0], xs[i0], xs[i1 + 1]), cover("h", ys[j1 + 1], xs[i0], xs[i1 + 1])) < 0.6:
            continue
        total += 2 * (w + h) / 1000
        n += 1
    return (total if n else None), n


def compute(items, final_qty, rules=None):
    """반환: (요약 rows, 부호별 상세 rows)"""
    R = rules or load_rules()
    summ = defaultdict(float)
    detail = []
    for it in items:
        q = final_qty.get(it.code, (it.qty,))[0] if final_qty else it.qty
        if not q or not it.width or not it.height:
            continue
        W, H = it.width, it.height
        per = 2 * (W + H) / 1000
        spec = " ".join(x for x in (getattr(it, "spec_text", ""), it.frame, it.glass, it.hardware) if x)
        row = {"부호": it.code, "W": W, "H": H, "수량": q, "둘레(m/개)": round(per, 3)}
        for key in ("창호주위코킹", "창호주위충전"):
            r = R[key]
            if it.type not in r.get("제외종류", []):
                v = per * r.get("면수", 1) * q
                summ[f"{key} (m)"] += v
                row[key] = round(v, 2)
        g = glass_label(it.glass or spec)
        layers = glass_layers(it)
        if layers > 1:
            row["유리겹"] = layers
        if it.type not in R["유리"].get("제외종류", []) and g:
            a = W * H / 1e6 * q * layers
            summ[f"유리 {g} (m2)"] += a
            row["유리"] = f"{g} {a:.2f}m2"
        pp, n = pane_perimeter(it, min_w=R["유리주위코킹"].get("최소유리폭", 150))
        if pp and it.type not in R["유리"].get("제외종류", []):
            v = pp * R["유리주위코킹"].get("면수", 2) * q * layers
            summ["유리주위코킹 (m)"] += v
            row["유리칸"] = n
            row["유리주위코킹"] = round(v, 2)
        hw = hardware_counts(it.hardware or spec)
        RH = R["철물"]
        if re.search(r"D|ST", it.type) and it.type not in RH.get("제외종류", []):
            lv = leaves(it, RH.get("양개문폭", 1500))
            if it.type in RH.get("기본적용종류", []):
                for k, n in RH.get("문짝당기본", {}).items():
                    hw[k] = max(hw.get(k, 0), n * lv)
            if "피봇힌지" in hw:
                hw["피봇힌지"] *= RH.get("피봇힌지배수", 1)
            row["문짝"] = lv
        if hw and re.search(r"D|ST", it.type) and it.type not in R["철물"].get("제외종류", []):
            for k, v in hw.items():
                summ[f"{k} (조)"] += v * q
            row["철물"] = ", ".join(f"{k}{v:g}" for k, v in hw.items())
        detail.append(row)
    return dict(summ), detail


def match_estimate(summ, est):
    """파생 항목별로 견적서의 해당 품목들을 찾아 [(항목, 프로그램값, [(품목,규격,단위,수량)], 견적합)]
    유리는 1차 두께로, 못 찾으면 2차로 종류(삼중/복층/강화/접합) 글자로 짝짓는다."""
    glass_est = [e for e in est if e.unit.upper() in ("M2", "㎡") and "유리" in (e.section + e.name)
                 and not re.search(r"설치|끼우기|닦기|필름|장비|난간|유리문$", e.name)]
    gkeys = [k for k in summ if k.startswith("유리 ") and k.endswith("(m2)")]
    # 두께별로 묶어 한 줄로 비교 (같은 두께의 견적 품목이 여러 줄에 중복으로 붙지 않게)
    groups = {}
    for k in gkeys:
        th = re.match(r"유리\s+(\d+(?:\.\d+)?)T", k)
        groups.setdefault(th.group(1) if th else k, []).append(k)
    found, used = {}, set()
    for g, ks in groups.items():
        if re.match(r"^\d", g):
            lst = [e for e in glass_est if re.search(r"(?<!\d)%s(?:\.\d+)?\s*(?:T|t|mm|MM)|THK\s*%s|T\s*=\s*%s" % (g, g, g), e.name + " " + e.spec)]
            if lst:
                found[g] = lst
                used.update(id(e) for e in lst)
    def _th(e):
        m = re.search(r"(?<!\d)(\d{1,2})(?:\.\d+)?\s*(?:T|t|mm|MM)(?![A-Za-z])|THK\s*(\d{1,2})|T\s*=\s*(\d{1,2})", e.name + " " + e.spec)
        return float(next(x for x in m.groups() if x)) if m else None
    note = {}
    # 2차: 두께가 정확히 같은 견적이 없으면, 종류(복층/강화…)가 같고 두께가 ±4mm 안인 견적을 가까운 순서로 붙인다.
    #      (도면 26T 복층 ↔ 견적 THK24 복층 같은 개정 차이) 붙인 줄에는 '견적 두께 다름'을 적어 둔다.
    for g, ks in sorted(groups.items(), key=lambda kv: not re.match(r"^\d", kv[0])):
        if g in found:
            continue
        kinds = {x for k in ks for x in ("삼중", "복층", "강화", "접합", "로이") if x in k}
        lst = [e for e in glass_est if id(e) not in used and any(x in e.name + e.spec for x in kinds)]
        if re.match(r"^\d", g):
            gt = float(g)
            lst = sorted([e for e in lst if _th(e) is None or abs(_th(e) - gt) <= 4], key=lambda e: abs((_th(e) or gt) - gt))
            ths = sorted({_th(e) for e in lst if _th(e) is not None and _th(e) != gt})
            if ths:
                note[g] = "견적 두께 다름: " + ", ".join(f"THK{t:g}" for t in ths)
        if lst:
            found[g] = lst
            used.update(id(e) for e in lst)
    rows = []
    for k in summ:
        if k in gkeys:
            continue
        pat = EST_MATCH.get(k)
        lst = [e for e in est if pat and re.search(pat, e.name) and e.qty is not None and "설치" not in e.name]
        rows.append((k, summ[k], [(e.name, e.spec, e.unit, e.qty) for e in lst],
                     sum(e.qty for e in lst if e.qty) if lst else None))
    for g, ks in groups.items():
        lst = found.get(g, [])
        name = (f"유리 {g}T 계 (m2)" if re.match(r"^\d", g) else ks[0]) + (f"  [{', '.join(k[3:-5] for k in ks)}]" if len(ks) > 1 else "")
        if g in note:
            name += f"  ({note[g]})"
        rows.append((name, sum(summ[k] for k in ks), [(e.name, e.spec, e.unit, e.qty) for e in lst],
                     sum(e.qty for e in lst if e.qty) if lst else None))
    # 어느 줄에도 붙지 못한 견적 유리는 따로 보여 준다 (조용히 사라지면 비교가 왜곡된다)
    left = [e for e in glass_est if id(e) not in used and e.qty]
    if left:
        rows.append(("유리 (견적에만 있음) (m2)", None, [(e.name, e.spec, e.unit, e.qty) for e in left], sum(e.qty for e in left)))
    return rows
