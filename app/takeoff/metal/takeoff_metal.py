"""금속 물량 1차.

1) 외벽 하지철물 전개도: 페이지 범례(MEMBER LIST의 C1~C4 선 모양)를 읽어 같은 모양 선 길이를 합산
2) 보 감싸기 안내도: 범례 A형 선 모양의 길이를 층별로 합산
3) 창호에서 바로 나오는 금속: 외부그릴창 아연도강판(AG 부호), 도어클로저·접지 보강철물(문짝 수)
모든 길이는 도면의 이어진 치수로 구한 축척(mm/pt)으로 환산한다.
"""
import re
from collections import defaultdict

import pymupdf

from ..pdf_source import load_page
from .lines import build_runs, estimate_scale_any, merge_pairs, page_segments


def _legend_styles(sheet, runs, labels):
    """범례 라벨(C1, A ...) 오른쪽의 견본 선 → {라벨: (굵기, 점선길이, 간격, 규격글자)}"""
    out = {}
    # 범례 표는 보통 오른쪽에 있으므로 오른쪽 라벨부터 본다 (도면 안 상세 표시 글자 제외)
    for t in sorted(sheet.texts, key=lambda t: -t.x0):
        if t.text not in labels or t.text in out:
            continue
        rs = [r for r in runs if r.orient == "h" and abs(r.c - t.cy) < 4 and t.x1 + 2 <= r.a <= t.x1 + 50 and r.length > 8 and r.n >= 2]
        if not rs:
            continue
        r = max(rs, key=lambda r: r.length)
        size = [u.text for u in sheet.texts if abs(u.cy - t.cy) < 3.5 and u.x0 > r.b + 2 and re.search(r"\d", u.text)]
        out[t.text] = (r.width, r.dash, r.gap, r.color, size[0] if size else "")
    return out


def _match(r, sty, dash_tol=0.45, gap_tol=0.6):
    w, dash, gap, col, _ = sty
    if abs(r.width - w) > 0.01 or tuple(round(v, 2) for v in r.color) != tuple(round(v, 2) for v in col):
        return False
    if r.n < 2 or r.gap < 0.3:
        return False
    # 점선 간격은 출력 위치(위상)에 따라 흔들리므로 넓게 본다
    return abs(r.dash - dash) <= dash * dash_tol and 0.3 <= r.gap <= max(gap * 2.5, 1.5)


def _classify(runs, styles):
    out = defaultdict(list)
    for r in runs:
        best = None
        for lab, sty in styles.items():
            if _match(r, sty):
                d = abs(r.dash - sty[1]) / sty[1]
                if best is None or d < best[0]:
                    best = (d, lab)
        if best:
            out[best[1]].append(r)
    return out


def _drawing_halves(sheet):
    """한 장에 도면이 둘 이상이면 제목 글자(…도)의 x 위치로 나눈다."""
    W = sheet.width
    legend_x = min([t.x0 for t in sheet.texts if t.text in ("MEMBER LIST", "■ 보 감싸기 범례") or "범례" in t.text] or [W * 0.83])
    titles = sorted([t for t in sheet.texts if re.search(r"(전개도|안내도)", t.text) and t.size > 8 and t.cx < legend_x],
                    key=lambda t: t.cx)
    if len(titles) <= 1:
        return [(0, legend_x, titles[0].text if titles else sheet.title)]
    xs = [t.cx for t in titles]
    cuts = [0] + [(a + b) / 2 for a, b in zip(xs, xs[1:])] + [legend_x]
    return [(cuts[i], cuts[i + 1], titles[i].text) for i in range(len(titles))]


