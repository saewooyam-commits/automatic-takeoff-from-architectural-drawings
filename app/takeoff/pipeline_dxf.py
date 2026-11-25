"""DXF(DWG) 한 파일 → 창호 물량.

1. 모델공간을 도곽별 도면으로 나누고 일람표/창호평면도/평면도를 분류
2. 일람표: 층별수량표형(A현장식) 먼저, 안 되면 부호 기준 범용 파서
3. 평면도: 층마다 한 장(부호가 가장 많은 것)만 골라 부호 태그를 세고,
   '2~4층'처럼 여러 층 대표 도면은 층수만큼 곱한다
4. 수량 = 일람표 수량(있으면) / 없으면 평면도 집계, 둘 다 있으면 교차검증
"""
import re
from collections import Counter, defaultdict

from .dxf_source import load_dxf
from .frames import split_frames, classify
from .schedule import parse_schedule_sheet
from .schedule2 import parse_tag_schedule, find_tags
from .tags import floors_from_title


class PlanCounts(defaultdict):
    """층별 부호 수 + 위치 정보(다층 연속창호 판정용)."""
    def __init__(self):
        super().__init__(Counter)
        self.positions = {}
        self.frame_counts = {}
        self.code_mult = {}      # code -> {도면 한 장이 대표하는 층수}


def load_frames(path, log=print, xref_acc=None):
    """DXF 한 파일 → 도곽 목록. 파일 이름을 도곽에 기록해 폴더 입력 때 출처를 구분한다.
    xref_acc: {'합침': Counter(xref 파일), '못찾음': {xref 이름: [도면 파일]}} — 폴더 입력 때 xref 줄을 파일마다 찍지 않고 모아서 한 번에 요약."""
    import os
    sheet = load_dxf(path)
    for w in getattr(sheet, "warnings", []):
        m_ok = re.match(r"xref '(.+?)' ← (.+?) 합침", w)
        m_no = re.match(r"xref '(.+?)' 파일을 찾지 못함", w)
        if xref_acc is not None and m_ok:
            xref_acc["합침"][m_ok.group(2)] += 1
        elif xref_acc is not None and m_no:
            xref_acc["못찾음"].setdefault(m_no.group(1), []).append(os.path.basename(path))
        else:
            log(f"주의 [{os.path.basename(path)}]: " + w)
    frames = split_frames(sheet)
    for f in frames:
        f.file = os.path.basename(path)
        # 도곽이 한 장뿐이고 제목이 없으면 파일 이름이 곧 도면명(P현장식 '창호도-1.dwg')
        if len(frames) == 1 and not f.title:
            f.title = os.path.splitext(f.file)[0]
            f.title_from_file = True
    frames.method = getattr(sheet, "frame_method", "")
    return frames


def _dedupe_schedule_files(kinds, log=print):
    """같은 창호일람표가 여러 파일에 들어 있으면 수정일이 최신인 파일 것만 남긴다. 반환: 진단 줄 목록."""
    import datetime as _dt
    by_file = defaultdict(list)
    for f in kinds["schedule"]:
        by_file[getattr(f, "file", "")].append(f)
    if len(by_file) < 2:
        return []
    def read(fs):
        d = {}
        for f in fs:
            try:
                for it in (parse_schedule_sheet(f) or parse_tag_schedule(f)):
                    d.setdefault(it.code, (it.width, it.height, it.qty))
            except Exception:
                pass
        return d
    data = {fn: read(fs) for fn, fs in by_file.items()}
    notes, dropped, partial = [], set(), set()
    files = sorted(by_file, key=lambda fn: -(getattr(by_file[fn][0], "mtime", None) or 0))      # 최신 먼저
    for i, a in enumerate(files):
        if a in dropped:
            continue
        for b in files[i + 1:]:
            if b in dropped:
                continue
            ca, cb = set(data[a]), set(data[b])
            if min(len(ca), len(cb)) < 3 or len(ca & cb) < 0.7 * min(len(ca), len(cb)):
                continue
            ma, mb = getattr(by_file[a][0], "mtime", None), getattr(by_file[b][0], "mtime", None)
            na, nb = getattr(by_file[a][0], "src_name", a), getattr(by_file[b][0], "src_name", b)
            if len(ca) < 0.7 * len(cb):
                # 새 파일이 옛 파일의 일부만 담고 있으면(부분 개정 도면: 계단창만 다시 그린 파일 등) 옛 파일을 버리지 않는다.
                # 겹치는 부호만 새 파일 것을 쓰도록 새 파일 도곽을 앞에 둔다.
                chg = [c for c in sorted(ca & cb) if data[a][c] != data[b][c]]
                notes.append(f"개정본 중복(창호일람표 일부): '{na}' 에 '{nb}' 의 부호 {len(ca & cb)}개가 다시 그려져 있음 → 그 부호만 '{na}' 것을 씀"
                             + (f" (달라진 부호: {', '.join(chg[:8])}{' …' if len(chg) > 8 else ''})" if chg else " (내용은 같음)") + " → 확인")
                partial.add(a)
                continue
            diffs = []
            for c in sorted(ca | cb):
                va, vb = data[a].get(c), data[b].get(c)
                if va != vb:
                    f_ = lambda v: "없음" if v is None else f"{v[0] or '-'}×{v[1] or '-'}" + (f" {v[2]:g}개소" if v[2] is not None else "")
                    diffs.append(f"{c}: {f_(va)} ↔ {f_(vb)}")
            dtxt = ("; 다른 점(쓴 쪽 ↔ 안 쓴 쪽) " + " / ".join(diffs[:6]) + (" …" if len(diffs) > 6 else "")) if diffs else "; 내용은 같음"
            if ma and mb and abs(ma - mb) >= 86400:
                notes.append(f"개정본 중복(창호일람표): '{na}'({_dt.date.fromtimestamp(ma)})와 '{nb}'({_dt.date.fromtimestamp(mb)})에 같은 일람표가 있음 "
                             f"→ 수정일이 최신인 '{na}' 사용{dtxt} → 어느 쪽이 최종본인지 확인")
            else:
                notes.append(f"개정본 중복(창호일람표): '{na}'와 '{nb}'에 같은 일람표가 있음(수정일로 구분 못 함) → '{na}' 사용{dtxt} → 확인")
            dropped.add(b)
    if partial:
        kinds["schedule"].sort(key=lambda f: 0 if getattr(f, "file", "") in partial else 1)
    if dropped:
        kinds["schedule"] = [f for f in kinds["schedule"] if getattr(f, "file", "") not in dropped]
        kinds["old_revision"] = list(dropped)
    return notes


