"""검토용 표시 도면(PDF) - 찾은 결과를 도면 위에 색으로 표시.

페이지 구성
  1) 일람표: 인식한 부호(초록 상자)와 치수를 읽은 입면 칸(파란 상자), 인식 결과 라벨
  2) 평면도: 센 부호마다 색 동그라미 + 부호 라벨 (층·배수 표시)
  3) 입면도: 창호도 모양이 일치한 위치(주황 상자) + 부호/점수
벡터 PDF라 확대해도 선명하다.
"""
import colorsys
import hashlib

import pymupdf

import time

PAGE_W = 1600.0
MIN_LINE_PT = 0.35        # 이보다 짧은 선(해치·무늬 조각)은 페이지에서 보이지 않으므로 그리지 않는다


PAGE_LIMIT_SEC = 40       # 한 장에 이보다 오래 걸리면 그 장만 건너뛴다
MAX_SEGS_PAGE = 120000    # 한 장에 그리는 선 수 상한 (넘으면 짧은 선부터 뺀다)


class ReviewTimeout(Exception):
    pass


class PageTimeout(Exception):
    pass


_DEADLINE = [None]
_PAGE_DEADLINE = [None]


def _check():
    now = time.time()
    if _DEADLINE[0] is not None and now > _DEADLINE[0]:
        raise ReviewTimeout()
    if _PAGE_DEADLINE[0] is not None and now > _PAGE_DEADLINE[0]:
        raise PageTimeout()


