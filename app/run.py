"""창호 물량산출 실행.

사용법:
  python run.py 도면.pdf|도면.dwg|도면.dxf [-e 견적서.xls(x)] [-o 결과.xlsx]

- PDF: 페이지별 도면명으로 일람표/창호부호 평면도를 찾는다.
- DWG: dwg2dxf(LibreDWG) 또는 ODA File Converter로 DXF 변환 후 처리 (환경변수 DWG2DXF로 경로 지정)
- DXF: 모델공간을 도곽 단위로 나눠 일람표/평면도를 찾는다.
"""
import argparse
import os
import datetime
from takeoff.tags import floor_sort_key as _floor_sort_key
import re
import sys
from collections import Counter, defaultdict

from takeoff.pdf_source import load_pdf
from takeoff.schedule import is_schedule_sheet, parse_schedule_sheet
from takeoff.tags import count_tags, floor_of
from takeoff.report import write_report


def is_tag_plan(sh):
    t = sh.title.replace(" ", "")
    return "창호" in t and "평면" in t and "일람" not in t


def is_plain_plan(sh):
    t = sh.title.replace(" ", "")
    return t.endswith("평면도") and "층" in t and not any(
        k in t for k in ("천정", "천장", "보감싸기", "그레이팅", "비계", "방수", "방화", "단열", "우수", "확대", "배치"))


def run(drawing, estimate=None, out=None, log=print):
    sheets = load_pdf(drawing)
    sched = [s for s in sheets if is_schedule_sheet(s)]
    items, seen = [], set()
    for s in sched:
        for it in parse_schedule_sheet(s):
            if it.code in seen:
                it.notes.append("중복 부호")
            seen.add(it.code)
            items.append(it)
    log(f"창호일람표 {len(sched)}장에서 부호 {len(items)}개 인식: "
        + ", ".join(f"p.{s.index}" for s in sched))

    plans = [s for s in sheets if is_tag_plan(s)] or [s for s in sheets if is_plain_plan(s)]
    types = {it.type for it in items if it.type}
    plan_counts = defaultdict(Counter)
    notes = []
    for s in plans:
        fl = floor_of(s)
        c, _ = count_tags(s, types)
        for k, v in c.items():
            plan_counts[k][fl] += v
        for t in s.texts:
            if "참조" in t.text and re.search(r"창호|그릴|입면", t.text) and t.text not in notes:
                notes.append(t.text.strip("* "))
    log(f"평면도 {len(plans)}장에서 부호 태그 집계: " + ", ".join(f"p.{s.index}({floor_of(s)})" for s in plans))

    est = None
    if estimate:
        from takeoff.estimate import read_estimate
        est = read_estimate(estimate)
        log(f"견적서 항목 {len(est)}개 읽음")

    out = out or re.sub(r"\.pdf$", "", drawing, flags=re.I) + "_창호물량.xlsx"
    meta = {
        "도면": drawing, "견적서": estimate or "-",
        "생성일시": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "프로그램 버전": VERSION,
        "일람표 페이지": ", ".join(f"{s.number} p.{s.index}" for s in sched),
        "평면도 페이지": ", ".join(f"{s.number} p.{s.index} {floor_of(s)}" for s in plans),
        "치수 규칙": "치수선에서 양옆이 30mm 이하 여유치수로 둘러싸인 값을 개구부 크기로, 없으면 최대 치수를 사용",
    }
    from takeoff.derived import compute, load_rules
    rules = load_rules(RULES_PATH)
    meta.update(rules_meta(rules))
    derived = compute(items, {}, rules)
    metal = None
    try:
        from takeoff.metal.takeoff_metal import run_metal_pdf, summary_rows
        fr, be, wm = run_metal_pdf(drawing, items, None, log=log, rules=rules)
        metal = summary_rows(fr, be, wm, est)
        if fr or be:
            from takeoff.metal.takeoff_metal import write_metal_review
            write_metal_review(drawing, re.sub(r"\.xlsx$", "", out) + "_금속검토도면.pdf", log=log)
    except Exception as ex:
        log(f"금속 산출 실패: {ex}")
    write_report(out, items, plan_counts, notes, est, meta, derived, metal)
    log(f"결과 저장: {out}")
    return out, items, plan_counts, est


HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "rules.json")


VERSION = "v25 (2026-11-25)"
REVIEW_LIMIT_SEC = 300
CONVERT_WARNINGS = []      # 변환 결과가 잘린 것으로 보이는 파일 (진단·신뢰도에 올린다)
SKIPPED_FILES = []         # 이름 규칙(구조·설비 등)으로 뺀 파일


def dxf_health(path):
    """변환된 DXF 가 온전한지: ENTITIES 구역·EOF 가 있고 BLOCK/ENDBLK 수가 맞는지. (정상, 설명)"""
    try:
        n_block = n_endblk = 0
        has_ent = has_eof = False
        prev = ""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                t = line.strip()
                if prev == "0":
                    if t == "BLOCK":
                        n_block += 1
                    elif t == "ENDBLK":
                        n_endblk += 1
                    elif t == "EOF":
                        has_eof = True
                elif prev == "2" and t == "ENTITIES":
                    has_ent = True
                prev = t
        probs = []
        if not has_ent:
            probs.append("ENTITIES 구역 없음")
        if not has_eof:
            probs.append("EOF 없음")
        if n_block != n_endblk:
            probs.append(f"BLOCK {n_block} / ENDBLK {n_endblk}")
        return (not probs), ", ".join(probs)
    except Exception as ex:
        return False, f"검사 실패: {ex}"


