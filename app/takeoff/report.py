"""결과 엑셀: 창호물량 / 교차검증 / 견적비교 시트."""
import re
from collections import Counter, defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .tags import floors_matching

THIN = Side(style="thin", color="999999")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD = PatternFill("solid", fgColor="DCE6F1")
OK = PatternFill("solid", fgColor="E2EFDA")
WARN = PatternFill("solid", fgColor="FFF2CC")
BAD = PatternFill("solid", fgColor="F8CBAD")
FONT = "맑은 고딕"


def _sheet(wb, title, headers, widths):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for c in ws[1]:
        c.fill, c.font, c.border = HEAD, Font(name=FONT, bold=True), BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    return ws


def _style_rows(ws):
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.border = BOX
            c.font = Font(name=FONT, size=10)
            c.alignment = Alignment(vertical="center", wrap_text=isinstance(c.value, str) and len(c.value) > 20)


def cross_check(items, plan_counts, plan_notes):
    """일람표 계 vs 평면도 태그 수. 반환: code -> (plan_total, status, memo)."""
    out = {}
    for it in items:
        per = plan_counts.get(it.code, Counter())
        tot = sum(per.values())
        floors_with_tag = len([f for f, v in per.items() if v])
        sq = getattr(it, "sched_qty", it.qty)            # 일람표 원래 수량 (최종 채택값이 아니라)
        if sq is None and tot:
            # 일람표에 수량을 안 적는 도면이 많다 → 평면도 집계가 정상 경로. '확인'은 정말 볼 것에만 쓴다
            out[it.code] = (tot, "평면도 집계", "일람표에 수량 표기 없음")
        elif sq is None:
            out[it.code] = (tot, "확인", "일람표·평면도 어디에도 수량 없음")
        elif tot == sq:
            out[it.code] = (tot, "일치", "")
        elif tot == 0:
            memo = "평면도에 부호 없음"
            if plan_notes:
                memo += f" (도면 주석: {plan_notes[0]})"
            out[it.code] = (tot, "평면도 미표기", memo + " → 일람표 수량 채택")
        elif tot > sq and floors_with_tag > 1 and \
                ((it.type in ("ACW", "CW", "SCW") and (it.height or 3000) >= 3000) or max(it.width or 0, it.height or 0) >= 10000):
            out[it.code] = (tot, "다층 연속", f"여러 층 평면에 반복 표기({floors_with_tag}개 층) - 한 덩어리 커튼월로 판단, 일람표 수량 채택")
        else:
            out[it.code] = (tot, "불일치", "층별 수량 확인 필요")
    return out


def _size_eq(it, e, tol=60):
    if not e.size_mm or not it.width or not it.height:
        return None
    ew, eh = e.size_mm
    return (abs(ew - it.width) <= tol and abs(eh - it.height) <= tol) or \
           (abs(eh - it.width) <= tol and abs(ew - it.height) <= tol)


