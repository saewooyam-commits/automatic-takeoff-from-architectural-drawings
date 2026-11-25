"""창호일람표 파서.

일람표 한 칸(부호 1개)의 구성:
  [부호 행]  번호  명칭 ........ B1 1F 2F 3~5 RF 계
  [위치 행]  종류  위치 ........  -  1  1  -   -  2
  [형태]     입면도 + 치수선
  [프레임] [유리/마감] [부속철물] 사양 행

'계' 글자를 기준점으로 칸을 찾고, 같은 행의 층 라벨과 그 아래 값을 읽는다.
"""
import re
from dataclasses import dataclass, field

from .model import Sheet

NUM_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d{1,5}$")
CODE_TYPE_RE = re.compile(r"^[A-Z]{1,4}$")
ROW_TOL = 3.0        # PDF(포인트) 기준 값. 실제로는 시트 글자 크기 배율 k(PDF≈1, CAD mm 도면은 수십 배)를 곱한다.
MARGIN_MAX = 30      # 치수선 양끝 여유치수(10, 15 등) 상한
MIN_SIZE = 100       # 창호 크기로 볼 최소 치수(mm)


@dataclass
class ScheduleItem:
    code: str                 # 정규화 부호 (예: AW-2, FSD-4A)
    type: str                 # 종류 문자 (AW, FSD ...)
    number: str               # 번호 (2, 4a ...)
    name: str                 # 명칭 (알루미늄 고정창 ...)
    location: str
    width: int | None
    height: int | None
    qty_by_floor: dict = field(default_factory=dict)
    qty: float | None = None
    qty_floor_sum: float | None = None
    frame: str = ""
    glass: str = ""
    hardware: str = ""
    sheet: str = ""           # 근거 도면 (번호/페이지)
    notes: list = field(default_factory=list)
    width_candidates: list = field(default_factory=list)
    height_candidates: list = field(default_factory=list)

    @property
    def area_m2(self):
        if self.width and self.height:
            return self.width * self.height / 1e6
        return None