def cache_dir_for(src):
    """변환 캐시 폴더: 이름이 같은 다른 현장과 섞이지 않게 전체 경로 해시를 붙이고, 실행할 때마다 비운다."""
    import hashlib
    import shutil
    ab = os.path.abspath(src)
    tag = hashlib.md5(ab.encode("utf-8")).hexdigest()[:8]
    name = os.path.splitext(os.path.basename(os.path.normpath(ab)))[0] if os.path.isfile(ab) else os.path.basename(os.path.normpath(ab))
    d = os.path.join(os.getcwd(), "dxf_cache", f"{name}_{tag}")
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


STAGES = []            # [(단계 이름, 시작 시각)] — 로그와 실행기록에 단계별 초를 남긴다
_T0 = [None]


def stage(name):
    import time
    STAGES.append((name, time.time()))


def stage_seconds():
    import time
    out, now = [], time.time()
    for i, (nm, t) in enumerate(STAGES):
        end = STAGES[i + 1][1] if i + 1 < len(STAGES) else now
        out.append((nm, end - t))
    return out


def convert_xref_cached(src, out_dir):
    """xref 변환 결과를 다시 쓴다: 원본 크기·수정일이 같으면 work/dxf_cache/_xref_store 의 예전 변환을 복사만 한다.
    (본 도면 캐시는 실행마다 비우지만 xref 는 여러 장이 같이 쓰는 큰 배경 도면이라 변환이 전체 시간의 절반을 넘기도 한다)"""
    import hashlib
    import shutil
    try:
        st = os.stat(src)
        key = hashlib.md5(f"{os.path.abspath(src)}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()[:16]
        store = os.path.join(os.getcwd(), "dxf_cache", "_xref_store")
        os.makedirs(store, exist_ok=True)
        kept = os.path.join(store, key + ".dxf")
        out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + ".dxf")
        if os.path.exists(kept) and os.path.getsize(kept) > 1000 and dxf_health(kept)[0]:
            os.makedirs(out_dir, exist_ok=True)
            shutil.copyfile(kept, out)
            XREF_REUSED.append(os.path.basename(src))
            return out
        res = convert_dwg(src, out_dir)
        try:
            if res and dxf_health(res)[0]:
                shutil.copyfile(res, kept)
        except OSError:
            pass
        return res
    except OSError:
        return convert_dwg(src, out_dir)


XREF_REUSED = []


def is_efs(path):
    """Windows EFS 암호화 파일인지 (다른 PC·계정에서 암호화한 파일은 권한을 줘도 못 읽는다)."""
    try:
        return bool(getattr(os.stat(path), "st_file_attributes", 0) & 0x4000)
    except OSError:
        return False


def find_converter():
    """DWG→DXF 변환기 찾기: 환경변수 DWG2DXF > PATH의 dwg2dxf > 프로그램 폴더의 bin/."""
    import shutil
    cands = [os.environ.get("DWG2DXF"), shutil.which("dwg2dxf"),
             os.path.join(HERE, "bin", "dwg2dxf"), os.path.join(HERE, "bin", "dwg2dxf.exe"),
             os.path.join(HERE, "..", "bin", "dwg2dxf.exe")]          # Windows 배포 묶음: app/ 옆의 bin/
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise RuntimeError("DWG 변환기(dwg2dxf)를 찾을 수 없습니다. 환경변수 DWG2DXF에 경로를 지정하거나 bin/ 폴더에 두세요.")


