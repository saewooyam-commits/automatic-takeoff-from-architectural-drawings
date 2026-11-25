"""회사 견적서(내역서) 읽기.

시트에서 '품목/품명', '규격', '단위', '수량' 열을 자동으로 찾아 항목 목록으로 만든다.
.xlsx(openpyxl)와 구형 .xls(xlrd) 모두 지원.
"""
import re
from dataclasses import dataclass

CODE_RE = re.compile(r"^\s*([A-Z]{1,4})\s*-?\s*((?:[IVX]{1,4}-)|[가-힣]{1,2})?0*(\d{1,3})(?:\s*-\s*0*(\d{1,2}))?\s*([A-Za-z]?)('?)(?![A-Za-z])")
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*(m|M|mm)?")


@dataclass
class EstItem:
    row: int
    section: str
    name: str
    spec: str
    unit: str
    qty: float | None
    note: str = ""
    building: str | None = None      # 여러 동 현장에서 견적서의 동 구분 ('1동')

    @property
    def code(self):
        m = CODE_RE.match(self.name)
        if not m:
            return None
        c = f"{m.group(1)}-{m.group(2) or ''}{int(m.group(3))}" + (f"-{int(m.group(4))}" if m.group(4) else "") + m.group(5).upper() + m.group(6)
        if self.building:
            from .buildings import tag
            c = tag(c, self.building)
        return c

    @property
    def size_mm(self):
        """규격에서 W×H 추출 → mm. '1.1x2.1m' 또는 '1100x2100' 모두 처리."""
        m = SIZE_RE.search(self.spec or "")
        if not m:
            return None
        a, b = float(m.group(1)), float(m.group(2))
        if a < 100 and b < 100:      # m 단위로 적힌 경우
            a, b = a * 1000, b * 1000
        return round(a), round(b)


def _rows(path, sheet_name=None):
    if path.lower().endswith(".xls"):
        import xlrd
        wb = xlrd.open_workbook(path)
        sheets = [wb.sheet_by_name(sheet_name)] if sheet_name else wb.sheets()
        for sh in sheets:
            yield sh.name, [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        sheets = [wb[sheet_name]] if sheet_name else wb.worksheets
        for sh in sheets:
            yield sh.title, [list(r) for r in sh.iter_rows(values_only=True)]


def _norm(v):
    return re.sub(r"\s+", "", str(v)) if v is not None else ""


def _header_at(rows, r):
    cells = [_norm(v) for v in rows[r]]
    name_c = next((i for i, v in enumerate(cells) if v in ("품목", "품명", "공종", "명칭", "품목명")), None)
    spec_c = next((i for i, v in enumerate(cells) if v == "규격"), None)
    unit_c = next((i for i, v in enumerate(cells) if v == "단위"), None)
    if name_c is None or unit_c is None:
        return None
    qty_c = None
    for rr in (r, r + 1, r + 2):
        if rr < len(rows):
            qty_c = next((i for i, v in enumerate(rows[rr]) if _norm(v) == "수량"), None)
            if qty_c is not None:
                break
    note_c = next((i for i, v in enumerate(cells) if v.startswith("비고")), None)
    return r, name_c, spec_c, unit_c, qty_c, note_c


def _find_header(rows):
    """본문 머리글 찾기. '규격' 열이 있는 머리글 우선(갑지 요약표 제외)."""
    found = [h for h in (_header_at(rows, r) for r in range(min(len(rows), 80))) if h]
    with_spec = [h for h in found if h[2] is not None]
    if with_spec:
        return with_spec[0]
    return found[0] if found else None


def read_estimate(path):
    items = []
    for sname, rows in _rows(path):
        hdr = _find_header(rows)
        if not hdr:
            continue
        r0, nc, sc, uc, qc, oc = hdr
        section = ""
        for r in range(r0 + 1, len(rows)):
            row = rows[r]

            def g(c):
                return row[c] if c is not None and c < len(row) else None
            name = str(g(nc) or "").strip()
            if not name or _norm(name) == _norm(rows[r0][nc]):
                continue   # 빈 행 / 페이지마다 반복되는 머리글
            unit = str(g(uc) or "").strip()
            qty = g(qc)
            # 머리글은 '수량 | 단위'인데 본문은 'EA | 2' 로 바뀌어 적힌 양식(O현장): 단위 칸이 숫자고 수량 칸이 글자면 서로 바꾼다
            if isinstance(g(uc), (int, float)) and isinstance(qty, str) and re.match(r"^[A-Za-z가-힣㎡㎥]{1,4}$", qty.strip()):
                unit, qty = qty.strip(), g(uc)
            try:
                qty = float(qty) if qty not in (None, "") else None
            except (TypeError, ValueError):
                qty = None
            if not unit and qty is None:
                # 공종 제목 행 (예: '11.창호공사')
                if not name.startswith("["):
                    section = name
                continue
            items.append(EstItem(r + 1, section, name, str(g(sc) or "").strip(),
                                 unit, qty, str(g(oc) or "").strip()))
    return items