def facade_framing(doc, path, pages):
    rows = []
    for i in pages:
        sh = load_page(doc, i, path)
        runs = build_runs(page_segments(doc[i]))
        sc, n = estimate_scale_any(sh.texts)
        if not sc:
            continue
        styles = _legend_styles(sh, runs, {"C1", "C2", "C3", "C4"})
        for x0, x1, ttl in _drawing_halves(sh):
            part = [r for r in runs if x0 <= r.a and r.b <= x1]
            for lab, lst in _classify(part, styles).items():
                m = merge_pairs(lst, 160 / sc)
                L = sum(r.length for r in m) * sc / 1000
                rows.append({"도면": f"p.{i + 1} {ttl}", "부재": lab, "규격": styles[lab][4], "길이(m)": L, "축척": sc})
    return rows


def beam_encasing(doc, path, pages):
    rows = []
    for i in pages:
        sh = load_page(doc, i, path)
        runs = build_runs(page_segments(doc[i]), max_gap=4.5)
        sc, n = estimate_scale_any(sh.texts)
        if not sc:
            continue
        styles = _legend_styles(sh, runs, {"A", "B"})
        x_leg = min([t.x0 for t in sh.texts if "범례" in t.text] or [sh.width * 0.83])
        part = [r for r in runs if r.b < x_leg]
        for lab, lst in _classify(part, styles).items():
            m = merge_pairs(lst, 3.5)
            rows.append({"도면": f"p.{i + 1} {sh.title}", "부재": f"{lab}형 표시선", "규격": "",
                         "길이(m)": sum(r.length for r in m) * sc / 1000, "축척": sc})
    return rows


def find_pages(doc, path):
    fr, be = [], []
    for i in range(len(doc)):
        t = load_page(doc, i, path).title.replace(" ", "")
        if "하지철물" in t and "전개도" in t:
            fr.append(i)
        elif "감싸기" in t and "안내도" in t:
            be.append(i)
    return fr, be


def window_metal(items, final_qty=None):
    """창호 일람표에서 바로 나오는 금속 항목."""
    from ..derived import leaves
    rows = []
    grills = defaultdict(float)
    nleaf = 0
    for it in items:
        q = (final_qty or {}).get(it.code, (it.qty,))[0]
        if not q:
            continue
        if it.type == "AG" and it.width and it.height and "그릴" in (it.name + it.frame + it.glass):
            grills[f"{it.width}*{it.height}"] += q
        if it.type in ("FSD", "SD"):
            nleaf += leaves(it) * q
    for size, q in sorted(grills.items()):
        rows.append({"항목": "외부그릴창 아연도강판", "규격": size, "단위": "EA", "수량": q, "근거": "AG 부호 크기·수량"})
    if nleaf:
        rows.append({"항목": "도어클로저 보강철물", "규격": "", "단위": "EA", "수량": nleaf, "근거": "여닫이문(FSD·SD) 문짝 수"})
        rows.append({"항목": "도어접지용 보강철물", "규격": "", "단위": "EA", "수량": nleaf, "근거": "여닫이문(FSD·SD) 문짝 수"})
    return rows


def run_metal_pdf(path, items=None, final_qty=None, log=print, rules=None):
    doc = pymupdf.open(path)
    fr, be = find_pages(doc, path)
    log(f"금속: 하지철물 전개도 {len(fr)}장, 보 감싸기 안내도 {len(be)}장")
    framing = facade_framing(doc, path, fr)
    beams = beam_encasing(doc, path, be)
    wm = window_metal(items or [], final_qty)
    wm += table_items(doc, path, rules)
    wm += stair_handrail(doc, path, rules)
    return framing, beams, wm


def _size_key(s):
    m = re.search(r"(\d{2,3})\s*[x*×]\s*(\d{2,3})", s or "")
    return f"{m.group(1)}x{m.group(2)}" if m else None