def convert_dwg(path, out_dir=None):
    """DWG → DXF. 변환기(dwg2dxf.exe)는 한글 경로를 못 여는 일이 있어(Windows, READ ERROR 0x1000)
    영문 임시 이름으로 복사해 변환한 뒤 결과를 원래 이름으로 돌려 놓는다."""
    import subprocess
    import shutil
    import hashlib
    exe = find_converter()
    out_dir = out_dir or os.path.join(os.getcwd(), "dxf_cache")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".dxf")
    if os.path.exists(out):
        os.remove(out)                      # 같은 이름의 예전 변환 결과를 쓰지 않도록
    tag = hashlib.md5(os.path.abspath(path).encode("utf-8")).hexdigest()[:8]
    tmp_in = os.path.join(out_dir, f"_in_{tag}.dwg")
    tmp_out = os.path.join(out_dir, f"_out_{tag}.dxf")
    for f in (tmp_in, tmp_out):
        if os.path.exists(f):
            os.remove(f)
    try:
        shutil.copyfile(path, tmp_in)
    except PermissionError:
        if is_efs(path):
            raise PermissionError(f"암호화(EFS)된 파일이라 읽을 수 없습니다: {path}\n  → 이 파일은 다른 PC/계정에서 암호화되었습니다. 권한을 줘도 열리지 않습니다. "
                                  "암호화한 PC에서 파일 속성 → 고급 → '데이터 보호를 위해 내용 암호화'를 해제한 뒤 다시 복사해 오세요.")
        raise PermissionError(f"읽기 권한이 없는 파일입니다: {path}\n  → 탐색기에서 폴더 속성 → 보안 → 현재 사용자에게 읽기 권한을 주거나, "
                              f"관리자 명령창에서  takeown /f \"{os.path.dirname(path)}\" /r /d y  와  "
                              f"icacls \"{os.path.dirname(path)}\" /grant *S-1-5-32-545:(OI)(CI)F /T /C  를 실행한 뒤 다시 돌리세요.")
    try:
        # 변환기가 여럿 있으면(bin/dwg2dxf.exe 최신 빌드, dwg2dxf_0.13.3.exe 예전 판) 차례로 시도한다
        import glob
        exes = [exe] + sorted(e for e in glob.glob(os.path.join(os.path.dirname(exe), "dwg2dxf*")) if e != exe and (e.endswith(".exe") or os.access(e, os.X_OK)))
        msg = []
        best = None            # (정상 여부, 크기, 임시파일, 설명)
        for k, e in enumerate(exes):
            cand = tmp_out + f".{k}"
            if os.path.exists(cand):
                os.remove(cand)
            r = subprocess.run([e, "-y", "-o", cand, tmp_in], check=False, capture_output=True, timeout=900)
            if os.path.exists(cand) and os.path.getsize(cand) > 1000:
                ok, why = dxf_health(cand)
                if best is None or (ok, os.path.getsize(cand)) > (best[0], best[1]):
                    if best:
                        os.remove(best[2])
                    best = (ok, os.path.getsize(cand), cand, why)
                else:
                    os.remove(cand)
                if ok:
                    break              # 온전한 결과가 나오면 다음 변환기는 안 돌린다
            else:
                msg = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
                if os.path.exists(cand):
                    os.remove(cand)
        if best is None:
            raise RuntimeError(f"DWG 변환 실패: {path}" + (" / " + " | ".join(msg) if msg else "") +
                               "\n  → CAD에서 DXF(2013 이하)로 저장한 파일을 넣어 보세요.")
        os.replace(best[2], out)
        if not best[0]:
            CONVERT_WARNINGS.append(f"{os.path.basename(path)}: 변환 결과가 잘린 것으로 보임({best[3]}) → 이 파일의 도면 일부가 빠졌을 수 있음")
    finally:
        if os.path.exists(tmp_in):
            os.remove(tmp_in)
    return out


def rules_meta(rules):
    """결과 '정보' 시트에 적을 규칙 목록. 견적에 맞춘 값은 그대로 드러나게 둔다."""
    out = {}
    for k, v in rules.items():
        if not isinstance(v, dict):
            continue
        src = v.get("_출처", "")
        vals = ", ".join(f"{a}={b}" for a, b in v.items() if not a.startswith("_") and a != "단위")
        tag = "[맞춤값] " if "맞춤" in src else ""
        out[f"규칙·{k}"] = f"{tag}{vals}" + (f"  ({src})" if src else "")
    return out


SKIP_FILE = re.compile(r"xref|x-ref|구조|전기|설비|기계|토목|조경|가시설|흙막이|정화조|소방|내진|표지|개요|배치도|산출근거|조권|plot", re.I)


# 제외 낱말이 들어 있어도 빼지 않는 파일: 계단(핸드레일 산출에 필요: 'A-705 기계실 및 계단실 단면도'), 창호
KEEP_FILE = re.compile(r"계단|창호", re.I)


def prepare_folder(folder, log=print):
    """폴더 안 DWG/DXF를 모아 DXF 폴더로 만든다 (DWG는 변환). 구조·설비 등 비건축 파일은 제외."""
    out_dir = cache_dir_for(folder)
    n, skipped = 0, []
    sources = {}          # 변환된 DXF 이름 → 원본 파일 이름·수정일 (같은 도면이 여러 파일에 있을 때 최신본을 고르는 데 쓴다)
    # 하위 폴더까지 본다(3단계). 구조·전기·설비 같은 비건축 폴더/파일은 이름으로 뺀다
    files = []
    base_depth = folder.rstrip("/\\").count(os.sep)
    for dp, dn, fns in os.walk(folder):
        dn[:] = sorted(d for d in dn if not SKIP_FILE.search(d) and not d.startswith(("_", ".")) and dp.count(os.sep) - base_depth < 3)
        for fn in sorted(fns):
            if fn.lower().endswith((".dwg", ".dxf")):
                if SKIP_FILE.search(fn) and not KEEP_FILE.search(fn):
                    SKIPPED_FILES.append(fn)
                else:
                    files.append(os.path.join(dp, fn))
    if SKIPPED_FILES:
        log(f"이름으로 건축 도면이 아니라고 보고 뺀 파일 {len(SKIPPED_FILES)}개: " + ", ".join(SKIPPED_FILES[:8]) + (" …" if len(SKIPPED_FILES) > 8 else "")
            + "  (창호가 든 파일이 빠졌으면 파일 이름에서 구조/설비/개요 같은 낱말을 빼고 다시 돌리세요)")
    if any(os.path.dirname(f) != folder for f in files):
        log("하위 폴더의 도면도 함께 봅니다: " + ", ".join(sorted({os.path.relpath(os.path.dirname(f), folder) for f in files if os.path.dirname(f) != folder})[:6]))
    seen_names = set()
    for src in files:
        fn = os.path.basename(src)
        if fn in seen_names:                      # 하위 폴더에 같은 이름이 있으면 폴더 이름을 붙여 구분
            fn = os.path.basename(os.path.dirname(src)) + "_" + fn
        seen_names.add(fn)
        low = fn.lower()
        try:
            if low.endswith(".dwg"):
                log(f"DWG → DXF 변환: {fn}")
                out = convert_dwg(src, out_dir)
                want = os.path.join(out_dir, os.path.splitext(fn)[0] + ".dxf")
                if os.path.abspath(out) != os.path.abspath(want):
                    os.replace(out, want)
            else:
                import shutil
                shutil.copy(src, os.path.join(out_dir, fn))
            try:
                sources[os.path.splitext(fn)[0] + ".dxf"] = {"원본": fn, "수정일": os.path.getmtime(src)}
            except OSError:
                pass
            n += 1
        except PermissionError:
            if is_efs(src):
                log(f"  !! 암호화(EFS)된 파일 → 건너뜀: {fn}  (암호화한 PC에서 암호화를 풀고 다시 복사해야 합니다)")
                skipped.append(fn + " (암호화)")
            else:
                log(f"  !! 읽기 권한 없음 → 건너뜀: {fn}")
                skipped.append(fn + " (권한)")
        except Exception as ex:
            log(f"  !! 변환 실패 → 건너뜀: {fn} ({str(ex).splitlines()[0][:80]})")
            skipped.append(fn + " (변환 실패)")
    try:
        import json as _json
        with open(os.path.join(out_dir, "_sources.json"), "w", encoding="utf-8") as fp:
            _json.dump(sources, fp, ensure_ascii=False)
    except OSError:
        pass
    if skipped:
        log(f"건너뛴 파일 {len(skipped)}개: " + ", ".join(skipped))
    if not n:
        if skipped and all("암호화" in x for x in skipped):
            raise RuntimeError("폴더의 도면이 모두 암호화(EFS)되어 읽을 수 없습니다. 암호화한 PC에서 폴더 속성 → 고급 → '내용 암호화'를 해제한 뒤 다시 복사해 오세요.")
        if skipped and all("권한" in x for x in skipped):
            raise RuntimeError("폴더의 DWG 파일을 하나도 읽을 수 없습니다 (Windows 폴더 권한). 폴더 속성 → 보안에서 현재 사용자에게 읽기 권한을 주거나 "
                               "관리자 명령창에서  takeown /f \"" + folder + "\" /r /d y  와  icacls \"" + folder + "\" /grant *S-1-5-32-545:(OI)(CI)F /T /C  를 실행한 뒤 다시 돌리세요.")
        raise RuntimeError("폴더에 읽을 수 있는 DWG/DXF 파일이 없습니다: " + folder)
    return out_dir


