"""회귀 검사.

  python tests/regress.py --save      현재 결과를 기준값(tests/baseline.json)으로 저장
  python tests/regress.py             기준값과 비교 (종료코드 1 = 나빠짐, 실행 실패, 또는 실행한 현장 없음)
  python tests/regress.py --only 견본  이름에 '견본'이 들어간 현장만

지표: 창호 수량 일치 n/N, 크기 일치 n/N, 파생물량·금속물량 각 항목의 (견적/프로그램) 비율.
      tests/truth/<현장>.json(도면을 사람이 읽은 정답지)이 있으면 '도면정답' 지표를 추가한다:
      부호 인식 n/N(정답 부호 중 프로그램이 찾은 수), 크기 일치, 수량 일치, 가짜 부호(정답에 없는 부호 수).
      견적서는 다른 개정본일 수 있으므로 '견적 일치'보다 '도면정답' 지표가 정확도의 기준이다.
"""
import argparse
import re
import json
import os
import sys
import tempfile
from contextlib import closing

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
BASE = os.path.join(HERE, "baseline.json")


CASE_TIMEOUT = 1200   # 현장 하나당 최대 초. 넘기면 '시간초과'로 기록


def metrics_for(case):
    import subprocess
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        out = os.path.join(temp_dir, "r.xlsx")
        cmd = [sys.executable, os.path.join(os.path.dirname(HERE), "run.py"), case["drawing"]]
        if case.get("estimate"):
            cmd.extend(["-e", case["estimate"]])
        cmd.extend(["-o", out])
        # 창호물량.bat 과 같은 한글 설정. 없으면 로그의 '—' 글자를 cp949 로 못 찍어 실행이 실패한다
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            run = subprocess.run(cmd, capture_output=True, timeout=CASE_TIMEOUT, cwd=os.path.dirname(HERE), env=env)
        except subprocess.TimeoutExpired:
            return {"시간초과": True}
        except OSError:
            return {"실행실패": True}
        if run.returncode != 0 or not os.path.isfile(out):
            return {"실행실패": True}
        with open(out, "rb") as stream, closing(openpyxl.load_workbook(stream, data_only=True)) as wb:
            return workbook_metrics(wb, case)


