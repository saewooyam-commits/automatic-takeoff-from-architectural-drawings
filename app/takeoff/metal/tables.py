"""도면 안에 그려진 수량표 읽기 (예: 트렌치 커버 '위치 | 크기(X x Y) | 깊이 | 갯수').

머리글 행(크기/규격 + 갯수/수량/개수)을 찾고, 머리글 글자 x 위치로 열을 나눈 뒤
아래 행들을 읽는다. 병합된 '위치' 칸은 위 행 값을 이어받는다.
"""
import re

QTY_HDR = re.compile(r"^(갯\s*수|개\s*수|수\s*량|EA|QTY)$", re.I)
SIZE_HDR = re.compile(r"^(크기|규격|SIZE|치수)", re.I)
SIZE_VAL = re.compile(r"(\d{2,5})\s*[xX×*]\s*(\d{2,5})(?:\s*[xX×*]\s*(\d{2,5}))?")


def _rows(texts, tol):
    rows = []
    for t in sorted(texts, key=lambda t: t.cy):
        for r in rows:
            if abs(r[0] - t.cy) <= tol:
                r[1].append(t)
                break
        else:
            rows.append([t.cy, [t]])
    return [(y, sorted(ts, key=lambda t: t.cx)) for y, ts in rows]


def read_tables(sheet):
    """반환: [{'title': 표 근처 제목, 'rows': [{'위치':..,'크기':(X,Y),'개수':n,'깊이':..,'raw':..}]}]"""
    out = []
    hz = [t for t in sheet.texts if t.angle == 0]
    for qh in [t for t in hz if QTY_HDR.match(t.text.strip())]:
        h = max(qh.y1 - qh.y0, 1)
        row = [t for t in hz if abs(t.cy - qh.cy) <= h * 0.6]
        sh = [t for t in row if SIZE_HDR.match(t.text.strip())]
        if not sh:
            continue
        cols = sorted(row, key=lambda t: t.cx)
        names = [re.sub(r"\s", "", t.text) for t in cols]
        xs = [t.cx for t in cols]
        bounds = [(-1e9 if i == 0 else (xs[i - 1] + xs[i]) / 2, 1e9 if i == len(xs) - 1 else (xs[i] + xs[i + 1]) / 2)
                  for i in range(len(xs))]
        left, right = cols[0].x0 - h * 6, cols[-1].x1 + h * 6
        body = [t for t in hz if left <= t.cx <= right and qh.cy + h * 0.8 < t.cy < qh.cy + h * 60]
        rows = _rows(body, h * 0.6)
        recs, last_loc, empty = [], "", 0
        for y, ts in rows:
            cells = {}
            for t in ts:
                for nm, (a, b) in zip(names, bounds):
                    if a <= t.cx < b:
                        cells[nm] = (cells.get(nm, "") + " " + t.text).strip()
            size_key = next((k for k in cells if SIZE_HDR.match(k)), None)
            qty_key = next((k for k in cells if QTY_HDR.match(k)), None)
            m = SIZE_VAL.search(cells.get(size_key, "").replace(",", "")) if size_key else None
            q = re.search(r"\d+(?:\.\d+)?", cells.get(qty_key, "")) if qty_key else None
            if not m or not q:
                empty += 1
                if empty >= 2 and recs:
                    break
                continue
            empty = 0
            loc_key = next((k for k in cells if k.startswith("위치") or k.startswith("구분")), None)
            loc = cells.get(loc_key, "") if loc_key else ""
            if loc and not re.fullmatch(r"\d+", loc):
                last_loc = loc
            depth_key = next((k for k in cells if k.startswith("깊이") or k.startswith("높이")), None)
            recs.append({"위치": last_loc, "X": int(m.group(1)), "Y": int(m.group(2)),
                         "개수": float(q.group()), "깊이": cells.get(depth_key, ""), "raw": cells, "_y": y})
        if recs:
            # 병합된 '위치' 칸: 위치 글자는 여러 행의 가운데에 있으므로 가장 가까운 위치 글자를 배정
            li = next((i for i, nm in enumerate(names) if nm.startswith("위치") or nm.startswith("구분")), None)
            if li is not None:
                a, b = bounds[li]
                y0, y1 = recs[0]["_y"] - h, recs[-1]["_y"] + h
                locs = [t for t in body if a <= t.cx < b and y0 <= t.cy <= y1 and not re.fullmatch(r"\d+", t.text)]
                if locs:
                    for r in recs:
                        r["위치"] = min(locs, key=lambda t: abs(t.cy - r["_y"])).text
            # 표 제목: 표 위쪽/왼쪽 가까운 '…상세도' 글자 또는 표 아래쪽 번호 제목
            near = [t for t in hz if re.search(r"(상세도|평면도|안내도|일람)", t.text) and len(t.text) < 50]
            bottom = recs[-1]["_y"]
            below = [t for t in near if t.cy > bottom]
            title = ""
            if below:      # 도면 제목은 보통 그림 아래쪽에 있다
                title = min(below, key=lambda t: (t.cy - bottom) + abs(t.cx - qh.cx) * 0.3).text
            elif near:
                title = min(near, key=lambda t: abs(t.cx - qh.cx) + abs(t.cy - qh.cy) * 0.5).text
            out.append({"title": title, "rows": recs, "header": names, "at": (qh.cx, qh.cy)})
    return out