def self_checks(items, plan_counts, fq):
    """도면 안 정보만으로 하는 상식·모순 검사 → 사람이 먼저 볼 줄 목록"""
    out = []
    real = [it for it in items if it.sheet != "(평면도에만)"]
    # 크기 상식 범위
    odd = []
    for it in real:
        w, h = it.width, it.height
        if not (w and h):
            continue
        big_ok = it.type in ("CW", "ACW", "SCW", "AW", "SSW", "FSW", "PW", "SSD", "FSS", "FST", "SST")
        if w < 300 or h < 300 or h > (60000 if big_ok else 6000) or w > (60000 if big_ok else 12000):
            odd.append(f"{it.code} {w}×{h}")
    if odd:
        out.append(f"크기가 상식 범위를 벗어난 부호 {len(odd)}개: " + ", ".join(odd[:10]) + (" …" if len(odd) > 10 else "") + " → 일람표와 대조")
    nosz = [it.code for it in real if not (it.width and it.height)]
    if nosz:
        out.append(f"크기를 못 읽은 부호 {len(nosz)}개: " + ", ".join(nosz[:12]) + (" …" if len(nosz) > 12 else ""))
    noq = [c for c, v in fq.items() if v[0] is None]
    if noq:
        out.append(f"수량을 정하지 못한 부호 {len(noq)}개: " + ", ".join(sorted(noq)[:12]) + (" …" if len(noq) > 12 else ""))
    # 일람표 수량 vs 평면도 집계
    diff = sorted(c for c, v in fq.items() if v[2] is not None and v[3] is not None and v[2] != v[3])
    if diff:
        out.append(f"일람표 수량과 평면도 집계가 다른 부호 {len(diff)}개: " + ", ".join(diff[:12]) + (" …" if len(diff) > 12 else "") + " → 교차검증 시트")
    only_plan = sorted(it.code for it in items if it.sheet == "(평면도에만)")
    if only_plan:
        out.append(f"평면도에는 있는데 일람표에 없는 부호 {len(only_plan)}개: " + ", ".join(only_plan[:12]) + (" …" if len(only_plan) > 12 else ""))
    zero = sorted(it.code for it in real if it.code in fq and fq[it.code][2] is not None and fq[it.code][2] > 0 and fq[it.code][3] == 0)
    # 층: 평면도 제목에서 읽은 층 가운데 빠진 층(예: 1F·2F·4F 만 있고 3F 없음)
    try:
        from takeoff.tags import floor_sort_key
        fl = sorted({f for f in (getattr(plan_counts, "all_floors", []) or []) if re.fullmatch(r"\d+F", f)}, key=lambda f: int(f[:-1]))
        if len(fl) >= 2:
            nums = [int(f[:-1]) for f in fl]
            miss = [f"{n}F" for n in range(nums[0], nums[-1] + 1) if n not in nums]
            if miss:
                out.append(f"평면도가 없는 층: {', '.join(miss[:10])}{' …' if len(miss) > 10 else ''} (제목에서 읽은 층 {fl[0]}~{fl[-1]} 사이) → 그 층 수량이 빠졌을 수 있음")
        allf, used = set(getattr(plan_counts, "all_floors", []) or []), set(getattr(plan_counts, "used_floors", []) or [])
        unused = sorted(allf - used, key=floor_sort_key)
        if used and unused:
            out.append(f"부호를 센 평면도가 없는 층: {', '.join(unused[:12])}{' …' if len(unused) > 12 else ''} (그 층 평면도에 창호 부호가 없거나 못 읽음)")
    except Exception:
        pass
    return out


