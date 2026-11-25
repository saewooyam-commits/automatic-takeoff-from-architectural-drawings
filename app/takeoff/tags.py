"""평면도의 창호 부호 태그 집계.

태그 형태: 원 안에 위는 번호(1, 4a), 아래는 종류(FSD, AW).
같은 형태로 '종류 위/번호 아래'인 사무소도 있어 둘 다 탐색한다.
"""
import re
from collections import Counter, defaultdict

from .schedule import normalize_code, NUM1

FLOOR_PATTERNS = [
    (re.compile(r"지하\s*(\d)\s*층"), lambda m: f"B{m.group(1)}"),
    (re.compile(r"(?:지상\s*)?(\d{1,2})\s*층"), lambda m: f"{m.group(1)}F"),
    (re.compile(r"옥탑|지붕|PH"), lambda m: "RF"),
]


def floor_of(sheet):
    t = sheet.title
    for pat, f in FLOOR_PATTERNS:
        m = pat.search(t)
        if m:
            return f(m)
    return None


def count_tags(sheet, types):
    """sheet에서 types(예: {'FSD','AW'}) 부호 태그를 세어 Counter 반환."""
    type_re = re.compile(r"^(%s)$" % "|".join(sorted(types, key=len, reverse=True)))
    num_re = re.compile("^" + NUM1 + "$")
    found = Counter()
    where = defaultdict(list)
    used = set()
    for t in sheet.texts:
        if t.angle != 0 or not type_re.match(t.text):
            continue
        h = max(t.y1 - t.y0, 3)
        best = None
        for u in sheet.texts:
            if id(u) in used or u is t or u.angle != 0 or not num_re.match(u.text):
                continue
            dx, dy = u.cx - t.cx, u.cy - t.cy
            if abs(dx) <= h * 1.2 and h * 0.6 <= abs(dy) <= h * 2.2:
                d = abs(dx) + abs(dy)
                if best is None or d < best[0]:
                    best = (d, u)
        if best:
            used.add(id(best[1]))
            code = normalize_code(t.text, best[1].text)
            found[code] += 1
            where[code].append((round(t.cx), round(t.cy)))
    return found, where


def floors_matching(label, floor):
    """일람표 층 라벨('3~5', '1F', 'B1', 'RF')이 floor('3F')를 포함하는지."""
    if label == floor:
        return True
    m = re.match(r"^(\d+)~(\d+)F?$", label)
    f = re.match(r"^(\d+)F$", floor or "")
    if m and f:
        return int(m.group(1)) <= int(f.group(1)) <= int(m.group(2))
    return False


def floors_from_title(title):
    """도면명에서 해당 층 목록. '2~4층'→[2F,3F,4F], '2.3층'→[2F,3F], '지하1층'→[B1],
    '옥탑/지붕'→[RF], '다락'→[ATTIC], 'B1F'→[B1], '평면도 4-7F'→[4F..7F]."""
    t = re.sub(r"\s", "", title or "")
    t = re.sub(r"\([^)]*동\)", "", t)          # (A동) 제거
    t = re.sub(r"옥탑(지붕)?\d*층?|PH\d*F?(?![A-Za-z])", "옥탑", t)   # '옥탑1층'의 1을 지상 1층으로 읽지 않게 (M현장 정답지로 발견)
    m = re.search(r"지하(\d)(?:[~\-](\d))?층", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        return [f"B{i}" for i in range(min(a, b), max(a, b) + 1)]
    m = re.search(r"B(\d)F", t, re.I)
    if m:
        return [f"B{m.group(1)}"]
    m = re.search(r"(\d{1,2})[~\-](\d{1,2})(?:층|F)", t, re.I)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return [f"{i}F" for i in range(min(a, b), max(a, b) + 1)]
    m = re.search(r"(\d{1,2}(?:[.,]\d{1,2})+)(?:층|F)", t, re.I)
    if m:
        return [f"{int(x)}F" for x in re.split(r"[.,]", m.group(1))]
    m = re.search(r"(?:지상)?(\d{1,2})(?:층|F)", t, re.I)
    if m:
        return [f"{int(m.group(1))}F"]
    if re.search(r"옥탑|지붕|PH|RF", t, re.I):
        return ["RF"]
    if "다락" in t:
        return ["ATTIC"]
    if re.search(r"PIT층|피트층", t, re.I):
        return ["PIT"]
    return []


def floor_sort_key(f):
    """층 이름 정렬: 지하(B2,B1) → PIT → 1F.. → RF. 동 접두('1동 3F')는 동 먼저."""
    parts = f.split(" ")
    name = parts[-1]
    pre = " ".join(parts[:-1])
    if name.startswith("B"):
        k = -int(re.sub(r"\D", "", name) or 1)
    elif name == "PIT":
        k = -50
    elif name in ("RF", "PH"):
        k = 99
    elif name == "ATTIC":
        k = 98
    else:
        k = int(re.sub(r"\D", "", name) or 0)
    return (pre, k)