def summary_rows(framing, beams, wm, est=None):
    """금속물량 시트 행: (구분, 항목, 규격, 단위, 프로그램 값, 견적 품목, 견적 수량, 근거)"""
    est = est or []
    rows = []
    # 하지철물: 같은 규격(예: ㅁ-100x50 수평재+수직재)끼리 합쳐 견적과 비교
    by = defaultdict(lambda: defaultdict(float))
    labs = defaultdict(set)
    names = {}
    for r in framing:
        side = "외장재" if "외장재" in r["도면"] else "외벽"
        k = _size_key(r["규격"]) or r["부재"]
        by[k][side] += r["길이(m)"]
        labs[k].add(r["부재"])
        names[k] = re.sub(r"\(.*?\)", "", r["규격"]).strip()
    est_fr = [e for e in est if "하지철물" in e.name and e.unit.upper() == "M" and not re.match(r"\s*L-", e.spec)]
    for k, d in sorted(by.items()):
        tot = sum(d.values())
        ms = [e for e in est_fr if _size_key(e.spec) == k]
        rows.append(("외벽하지철물", "+".join(sorted(labs[k])) + " 부재", names[k], "M", round(tot, 2),
                     " / ".join(f"{e.name} {e.spec[:20]}" for e in ms), sum(e.qty or 0 for e in ms) if ms else None,
                     "전개도 범례 선 모양별 길이 (" + ", ".join(f"{s} {v:.0f}m" for s, v in d.items()) + ")"))
    if framing:
        allm = sum(r["길이(m)"] for r in framing)
        rows.append(("외벽하지철물", "각관 부재 길이 합계", "", "M", round(allm, 2), "하지철물 각관(M) 합계",
                     sum(e.qty or 0 for e in est_fr) if est_fr else None, "모든 각관 부재 합 (앵글 간격재 제외)"))
    # 보 감싸기
    if beams:
        L = sum(r["길이(m)"] for r in beams)
        eb = [e for e in est if "감싸기" in e.name and e.unit.upper() in ("M2", "㎡")]
        ea = sum(e.qty or 0 for e in eb) if eb else None
        rows.append(("보 감싸기", "보 감싸기 구간 길이", "", "M", round(L, 2),
                     " / ".join(e.name for e in eb), ea,
                     ("층별: " + ", ".join(f"{r['도면'].split(' ',1)[1][:8]} {r['길이(m)']:.0f}m" for r in beams)) +
                     (f"  ※ 견적 면적/길이 = 평균 둘레 {ea / L:.2f}m" if ea and L else "")))
    # 계단 핸드레일 (DXF 기하): 계단실이 여럿이고 견적 줄이 계단실별로 안 나뉘면 합계 줄로 비교
    stair = [r for r in wm if r["항목"] == "계단 핸드레일"]
    if stair:
        # 견적 줄: '계단'이 붙은 난간/핸드레일, 또는 자리 말 없는 그냥 '핸드레일'(작은 건물에선 계단 것)
        est_st = [e for e in est if e.unit.upper() == "M"
                  and not re.search(r"벽부|램프|경사로|발코니|옥상|선큰|복도|장애", e.name + " " + e.spec)
                  and ((re.search(r"계[단딘]", e.name + " " + e.spec) and re.search(r"핸드레일|난간|HAND\s*RAIL", e.name + " " + e.spec, re.I))
                       or re.search(r"핸드레일|HAND\s*RAIL", e.name, re.I))]
        used = set()
        for r in stair:
            ms = []
            m = re.search(r"계단실\s*-?\s*(\d)", r["규격"] or "")
            if m:
                ms = [e for e in est_st if re.search(r"계단실\s*-?\s*" + m.group(1) + r"(?!\d)", e.name)]
            elif len(stair) == 1:
                ms = est_st
            used.update(id(e) for e in ms)
            rows.append(("계단", r["항목"], r["규격"], r["단위"], r["수량"],
                         " / ".join(f"{e.name} {e.spec[:16]}" for e in ms), sum(e.qty or 0 for e in ms) if ms else None, r["근거"]))
        if len(stair) > 1:
            tot = sum(r["수량"] or 0 for r in stair)
            rows.append(("계단", "계단 핸드레일 합계", f"{len(stair)}개 계단", "M", round(tot, 1),
                         " / ".join(f"{e.name} {e.spec[:16]}" for e in est_st), sum(e.qty or 0 for e in est_st) if est_st else None,
                         "계단실별 길이 합 (견적이 계단실별로 안 나뉘면 이 줄로 비교)"))
    # 창호에서 나오는 금속
    for r in wm:
        if r["항목"] == "계단 핸드레일":
            continue
        if r.get("구분") in ("재료분리대", "트렌치"):
            # v25 트렌치·재료분리대: 견적 품목과 짝짓지 않는다(견적 칸 비움). 수량 None(길이 미산출·세지 않음)은 그대로 둔다.
            rows.append((r["구분"], r["항목"], r.get("규격", ""), r.get("단위", ""), r.get("수량"), "", None, r.get("근거", "")))
            continue
        if r["항목"].startswith("외부그릴창"):
            ms = [e for e in est if "그릴창" in e.name and _size_key(e.spec) == _size_key(r["규격"].replace("*", "x"))]
        elif r["항목"].startswith("스테인리스핸드레일"):
            ms = [e for e in est if e.name.replace(" ", "") == r["항목"].replace(" ", "") and e.unit.upper() == "M"
                  and "900" in e.spec]
        elif r.get("견적패턴"):
            cand = [e for e in est if re.search(r["견적패턴"], e.name + " " + e.spec) and e.qty]
            ms = [e for e in cand if e.unit.upper() == r["단위"].upper()]
            if cand and not ms:          # 견적은 길이(M)로 잡은 품목: 개수와 비교하지 않고 이름만 보여 줌
                r = dict(r, 근거=r["근거"] + " / 견적은 " + ", ".join(f"{e.name} {e.qty:g}{e.unit}" for e in cand[:3]) + " (단위 달라 비교 안 함)")
            elif ms:
                # 글자 수는 참고값이다. 견적 수량과 2배 넘게 다르면 같은 품목이 아니다(우편함 글자 1 vs 세대 우편함 98, 점검구 1 vs 점검구용 키 90)
                tot = sum(e.qty or 0 for e in ms)
                if not tot or not (0.5 <= (r["수량"] or 0) / tot <= 2):
                    r = dict(r, 근거=r["근거"] + " / 견적의 비슷한 이름: " + ", ".join(f"{e.name} {e.qty:g}{e.unit}" for e in ms[:3]) + " (수량이 크게 달라 비교 안 함)")
                    ms = []
        elif r["항목"].startswith("케이블트렌치") or r["항목"].startswith("집수정"):
            word = {"케이블트렌치 뚜껑": r"트[렌랜]치\s*뚜껑\s*설치", "케이블트렌치 뚜껑 휨방지앵글": r"휨방지",
                    "케이블트렌치 뚜껑 마구리앵글": r"마구리", "케이블트렌치 뚜껑 철재손잡이": r"손잡이",
                    "집수정 그레이팅 커버": r"집수정.*커버"}[r["항목"]]
            ms = [e for e in est if re.search(word, e.name) and ("트" in e.name or "집수정" in e.name)]
        else:
            key = "도어클러져|도어클로저" if "클로저" in r["항목"] else "접지"
            ms = [e for e in est if re.search(key, e.name) and "보강" in e.name]
        grp = ("계단" if r["항목"].startswith(("스테인리스핸드레일", "계단 핸드레일")) else
               "도면 글자 집계(참고)" if r.get("견적패턴") else ("도면 수량표" if "수량표" in r["근거"] else "창호 연계 금속"))
        rows.append((grp, r["항목"], r["규격"], r["단위"], r["수량"],
                     " / ".join(f"{e.name} {e.spec[:16]}" for e in ms), sum(e.qty or 0 for e in ms) if ms else None, r["근거"]))
    return rows