def run_dxf(path, log=print):
    """path: DXF 파일 또는 DXF 여러 개가 든 폴더(→ 도곽을 모두 합쳐 한 현장으로 처리)."""
    import os
    from . import schedule2 as _s2
    _s2.SINGLE_TYPES.clear()
    _s2.STACKED.clear()
    if os.path.isdir(path):
        frames, methods = [], []
        files = sorted(f for f in os.listdir(path) if f.lower().endswith(".dxf") and "._eof" not in f and not f.startswith("_"))
        bad = []
        xacc = {"합침": Counter(), "못찾음": {}}
        for fn in files:
            try:
                fr = load_frames(os.path.join(path, fn), log=log, xref_acc=xacc)
            except Exception as ex:                      # 손상된 DXF 하나 때문에 전체를 멈추지 않는다
                log(f"  !! 읽기 실패 → 건너뜀: {fn} ({str(ex).splitlines()[0][:90]})")
                bad.append(fn)
                continue
            methods.append(getattr(fr, "method", ""))
            frames += fr
        # 원본 파일 수정일 (run.py 가 변환하며 적어 둔 _sources.json)
        try:
            import json as _json
            _src = _json.load(open(os.path.join(path, "_sources.json"), encoding="utf-8"))
        except Exception:
            _src = {}
        for i, f in enumerate(frames, 1):
            f.index = i
            info = _src.get(getattr(f, "file", ""), {})
            f.mtime = info.get("수정일")
            f.src_name = info.get("원본", getattr(f, "file", ""))
        if xacc["합침"]:
            log(f"외부참조(xref) {len(xacc['합침'])}종을 찾아 합침: " + ", ".join(f"{n} ×{c}" for n, c in xacc["합침"].most_common()))
        for xn, fl in xacc["못찾음"].items():
            log(f"주의: xref '{xn}' 파일을 찾지 못함 → 그 부분은 비어 있음 ({len(fl)}개 도면: " + ", ".join(fl[:4]) + (" …" if len(fl) > 4 else "") + ")")
        log(f"폴더 입력: 파일 {len(files)}개 → 도곽 {len(frames)}장" + (f" (읽기 실패 {len(bad)}개: {', '.join(bad)})" if bad else ""))
        frame_method = "; ".join(m for m in methods if m) + ((" / 읽기 실패: " + ", ".join(bad)) if bad else "")
        if not frames:
            raise RuntimeError("폴더의 DXF 를 하나도 읽지 못했습니다: " + ", ".join(bad))
    else:
        frames = load_frames(path, log=log)
        frame_method = getattr(frames, "method", "")
    kinds = defaultdict(list)
    for f in frames:
        kinds[classify(f)].append(f)
    # 개정본 중복(일람표): 같은 창호일람표가 여러 파일에 있으면(부호 70% 이상 겹침) 수정일이 최신인 파일 것만 쓴다.
    revision_notes = _dedupe_schedule_files(kinds, log)
    # 한 도곽 안에 층별 평면도가 여러 개(소제목 '1 층평면도 / 2-3층평면도 / …')이고 태그가 있으면 → 소제목으로 나눠 층별 창호평면도로 본다.
    # (도곽 제목이 마지막 소제목으로 잡혀 태그 전부가 한 층으로 세지던 것: P현장 창호부호도)
    # 파일 이름이 창호부호도/창호평면도면 일반 평면도보다 우선(창호평면도로 분류).
    from .frames import split_by_subtitles as _sbs
    split_notes = []
    for f in list(kinds["plan"]) + list(kinds["tagplan"]):
        try:
            parts = _sbs(f, lambda t: bool(re.search(r"평면도|안내도|부호도|창호도", t)) and bool(floors_from_title(t)))
        except Exception:
            parts = []
        with_tags = [(st, sub) for st, sub in parts if find_tags(sub)]
        floorsets = {tuple(floors_from_title(st)) for st, _ in parts}
        if len(parts) >= 2 and len(floorsets) >= 2 and len(with_tags) >= 2:
            for k in ("plan", "tagplan"):
                if f in kinds[k]:
                    kinds[k].remove(f)
            for st, sub in parts:
                sub.file = getattr(f, "file", "")
                sub.split_from = f.title
                kinds["tagplan"].append(sub)
            split_notes.append(f"도곽 {f.index} '{f.title}'" + (f" ({f.file})" if getattr(f, "file", "") else "")
                               + f" 안의 층별 평면도 {len(parts)}개를 소제목으로 나눠 집계: " + ", ".join(st.strip() for st, _ in parts))
    for f in list(kinds["plan"]):
        if re.search(r"창호\s*(부호|평면|안내)|부호도", os.path.splitext(getattr(f, "file", "") or "")[0]) and find_tags(f):
            kinds["plan"].remove(f)
            kinds["tagplan"].append(f)
    content_sched = []
    if not kinds["schedule"]:
        # 제목으로 일람표를 하나도 못 찾았을 때(처음 보는 표제란 양식·도면명 오기): 내용으로 판정한다.
        # 일람표는 부호가 한 번씩만 나오고(평면도는 같은 부호가 반복) 부호마다 크기 치수가 붙는다.
        for f in frames:
            if classify(f) in ("tagplan", "plan") or floors_from_title(f.title):
                continue
            try:
                tg = find_tags(f)
                codes = [t[0] for t in tg]
                if len(set(codes)) < 3 or len(set(codes)) < 0.8 * len(codes):
                    continue
                got = parse_tag_schedule(f)
                sized = [it for it in got if it.width and it.height]
                if len(sized) >= 3 and len(sized) >= 0.6 * len(got):
                    content_sched.append(f)
            except Exception:
                continue
        for f in content_sched:
            for k in list(kinds):
                if f in kinds[k]:
                    kinds[k].remove(f)
            kinds["schedule"].append(f)
    log(f"도곽 {len(frames)}장: 일람표 {len(kinds['schedule'])}, 창호평면도 {len(kinds['tagplan'])}, 평면도 {len(kinds['plan'])}")
    if content_sched:
        log("  (제목으로는 일람표를 찾지 못해 내용(부호+치수)으로 판정: " + ", ".join(f"도곽 {f.index} '{f.title}'" for f in content_sched) + ")")
    # 여러 동: 일람표 제목에 동이 2개 이상이면 부호 앞에 '1동·' 을 붙여 동별로 따로 다룬다
    from .buildings import assign_buildings, tag as btag
    bmap = assign_buildings(frames, classify)
    buildings = sorted({bmap.get(f.index) for f in kinds["schedule"] if bmap.get(f.index)})
    multi = len(buildings) >= 2
    if multi:
        log(f"여러 동 도면: {', '.join(buildings)} → 부호를 동별로 구분")
    def bld(f):
        return bmap.get(f.index) if multi else None

    items = {}
    diag_dups = set()        # 같은 장 안에서 부호가 중복된 것
    conflicts = {}           # code -> {(도곽, 수량)} : 여러 일람표에 같은 부호가 다른 수량으로 적힌 경우
    size_conf = {}           # code -> {(도곽, WxH)} : 같은 부호가 다른 크기로 두 번 그려진 경우(부호 중복 오류)
    partial_sum_notes = []
    for f in kinds["schedule"]:
        got = parse_schedule_sheet(f) or parse_tag_schedule(f)
        for it in got:
            it.building = bld(f)
            it.code = btag(it.code, it.building)
            cur = items.get(it.code)
            # 같은 장 안에 같은 부호가 다른 크기로 여러 번(부호 복사 실수: M현장 PD-1 ×3) → 따로 살린다: PD-1, PD-1(2), PD-1(3)
            if cur is not None and cur.sheet == it.sheet and cur.width and it.width and \
                    (abs(cur.width - it.width) > 60 or abs((cur.height or 0) - (it.height or 0)) > 60):
                n = 2
                while f"{it.code}({n})" in items:
                    n += 1
                dup_note = f"같은 장에 '{it.code}' 부호가 다른 크기로 여러 번 → 임시로 {it.code}({n}) 로 구분 (도면의 부호 오기 가능성, 확인 필요)"
                it.code = f"{it.code}({n})"
                it.notes.append(dup_note)
                cur_first = items.get(it.code.split("(")[0])
                if cur_first is not None and dup_note not in cur_first.notes:
                    cur_first.notes.append(f"같은 장에 같은 부호가 다른 크기로 여러 번 → {it.code} 등으로 구분 (확인 필요)")
                diag_dups.add(it.code.split("(")[0])
                cur = None
            if cur is not None and cur.width and it.width and cur.sheet != it.sheet and \
                    (abs(cur.width - it.width) > 60 or abs((cur.height or 0) - (it.height or 0)) > 60):
                # 한쪽 값이 이어진 치수의 '일부만 더한 합'이면(치수선이 중간에 끊겨 읽힘) 큰 쪽이 맞다: 어느 파일이 새것인지보다 먼저 본다
                picked = None
                for dim, word in (("height", "세로"), ("width", "가로")):
                    other = "width" if dim == "height" else "height"
                    a_, b_ = getattr(cur, dim) or 0, getattr(it, dim) or 0
                    if abs((getattr(cur, other) or 0) - (getattr(it, other) or 0)) > 60 or abs(a_ - b_) <= 60:
                        continue
                    small, big = (cur, it) if a_ < b_ else (it, cur)
                    if any(f"이어진 {word} 치수 합" in n for n in small.notes):
                        picked = (small, big, dim)
                if picked:
                    small, big, dim = picked
                    note = (f"같은 부호가 {small.sheet} 에서는 {small.width}×{small.height} 로 읽힘 — 이어진 치수의 일부만 더한 값으로 보고 "
                            f"큰 쪽({big.sheet} {big.width}×{big.height})을 씀 (확인)")
                    if big is it:
                        it.qty = it.qty or cur.qty
                        items[it.code] = it
                        cur = it
                    if note not in cur.notes:
                        cur.notes.append(note)
                    partial_sum_notes.append(f"{it.code}: {note}")
                    continue
                size_conf.setdefault(it.code, set()).update({(cur.sheet, f"{cur.width}×{cur.height}"), (it.sheet, f"{it.width}×{it.height}")})
            if cur is None or (not cur.width and it.width):
                if cur is not None and cur.qty and it.qty and cur.qty != it.qty:
                    conflicts.setdefault(it.code, set()).update({(cur.sheet, cur.qty), (it.sheet, it.qty)})
                items[it.code] = it
            elif it.qty and not cur.qty:
                cur.qty = it.qty
            elif it.qty and cur.qty and it.qty != cur.qty:
                conflicts.setdefault(it.code, set()).update({(cur.sheet, cur.qty), (it.sheet, it.qty)})
    # 문 번호 목록(한 줄 = 문 하나)은 평면도에서 세지 않는다: 수량이 목록에 이미 있고, 'A'·'B' 같은 한 글자 종류가 평면도의 다른 글자와 섞인다
    types = {it.type for it in items.values() if it.type and getattr(it, "layout", "") != "doorlist"}
    log(f"일람표 부호 {len(items)}개, 종류 {sorted(types)}")
    diag = []
    n_dl = sum(1 for it in items.values() if getattr(it, "layout", "") == "doorlist")
    if n_dl:
        diag.append(f"문 번호 목록형 일람표에서 문 {n_dl}개를 읽음 (한 줄 = 문 하나, 수량은 목록·비고·층별 기준. 평면도 대조 없음)")
    voids = sorted({c for f in kinds["schedule"] for c in getattr(f, "void_codes", [])} - set(items))
    if voids:
        diag.append(f"일람표에 VOID(삭제) 로 덮인 부호 {len(voids)}개는 뺌: " + ", ".join(voids))
    plan_counts_dups = set(diag_dups)
    for code in sorted(diag_dups):
        diag.append(f"{code}: 한 일람표 안에 같은 부호가 다른 크기로 여러 번 그려짐 → {code}(2), {code}(3)… 으로 구분해 모두 올림 (도면 부호 오기 가능성, 확인 필요)")
    for code, s in sorted(conflicts.items()):
        diag.append(f"{code}: 일람표 여러 장에 수량이 다르게 적힘 → " +
                    ", ".join(f"{i} = {q:g}" for i, q in sorted(s, key=lambda x: str(x[0]))) + " (확인 필요)")
    diag.extend(partial_sum_notes)
    for code, s in sorted(size_conf.items()):
        diag.append(f"{code}: 같은 부호가 다른 크기로 두 번 그려짐 → " +
                    ", ".join(f"{i} = {q}" for i, q in sorted(s, key=lambda x: str(x[0]))) +
                    " (부호 중복 오류: 어느 쪽인지 확인 필요, 결과에는 먼저 읽은 것만 반영)")
        by = [it for it in items.values() if it.code == code]
        for it in by:
            it.notes.append("부호 중복(다른 크기로 두 번 그려짐) → 확인 필요")
    if not kinds["schedule"]:
        diag.append("창호일람표 도곽을 찾지 못함")
    diag += revision_notes
    diag += split_notes
    if content_sched:
        diag.append("도면 제목으로는 일람표를 찾지 못해 내용(부호+치수)으로 판정한 도곽: " + ", ".join(f"{f.index} '{f.title}'" for f in content_sched) + " → 제목·분류 확인 필요")
    elif not items:
        diag.append(f"창호일람표 도곽 {len(kinds['schedule'])}장은 찾았으나 부호를 읽지 못함 (일람표 형식이 다를 수 있음)")
    if not items:
        any_tags = sum(len(find_tags(f)) for f in frames)
        if any_tags == 0:
            diag.append("도면 전체에 창호 부호 태그가 없음 → 이 파일에는 창호 정보가 없는 것으로 판단 (평면·단면만 있는 변경도면 등)")
    if frame_method:
        diag.append("도곽: " + frame_method)
    if multi:
        diag.append("동별 도곽: " + "; ".join(
            f"{b}: " + ", ".join(str(f.index) for f in frames if bmap.get(f.index) == b and classify(f) in ("schedule", "plan", "tagplan"))
            for b in buildings) + " (제목에 동이 없는 평면도는 앞 장의 동을 이어받음)")
    for d in diag:
        log("진단: " + d)

    # 창호평면도가 있으면 우선, 단 도면명에 층이 없으면(여러 층이 한 장에 든 경우 등) 일반 평면도로 대체
    def usable(fs):
        return [f for f in fs if floors_from_title(f.title) and not re.search(r"확대|크린넷|부분|상세", f.title)]
    plans = usable(kinds["tagplan"])
    # 제목이 여러 층('지상3, 4층 창호평면도')인데 안에 층별 소제목 그림이 따로 있으면(3층 그림 + 4층 그림) 소제목으로 나눠 센다
    # (안 나누면 한 장의 태그를 층수만큼 곱해 두 배가 된다)
    from .frames import split_by_subtitles
    split_plans = []
    for f in plans:
        fl = floors_from_title(f.title)
        parts = []
        if len(fl) >= 2:
            parts = split_by_subtitles(f, lambda t: bool(re.search(r"평면도|안내도|창호도", t)) and len(floors_from_title(t)) == 1
                                       and set(floors_from_title(t)) <= set(fl))
            parts = [(st, sub) for st, sub in parts if find_tags(sub, types)]
        if len(parts) >= 2 and len({x for st, _ in parts for x in floors_from_title(st)}) >= 2:
            diag.append(f"'{f.title}' (도곽 {f.index}) 안의 층별 그림 {len(parts)}개를 소제목으로 나눠 집계")
            for _, sub in parts:
                sub.file = getattr(f, "file", "")
            split_plans += [sub for _, sub in parts]
        else:
            split_plans.append(f)
    plans = split_plans
    if kinds["tagplan"] and not plans:
        # 창호평면도 한 장에 여러 층이 든 경우: 소제목(…층 창호평면도)으로 나눈다
        from .frames import split_by_subtitles
        for f in kinds["tagplan"]:
            parts = split_by_subtitles(f, lambda t: bool(re.search(r"평면도|안내도|창호도", t)) and bool(floors_from_title(t)))
            for _, sub in parts:
                sub.file = getattr(f, "file", "")
            plans += [sub for _, sub in parts]
        if plans:
            diag.append("창호평면도를 소제목으로 " + str(len(plans)) + "개 층 도면으로 나눠 집계")
    if not plans:
        plans = usable(kinds["plan"])
        if kinds["tagplan"]:
            diag.append("창호평면도 " + ", ".join(f.title for f in kinds["tagplan"]) + ": 층을 나눌 수 없어 일반 평면도로 집계")
    by_floor = defaultdict(list)
    for f in plans:
        fl = tuple(floors_from_title(f.title))
        if not fl:
            continue
        b = bld(f)
        tg = [(btag(t[0], b),) + tuple(t[1:]) for t in find_tags(f, types)]
        if b:
            fl = tuple(f"{b} {x}" for x in fl)          # 층 이름에도 동을 붙여 동별로 따로 묶는다
        by_floor[fl].append((f, Counter(t[0] for t in tg), tg))
    # 같은 층 제목의 도면이 여럿이면: 부호 구성이 사실상 같으면(90% 이상 겹침) 같은 도면의 중복 → 부호가 많은 쪽 채택,
    # 아니면 다른 층의 제목 오기이거나 한 층을 나눠 그린 것 → 둘 다 집계하고 '(2)' 층으로 구분해 확인 요청
    # (지하1층/지하2층처럼 코어가 같은 층끼리도 절반 넘게 겹치므로 느슨한 기준은 쓰지 않는다)
    # 개정본 중복(평면도): 같은 층의 창호평면도가 수정일이 다른 여러 파일에 있으면 최신 파일 것만 쓴다 (예전 것을 '(2)층'으로 또 세지 않게)
    _finfo = {getattr(f, "file", ""): (getattr(f, "mtime", None), getattr(f, "src_name", getattr(f, "file", ""))) for f in frames}
    for lst in by_floor.values():
        for x in lst:
            if getattr(x[0], "mtime", None) is None:          # 소제목으로 나눈 조각은 원래 도곽의 파일 정보를 물려받는다
                x[0].mtime, x[0].src_name = _finfo.get(getattr(x[0], "file", ""), (None, getattr(x[0], "file", "")))
    old_plan_files = {}
    for fl, lst in list(by_floor.items()):
        mts = [getattr(x[0], "mtime", None) for x in lst]
        if len(lst) < 2 or any(m is None for m in mts) or max(mts) - min(mts) < 86400:
            continue
        newest = max(mts)
        keep = [x for x in lst if newest - x[0].mtime < 86400]
        for x in lst:
            if x not in keep and sum(x[1].values()):
                old_plan_files.setdefault((getattr(x[0], "src_name", ""), x[0].mtime), set()).update(fl)
        by_floor[fl] = keep
    if old_plan_files:
        import datetime as _dt
        newest_names = sorted({(getattr(x[0], "src_name", ""), x[0].mtime) for lst in by_floor.values() for x in lst if getattr(x[0], "mtime", None)},
                              key=lambda a: -a[1])[:3]
        diag.append("개정본 중복(창호평면도): " + ", ".join(f"'{n}'({_dt.date.fromtimestamp(m)}) {len(fs)}개 층" for (n, m), fs in sorted(old_plan_files.items()))
                    + " 은 같은 층의 더 새 파일(" + ", ".join(f"'{n}'({_dt.date.fromtimestamp(m)})" for n, m in newest_names) + " 등)이 있어 쓰지 않음 → 어느 쪽이 최종본인지 확인")
    best = {}
    for fl, lst in by_floor.items():
        lst.sort(key=lambda x: -sum(x[1].values()))
        best[fl] = lst[0]
        for k, (f, c, tg) in enumerate(lst[1:], 2):
            c0 = best[fl][1]
            overlap = sum((c & c0).values()) / max(sum(c.values()), 1)
            t0, t1 = re.sub(r"\s", "", best[fl][0].title or ""), re.sub(r"\s", "", f.title or "")
            if c and t0 != t1 and fl == ("RF",) and getattr(f, "file", "") == getattr(best[fl][0], "file", ""):
                # '지붕 평면도'와 '옥탑지붕 평면도'는 같은 파일 안의 서로 다른 층이다. 부호 구성이 비슷해도 둘 다 센다 (Q현장 FSD-5: 지붕 8 + 옥탑지붕 2)
                fl2 = tuple(f"{x}({k})" for x in fl)
                best[fl2] = (f, c, tg)
                diag.append(f"'{best[fl][0].title}' 와 '{f.title}' 는 서로 다른 층으로 보고 둘 다 셈 ({'/'.join(fl2)})")
                continue
            if overlap >= 0.9 or not c:
                continue
            fl2 = tuple(f"{x}({k})" for x in fl)
            best[fl2] = (f, c, tg)
            diag.append(f"'{f.title}' 제목의 평면도가 {len(lst)}장이고 부호 구성이 다름 → "
                        f"{'/'.join(fl2)} 로 따로 집계 (다른 층의 제목 오기이거나 한 층을 나눠 그린 도면: 층 이름 확인 필요)")
    overlays = _split_typical(best, kinds["schedule"], diag)
    plan_counts = PlanCounts()
    plan_counts.dup_codes = plan_counts_dups
    plan_counts.floor_rule = {}       # 부호 → 층별 적용 근거 글
    # 검산용 두 번째 집계: 층 이름으로 고르는 과정(같은 층 도면 중 하나만 쓰기)을 거치지 않고,
    # 남아 있는 평면도 도곽을 '제목이 다른 장'마다 모두 더한다. 첫 집계가 도면 한 장을 통째로 빠뜨리면 여기서 드러난다.
    alt = Counter()
    for fl, lst in by_floor.items():
        seen_t = {}
        for f_, c_, _tg in lst:
            tkey = re.sub(r"\s", "", f_.title or "")
            if tkey not in seen_t or sum(c_.values()) > sum(seen_t[tkey].values()):
                seen_t[tkey] = c_
        for c_ in seen_t.values():
            for code, n in c_.items():
                alt[code] += n * len(fl)
    plan_counts.alt = alt
    plan_counts_pos = defaultdict(list)    # code -> [(도곽, 상대x, 상대y)]
    used = []
    for fl, (f, c, tg) in sorted(best.items()):
        bx0, by0, bx1, by1 = f.bounds
        for code in c:
            plan_counts.code_mult.setdefault(code, set()).add(len(fl))
        for t in tg:
            plan_counts_pos[t[0]].append((f.index, (t[3].cx - bx0) / (bx1 - bx0), (t[3].cy - by0) / (by1 - by0)))
        ov = overlays.get(fl)
        used.append(f"{f.title} (도곽 {f.index}) ×{len(fl)}층" + (" (자리마다 해당 층만 곱함)" if ov and ov.get("ok") else ""))
        for code, n in c.items():
            if ov and ov.get("ok") and code in ov["per"]:
                for x in fl:
                    plan_counts[code][x] += ov["per"][code].get(x, 0)
                plan_counts.floor_rule[code] = ov["why"].get(code, "")
            else:
                for x in fl:
                    plan_counts[code][x] += n
                if ov and not ov.get("ok") and code in ov.get("suspect", ()):
                    plan_counts.floor_rule[code] = ov["flag"]
    # 평면도의 하위 변형 부호(AW-3-1)가 일람표에 없고 기본 부호(AW-3)만 있으면 기본 부호로 합산
    from .schedule import base_code
    folded = []
    for code in list(plan_counts.keys()):
        b = base_code(code)
        if b != code and code not in items and b in items:
            for fl, n in plan_counts.pop(code).items():
                plan_counts[b][fl] += n
            folded.append(f"{code}→{b}")
    if folded:
        diag.append("평면도 변형 부호를 기본 부호로 합산: " + ", ".join(folded))
    log("평면도 사용: " + "; ".join(used))
    # 입면도에서 창호도 모양 찾기
    plan_counts.elev = {}
    plan_counts.elev_hits = {}
    if kinds["elevation"]:
        from .elevmatch import match_elevations
        try:
            # 여러 동이면 같은 동의 입면도에서만 찾는다 (다른 동 입면도에서 같은 모양을 세면 배로 늘어남)
            groups = [(None, list(items.values()), kinds["elevation"])] if not multi else \
                [(b, [it for it in items.values() if it.building == b], [f for f in kinds["elevation"] if bmap.get(f.index) == b])
                 for b in buildings]
            all_res, all_tpls = {}, set()
            for b, its, efs in groups:
                if not its or not efs:
                    continue
                res, tpls = match_elevations(its, efs, tol=15, log=log)
                all_res.update(res)
                all_tpls |= set(tpls)
                diag += list(getattr(res, "skipped", []))
            plan_counts.elev = {c: len(v) for c, v in all_res.items()}
            plan_counts.elev_hits = dict(all_res)
            plan_counts.elev_templates = all_tpls
            log(f"입면도 {len(kinds['elevation'])}장에서 모양 비교: 형판 {len(all_tpls)}개, 찾은 부호 {len(all_res)}개")
        except Exception as ex:
            log(f"입면도 비교 실패: {ex}")
    # 세대 타입별 확대 평면도("A" TYPE 확대 창호평면도 / 단위세대 평면도): 층 평면도에 세대 안 부호가 없을 때 세대수 × 타입별 개수로 보탠다
    plan_counts.unit_types = {}
    plan_counts.unit_count = None
    unit_fs = [f for f in kinds["tagplan"] + kinds["plan"] if not floors_from_title(f.title)
               and re.search(r"TYPE|타입|단위\s*세대|세대\s*(확대|창호)?\s*평면", f.title, re.I)]
    if unit_fs:
        for f in unit_fs:
            b = bld(f)
            tg = [(btag(t[0], b),) + tuple(t[1:]) for t in find_tags(f, types)]
            cnt = Counter(t[0] for t in tg)
            if cnt:
                plan_counts.unit_types[f"{f.title} (도곽 {f.index})"] = cnt
        if plan_counts.unit_types:
            # 세대수: 모든 타입에 꼭 1개씩 있는 부호의 일람표 수량이 서로 같으면 그 값 (예: PW-2·PPW-1·PD-1 모두 58EA → 58세대)
            common = [c for c in set.intersection(*[set(v) for v in plan_counts.unit_types.values()])
                      if all(v[c] == 1 for v in plan_counts.unit_types.values())]
            vals = Counter(items[c].qty for c in common if c in items and items[c].qty)
            if vals:
                n, k = vals.most_common(1)[0]
                if k >= 2 and k >= 0.6 * sum(vals.values()):
                    plan_counts.unit_count = int(n)
                    plan_counts.unit_count_basis = ", ".join(f"{c} {items[c].qty:g}EA" for c in sorted(common) if c in items and items[c].qty == n)
            if plan_counts.unit_count:
                diag.append(f"세대 타입 평면도 {len(plan_counts.unit_types)}장 → 세대수 {plan_counts.unit_count} 추정"
                            f"(모든 타입에 1개씩 있는 부호의 일람표 수량: {plan_counts.unit_count_basis}); "
                            "일람표 수량이 없는 세대 안 부호는 세대수 × 타입별 개수로 보탬 (타입별 세대수는 알 수 없어 모든 타입에 있는 부호만)")
            else:
                diag.append(f"세대 타입 평면도 {len(plan_counts.unit_types)}장이 있으나 세대수를 정할 근거(일람표 수량)가 없음 → 세대 안 부호 수량은 확인 필요")
    plan_counts.positions = dict(plan_counts_pos)
    plan_counts.frame_counts = {fl: (f.index, c) for fl, (f, c, tg) in best.items()}
    plan_counts.plan_frames = [(f, fl, tg) for fl, (f, c, tg) in sorted(best.items())]
    # 쓴 평면도가 건물 층을 얼마나 덮나 (지붕층 부호도만 있는 현장처럼 일부만 있으면 평면도 집계를 일람표보다 앞세우지 않는다)
    all_floors = set()
    for f in kinds["plan"] + kinds["tagplan"]:
        for x in floors_from_title(f.title):
            all_floors.add(x)
    used_floors = {x.split(" ")[-1] for fl in best for x in fl}        # '1동 RF' → 'RF' (동 접두 제거)
    plan_counts.plan_coverage = (len(used_floors) / len(all_floors)) if all_floors else (1.0 if used_floors else 0.0)
    plan_counts.all_floors = sorted(all_floors)
    plan_counts.used_floors = sorted(used_floors)
    if _s2.STACKED:
        diag.append("평면도에 같은 부호가 정확히 같은 자리에 겹쳐 그려진 것은 하나로 셈: "
                    + ", ".join(f"'{t}' {n}개" for (_, t), n in sorted(_s2.STACKED.items(), key=lambda kv: kv[0][1])[:8]))
    if all_floors and plan_counts.plan_coverage < 0.6:
        diag.append(f"평면도 집계가 전체 층의 {plan_counts.plan_coverage:.0%}만 덮음({len(used_floors)}/{len(all_floors)}층) → 수량은 일람표 우선")
    plan_counts.sched_frames = kinds["schedule"]
    plan_counts.diagnosis = diag
    plan_counts.elev_frames = kinds["elevation"]
    return frames, kinds, list(items.values()), plan_counts, used