def match_estimate(items, est):
    """부호 → 견적 항목.
    1차: 부호가 같고 크기도 맞음(또는 견적에 크기 없음)
    2차: 같은 종류 안에서 크기가 맞는 다른 번호('번호 어긋남') — 수량까지 같거나 같은 부호가 견적에 없을 때만
    3차: 부호만 같음 (크기 불일치 그대로 표시)
    끝으로 견적의 접미 글자 부호(FSD-1A)를 합쳐 도면 수량과 같으면 합산."""
    by_code = {}
    for e in est:
        if e.code and e.code not in by_code:
            by_code[e.code] = e
    from .schedule import code_variants
    matched, used = {}, set()
    for it in items:
        for v in code_variants(it.code):
            e = by_code.get(v)
            if e and id(e) not in used and _size_eq(it, e) in (True, None):
                matched[it.code] = (e, "부호" if v == it.code else f"부호 (표기 차이: 견적 {v})")
                used.add(id(e))
                break
    # 2차: 번호 어긋남 — 같은 종류(PD↔PD) 안에서 크기가 맞는 다른 번호. (견적서 개정으로 번호가 밀린 현장: E현장·G현장)
    #   남의 짝을 빼앗지 않도록: 수량까지 같거나, 이 부호와 같은 부호가 견적에 아예 없을 때만.
    #   (종류가 다른 것끼리는 안 붙인다: L현장 PD-1↔ASD-2. 크기만 같고 같은 부호가 견적에 있으면 3차로: O현장 AW-4↔AW-2)
    def _typ(code):
        m = re.match(r"^(?:.*·)?([A-Z]+)", code or "")
        return m.group(1) if m else ""
    def _own(it):
        return next((by_code[v] for v in code_variants(it.code) if v in by_code and id(by_code[v]) not in used), None)
    pool = [e for e in est if e.code and id(e) not in used]
    for it in items:
        if it.code in matched or not it.width:
            continue
        cands = [e for e in pool if id(e) not in used and _typ(e.code) == _typ(it.code) and _size_eq(it, e)]
        exact = [e for e in cands if e.qty == it.qty]
        pick = exact or ([] if _own(it) is not None else cands)
        if pick:
            e = pick[0]
            how = f"번호 어긋남: 견적 {e.code} ↔ 도면 {it.code} (크기{'·수량' if exact else ''} 일치)"
            matched[it.code] = (e, how)
            used.add(id(e))
    # 3차: 같은 부호인데 크기가 다름
    for it in items:
        if it.code in matched:
            continue
        for v in code_variants(it.code):
            e = by_code.get(v)
            if e and id(e) not in used:
                matched[it.code] = (e, "부호 (크기 다름)")
                used.add(id(e))
                break
    # 견적이 한 부호를 접미 글자로 나눠 적은 경우(FSD-1 64 + FSD-1A 58 = 도면 FSD-1 122): 합이 도면 수량과 같으면 합쳐서 비교
    import copy
    have = {it.code for it in items}
    for it in items:
        if it.code not in matched or it.qty is None:
            continue
        e, how = matched[it.code]
        if e.qty is None or e.qty == it.qty:
            continue
        subs = [x for x in est if x.code and id(x) not in used and x.code not in have
                and re.fullmatch(re.escape(e.code) + r"-?[A-Z]", x.code) and x.qty]
        if subs and e.qty + sum(x.qty for x in subs) == it.qty:
            m = copy.copy(e)
            m.qty = e.qty + sum(x.qty for x in subs)
            matched[it.code] = (m, how + f" + 견적 {', '.join(x.code for x in subs)} 합산({e.qty:g}+{'+'.join(f'{x.qty:g}' for x in subs)})")
            for x in subs:
                used.add(id(x))
    leftovers = [e for e in est if e.code and id(e) not in used]
    return matched, leftovers


def size_status(it, e):
    if not e.size_mm or not it.width or not it.height:
        return "확인", ""
    ew, eh = e.size_mm
    tol = 60
    if abs(ew - it.width) <= tol and abs(eh - it.height) <= tol:
        return "일치", ""
    if abs(eh - it.width) <= tol and abs(ew - it.height) <= tol:
        return "일치", "견적서는 H×W 순서로 표기"
    return "차이", f"도면 {it.width}×{it.height} / 견적 {ew}×{eh}"