def _num(s):
    s = s.strip()
    if s in ("-", "", "—", "－"):
        return 0
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def text_scale(sheet):
    """시트 글자 높이 중앙값 / 7pt. PDF는 약 1, CAD(mm)는 도면 축척에 따라 수십~수백."""
    hs = sorted(min(t.y1 - t.y0, t.x1 - t.x0) for t in sheet.texts
                if 1 <= len(t.text) <= 6 and (t.y1 - t.y0) > 0)
    if not hs:
        return 1.0
    return max(hs[len(hs) // 2] / 7.0, 1e-6)


def _same_row(a, b, tol=None, k=1.0):
    tol = (tol if tol is not None else ROW_TOL) * k
    return abs(a.cy - b.cy) <= tol


# 부호 번호 문법: 1, 4a, 3-1, 1-19, 4-1a 에 더해 로마숫자/한글 접두(I-5, II-3, I-11', 지1, 화4)
NUM_PREFIX = r"(?:[IVX]{1,4}-|[가-힣]{1,2})"
NUM1 = NUM_PREFIX + r"?\d{1,3}(?:-\d{1,2})?[A-Za-z]?'?"
_NUM_PARTS = re.compile(r"^(" + NUM_PREFIX + r")?0*(\d+)(?:\s*-\s*0*(\d+))?\s*([A-Za-z]?)('?)")   # 앞부분만 맞으면 됨('1 (2개소)' 등)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}


def normalize_code(type_, number):
    """'FSD','04a' → FSD-4A ; 'AW','3-1' → AW-3-1 ; 'SSD','1-19' → SSD-1-19 ; 'SSD','I-5' → SSD-I-5 ; 'SSD','지1' → SSD-지1"""
    m = _NUM_PARTS.match(number.strip())
    if not m:
        return f"{type_}-{number}".upper()
    pre, n, sub, suf, prime = m.group(1) or "", m.group(2), m.group(3), m.group(4).upper(), m.group(5)
    return f"{type_}-{pre}{n}" + (f"-{sub}" if sub else "") + suf + prime


def code_variants(code):
    """견적서 표기 차이를 흡수하기 위한 동치 부호 목록.
    SSD-I-5 → [SSD-I-5, SSD-1-5] (도면의 로마숫자 I 을 견적서는 1 로 적는 경우), SSD-I-11' → SSD-I-11, SSD-1-11 도 포함."""
    from .buildings import split as _bsplit, tag as _btag
    pre, code = _bsplit(code)
    out = [code]
    m = re.match(r"^([A-Z]+)-([IVX]{1,4})-(\d+)([A-Z]?)('?)$", code)
    if m and m.group(2) in _ROMAN:
        out.append(f"{m.group(1)}-{_ROMAN[m.group(2)]}-{m.group(3)}{m.group(4)}")
    m = re.match(r"^([A-Z]+)-(\d)-(\d)([A-Z]?)$", code)
    if m:
        # 하위 변형 SSD-1-1 을 견적서가 'SSD011' 처럼 붙여 적는 경우 → SSD-11 로 읽힌다
        out.append(f"{m.group(1)}-{m.group(2)}{m.group(3)}{m.group(4)}")
    if code.endswith("'"):
        out += [c[:-1] for c in list(out) if c.endswith("'")]
    return [_btag(c, pre) for c in out]


def base_code(code):
    """AW-3-1 → AW-3 (하위 변형의 기본 부호). 로마숫자 접두(SSD-I-5)는 변형이 아니라 독립 부호."""
    from .buildings import split as _bsplit, tag as _btag
    pre, bare = _bsplit(code)
    m = re.match(r"^([A-Z]+-\d+)-\d+([A-Z]?)$", bare)
    return _btag(m.group(1) + m.group(2), pre) if m else code


def _row_label_y(sheet, label, x_near, below_y):
    """'프 레 임' 같은 행 라벨 중 below_y 아래에서 가장 가까운 것."""
    key = label.replace(" ", "")
    cands = [t for t in sheet.texts
             if t.text.replace(" ", "") == key and t.cy > below_y]
    if not cands:
        return None
    return min(cands, key=lambda t: t.cy)


def _dims(texts, horizontal, k=1.0):
    """치수 글자를 치수선(같은 행/열)별로 묶어 축 방향으로 정렬."""
    ts = [t for t in texts if NUM_RE.match(t.text) and
          ((t.angle == 0) == horizontal)]
    groups = []
    for t in sorted(ts, key=lambda t: t.cy if horizontal else t.cx):
        key = t.cy if horizontal else t.cx
        for g in groups:
            if abs(g[0] - key) <= 2.5 * k:
                g[1].append(t)
                break
        else:
            groups.append([key, [t]])
    out = []
    for _, g in groups:
        g.sort(key=lambda t: t.cx if horizontal else t.cy)
        out.append([(_num(t.text), t) for t in g])
    return out


def pick_size(groups):
    """치수선 묶음에서 개구부 크기를 고른다.

    1순위: 양옆이 작은 여유치수(≤30)로 둘러싸인 값 (예: 15 | 4,400 | 15)
    2순위: 가장 큰 값 (전체 치수)
    """
    bracketed, allv = [], []
    for g in groups:
        vals = [v for v, _ in g]
        for i, v in enumerate(vals):
            if v is None or v < MIN_SIZE:
                continue
            allv.append(v)
            left = vals[i - 1] if i > 0 else None
            right = vals[i + 1] if i + 1 < len(vals) else None
            if left is not None and right is not None and \
                    left <= MARGIN_MAX and right <= MARGIN_MAX:
                bracketed.append(v)
    if bracketed:
        return int(max(bracketed)), sorted(set(bracketed + allv))
    if allv:
        return int(max(allv)), sorted(set(allv))
    return None, []


def rotate_sheet_cw(sheet: Sheet):
    """화면을 시계방향 90° 돌린 Sheet 사본 (세로로 놓인 일람표용)."""
    from .model import Text
    H = sheet.height
    out = Sheet(source=sheet.source, index=sheet.index, width=sheet.height,
                height=sheet.width, title=sheet.title, number=sheet.number)
    for t in sheet.texts:
        # (x, y) -> (H - y, x)
        out.texts.append(Text(H - t.y1, t.x0, H - t.y0, t.x1, t.text,
                              (t.angle + 90) % 360, t.size))
    return out


def parse_schedule_sheet(sheet: Sheet):
    tots = [t.angle for t in sheet.texts if t.text == "계"]
    if tots and tots.count(270) > tots.count(0):
        items = _parse(rotate_sheet_cw(sheet))
        for it in items:
            it.width, it.height = it.height, it.width
            it.width_candidates, it.height_candidates = it.height_candidates, it.width_candidates
            it.notes.append("세로 배치 일람표(90° 회전) - 가로/세로 치수 교환 적용")
        return items
    return _parse(sheet)


def _parse(sheet: Sheet):
    k = text_scale(sheet)
    items = []
    totals = [t for t in sheet.texts if t.text == "계" and t.angle == 0]
    for tot in totals:
        row = [t for t in sheet.texts if _same_row(t, tot, k=k) and t.angle == 0]
        # 칸 왼쪽 경계: 같은 행에서 이 '계'보다 왼쪽에 있는 이전 '계' 또는 '부 호'
        lefts = [t.x1 for t in row if t.x1 < tot.x0 and
                 (t.text == "계" or t.text.replace(" ", "") == "부호")]
        left = max(lefts) if lefts else 0
        cell = sorted([t for t in row if left < t.cx <= tot.x1 + 1 * k], key=lambda t: t.x0)
        # 층 라벨: '계' 바로 왼쪽으로 연속된 짧은 라벨들
        labels = []
        for t in reversed(cell):
            if t is tot:
                labels.append(t)
                continue
            if labels and re.match(r"^(B\d|\d+F|\d+~\d+F?|RF|PH|지하\d?|지상\d?|옥탑|\d+층)$", t.text):
                labels.append(t)
            elif labels:
                break
        labels.reverse()
        if len(labels) < 2:
            continue
        head = [t for t in cell if t.x1 <= labels[0].x0 + 0.5 * k]
        if not head:
            continue
        number, name = head[0].text, " ".join(t.text for t in head[1:])
        # 값 행: 헤더 바로 아래 행
        below = [t for t in sheet.texts if t.angle == 0 and
                 tot.y1 < t.cy < tot.y1 + (tot.y1 - tot.y0) * 2.6 and
                 left < t.cx <= tot.x1 + 1 * k]
        if not below:
            continue
        vy = min(t.cy for t in below)
        vrow = [t for t in below if abs(t.cy - vy) <= ROW_TOL * k]
        qty_by_floor = {}
        for lab in labels:
            v = [t for t in vrow if abs(t.cx - lab.cx) <= max(5 * k, (lab.x1 - lab.x0))]
            qty_by_floor[lab.text] = _num(v[0].text) if v else None
        vhead = sorted([t for t in vrow if t.x1 <= labels[0].x0 + 0.5 * k], key=lambda t: t.x0)
        type_ = vhead[0].text if vhead and CODE_TYPE_RE.match(vhead[0].text) else ""
        location = " ".join(t.text for t in vhead[1:]) if type_ else " ".join(t.text for t in vhead)
        if not type_ and not re.match(r"^\w{1,3}$", number):
            continue
        # 빈 칸(부호 없음) 건너뜀
        if not type_ and all(v in (None, 0) for v in qty_by_floor.values()):
            continue

        bottom_lab = _row_label_y(sheet, "프레임", tot.x0, vy)
        bottom = bottom_lab.y0 if bottom_lab else vy + 250 * k
        shape = sheet.texts_in(left, vy + ROW_TOL * k, tot.x1 + 25 * k, bottom)
        w, wc = pick_size(_dims(shape, horizontal=True, k=k))
        h, hc = pick_size(_dims(shape, horizontal=False, k=k))

        def spec(label):
            lab = _row_label_y(sheet, label, tot.x0, vy)
            if not lab:
                return ""
            ts = [t for t in sheet.texts if _same_row(t, lab, 4, k) and left < t.cx <= tot.x1 + 1 * k]
            return " ".join(t.text for t in sorted(ts, key=lambda t: (t.y0, t.x0)))

        item = ScheduleItem(
            code=normalize_code(type_, number) if type_ else number,
            type=type_, number=number, name=name, location=location,
            width=w, height=h, qty_by_floor=qty_by_floor,
            qty=qty_by_floor.get("계"),
            frame=spec("프레임"), glass=spec("유리/마감"), hardware=spec("부속철물"),
            sheet=f"{sheet.number or ''} (p.{sheet.index})".strip(),
            width_candidates=wc, height_candidates=hc,
        )
        floors = [v for k, v in qty_by_floor.items() if k != "계" and v is not None]
        item.qty_floor_sum = sum(floors) if floors else None
        if item.qty is not None and item.qty_floor_sum is not None and \
                abs(item.qty - item.qty_floor_sum) > 1e-6:
            item.notes.append(f"층별합({item.qty_floor_sum:g})≠계({item.qty:g}) - 3~5층처럼 묶인 층은 층수 곱 확인")
        if not w or not h:
            item.notes.append("치수 자동인식 실패")
        items.append(item)
    return items


def is_schedule_sheet(sheet: Sheet):
    t = sheet.title.replace(" ", "")
    if "일람표" in t and "창호" in t:
        return True
    return sum(1 for x in sheet.texts if x.text == "계") >= 3 and \
        any(x.text.replace(" ", "") == "부속철물" for x in sheet.texts)