def run_cad(path, estimate=None, out=None, log=print):
    from takeoff.pipeline_dxf import run_dxf, final_quantities
    src = path.rstrip("/\\")                     # 결과 파일 이름의 기준은 사용자가 넣은 원본(DWG/폴더)
    # 외부참조(xref) 파일은 입력 폴더(또는 입력 파일의 폴더)와 그 위 폴더까지 뒤져 찾는다
    from takeoff import dxf_source as _ds
    base_dir = src if os.path.isdir(src) else os.path.dirname(os.path.abspath(src))
    _ds.XREF_DIRS = [base_dir, os.path.dirname(base_dir.rstrip("/\\"))]
    _ds.XREF_CONVERT = lambda f, out_dir: convert_xref_cached(f, out_dir)
    _ds.XREF_LOG = log
    stage("변환")
    if os.path.isdir(path):
        path = prepare_folder(path, log=log)
    elif path.lower().endswith(".dwg"):
        log("DWG → DXF 변환 중...")
        path = convert_dwg(path, cache_dir_for(path))
    stage("분석")
    for w in CONVERT_WARNINGS:
        log("  !! " + w)
    from takeoff.derived import compute, load_rules
    rules = load_rules(RULES_PATH)
    frames, kinds, items, plan_counts, used = run_dxf(path, log=log)
    if XREF_REUSED:
        log(f"xref 변환 재사용 {len(XREF_REUSED)}개 (원본이 바뀌지 않아 예전 변환을 씀)")
    stage("집계·보고")
    if CONVERT_WARNINGS:
        plan_counts.diagnosis = list(getattr(plan_counts, "diagnosis", [])) + ["변환: " + w for w in CONVERT_WARNINGS]
    if SKIPPED_FILES:
        plan_counts.diagnosis = list(getattr(plan_counts, "diagnosis", [])) + [
            f"이름으로 뺀 파일 {len(SKIPPED_FILES)}개(구조·설비·개요 등): " + ", ".join(SKIPPED_FILES[:12]) + (" …" if len(SKIPPED_FILES) > 12 else "")]
    fq = final_quantities(items, plan_counts, rules)
    # 평면도(창호평면도·안내도)에만 있고 일람표에 없는 부호도 결과에 올린다 (일람표 누락이거나 삭제된 부호 → 확인 필요)
    from takeoff.schedule import ScheduleItem
    have = {it.code for it in items}
    for code in sorted(set(plan_counts) - have):
        n = sum(plan_counts[code].values())
        if not n:
            continue
        from takeoff.buildings import split as bsplit
        bname, bare = bsplit(code)
        typ = bare.split("-")[0]
        it = ScheduleItem(code=code, type=typ, number=bare[len(typ) + 1:], name="", location="", width=None, height=None,
                          sheet="(평면도에만)")
        it.building = bname or None
        it.notes.append(f"일람표에 없는 부호: 평면도 태그 {n}개 (" +
                        ", ".join(f"{f} {q}" for f, q in sorted(plan_counts[code].items())) + ") → 일람표 누락인지 확인 필요")
        items.append(it)
        plan_counts.diagnosis.append(f"{code}: 일람표에 없고 평면도에만 {n}개")
    for it in items:
        q, basis, sq, pq = fq.get(it.code, (None, "", None, None))
        fr_ = (getattr(plan_counts, "floor_rule", None) or {}).get(it.code)
        if fr_:
            basis = f"{basis} [{fr_}]"
        if sq is not None and pq is not None and sq != pq:
            it.notes.append(f"일람표 {sq:g} / 평면도 {pq:g} 불일치")
        it.notes.append(f"수량 근거: {basis}")
        it.sched_qty = it.qty                      # 일람표에 적힌 원래 수량 (교차검증 시트용)
        it.qty_basis = basis
        it.qty = q
        # 일람표에 층별표가 없고 평면도 집계를 채택했으면 층별 칸을 평면도 집계로 채운다 (층 열이 비지 않도록)
        if not any(k != "계" for k in it.qty_by_floor):
            per = plan_counts.get(it.code)
            # 평면도 집계를 채택했거나, 일람표 수량과 평면도 합계가 같을 때(층별 분포를 그대로 믿을 수 있을 때)
            if per and (basis.startswith("평면도") or (q is not None and abs(sum(per.values()) - q) < 1e-6)):
                it.qty_by_floor = dict(sorted(per.items(), key=lambda kv: _floor_sort_key(kv[0])))
                it.qty_by_floor["계"] = q
        if getattr(it, "spec_text", "") and not it.frame:
            it.frame = it.spec_text
    est = None
    if estimate:
        from takeoff.estimate import read_estimate
        from takeoff.buildings import building_of
        est = read_estimate(estimate)
        if any(getattr(it, "building", None) for it in items):
            for e in est:
                e.building = building_of(e.section) or building_of(e.name)      # 견적서가 동별 구분으로 나뉜 경우
        log(f"견적서 항목 {len(est)}개 읽음")
    out = out or re.sub(r"\.(dxf|dwg)$", "", src, flags=re.I) + "_창호물량.xlsx"
    meta = {
        "도면": src, "견적서": estimate or "-",
        "생성일시": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "프로그램 버전": VERSION,
        "진단": " / ".join(getattr(plan_counts, "diagnosis", [])) or "정상",
        "일람표 도곽": ", ".join(f"{f.index}:{f.title}" for f in kinds["schedule"]),
        "사용한 평면도": "; ".join(used),
        "수량 규칙": "수량의 기준은 평면도 부호 집계(회사 기준 2026-11-14). 일람표 개수와 다르면 평면도 값을 쓰고 표시한다 "
                   "(평면도 집계가 일람표와 절반도 안 맞거나 일부 층만 있어 집계 자체가 성립하지 않을 때만 일람표). 여러 층 대표 도면은 층수 곱, "
                   "평면도에 찍힌 부호는 자리마다 센다. 외부창은 입면도 모양 일치 수를 참고로 함께 적는다. "
                   "다른 쪽 값은 교차검증 시트에 남는다. rules.json '수량우선'을 '일람표'로 바꾸면 항상 일람표. "
                   f"높이 {rules['다층창호']['높이기준_mm']}mm 이상 창호·커튼월이 여러 층에 표기되면 한 덩어리로 보고 층당 최대 개수 채택",
        "치수 규칙": "CAD 치수 객체의 측정값 사용. 가로는 30mm 이하 여유치수로 둘러싸인 값 우선, "
                   "세로는 창틀 선이 실제로 덮는 치수만(창대·벽 구간 제외)",
    }
    meta.update(rules_meta(rules))
    # 현장 신뢰도: 처음 보는 양식이면 결과 맨 앞 시트에 경고
    try:
        from takeoff.confidence import assess
        from takeoff.report import match_estimate
        matched = match_estimate(items, est)[0] if est else None
        fm = next((d[len("도곽: "):] for d in getattr(plan_counts, "diagnosis", []) if d.startswith("도곽: ")), "")
        grade, reasons = assess(items, plan_counts, kinds, fq, est, matched, fm)
        meta["_confidence"] = (grade, reasons)
        meta["현장 신뢰도"] = grade + " — " + "; ".join(reasons)
        # 화면 로그에는 금속 검사까지 끝난 뒤의 최종 등급을 한 번만 찍는다 (앞서는 여기서 찍은 '높음'과 실행기록의 '보통'이 달랐다)
    except Exception as ex:
        log(f"신뢰도 계산 실패: {ex}")
    # 견적서 없이 스스로 거르는 검사: 도면 안 정보끼리 안 맞는 것을 모아 확인사항 맨 위에 보여 준다
    checks = []
    try:
        checks += self_checks(items, plan_counts, fq)
    except Exception as ex:
        log(f"자체 검사 실패: {ex}")
    meta["_checks"] = checks
    derived = compute(items, fq, rules)
    metal = None
    try:
        from takeoff.metal.takeoff_metal import window_metal, summary_rows
        from takeoff.metal.dxf_metal import stair_handrail_dxf, keyword_inventory
        wm = window_metal(items, fq)
        from takeoff.metal import dxf_metal as _dm
        n_fl = len(getattr(plan_counts, "all_floors", []) or []) or None
        st = stair_handrail_dxf(frames, rules, n_floors=n_fl)
        plan_counts.stair_rows = st                    # 검토도면에 계단 단면 페이지를 넣기 위해
        got = [r for r in st if r["수량"] is not None]
        if got:
            log(f"금속: 계단 핸드레일 {len(got)}개 계단, 합계 {sum(r['수량'] for r in got):.1f}m — " +
                ", ".join(f"{r['규격']} {r['수량']}m" for r in got[:8]) + (" …" if len(got) > 8 else "") + "  (근거는 엑셀 금속물량 시트)")
        for w in _dm.LAST_WARNINGS:
            log("  !! " + w)
        # 금속 경고(계단 수·높이가 도면과 안 맞음)는 '먼저 볼 것'에 올리고 신뢰도를 한 단계 낮춘다. 추정이 섞인 줄은 한 줄로 요약.
        checks += list(_dm.LAST_WARNINGS)
        weak = [r for r in got if re.search(r"규칙값|만 챌판이 읽힘|추정", r["근거"])]
        if meta.get("_confidence"):
            g_, rs_ = meta["_confidence"]
            rs_ = list(rs_)
            if weak:
                rs_.append(f"[금속] 계단 핸드레일 {len(got)}개 중 {len(weak)}개에 추정이 섞임(계단참 규칙값 또는 판 수 추정) → 계단 단면도와 대조: " +
                           ", ".join(f"{r['규격']} {r['수량']}m" for r in weak[:6]) + (" …" if len(weak) > 6 else ""))
            if _dm.LAST_WARNINGS:
                g_ = {"높음": "보통"}.get(g_, g_)          # 금속 경고만으로 '낮음'까지 내리지는 않는다
                rs_.insert(0, f"[금속] 계단 핸드레일이 도면 안의 다른 정보와 안 맞음 {len(_dm.LAST_WARNINGS)}건 (맨 위 '먼저 볼 것')")
            meta["_confidence"] = (g_, rs_)
            meta["현장 신뢰도"] = g_ + " — " + "; ".join(rs_)
        inv = keyword_inventory(getattr(plan_counts, "plan_frames", []), rules)
        # v25 트렌치·재료분리대: 둘을 따로 격리한다 (하나가 실패해도 계단·창호 연계 금속과 다른 하나는 나온다)
        floor_rows, floor_chk = [], []
        from takeoff.metal import floor_metal as _fm
        for _nm, _fn in (("재료분리대", lambda: _fm.divider_rows(frames, plan_counts, items, rules)),
                         ("트렌치", lambda: _fm.trench_rows(frames, plan_counts, rules))):
            try:
                got_ = _fn()
                floor_rows += got_
                floor_chk += [r for r in got_ if "확인 필요" in r["항목"]]
            except Exception as ex:
                log(f"금속 {_nm} 산출 실패: {ex}")
                checks.append(f"[금속] {_nm} 산출 실패: {str(ex).splitlines()[0] if str(ex) else type(ex).__name__}")
        if floor_rows:
            q_ = [r for r in floor_rows if r.get("수량") is not None]
            log(f"금속: 트렌치·재료분리대 {len(floor_rows)}줄" + (" — " + ", ".join(f"{r['항목']} {r['수량']}m" for r in q_[:4]) + (" …" if len(q_) > 4 else "") if q_ else " (길이를 낸 줄 없음)")
                + (f", 확인 필요 {len(floor_chk)}줄" if floor_chk else "") + "  (근거는 엑셀 금속물량 시트)")
        if floor_chk:
            checks.append(f"[금속] 트렌치·재료분리대에서 사람이 확인할 줄 {len(floor_chk)}개 — " + ", ".join(r["항목"] for r in floor_chk[:5])
                          + (" …" if len(floor_chk) > 5 else "") + " (금속물량 시트 근거 칸)")
        metal = summary_rows([], [], wm + st + inv + floor_rows, est or [])
    except Exception as ex:
        log(f"금속 산출 실패: {ex}")
    if meta.get("_confidence"):
        log(f"현장 신뢰도: {meta['_confidence'][0]} — " + "; ".join(meta["_confidence"][1]))
    saved = write_report(out, items, plan_counts, [], est, meta, derived, metal)
    out = saved or out
    log(f"결과 저장: {out}")
    rv_note = ""
    stage("검토도면 PDF")
    try:
        from takeoff.review import write_review
        rv = re.sub(r"\.xlsx$", "", out) + "_검토도면.pdf"
        log("검토도면 PDF 만드는 중… (5분 넘으면 건너뜁니다. 엑셀은 이미 저장됨)")
        rv_path, rv_note = write_review(rv, items, plan_counts, fq, title=os.path.basename(path), time_limit=REVIEW_LIMIT_SEC)
        if rv_path:
            log(f"검토도면 저장: {rv_path}")
    except Exception as ex:
        rv_note = f"검토도면 생성 실패: {str(ex).splitlines()[0] if str(ex) else type(ex).__name__}"
    if rv_note:
        log("주의: " + rv_note)
        # 엑셀 확인사항에도 남긴다 (엑셀은 이미 저장돼 있으므로 실패해도 결과는 그대로)
        try:
            if meta.get("_confidence"):
                g_, rs_ = meta["_confidence"]
                meta["_confidence"] = (g_, list(rs_) + [rv_note])
                meta["현장 신뢰도"] = g_ + " — " + "; ".join(meta["_confidence"][1])
            write_report(out, items, plan_counts, [], est, meta, derived, metal)
        except Exception as ex:
            log(f"  (확인사항에 적지 못함: {ex})")
    RUN_INFO.update({"결과": out, "도곽": len(frames), "일람표": len(kinds["schedule"]), "부호": len(items),
                     "신뢰도": (meta.get("_confidence") or ("", []))[0], "먼저볼것": len(meta.get("_checks") or []),
                     "파일": len([f for f in os.listdir(path) if f.lower().endswith(".dxf")]) if os.path.isdir(path) else 1,
                     "계단": sum(1 for r in (getattr(plan_counts, "stair_rows", []) or []) if r.get("수량") is not None),
                     "핸드레일m": round(sum(r["수량"] for r in (getattr(plan_counts, "stair_rows", []) or []) if r.get("수량") is not None), 1)})
    return out, items, plan_counts, est