def is_multistorey(it, per_floor, rules=None):
    """여러 층 평면에 반복 표기된 한 덩어리 창호(커튼월 등)인지. 기준은 rules["다층창호"]."""
    R = (rules or {}).get("다층창호", {"높이기준_mm": 4500, "항상종류": ["ACW", "CW", "SCW"], "항상종류_최소높이_mm": 3000})
    if it is None or len([f for f, n in per_floor.items() if n]) < 2:
        return False
    if it.type in R.get("항상종류", []):
        # 커튼월 종류라도 작은 창(E현장 ACW-5 750×1,600 프로젝트창)은 층마다 따로 세는 보통 창
        h = it.height or 0
        return h == 0 or h >= R.get("항상종류_최소높이_mm", 3000)
    if any("비례로 높이" in n for n in getattr(it, "notes", [])):
        return False                # 그림 비례로 추정한 높이로는 다층 창호라 단정하지 않는다 (N현장 AW-2: 추정 4800 → 15개가 12개로 깎임)
    return (it.height or 0) >= R.get("높이기준_mm", 4500) and (it.width or 0) > 0


_STATED_RE = re.compile(r"\d+\s*개\s*소\s*\(\s*(지상\s*(\d{1,2})\s*(?:층)?\s*(?:[~\-]\s*(?:지상)?\s*(\d{1,2})\s*)?층)\s*[:)]")