COLORS = {"C1": (0.9, 0.1, 0.1), "C2": (0.0, 0.6, 0.0), "C3": (0.1, 0.2, 0.95), "C4": (1.0, 0.55, 0.0),
          "A": (0.85, 0.0, 0.7), "B": (0.0, 0.6, 0.7)}


def write_metal_review(path, out_pdf, log=print):
    """하지철물 전개도·보 감싸기 안내도 원본 위에 부재별 색 선을 덧그린 검토 PDF."""
    doc = pymupdf.open(path)
    fr, be = find_pages(doc, path)
    out = pymupdf.open()
    for i in fr + be:
        sh = load_page(doc, i, path)
        runs = build_runs(page_segments(doc[i]), max_gap=4.5 if i in be else 4.0)
        labels = {"C1", "C2", "C3", "C4"} if i in fr else {"A", "B"}
        styles = _legend_styles(sh, runs, labels)
        x_leg = min([t.x0 for t in sh.texts if t.text == "MEMBER LIST" or "범례" in t.text] or [sh.width * 0.83])
        cls = _classify([r for r in runs if r.b < x_leg], styles)
        out.insert_pdf(doc, from_page=i, to_page=i)
        p = out[-1]
        inv = ~p.rotation_matrix          # 화면좌표 → 페이지 원래 좌표
        for lab, lst in cls.items():
            shp = p.new_shape()
            for r in lst:
                a, b = ((r.a, r.c), (r.b, r.c)) if r.orient == "h" else ((r.c, r.a), (r.c, r.b))
                shp.draw_line(pymupdf.Point(a) * inv, pymupdf.Point(b) * inv)
            shp.finish(color=COLORS.get(lab, (1, 0, 1)), width=1.0)
            shp.commit()
        # 색 범례
        y = 20
        for lab, sty in styles.items():
            p.insert_text(pymupdf.Point(20, y) * inv, f"{lab} {sty[4]}", fontname="korea", fontsize=9,
                          color=COLORS.get(lab, (1, 0, 1)), rotate=p.rotation)
            y += 12
    out.save(out_pdf, garbage=3, deflate=True)
    log(f"금속 검토도면 저장: {out_pdf}")
    return out_pdf