def write_report(path, items, plan_counts, plan_notes, est, meta, derived=None, metal=None):
    wb = Workbook()
    wb.remove(wb.active)

    # 1. 창호물량
    floors = []
    for it in items:
        for k in it.qty_by_floor:
            if k != "계" and k not in floors:
                floors.append(k)
    if all(re.match(r"^(\S+ )?(B\d+|PIT|\d+F|RF|PH|ATTIC)$", f) for f in floors):
        from .tags import floor_sort_key
        floors.sort(key=floor_sort_key)       # 평면도 집계로 채운 층 이름은 지하→지상→지붕 순서로
    ws = _sheet(wb, "창호물량",
                ["부호", "명칭", "위치", "W(mm)", "H(mm)", "면적(㎡/개)"] + floors +
                ["수량(계)", "총면적(㎡)", "둘레(m/개)", "프레임", "유리/마감", "부속철물", "근거도면", "메모"],
                [9, 24, 20, 8, 8, 9] + [6] * len(floors) + [8, 10, 9, 36, 40, 26, 16, 36])
    for it in items:
        per = (it.width + it.height) * 2 / 1000 if it.width and it.height else None
        ws.append([it.code, it.name, it.location, it.width, it.height,
                   round(it.area_m2, 3) if it.area_m2 else None] +
                  [it.qty_by_floor.get(f) for f in floors] +
                  [it.qty, round(it.area_m2 * it.qty, 3) if it.area_m2 and it.qty else None,
                   round(per, 2) if per else None,
                   it.frame, it.glass, it.hardware, it.sheet, "; ".join(it.notes)])
    _style_rows(ws)
    n = ws.max_row
    ws.append(["합계"] + [None] * (5 + len(floors)) +
              [f"=SUM({get_column_letter(7 + len(floors))}2:{get_column_letter(7 + len(floors))}{n})",
               f"=SUM({get_column_letter(8 + len(floors))}2:{get_column_letter(8 + len(floors))}{n})"])
    for c in ws[ws.max_row]:
        c.font = Font(name=FONT, bold=True)

    # 2. 교차검증
    cc = cross_check(items, plan_counts, plan_notes)
    elev = getattr(plan_counts, "elev", None)
    hits = getattr(plan_counts, "elev_hits", {}) or {}
    tpls = getattr(plan_counts, "elev_templates", set())
    ws = _sheet(wb, "교차검증(일람표-평면도)",
                ["부호", "일람표 계", "평면도 태그 수", "판정", "채택 수량", "채택 근거", "층별 (일람표/평면도)", "메모", "검산"] +
                (["입면도 모양일치 수(참고)", "입면도 위치(도곽:x,y)"] if elev is not None else []),
                [9, 10, 12, 12, 10, 40, 46, 60, 44] + ([14, 40] if elev is not None else []))
    alt = getattr(plan_counts, "alt", None) or {}
    verify = {}
    for it in items:
        tot, st, memo = cc[it.code]
        per = plan_counts.get(it.code, Counter())
        fl = []
        for lab, q in it.qty_by_floor.items():
            if lab == "계":
                continue
            pc = sum(v for f, v in per.items() if floors_matching(lab, f))
            if q or pc:
                fl.append(f"{lab}: {q:g}/{pc}" if q is not None else f"{lab}: ?/{pc}")
        # 검산: 문은 두 번째 집계(제목이 다른 평면도 장을 모두 더함)와, 창·커튼월은 입면도에서 센 값과 견준다. 근거가 있을 때만 '확정'.
        is_door = bool(re.search(r"D$|DOOR|^D", it.type or "")) or getattr(it, "layout", "") == "doorlist"
        if getattr(it, "layout", "") == "doorlist":
            vf = "검산 못 함: 문 일람표의 줄 수로 낸 수량(평면도에서 다시 세지 않음)"
        elif is_door:
            a2 = alt.get(it.code)
            vf = ("검산 못 함: 평면도에 부호 없음" if not a2 and not tot else
                  f"확정: 두 번째 집계도 {a2}" if a2 == it.qty else f"사람이 볼 것: 두 번째 집계 {a2} ≠ 채택 {it.qty:g}" if it.qty is not None else "")
        else:
            if it.code not in tpls:
                vf = "검산 못 함: 입면도에서 이 창 모양을 찾을 형판 없음"
            else:
                e_n = len(hits.get(it.code, []))
                vf = (f"확정: 입면도에서도 {e_n}" if it.qty is not None and e_n == it.qty else
                      f"사람이 볼 것: 입면도 {e_n} ≠ 채택 {it.qty:g}" if it.qty is not None else f"입면도 {e_n}")
        verify[it.code] = vf
        row = [it.code, getattr(it, "sched_qty", it.qty), tot, st, it.qty, getattr(it, "qty_basis", ""), "  ".join(fl), memo, vf]
        if elev is not None:
            hs = hits.get(it.code, [])
            row += [len(hs) if it.code in tpls else "형판 없음",
                    ", ".join(f"{h[0]}:({h[2]:.0f},{h[3]:.0f})" for h in hs[:12])]
        ws.append(row)
        ws.cell(ws.max_row, 4).fill = {"일치": OK, "불일치": BAD}.get(st, WARN)
    _style_rows(ws)

    # 3. 견적비교
    if est is not None:
        matched, left = match_estimate(items, est)
        ws = _sheet(wb, "견적비교",
                    ["부호", "도면 W×H", "도면 수량", "견적 품목", "견적 규격", "견적 수량", "매칭 방법",
                     "수량 판정", "크기 판정", "메모", "견적 행"],
                    [9, 13, 9, 18, 30, 9, 22, 10, 10, 40, 8])
        for it in items:
            m = matched.get(it.code)
            if not m:
                ws.append([it.code, f"{it.width}×{it.height}", it.qty, None, None, None, "-",
                           "견적 없음", "-", "견적서에 해당 부호 없음", None])
                ws.cell(ws.max_row, 8).fill = BAD
                continue
            e, how = m
            qs = "일치" if e.qty == it.qty else "차이"
            ss, memo = size_status(it, e)
            ws.append([it.code, f"{it.width}×{it.height}", it.qty, e.name, e.spec, e.qty, how,
                       qs, ss, memo, e.row])
            ws.cell(ws.max_row, 8).fill = OK if qs == "일치" else BAD
            ws.cell(ws.max_row, 9).fill = OK if ss == "일치" else (WARN if ss == "확인" else BAD)
        for e in left:
            ws.append([None, None, None, e.name, e.spec, e.qty, "-", "도면 없음", "-",
                       "도면 일람표에 없는 견적 부호", e.row])
            ws.cell(ws.max_row, 8).fill = BAD
        _style_rows(ws)

    # 3-1. 파생물량
    if derived is not None:
        summ, detail = derived
        ws = _sheet(wb, "파생물량", ["항목", "프로그램 값", "견적서 품목", "견적서 규격", "견적서 수량(합)", "비율(견적/프로그램)"],
                    [26, 13, 30, 36, 14, 14])
        if est is not None:
            from .derived import match_estimate as match_derived
            rows = match_derived(summ, est)
        else:
            rows = [(k, v, [], None) for k, v in summ.items()]
        for k, v, lst, tot in rows:
            ws.append([k, round(v, 2) if v is not None else None, " / ".join(x[0] for x in lst)[:200],
                       " / ".join(str(x[1]) for x in lst)[:200], tot, round(tot / v, 2) if tot and v else None])
        _style_rows(ws)
        ws2 = _sheet(wb, "파생물량_부호별", ["부호", "W", "H", "수량", "둘레(m/개)", "창호주위코킹", "창호주위충전",
                                          "유리", "유리겹", "유리칸", "유리주위코킹", "문짝", "철물"],
                     [9, 8, 8, 7, 10, 12, 12, 26, 7, 7, 12, 7, 40])
        for d in detail:
            ws2.append([d.get(c) for c in ("부호", "W", "H", "수량", "둘레(m/개)", "창호주위코킹", "창호주위충전",
                                          "유리", "유리겹", "유리칸", "유리주위코킹", "문짝", "철물")])
        _style_rows(ws2)

    # 3-2. 금속물량
    if metal is not None:
        ws = _sheet(wb, "금속물량", ["구분", "항목", "규격", "단위", "프로그램 값", "견적서 품목", "견적서 수량", "비율(견적/프로그램)", "근거"],
                    [14, 24, 26, 6, 13, 40, 13, 12, 60])
        for g, name, size, unit, v, en, eq, basis in metal:
            ws.append([g, name, size, unit, v, en, eq, round(eq / v, 2) if eq and v else None, basis])
            r = ws.max_row
            if eq and v:
                ratio = eq / v
                ws.cell(r, 8).fill = OK if 0.95 <= ratio <= 1.05 else (WARN if 0.8 <= ratio <= 1.25 else BAD)
        _style_rows(ws)

    # 문 번호 목록(문 일람표)에서 읽은 문을 같은 종류끼리 묶은 표: 재료 · 문 형태 · 크기 · 방화 구분이 같으면 한 줄
    doors = [it for it in items if getattr(it, "layout", "") == "doorlist"]
    if doors:
        groups = {}
        for it in doors:
            d = getattr(it, "door_cols", {}) or {}
            fire = next((x for x in re.split(r"[,/]", d.get("비고", "")) if "방화" in x), "").strip()
            key = (d.get("재료", ""), d.get("형태", ""), it.width, it.height, fire)
            g = groups.setdefault(key, {"qty": 0, "codes": [], "hw": {}, "rooms": {}})
            q = it.qty or 0
            g["qty"] += q
            g["codes"].append(it.code)
            hw = d.get("철물", "") or "(없음)"
            g["hw"][hw] = g["hw"].get(hw, 0) + q
            room = re.sub(r"^\S*층\s*", "", it.location or "").strip() or "-"
            g["rooms"][room] = g["rooms"].get(room, 0) + q
        ws = _sheet(wb, "문_종류별", ["재료", "문 형태", "W(mm)", "H(mm)", "방화 구분", "수량(계)", "면적(㎡)", "문 번호 수", "철물 SET NO별 수량", "적용실(많은 순)", "문 번호"],
                    [10, 9, 8, 8, 12, 9, 10, 9, 34, 50, 60])
        for key, g in sorted(groups.items(), key=lambda kv: (str(kv[0][4]) == "", str(kv[0][0]), str(kv[0][1]), kv[0][2] or 0, kv[0][3] or 0)):
            mat, typ, w_, h_, fire = key
            ws.append([mat or "(안 적힘)", typ or "(안 적힘)", w_, h_, fire or "-", g["qty"],
                       round((w_ or 0) * (h_ or 0) / 1e6 * g["qty"], 2), len(g["codes"]),
                       ", ".join(f"SET {k}: {v:g}" for k, v in sorted(g["hw"].items(), key=lambda kv: str(kv[0]))),
                       ", ".join(f"{k} {v:g}" for k, v in sorted(g["rooms"].items(), key=lambda kv: -kv[1])[:12]),
                       ", ".join(g["codes"][:60]) + (" …" if len(g["codes"]) > 60 else "")])
        _style_rows(ws)
        ws.append(["합계", None, None, None, None, sum(g["qty"] for g in groups.values())])
        ws.append(["※ 문 일람표의 한 줄 = 문 번호 하나. 수량은 일람표의 층 범위·비고('N개소')로 낸 값이며 평면도에서 다시 세어 검산한 값이 아님."])

    # 4. 정보
    ws = wb.create_sheet("정보")
    for k, v in meta.items():
        if not k.startswith("_"):
            ws.append([k, v])
    # 0. 확인사항 (맨 앞 시트): 현장 신뢰도 + 이유 + 진단
    conf = meta.get("_confidence")
    if conf:
        grade, reasons = conf
        ws0 = wb.create_sheet("확인사항", 0)
        ws0.column_dimensions["A"].width = 120
        from .confidence import banner_text
        head, _ = banner_text(grade, reasons)
        ws0.append([head])
        ws0["A1"].font = Font(name=FONT, bold=True, size=13,
                              color={"높음": "1F7A1F", "보통": "B36B00", "낮음": "B00020"}[grade])
        LIM = 170

        def short(t):
            t = str(t)
            return t if len(t) <= LIM else t[:LIM].rstrip() + " … (전체는 '확인사항_상세' 시트)"

        def title(t):
            ws0.append([t])
            ws0[f"A{ws0.max_row}"].font = Font(name=FONT, bold=True)

        checks = meta.get("_checks") or []
        title("먼저 볼 것 — 도면 안에서 서로 안 맞거나 상식 범위를 벗어난 것 (견적서 없이 한 검사)")
        if checks:
            for c_ in checks:
                ws0.append(["▶ " + short(c_)])
        else:
            ws0.append(["  없음"])
        ws0.append([""])
        title("신뢰도 이유")
        for r in reasons:
            ws0.append(["• " + short(r)])
        diags = [d for d in (meta.get("진단") or "").split(" / ") if d and d != "정상"]
        # 진단은 종류별로 묶어 한 줄씩만: 부호별로 되풀이되는 줄('AW-1: 일람표에 없고 …')은 개수만 적는다
        groups, singles = {}, []
        for d in diags:
            m_ = re.match(r"^[A-Z0-9가-힣'-]+(?:\s[A-Z0-9-]+)?: (일람표에 없고 평면도에만|.{0,12}?(?:불일치|중복))", d)
            if m_:
                groups.setdefault(m_.group(1), []).append(d.split(":")[0])
            else:
                singles.append(d)
        ws0.append([""])
        title(f"진단 요약 (프로그램이 읽으며 남긴 메모 {len(diags)}줄 — 전체는 '확인사항_상세' 시트)")
        for k_, cs in groups.items():
            ws0.append([f"• {k_}: {len(cs)}개 부호 ({', '.join(cs[:10])}{' …' if len(cs) > 10 else ''})"])
        for d in singles[:12]:
            ws0.append(["• " + short(d)])
        if len(singles) > 12:
            ws0.append([f"• … 그 밖에 {len(singles) - 12}줄"])
        for row in ws0.iter_rows():
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
                if not c.font.bold:
                    c.font = Font(name=FONT)
        # 상세: 줄이지 않은 전체
        ws1 = wb.create_sheet("확인사항_상세", 1)
        ws1.column_dimensions["A"].width = 14
        ws1.column_dimensions["B"].width = 160
        ws1.append(["구분", "내용"])
        for c_ in checks:
            ws1.append(["먼저 볼 것", c_])
        for r in reasons:
            ws1.append(["신뢰도 이유", r])
        for d in diags:
            ws1.append(["진단", d])
        for row in ws1.iter_rows():
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
                c.font = Font(name=FONT, bold=(c.row == 1))
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 90
    try:
        wb.save(path)
    except PermissionError:
        # 같은 이름의 결과가 엑셀에 열려 있으면 저장이 막힌다 → 다 돌린 결과를 버리지 않고 다른 이름으로 저장
        import os, datetime
        alt = os.path.splitext(path)[0] + "_" + datetime.datetime.now().strftime("%H%M%S") + ".xlsx"
        wb.save(alt)
        print(f"!! '{os.path.basename(path)}' 이(가) 열려 있어 저장하지 못했습니다 → '{os.path.basename(alt)}' 로 저장했습니다.")
        return alt