RUN_INFO = {}


def write_run_record(drawing, code, log=print):
    """work\\실행기록.csv 에 실행마다 한 줄 (엑셀에서 바로 열리게 UTF-8 BOM)."""
    import csv
    path = os.path.join(os.getcwd(), "실행기록.csv")
    secs = stage_seconds()
    cols = ["시각", "버전", "입력", "결과코드", "파일수", "도곽수", "일람표장수", "부호수", "신뢰도", "먼저볼것", "계단수", "핸드레일m",
            "변환초", "분석초", "집계보고초", "PDF초", "전체초", "결과경로"]
    d = dict(secs)
    row = [datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), VERSION, drawing, code, RUN_INFO.get("파일", ""), RUN_INFO.get("도곽", ""),
           RUN_INFO.get("일람표", ""), RUN_INFO.get("부호", ""), RUN_INFO.get("신뢰도", ""), RUN_INFO.get("먼저볼것", ""),
           RUN_INFO.get("계단", ""), RUN_INFO.get("핸드레일m", ""),
           round(d.get("변환", 0)), round(d.get("분석", 0)), round(d.get("집계·보고", 0)), round(d.get("검토도면 PDF", 0)),
           round(sum(v for _, v in secs)), RUN_INFO.get("결과", "")]
    try:
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as fp:
            w = csv.writer(fp)
            if new:
                w.writerow(cols)
            w.writerow(row)
    except OSError as ex:
        log(f"  (실행기록.csv 에 적지 못함: {ex})")