def table_items(doc, path, rules=None):
    """도면 안 수량표 → 금속 항목 행.
    트렌치 커버 표: 뚜껑 면적(Σ X·Y·n), 휨방지 앵글(Σ X·n), 마구리 앵글(면수×Σ Y·n), 손잡이(n×개당)
    집수정 커버 표: 개수"""
    from .tables import read_tables
    R = (rules or {}).get("트렌치", {"손잡이_개당": 2, "마구리앵글_면수": 2})
    rows = []
    for i in range(len(doc)):
        sh = load_page(doc, i, path)
        if not any(k in sh.title for k in ("그레이팅", "트렌치", "상세", "집수정")):
            continue
        for tb in read_tables(sh):
            title = tb["title"]
            recs = tb["rows"]
            n = sum(r["개수"] for r in recs)
            src = f"p.{i + 1} {sh.title} 수량표"
            if "트렌치" in title or "트렌치" in " ".join(tb["header"]) or any("GIB" in r["위치"] for r in recs):
                area = sum(r["X"] * r["Y"] * r["개수"] for r in recs) / 1e6
                bend = sum(r["X"] * r["개수"] for r in recs) / 1000
                edge = R.get("마구리앵글_면수", 2) * sum(r["Y"] * r["개수"] for r in recs) / 1000
                floors = sorted({r["위치"] for r in recs})
                rows += [
                    {"항목": "케이블트렌치 뚜껑", "규격": "무늬강판", "단위": "M2", "수량": round(area, 2),
                     "근거": f"{src}: Σ 크기×개수 ({len(recs)}종 {n:g}개, {', '.join(floors)})"},
                    {"항목": "케이블트렌치 뚜껑 휨방지앵글", "규격": "", "단위": "M", "수량": round(bend, 2),
                     "근거": f"{src}: Σ 뚜껑 폭(X)×개수"},
                    {"항목": "케이블트렌치 뚜껑 마구리앵글", "규격": "", "단위": "M", "수량": round(edge, 2),
                     "근거": f"{src}: 트렌치 길이(Σ Y×개수)×{R.get('마구리앵글_면수', 2)}면"},
                    {"항목": "케이블트렌치 뚜껑 철재손잡이", "규격": "", "단위": "EA", "수량": n * R.get("손잡이_개당", 2),
                     "근거": f"{src}: 뚜껑 {n:g}개 × {R.get('손잡이_개당', 2)}개"},
                ]
            elif "집수정" in title:
                for r in recs:
                    rows.append({"항목": "집수정 그레이팅 커버", "규격": f"{r['X']}*{r['Y']}", "단위": "EA", "수량": r["개수"],
                                 "근거": f"{src}: {r['위치']}"})
    return rows