def _color(code):
    h = int(hashlib.md5(code.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return colorsys.hsv_to_rgb(h, 0.85, 0.85)


class _Page:
    def __init__(self, doc, bounds, title):
        x0, y0, x1, y1 = bounds
        self.x0, self.y0 = x0, y0
        self.s = PAGE_W / max(x1 - x0, 1e-9)
        h = (y1 - y0) * self.s
        self.page = doc.new_page(width=PAGE_W, height=h + 40)
        self.off = 40
        self.page.insert_text((12, 26), title, fontname="korea", fontsize=16, color=(0, 0, 0))

    def P(self, x, y):
        return pymupdf.Point((x - self.x0) * self.s, (y - self.y0) * self.s + self.off)

    def R(self, x0, y0, x1, y1):
        return pymupdf.Rect(self.P(x0, y0), self.P(x1, y1))

    def drawing(self, sheet, text=True):
        sh = self.page.new_shape()
        lim = (MIN_LINE_PT / self.s) if len(sheet.segments) > 20000 else 0
        if len(sheet.segments) > MAX_SEGS_PAGE:
            ls = sorted((max(abs(c - a), abs(d - b)) for a, b, c, d in sheet.segments), reverse=True)
            lim = max(lim, ls[MAX_SEGS_PAGE])
        n = 0
        for a, b, c, d in sheet.segments:
            if lim and abs(c - a) < lim and abs(d - b) < lim:
                continue
            sh.draw_line(self.P(a, b), self.P(c, d))
            n += 1
            if n % 5000 == 0:
                _check()
                sh.finish(color=(0.6, 0.6, 0.6), width=0.25)      # 큰 도면은 나눠서 넘긴다(한 덩어리가 너무 커지면 매우 느려짐)
                sh.commit()
                sh = self.page.new_shape()
        sh.finish(color=(0.6, 0.6, 0.6), width=0.25)
        sh.commit()
        _check()
        if not text:
            return
        hs = sorted(min(t.y1 - t.y0, t.x1 - t.x0) for t in sheet.texts)
        med = hs[len(hs) // 2] if hs else 1
        for i_, t in enumerate(sheet.texts):
            if i_ % 2000 == 1999:
                _check()
            h = min(t.y1 - t.y0, t.x1 - t.x0)
            if h > med * 4:            # 크기 계산이 이상한 글자(블록 배율 등)는 생략
                continue
            fs = max(h * self.s * 0.85, 1.5)
            if fs < 2.2:
                continue
            try:
                if t.angle == 0:
                    self.page.insert_text(self.P(t.x0, t.y1), t.text, fontname="korea", fontsize=fs, color=(0.2, 0.2, 0.55))
                else:
                    self.page.insert_text(self.P(t.x1 if t.angle == 270 else t.x0, t.y1 if t.angle == 270 else t.y0),
                                          t.text, fontname="korea", fontsize=fs, color=(0.2, 0.2, 0.55),
                                          rotate=90 if t.angle == 270 else 270)
            except Exception:
                pass

    def box(self, rect, color, label=None, width=1.6, fill_opacity=0.08, fs=9):
        r = self.R(*rect)
        sh = self.page.new_shape()
        sh.draw_rect(r)
        sh.finish(color=color, fill=color, fill_opacity=fill_opacity, width=width)
        sh.commit()
        if label:
            self._label(r.tl + (0, -2), label, color, fs)

    def circle(self, x, y, rad_pt, color, label=None, fs=9):
        c = self.P(x, y)
        sh = self.page.new_shape()
        sh.draw_circle(c, rad_pt)
        sh.finish(color=color, width=1.8, fill=color, fill_opacity=0.15)
        sh.commit()
        if label:
            self._label(c + (rad_pt + 1, -rad_pt), label, color, fs)

    def _label(self, pt, text, color, fs):
        w = pymupdf.get_text_length(text, fontname="helv", fontsize=fs) * 1.25 + 4
        r = pymupdf.Rect(pt.x, pt.y - fs - 2, pt.x + w, pt.y + 1)
        sh = self.page.new_shape()
        sh.draw_rect(r)
        sh.finish(color=None, fill=(1, 1, 1), fill_opacity=0.85)
        sh.commit()
        self.page.insert_text((pt.x + 2, pt.y - 1), text, fontname="korea", fontsize=fs, color=color)


def write_review(path, items, plan_counts, final=None, title="", time_limit=300):
    """검토도면 PDF. 순서: 일람표 → 계단 단면 → 평면도(부호 많은 장부터) → 입면도.
    한 장이 PAGE_LIMIT_SEC 를 넘으면 그 장만, 전체가 time_limit(초)을 넘으면 남은 장을 건너뛴다.
    반환: (path 또는 None, 메모) — 메모가 비어 있으면 전부 그린 것."""
    doc = pymupdf.open()
    t_end = (time.time() + time_limit) if time_limit else None
    tasks = _page_tasks(doc, items, plan_counts, final or {}, title)
    done, slow, left = 0, [], []
    try:
        for k, (label, fn) in enumerate(tasks):
            if t_end is not None and time.time() > t_end:
                left = [lb for lb, _ in tasks[k:]]
                break
            n0 = doc.page_count
            _DEADLINE[0] = t_end
            _PAGE_DEADLINE[0] = time.time() + PAGE_LIMIT_SEC
            try:
                fn()
                done += 1
            except PageTimeout:
                while doc.page_count > n0:
                    doc.delete_page(doc.page_count - 1)
                slow.append(label)
            except ReviewTimeout:
                while doc.page_count > n0:
                    doc.delete_page(doc.page_count - 1)
                left = [lb for lb, _ in tasks[k:]]
                break
    finally:
        _DEADLINE[0] = None
        _PAGE_DEADLINE[0] = None
    notes = []
    if slow:
        notes.append(f"선이 너무 많아 건너뛴 장 {len(slow)}개({', '.join(slow[:6])}{' …' if len(slow) > 6 else ''})")
    if left:
        notes.append(f"{time_limit // 60}분 제한으로 못 넣은 장 {len(left)}개({', '.join(left[:6])}{' …' if len(left) > 6 else ''})")
    note = (f"검토도면 PDF: {len(tasks)}장 중 {done}장 넣음 — " + "; ".join(notes)) if notes else ""
    if doc.page_count == 0:
        return None, note or "검토도면에 넣을 도면이 없음"
    if left:
        doc.save(path)                                    # 시간 초과 때는 압축·정리 없이 바로 저장
    else:
        doc.save(path, garbage=3, deflate=True)
    return path, note


def _page_tasks(doc, items, plan_counts, final, title=""):
    """그릴 장 목록 [(이름, 그리는 함수)] — 중요한 순서대로"""
    by_frame = {}
    for it in items:
        sh = getattr(it, "region_sheet", None)
        if sh is not None:
            by_frame.setdefault(id(sh), (sh, []))[1].append(it)
    tasks = []

    # 1) 일람표
    def sched(sh):
        pg = _Page(doc, sh.bounds, f"[일람표 인식] {title} - {sh.title} (도곽 {sh.index}{' ' + sh.file if getattr(sh, 'file', '') else ''})")
        pg.drawing(sh)
        for it in by_frame.get(id(sh), (None, []))[1]:
            if getattr(it, "region", None):
                pg.box(it.region, (0.1, 0.35, 0.9), None, width=1.0, fill_opacity=0.04)
            if getattr(it, "tag_box", None):
                q = final.get(it.code, (it.qty,))[0]
                lab = f"{it.code}  {it.width or '?'}x{it.height or '?'}  수량 {q if q is not None else '?'}"
                pg.box(it.tag_box, (0.0, 0.6, 0.2), lab, width=1.6, fill_opacity=0.15, fs=10)
    for sh in getattr(plan_counts, "sched_frames", []):
        tasks.append((f"일람표 도곽 {sh.index}", lambda sh=sh: sched(sh)))

    # 2) 금속: 계단 단면 (핸드레일 길이 근거)
    def stair(r):
        g, f = r.get("기하"), r.get("frame")
        pg = _Page(doc, f.bounds, f"[계단 핸드레일] {r['규격']} (도곽 {f.index}) - 높이 {g['H']:.2f}m, 판 {g['flights']}개 → {r['수량']}m")
        pg.drawing(f)
        for b in g.get("bands", []):
            pg.box((b[0], b[1], b[2], b[3]), (0.9, 0.1, 0.1), f"{(b[3] - b[1]):.0f}", width=1.0, fill_opacity=0.10, fs=7)
        for q in (g.get("parts") or [g]):
            if getattr(q.get("_frame", f), "index", f.index) != f.index:
                continue
            pg.box((q["x"][0] - 300, min(q["ymin"], q["ymax"]), q["x"][1] + 300, max(q["ymin"], q["ymax"])), (0.0, 0.5, 0.0),
                   f"높이 {q['H']:.2f}m (읽힌 판 {q['read_flights']}/{q['flights']})", width=1.2, fill_opacity=0.0, fs=9)
    for r in getattr(plan_counts, "stair_rows", []) or []:
        if r.get("기하") and r.get("frame") is not None:
            tasks.append((f"계단 도곽 {r['frame'].index}", lambda r=r: stair(r)))

    # 3) 평면도: 부호가 많이 읽힌 장부터
    def plan(f, fl, tg):
        mult = f" ×{len(fl)}개 층" if len(fl) > 1 else ""
        pg = _Page(doc, f.bounds, f"[평면도 부호 집계] {f.title} (도곽 {f.index}{' ' + f.file if getattr(f, 'file', '') else ''}) {'/'.join(fl)}{mult}  - 부호 {len(tg)}개")
        pg.drawing(f)
        for code, typ, num, tt, nt in tg:
            cx, cy = (tt.cx + nt.cx) / 2, (tt.cy + nt.cy) / 2
            rad = max((tt.y1 - nt.y0) * pg.s * 0.9, 5)
            pg.circle(cx, cy, rad, _color(code), code, fs=8)
    for f, fl, tg in sorted(getattr(plan_counts, "plan_frames", []), key=lambda x: -len(x[2])):
        tasks.append((f"평면도 도곽 {f.index}", lambda f=f, fl=fl, tg=tg: plan(f, fl, tg)))

    # 4) 입면도
    hits = getattr(plan_counts, "elev_hits", {}) or {}
    per_frame = {}
    for code, hs in hits.items():
        for h in hs:
            per_frame.setdefault(h[0], []).append((code,) + tuple(h[1:]))

    def elev(sh):
        hs = per_frame.get(sh.index, [])
        pg = _Page(doc, sh.bounds, f"[입면도 모양 일치] {title} - {sh.title or '입면도'} (도곽 {sh.index}) - {len(hs)}개")
        pg.drawing(sh)
        for code, sc, x, y, W, H in hs:
            pg.box((x, y, x + W, y + H), _color(code), f"{code} ({sc:.2f})", width=2.0, fill_opacity=0.12, fs=10)
    for sh in getattr(plan_counts, "elev_frames", []):
        tasks.append((f"입면도 도곽 {sh.index}", lambda sh=sh: elev(sh)))
    return tasks