def workbook_metrics(wb, case):
    m = {}
    if "견적비교" not in wb.sheetnames:
        # 견적서 없이 돌린 현장: 도면에서 읽은 것만 지표로 남긴다 (부호 수, 크기를 읽은 부호 수, 수량 합)
        w2 = wb["창호물량"]
        hd = [c.value for c in w2[1]]
        qi = hd.index("수량(계)")
        rs = [r for r in w2.iter_rows(min_row=2, values_only=True) if r[0]]
        m["창호수량 일치"] = m["창호크기 일치"] = "0/0"
        m["부호 수"] = len(rs)
        m["크기 읽은 부호"] = sum(1 for r in rs if r[3] and r[4])
        m["수량 합"] = round(sum(float(r[qi] or 0) for r in rs), 1)
        if "금속물량" in wb.sheetnames:
            for r in wb["금속물량"].iter_rows(min_row=2, values_only=True):
                if r[1] and "합계" in str(r[1]) and r[4] is not None:
                    try:
                        m[f"금속·{r[1]}"] = round(float(r[4]), 1)
                    except (TypeError, ValueError):
                        pass
        m.update(truth_metrics(wb, case))      # 견적서 없이 돌려도 정답지가 있으면 도면정답 지표를 낸다
        return m
    ws = wb["견적비교"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    both = [r for r in rows if r[0] and r[3]]
    m["창호수량 일치"] = f"{sum(1 for r in both if r[7] == '일치')}/{len(both)}"
    sized = [r for r in both if r[8] in ("일치", "차이")]
    m["창호크기 일치"] = f"{sum(1 for r in sized if r[8] == '일치')}/{len(sized)}"
    m["견적만 있는 부호"] = sum(1 for r in rows if not r[0])
    m["도면만 있는 부호"] = sum(1 for r in both if r[7] == "견적 없음")
    for name, col in (("파생물량", 5), ("금속물량", 7)):
        if name in wb.sheetnames:
            for r in wb[name].iter_rows(min_row=2, values_only=True):
                if r[col] is not None:
                    import re
                    label = re.sub(r"\s+\(견적 두께 다름.*\)$", "", str(r[0]))     # 비고는 지표 이름에서 뺀다
                    key = f"{name}·{label}" if name == "파생물량" else f"{name}·{r[1]} {r[2] or ''}".strip()
                    m[key] = round(float(r[col]), 3)
    if "확인사항" in wb.sheetnames:
        a1 = str(wb["확인사항"]["A1"].value or "")
        import re as _re
        g = _re.search(r"신뢰도:\s*(\S+)", a1)
        if g:
            m["신뢰도(참고)"] = g.group(1)
    m.update(truth_metrics(wb, case))
    return m


def _norm_code(c):
    """정답지와 프로그램 부호 표기 차이 흡수: 공백 제거, 대문자."""
    return (c or "").replace(" ", "").upper()


def truth_metrics(wb, case):
    """도면 정답지와 비교. 정답지 파일: tests/truth/<이름 앞부분>.json"""
    import glob
    import re
    tag = re.sub(r"^\[.*?\]", "", case["name"])
    files = [f for f in glob.glob(os.path.join(HERE, "truth", "*.json"))
             if os.path.splitext(os.path.basename(f))[0] in tag or tag in os.path.splitext(os.path.basename(f))[0]]
    if not files or "창호물량" not in wb.sheetnames:
        return {}
    truth = json.load(open(files[0], encoding="utf-8"))
    alias = {_norm_code(v): _norm_code(k) for k, v in truth.get("별칭", {}).items()}
    ws = wb["창호물량"]
    hdr = [c.value for c in ws[1]]
    ci, wi, hi, qi = hdr.index("부호"), hdr.index("W(mm)"), hdr.index("H(mm)"), hdr.index("수량(계)")
    prog = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[ci] and r[ci] != "합계":
            c = _norm_code(r[ci])
            prog[alias.get(c, c)] = (r[wi], r[hi], r[qi])
    T = {_norm_code(k): v for k, v in truth["창호"].items()}
    found = [c for c in T if c in prog]
    def _sz(c):
        w, h = prog[c][0], prog[c][1]
        W, H = T[c]["W"], T[c]["H"]
        if not (w and h and W and H):
            return False
        return (abs(w - W) <= 60 and abs(h - H) <= 60) or (abs(w - H) <= 60 and abs(h - W) <= 60)   # 눕혀 그린 창은 W/H 가 바뀜
    size_ok = sum(1 for c in found if T[c]["W"] and T[c]["H"] and _sz(c))
    sized = sum(1 for c in found if T[c]["W"] and T[c]["H"])
    def tq(c):
        v = T[c]
        return v.get("수량") if "수량" in v else v.get("계")
    qty_ok = sum(1 for c in found if tq(c) and prog[c][2] == tq(c))
    counted = sum(1 for c in found if tq(c))
    fake = [c for c in prog if c not in T]
    out = {"도면정답·부호 인식": f"{len(found)}/{len(T)}",
           "도면정답·크기 일치": f"{size_ok}/{sized}",
           "도면정답·수량 일치": f"{qty_ok}/{counted}"}
    if not truth.get("_부분"):                 # 일람표 일부 장만 눈으로 읽은 정답지는 '정답지에 없는 부호'를 가짜로 세지 않는다
        out["도면정답·가짜 부호"] = len(fake)
    # 금속: 계단 수(도면의 계단실 이름 수)와 프로그램이 읽은 계단 수
    m = truth.get("금속") or {}
    if m.get("계단수") is not None and "금속물량" in wb.sheetnames:
        n = sum(1 for r in wb["금속물량"].iter_rows(min_row=2, values_only=True) if r[1] == "계단 핸드레일" and r[4] not in (None, ""))
        out["도면정답·계단 수"] = f"{min(n, m['계단수'])}/{m['계단수']}" + (f" (읽은 계단 {n}개)" if n != m["계단수"] else "")
    return out


def frac(s):
    a, b = s.split("/")
    return int(re.match(r"\s*(\d+)", a).group(1)), int(re.match(r"\s*(\d+)", b).group(1))


def compare(base, cur):
    worse, better, changed = [], [], []
    for k, v in cur.items():
        b = base.get(k)
        if b is None:
            changed.append(f"  + {k}: {v} (새 지표)")
            continue
        if b == v:
            continue
        if isinstance(b, str) and isinstance(v, str) and re.match(r"\s*\d+\s*/\s*\d+", b) and re.match(r"\s*\d+\s*/\s*\d+", v):
            (a1, n1), (a2, n2) = frac(b), frac(v)
            misses1, misses2 = n1 - a1, n2 - a2
            note = f" (분모 변경: {n1} → {n2})" if n1 != n2 else ""
            # 비교 범위가 줄어 오답 수가 줄었을 때는 판정을 보류한다.
            if misses2 > misses1:
                bucket = worse
            elif misses2 < misses1 and n2 >= n1:
                bucket = better
            else:
                bucket = changed
            bucket.append(f"  {k}: {b} → {v}{note}")
        elif isinstance(v, (int, float)) and isinstance(b, (int, float)):
            if k.endswith(("가짜 부호", "견적만 있는 부호", "도면만 있는 부호")):
                (worse if v > b else better).append(f"  {k}: {b} → {v}")       # 개수는 작을수록 좋다
            else:
                (worse if abs(v - 1) > abs(b - 1) + 1e-9 else better).append(f"  {k}: {b} → {v}")   # 비율은 1에 가까울수록 좋다
        else:
            changed.append(f"  {k}: {b} → {v}")
    for k in base:
        if k not in cur:
            bucket = worse if k.startswith("도면정답·") else changed
            bucket.append(f"  - {k}: 지표 사라짐 (이전 {base[k]})")
    return worse, better, changed


def print_summary(executed, skipped, failed, worsened):
    print(f"요약: 실행한 현장 {executed}곳, 건너뛴 현장 {skipped}곳, 실패한 현장 {failed}곳, 나빠진 현장 {worsened}곳")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--part", default="", help="'1/2' = 현장 목록을 둘로 나눈 첫째 묶음 (두 창에서 나눠 돌릴 때)")
    a = ap.parse_args()
    if a.save and a.part:
        print("기준값 저장 안 함: --save와 --part는 함께 쓸 수 없습니다.")
        print_summary(0, 0, 0, 0)
        sys.exit(1)
    cases = [c for c in json.load(open(os.path.join(HERE, "cases.json"), encoding="utf-8"))["cases"] if a.only in c["name"]]
    if a.part:
        pi, pn = (int(x) for x in a.part.split("/"))
        cases = cases[pi - 1::pn]
    base = json.load(open(BASE, encoding="utf-8")) if os.path.exists(BASE) else {}
    results = {}
    executed = skipped = failed = worsened = 0
    for c in cases:
        print(f"## {c['name']}", flush=True)
        if c.get("못찾음"):
            skipped += 1
            print(f"   건너뜀 (도면 못 찾음: {c['못찾음']})")
            if os.path.exists(os.path.join(os.path.dirname(HERE), c["drawing"])):
                print("   경로가 있음 — 못찾음 표시를 확인하라")
            continue
        executed += 1
        m = metrics_for(c)
        if m.get("실행실패") or m.get("시간초과"):
            failed += 1
            print("   시간 초과" if m.get("시간초과") else "   실행 실패")
            if a.save:
                print("   기준값 저장 안 함 (기존 값 유지)")
            continue
        results[c["name"]] = m
        print(f"   창호 수량 {m['창호수량 일치']}  크기 {m['창호크기 일치']}" +
              (f"  | 도면정답: 부호 {m['도면정답·부호 인식']} 크기 {m['도면정답·크기 일치']} 수량 {m['도면정답·수량 일치']} 가짜 {m.get('도면정답·가짜 부호', '-')}" + (f" 계단 {m['도면정답·계단 수']}" if '도면정답·계단 수' in m else "")
               if "도면정답·부호 인식" in m else ""))
        if not a.save and c["name"] in base:
            w, b, ch = compare(base[c["name"]], m)
            for line in w:
                print("   ▼ 나빠짐" + line)
            for line in b:
                print("   ▲ 좋아짐" + line)
            for line in ch:
                print("   · 변경" + line)
            if not (w or b or ch):
                print("   = 기준값과 동일")
            worsened += bool(w)
    if not cases:
        print("선택된 현장이 0곳입니다.")
    elif not executed:
        print("선택된 현장을 모두 건너뛰었습니다.")
    if a.save and results:
        base.update(results)
        json.dump(base, open(BASE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("기준값 저장:", BASE)
    print_summary(executed, skipped, failed, worsened)
    sys.exit(1 if failed or worsened or not executed else 0)