RISE_RE = re.compile(r"\(?\s*(\d{3}(?:\.\d+)?)\s*[Xx×]\s*(\d{1,2})\s*R\s*\)?")
TREAD_RE = re.compile(r"\(?\s*(\d{3})\s*[Xx×]\s*(\d{1,2})\s*T\s*\)?")
LEVEL_RE = re.compile(r"GL\s*([+\-±])\s*([\d,]+)")


def stair_handrail(doc, path, rules=None):
    """계단 단면도 → 계단 난간 길이.
    전체 높이(계단이 걸친 최저~최고 층 레벨) × 경사계수(√(단너비·(단수-1))²+(단높이·단수)² / 판 높이)
    + 계단참 꺾임 길이 × 판 수."""
    import statistics
    R = (rules or {}).get("계단난간", {"꺾임길이_m": 0.4})
    rows = []
    for i in range(len(doc)):
        sh = load_page(doc, i, path)
        t = sh.title.replace(" ", "")
        if "계단" not in t or "단면" not in t:
            continue
        rises = [(float(m.group(1)), int(m.group(2)), x) for x in sh.texts for m in [RISE_RE.search(x.text)] if m]
        treads = [(float(m.group(1)), int(m.group(2))) for x in sh.texts for m in [TREAD_RE.search(x.text)] if m]
        levels = []
        for x in sh.texts:
            m = LEVEL_RE.search(x.text)
            if m:
                v = float(m.group(2).replace(",", "")) * (-1 if m.group(1) == "-" else 1)
                levels.append((v, x))
        if not rises or not treads or len(levels) < 2:
            continue
        # 계단이 걸친 범위: 가장 위 단높이 글자 바로 위 레벨 ~ 가장 아래 단높이 글자 바로 아래 레벨 (도면 반쪽마다)
        tops, bots = [], []
        for half in (lambda x: x.cx < sh.width / 2, lambda x: x.cx >= sh.width / 2):
            rs = [r for r in rises if half(r[2])]
            lv = [l for l in levels if half(l[1])]
            if not rs or not lv:
                continue
            ytop = min(r[2].cy for r in rs)
            ybot = max(r[2].cy for r in rs)
            above = [l for l in lv if l[1].cy <= ytop]
            below = [l for l in lv if l[1].cy >= ybot]
            if above:
                tops.append(max(above, key=lambda l: l[1].cy)[0])
            if below:
                bots.append(min(below, key=lambda l: l[1].cy)[0])
        if not tops or not bots:
            continue
        H = (max(tops) - min(bots)) / 1000
        h = statistics.median(r[0] for r in rises)
        n = statistics.median(r[1] for r in rises)
        tw = statistics.median(t_[0] for t_ in treads)
        flight_rise = h * n / 1000
        flight_slope = ((tw * (n - 1)) ** 2 + (h * n) ** 2) ** 0.5 / 1000
        k = flight_slope / flight_rise
        flights = round(H / flight_rise)
        turn = R.get("꺾임길이_m", 0.4)
        L = H * k + flights * turn
        rows.append({"항목": "스테인리스핸드레일/계단실", "규격": "계단난간", "단위": "M", "수량": round(L, 2),
                     "근거": f"p.{i + 1} {sh.title}: 높이 {H:.2f}m(GL{min(bots):+,.0f}~{max(tops):+,.0f}) × 경사계수 {k:.3f}"
                             f" (단높이 {h:.1f}×{n:g}단, 단너비 {tw:g}) + 계단참 꺾임 {turn}m × {flights}판 [수량표]"})
    return rows