_NOTE_PARITY = {}      # 부호 → ('even'|'odd'|'both', 주기 글): 그 부호 칸에 적힌 '해당층' 주기가 짝수층만/홀수층만인지


def _s2_find_tags(f):
    from . import schedule2 as _s2
    return _s2.find_tags(f)


_FLOOR_NOTE_RE = re.compile(r"해당\s*층\s*[:：]")


def _alt_floor_notes(sched_frames):
    """일람표의 '*배연창 해당층 : 2F, 4F, …, 16F', '*완강기 해당층 : A-TYPE(3F, 5F, 7F, 9F)' 같은 주기에서
    짝수층만 적힌 것과 홀수층만 적힌 것을 모은다. 둘 다 있으면 도면이 '짝수층과 홀수층의 배치가 다르다'고 말하는 것이다.
    반환 (짝수층 주기 예, 홀수층 주기 예)"""
    ev, od = [], []
    _NOTE_PARITY.clear()
    for f in sched_frames:
        for t in f.texts:
            if not _FLOOR_NOTE_RE.search(t.text):
                continue
            body = t.text.split(":", 1)[-1] if ":" in t.text else t.text
            if "~" in body:
                continue                      # '3F~10F' 는 전 층
            ns = [int(x) for x in re.findall(r"(\d{1,2})\s*F", body)]
            if len(ns) < 3 or any(b_ - a_ != 2 for a_, b_ in zip(ns, ns[1:])):
                continue
            (ev if ns[0] % 2 == 0 else od).append(t.text.strip()[:60])
            # 그 주기가 어느 부호 칸의 것인지: 같은 줄(부호 글자 높이의 3배 안) 바로 왼쪽의 부호 태그
            try:
                tags = f.__dict__.setdefault("_tags_cache", _s2_find_tags(f))
            except Exception:
                tags = []
            near = [(t.cx - g[3].cx, g) for g in tags if abs(g[3].cy - t.cy) <= 3 * max(g[3].y1 - g[3].y0, 1) and 0 <= t.cx - g[3].cx <= 40 * max(g[3].y1 - g[3].y0, 1)]
            if near:
                g = min(near, key=lambda z: z[0])[1]
                par = "even" if ns[0] % 2 == 0 else "odd"
                prev = _NOTE_PARITY.get(g[0])
                _NOTE_PARITY[g[0]] = (par, t.text.strip()[:60]) if prev is None or prev[0] == par else ("both", "")
    return ev, od