def _is_estimate_file(p):
    return p.lower().endswith((".xls", ".xlsx", ".xlsm"))


def main(argv=None):
    """명령줄 실행: 화면에는 진행 상황만, 자세한 기록(라이브러리 경고 포함)은 결과 옆 로그 파일에 남긴다."""
    import logging
    import traceback
    ap = argparse.ArgumentParser()
    ap.add_argument("drawing")
    ap.add_argument("-e", "--estimate")
    ap.add_argument("-o", "--out")
    a = ap.parse_args(argv)
    # 입력 점검: 견적서(엑셀)를 도면 자리에 넣은 경우
    if _is_estimate_file(a.drawing):
        if a.estimate and not _is_estimate_file(a.estimate):
            a.drawing, a.estimate = a.estimate, a.drawing          # 순서만 바뀐 것
            print("도면과 견적서 순서가 바뀌어 있어 바로잡았습니다.")
        else:
            print("!! 도면 자리에 엑셀 파일이 들어왔습니다: " + a.drawing)
            print("   도면(DWG/DXF/PDF 파일 또는 현장 폴더)을 넣어 주세요. 견적서는 -e 뒤(또는 두 번째)에 넣습니다.")
            return 2
    if not os.path.exists(a.drawing):
        print("!! 도면 경로를 찾을 수 없습니다: " + a.drawing)
        return 2
    if a.estimate and not os.path.exists(a.estimate):
        print("!! 견적서 경로를 찾을 수 없습니다(견적 비교 없이 진행): " + a.estimate)
        a.estimate = None
    is_cad = os.path.isdir(a.drawing) or a.drawing.lower().endswith((".dwg", ".dxf"))
    if not is_cad and not a.drawing.lower().endswith(".pdf"):
        print("!! 지원하지 않는 파일 형식입니다(DWG/DXF/PDF 또는 폴더): " + a.drawing)
        return 2
    src = a.drawing.rstrip("/\\")
    base = os.path.splitext(a.out)[0] if a.out else (src if os.path.isdir(src) else os.path.splitext(src)[0]) + "_창호물량"
    log_path = base + "_로그.txt"
    try:
        lf = open(log_path, "w", encoding="utf-8")
    except OSError:
        lf = None
    import time as _time
    t_start = _time.time()

    def log(msg):
        print(msg)
        if lf:
            el = int(_time.time() - t_start)
            lf.write(f"[{el // 60:02d}:{el % 60:02d}] {msg}\n"); lf.flush()

    class _Dedupe(logging.Filter):
        """같은 종류의 라이브러리 경고는 처음 2줄만 적고 개수를 센다"""
        seen = {}

        def filter(self, rec):
            k = re.sub(r"[#(\"'].*$", "", rec.getMessage())[:40]
            self.seen[k] = self.seen.get(k, 0) + 1
            return self.seen[k] <= 2
    dedupe = _Dedupe()
    STAGES.clear()
    RUN_INFO.clear()
    # 라이브러리(ezdxf 등) 경고는 화면에 뿌리지 않고 로그 파일에만
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    if lf:
        hd = logging.StreamHandler(lf)
        hd.setFormatter(logging.Formatter("  [라이브러리] %(name)s: %(message)s"))
        hd.addFilter(dedupe)
        root.addHandler(hd)
    else:
        root.addHandler(logging.NullHandler())
    root.setLevel(logging.WARNING)
    log(f"창호물량산출 {VERSION}  |  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log(f"도면: {a.drawing}" + (f"  |  견적서: {a.estimate}" if a.estimate else ""))
    code = 0
    try:
        if is_cad:
            run_cad(a.drawing, a.estimate, a.out, log=log)
        else:
            run(a.drawing, a.estimate, a.out)
    except PermissionError as ex:
        msg = str(ex)
        if msg.lower().endswith(".xlsx'") or ".xlsx" in msg:
            log("!! 결과 엑셀을 저장하지 못했습니다. 같은 이름의 결과 파일이 엑셀에 열려 있으면 닫고 다시 돌리세요.\n   " + msg)
        else:
            log("!! " + msg)
        code = 1
    except Exception as ex:
        log("!! 실행 중 오류: " + str(ex).splitlines()[0])
        if lf:
            lf.write(traceback.format_exc())
        code = 1
    finally:
        try:
            more = {k: n for k, n in dedupe.seen.items() if n > 2}
            if more and lf:
                lf.write("  [라이브러리] 되풀이된 경고: " + "; ".join(f"'{k.strip()}…' {n}번" for k, n in more.items()) + "\n")
            if STAGES:
                log("단계별 시간: " + ", ".join(f"{nm} {v:.0f}초" for nm, v in stage_seconds()) + f"  (전체 {_time.time() - t_start:.0f}초)")
            if is_cad:
                write_run_record(a.drawing, code, log=log)
            if code == 0 and RUN_INFO.get("결과"):
                # 배치 파일이 끝에 결과 위치를 보여 주는 데 쓴다
                with open(os.path.join(os.getcwd(), "마지막결과.txt"), "w", encoding="utf-8") as fp:
                    fp.write(os.path.abspath(RUN_INFO["결과"]) + "\n")
        except Exception:
            pass
        if lf:
            lf.close()
    print(f"로그: {log_path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
