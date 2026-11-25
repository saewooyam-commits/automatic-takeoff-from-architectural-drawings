"""개정도면 비교: 두 도면(DWG/DXF/PDF)의 창호 부호별 크기·수량·사양 차이.

사용법:
  python compare.py 이전도면.dwg 새도면.dwg [-o 비교결과.xlsx]
"""
import argparse
import os
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

THIN = Side(style="thin", color="999999")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FILL = {"동일": "E2EFDA", "변경": "F8CBAD", "추가": "DDEBF7", "삭제": "FCE4D6"}


def analyse(path, log=print):
    """도면 → {code: dict(w,h,qty,basis,spec)}"""
    if path.lower().endswith(".pdf"):
        from run import run
        out, items, pc, _ = run(path, out=os.devnull + ".xlsx" if False else _tmp(path), log=log)
        return {it.code: dict(w=it.width, h=it.height, qty=it.qty, basis="", spec=it.frame + " " + it.glass)
                for it in items}
    from run import convert_dwg, RULES_PATH
    from takeoff.derived import load_rules
    from takeoff.pipeline_dxf import run_dxf, final_quantities
    if path.lower().endswith(".dwg"):
        path = convert_dwg(path)
    frames, kinds, items, pc, used = run_dxf(path, log=log)
    fq = final_quantities(items, pc, load_rules(RULES_PATH))
    res = {}
    for it in items:
        q, basis, sq, pq = fq.get(it.code, (None, "", None, None))
        res[it.code] = dict(w=it.width, h=it.height, qty=q, basis=basis,
                            spec=getattr(it, "spec_text", "") or it.frame)
    return res


def _tmp(path):
    return os.path.join(os.getcwd(), "_tmp_" + os.path.basename(path) + ".xlsx")


def _norm_spec(s):
    return re.sub(r"\s+", "", s or "")


def compare(a, b):
    rows = []
    for c in sorted(set(a) | set(b), key=lambda x: (re.sub(r"\d.*", "", x), int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 0, x)):
        A, B = a.get(c), b.get(c)
        if A and not B:
            rows.append((c, "삭제", A, None, "새 도면에 없음"))
            continue
        if B and not A:
            rows.append((c, "추가", None, B, "이전 도면에 없음"))
            continue
        diffs = []
        if (A["w"], A["h"]) != (B["w"], B["h"]):
            diffs.append(f"크기 {A['w']}×{A['h']} → {B['w']}×{B['h']}")
        if A["qty"] != B["qty"]:
            diffs.append(f"수량 {A['qty']} → {B['qty']}")
        if _norm_spec(A["spec"]) != _norm_spec(B["spec"]):
            diffs.append("사양 글자 변경")
        rows.append((c, "변경" if diffs else "동일", A, B, "; ".join(diffs)))
    return rows


def write(path, rows, name_a, name_b):
    wb = Workbook()
    ws = wb.active
    ws.title = "도면비교"
    ws.append([f"이전: {name_a}"])
    ws.append([f"새것: {name_b}"])
    ws.append([])
    hdr = ["부호", "판정", "이전 W×H", "이전 수량", "새 W×H", "새 수량", "변경 내용", "이전 사양", "새 사양"]
    ws.append(hdr)
    for c in ws[4]:
        c.font = Font(name="맑은 고딕", bold=True)
        c.fill = PatternFill("solid", fgColor="DCE6F1")
        c.border = BOX
    for code, st, A, B, memo in rows:
        ws.append([code, st,
                   f"{A['w']}×{A['h']}" if A else "", A["qty"] if A else "",
                   f"{B['w']}×{B['h']}" if B else "", B["qty"] if B else "",
                   memo, (A or {}).get("spec", "")[:120], (B or {}).get("spec", "")[:120]])
        r = ws.max_row
        for c in ws[r]:
            c.border = BOX
            c.font = Font(name="맑은 고딕", size=10)
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.cell(r, 2).fill = PatternFill("solid", fgColor=FILL[st])
    for col, w in zip("ABCDEFGHI", [9, 8, 13, 9, 13, 9, 40, 40, 40]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"
    n = {k: sum(1 for r in rows if r[1] == k) for k in FILL}
    ws["D1"] = "요약: " + ", ".join(f"{k} {v}" for k, v in n.items())
    wb.save(path)
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    A = analyse(a.old)
    B = analyse(a.new)
    rows = compare(A, B)
    out = a.out or "도면비교.xlsx"
    n = write(out, rows, os.path.basename(a.old), os.path.basename(a.new))
    print("비교 결과:", n, "→", out)