def _split_typical(best, sched_frames, diag):
    """기준층 평면도 한 장('4~17층')에 짝수층 배치와 홀수층 배치의 부호가 같이 찍힌 경우, 자리마다 '그 창이 있는 층'만 곱한다.
    도면 근거 세 가지가 모두 있을 때만 적용한다(하나라도 없으면 전 층으로 곱하고 '층별 적용 확인 필요'로 표시):
      ① 일람표에 짝수층만 적힌 '해당층' 주기와 홀수층만 적힌 주기가 둘 다 있다 (배치가 한 층 걸러 다르다는 도면의 말)
      ② 기준층 바로 아래 두 층(2층·3층)의 평면도가 따로 있고 서로 부호 구성이 다르다 (두 배치가 실제로 그려져 있다)
      ③ 기준층 도면의 태그가 자리마다 그 두 장 중 하나의 같은 부호 태그와 맞는다 (기준층 도면 = 두 배치를 한 장에 찍은 것)
    층수는 추정하지 않는다: 2층 배치에서 온 자리는 그 도면 범위의 짝수층, 3층 배치에서 온 자리는 홀수층에만 곱한다.
    반환 {층묶음: {ok, per: {부호: {층: 개수}}, why: {부호: 글}} 또는 {ok: False, suspect, flag}}"""
    out = {}
    ev, od = _alt_floor_notes(sched_frames)
    for fl, (f, c, tg) in best.items():
        nums = [re.fullmatch(r"(\d+)F", x) for x in fl]
        if len(fl) < 4 or not all(nums):
            continue
        ns = sorted(int(m.group(1)) for m in nums)
        if ns != list(range(ns[0], ns[-1] + 1)) or ns[0] < 3:
            continue
        A, B = best.get((f"{ns[0] - 2}F",)), best.get((f"{ns[0] - 1}F",))
        if not A or not B or A[1] == B[1]:
            continue
        tot = sum(c.values())
        if not tot or tot < 1.6 * max(sum(A[1].values()), sum(B[1].values())):
            continue
        # ③ 자리 맞추기: 기준층 태그마다 같은 부호의 2층/3층 태그 중 가장 가까운 것(도곽 안 상대 위치, 한 번씩만)
        def rel(fr_, t_):
            x0_, y0_ = fr_.bounds[0], fr_.bounds[1]
            return (t_[3].cx - x0_, t_[3].cy - y0_)
        pool = [(t_[0], rel(A[0], t_), "A") for t_ in A[2]] + [(t_[0], rel(B[0], t_), "B") for t_ in B[2]]
        hh = max((t_[3].y1 - t_[3].y0) for t_ in tg) if tg else 1
        lim = hh * 8                                   # 한 창에 두 부호를 나란히 찍느라 옆으로 밀린 거리까지
        cand = []
        for i, t_ in enumerate(tg):
            px, py = rel(f, t_)
            for j, (code_, (qx, qy), src) in enumerate(pool):
                if code_ == t_[0]:
                    d_ = abs(px - qx) + abs(py - qy)
                    if d_ <= lim:
                        cand.append((d_, i, j))
        cand.sort()
        src_of, used_j = {}, set()
        for d_, i, j in cand:
            if i in src_of or j in used_j:
                continue
            src_of[i] = pool[j][2]
            used_j.add(j)
        matched = len(src_of) / len(tg)
        # 2층·3층 평면도에 같은 부호가 없는 자리: 같은 창(바로 옆)에 찍힌 다른 부호가 한쪽 배치로 정해졌으면 이 부호는 반대쪽 배치다
        # (한 창에 두 부호가 나란히 찍힌 것은 한 층 걸러 번갈아 들어간다는 뜻. 2층만 그 자리 창이 다르거나 부호가 다르게 적힌 경우)
        partner_note = {}
        for i, t_ in enumerate(tg):
            if i in src_of:
                continue
            px, py = rel(f, t_)
            near_ = sorted((abs(px - rel(f, u)[0]) + abs(py - rel(f, u)[1]), j) for j, u in enumerate(tg)
                           if j != i and u[0] != t_[0] and j in src_of)
            if near_ and near_[0][0] <= lim and (len(near_) < 2 or near_[1][0] > near_[0][0] * 1.2):
                j = near_[0][1]
                partner_note[i] = tg[j][0]
        for i, pc in partner_note.items():
            j = next(j for j, u in enumerate(tg) if u[0] == pc and j in src_of and
                     abs(rel(f, tg[i])[0] - rel(f, u)[0]) + abs(rel(f, tg[i])[1] - rel(f, u)[1]) <= lim)
            src_of[i] = "B" if src_of[j] == "A" else "A"
        names = f"{ns[0] - 2}층·{ns[0] - 1}층"
        if matched < 0.9:
            continue                                   # 두 배치를 겹친 도면이 아니다: 평소대로 전 층
        if not (ev and od):
            sus = {t_[0] for t_ in tg}
            out[fl] = {"ok": False, "suspect": sus,
                       "flag": f"층별 적용 확인 필요: '{f.title}' 의 태그 {matched:.0%} 가 {names} 평면도의 태그와 자리가 맞아 두 층 배치를 한 장에 찍은 것으로 보이나, "
                               f"일람표에서 짝수층/홀수층 '해당층' 주기를 찾지 못해 전 층({len(ns)}개 층)으로 곱함"}
            diag.append(out[fl]["flag"])
            continue
        even_src = "A" if (ns[0] - 2) % 2 == 0 else "B"
        per, why = {}, {}
        n_even = [n for n in ns if n % 2 == 0]
        n_odd = [n for n in ns if n % 2 == 1]
        cnt = defaultdict(lambda: Counter())
        by_partner = Counter()
        for i, t_ in enumerate(tg):
            cnt[t_[0]][src_of.get(i, "?")] += 1
            if i in partner_note:
                by_partner[t_[0]] += 1
        for code, k in cnt.items():
            ke, ko, ku = k[even_src], k["B" if even_src == "A" else "A"], k["?"]
            np_ = _NOTE_PARITY.get(code)
            by_note = f" (이 중 {by_partner[code]}곳은 2층·3층 평면도에 같은 부호가 없어, 같은 창에 나란히 찍힌 부호의 반대쪽 배치로 정함 → 확인)" if by_partner.get(code) else ""
            if ku and np_ and np_[0] in ("even", "odd"):
                # 2층·3층 평면도에는 없는 부호지만 일람표의 그 부호 칸에 '해당층' 주기가 있다 → 그 주기의 짝·홀 층에만 곱한다
                if np_[0] == "even":
                    ke += ku
                else:
                    ko += ku
                by_note += f" (이 중 {ku}곳은 일람표 그 부호 칸의 주기 '{np_[1]}' 로 정함)"
                ku = 0
            d = {}
            for n in ns:
                d[f"{n}F"] = (ke if n % 2 == 0 else ko) + ku       # 자리도 주기도 없는 태그는 전 층
            per[code] = d
            why[code] = (f"'{f.title}' 에 {sum(k.values())}곳: 짝수층 배치({ns[0] - 2 if even_src == 'A' else ns[0] - 1}층 평면도와 같은 자리) {ke}곳 × {len(n_even)}개 층"
                         f" + 홀수층 배치 {ko}곳 × {len(n_odd)}개 층{by_note}" + (f" + 층별 적용 확인 필요: 2층·3층 평면도와 자리가 안 맞고 주기도 없는 {ku}곳은 전 층({len(ns)}개)으로 곱함" if ku else "")
                         + f" — 근거: 일람표 주기 '{ev[0]}' / '{od[0]}', {names} 평면도")
        out[fl] = {"ok": True, "per": per, "why": why}
        diag.append(f"'{f.title}' 는 짝수층 배치와 홀수층 배치의 부호를 한 장에 찍은 도면: 태그 {len(tg)}개 중 {len(src_of)}개가 {names} 평면도의 같은 부호와 자리가 맞음. "
                    f"자리마다 그 창이 있는 층만 곱함(짝수층 {len(n_even)}개, 홀수층 {len(n_odd)}개). 근거: 일람표 주기 '{ev[0]}', '{od[0]}'"
                    + (f"; 자리를 못 맞춘 태그 {len(tg) - len(src_of)}개는 전 층으로 곱함 → 확인" if len(src_of) < len(tg) else ""))
    return out


# L현장식: '지상1층 외부창호(108,116호) = 2EA', '지상3~9층 외부창호(각 층6,7호) = 1EA'
_STATED_RE2 = re.compile(r"(지상\s*(\d{1,2})\s*(?:~\s*(?:지상)?\s*(\d{1,2})\s*)?층)\s*(?:외부|내부)?\s*(?:창호|출입구)[^=]{0,60}=\s*\d+\s*EA")


def _stated_floors(it):
    """일람표 칸의 'N개소 (지상17층 : 기계주차)', '(지상3~17층:복도)', '(지상1~지상17층 : …)' → ('지상17층', {층})
    지상 한 층 또는 한 범위로 분명히 적힌 것만 읽는다(쉼표로 여러 곳, 지하 섞임 등은 읽지 않음)."""
    if it is None:
        return None
    txt = " ".join(x for x in (it.location, getattr(it, "spec_text", ""), it.frame, it.name) if x)
    m = _STATED_RE.search(txt) or _STATED_RE2.search(txt)
    if not m:
        return None
    a = int(m.group(2)); b = int(m.group(3) or a)
    return re.sub(r"\s", "", m.group(1)), {f"{i}F" for i in range(min(a, b), max(a, b) + 1)}


def final_quantities(items, plan_counts, rules=None):
    """부호별 (최종수량, 근거, 일람표수량, 평면도수량)."""
    out = {}
    elev = getattr(plan_counts, "elev", {}) or {}
    codes = set(i.code for i in items) | set(plan_counts)
    by = {i.code: i for i in items}
    # 평면도 집계의 건강도: 일람표 수량과 평면도 집계가 둘 다 있는 부호 중 일치 비율
    both = [(i.qty, sum(plan_counts.get(i.code, Counter()).values())) for i in items
            if i.qty is not None and sum(plan_counts.get(i.code, Counter()).values())]
    agree_d = len(both)
    agree_n = sum(1 for a, b in both if a == b)
    agree_rate = (agree_n / agree_d) if agree_d >= 4 else None
    for c in codes:
        it = by.get(c)
        sq = it.qty if it else None
        per = plan_counts.get(c, Counter())
        pq = sum(per.values()) or None
        E = elev.get(c)
        if E and pq:
            if sq is not None and pq == E and sq != pq:
                out[c] = (E, f"평면도·입면도 모두 {E}개 (일람표 {sq:g} 표기 오류 의심)", sq, pq)
                continue
            if sq is None and E == pq:
                out[c] = (pq, f"평면도 집계 = 입면도 {E}개 (입면도 검산 일치)", sq, pq)
                continue
            single = plan_counts.code_mult.get(c, set()) == {1}
            floors = [n for n in per.values() if n]
            if sq is None and E < pq and single and len(floors) >= 2 and all(n == E for n in floors):
                out[c] = (E, f"다층 연속창호: 평면도 {len(floors)}개 층에 같은 개수({E})로 반복 표기, 입면도 {E}개 채택", sq, pq)
                continue
        if E and not pq and sq is None:
            # 입면도 모양 일치만으로 세는 것은 추정이다. 터무니없이 많으면(격자 창을 모두 센 경우) 수량 없음으로 둔다
            if E > 30:
                out[c] = (None, f"수량 없음 (입면도 모양 일치 {E}개는 믿을 수 없어 버림: 일람표·평면도에 수량 표기 없음)", sq, pq)
            else:
                out[c] = (E, f"입면도 모양 일치 {E}개 (평면도에 부호 없음 → 추정, 확인 필요)", sq, pq)
            continue
        if pq and is_multistorey(it, per, rules):
            pq_raw = pq
            # 도곽 하나에 찍힌 개수의 최댓값(여러 층 대표 도면도 곱하지 않음)
            fc = getattr(plan_counts, "frame_counts", {})
            per_frame = [cnt.get(c, 0) for _, (idx, cnt) in fc.items()]
            pq = max(per_frame) if per_frame and max(per_frame) else max(per.values())
            if sq is None:
                out[c] = (pq, f"평면도 집계(다층 연속창호: 층마다 반복 표기 {pq_raw}개 → 층당 최대 {pq}개)", sq, pq_raw)
                continue
            # 회사 기준: 수량의 기준은 평면도. 여러 층에 걸친 한 덩어리는 층마다 다시 찍히므로 한 층에 찍힌 자리 수(층당 최대)가 평면도에서 센 값이다.
            # 일람표 개수로 덮어쓰지 않고, 다르면 표시한다.
            eq_m = re.search(r"(\S[^=]{0,60})=\s*(\d+)\s*EA", getattr(it, "spec_text", "") or "")
            if eq_m and sq is not None and int(eq_m.group(2)) == sq and pq != sq:
                # 일람표 칸에 '지상3~9층 외부창호(각 층1,2,3,4,5호) = 1EA' 처럼 "이 범위 전체가 1개"라고 글로 적혀 있다.
                # 평면도의 태그는 그 범위의 호실·층마다 다시 찍힌 것이므로 태그 수가 개수가 아니다 (L현장 ACW-11: 태그 32개 = 1EA)
                out[c] = (sq, f"일람표 글 '{eq_m.group(0).strip()[:60]}' — 이 범위 전체가 {sq:g}개. 평면도 태그 {pq_raw}개는 호실·층마다 반복 표기 → 입면도에서 확인", sq, pq_raw)
            elif sq == pq:
                out[c] = (pq, f"평면도 집계 = 일람표 (여러 층에 걸친 창: {len([1 for n in per.values() if n])}개 층에 반복 표기, 한 층에 {pq}곳)", sq, pq_raw)
            else:
                out[c] = (pq, f"평면도 집계 (여러 층에 걸친 창: 한 층에 최대 {pq}곳 찍힘, 반복 표기 합 {pq_raw}; 일람표 {sq:g}개소와 다름 → 입면도에서 확인)", sq, pq_raw)
            continue
        # 세대 타입 평면도에 있는 부호: 일람표 수량이 없으면 (코어 평면도 집계) + 세대수 × 타입별 개수
        ut = getattr(plan_counts, "unit_types", {}) or {}
        N = getattr(plan_counts, "unit_count", None)
        if ut and sq is None and any(c in v for v in ut.values()):
            per_type = [v.get(c, 0) for v in ut.values()]
            if N and all(per_type) and len(set(per_type)) == 1:
                add = N * per_type[0]
                tot = (pq or 0) + add
                out[c] = (tot, f"세대 {N}×{per_type[0]}개={add}" + (f" + 층 평면도 {pq}개" if pq else "") + " (세대 타입 평면도 기준, 확인 필요)", sq, pq)
            else:
                out[c] = (pq, f"수량 확인 필요: 세대 타입 평면도 일부에만 있음({'/'.join(str(x) for x in per_type)}개) → 타입별 세대수를 몰라 못 셈"
                          + (f"; 층 평면도 {pq}개만" if pq else ""), sq, pq)
            continue
        base = c.split("(")[0]
        if sq is not None and base in getattr(plan_counts, "dup_codes", set()):
            # 같은 부호가 다른 크기로 여럿이면 평면도 태그를 나눠 줄 수 없다 → 일람표 수량으로
            out[c] = (sq, f"일람표 (부호 중복이라 평면도 태그 {pq or 0}개를 나눌 수 없음 → 확인)", sq, pq)
            continue
        if sq is not None and pq is not None:
            stated = _stated_floors(it) if sq != pq else None
            if False:
                pass
            elif sq == pq:
                out[c] = (sq, "일람표=평면도", sq, pq)
            else:
                # 둘이 다를 때 (rules.json '수량우선'):
                #  '평면도'(기본) — 평면도 집계가 더 많을 때만 평면도를 쓴다(도면에 실제로 그려진 개수). 평면도가 더 적으면 태그 누락일 수 있어 일람표.
                #     단, 평면도 집계 자체가 믿을 만할 때만: 층의 60% 이상을 덮고, 일람표와 비교되는 부호의 절반 이상이 일치할 때.
                #     (P현장: 부호도를 지붕층 한 장으로 잘못 세어 5/16만 일치했는데도 일람표를 덮어쓰던 것을 막는다)
                #  '일람표' — 항상 일람표.
                pref = (rules or {}).get("수량우선", "평면도")
                why = ""
                if getattr(plan_counts, "plan_coverage", 1.0) < 0.6:
                    pref, why = "일람표", "평면도가 일부 층만 있음"
                elif agree_rate is not None and agree_rate < 0.5:
                    pref, why = "일람표", f"평면도 집계가 일람표와 {agree_n}/{agree_d}부호만 일치 → 평면도 집계를 믿기 어려움"
                # 회사 기준(2026-11-14): 수량의 기준은 평면도 집계. 일람표 개수가 더 많아도 덮어쓰지 않고 표시만 한다
                # (설계를 바꾸고 일람표 개수를 안 고치는 일이 많다). 평면도 집계 자체가 성립하지 않는 위 두 경우만 일람표.
                if pref == "일람표":
                    out[c] = (sq, f"일람표 우선(평면도 {pq:g}개와 다름{': ' + why if why else ''} → 확인)", sq, pq)
                elif E and E == pq:
                    out[c] = (pq, f"평면도 집계 = 입면도 {E}개 (일람표 {sq:g}개소와 다름)", sq, pq)
                else:
                    loc = ""
                    if stated:
                        in_st = sum(n for fl_, n in per.items() if fl_ in stated[1])
                        if in_st != pq:
                          loc = f"; 일람표 위치는 '{stated[0]}' 뿐인데 평면도에는 {len([1 for n in per.values() if n])}개 층에 찍혀 있음(그 층만 세면 {in_st})"
                    out[c] = (pq, f"평면도 집계 (일람표 {sq:g}개소와 다름{loc} → {'입면도 ' + str(E) + '개, ' if E else ''}확인)", sq, pq)
        elif sq is not None:
            out[c] = (sq, "일람표", sq, pq)
        elif pq is not None:
            out[c] = (pq, "평면도 집계", sq, pq)
        else:
            out[c] = (None, "수량 없음", sq, pq)
    return out
