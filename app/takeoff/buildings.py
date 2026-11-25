"""여러 동(棟)이 한 도면 세트에 든 현장 (예: I현장 1동·2동·3동, D현장 A동·B동).

- 동 이름은 도면명에서 '(1동)', '2동', 'A동' 꼴로 읽는다 (○○동 같은 지명의 '동'은 앞이 숫자/영문자가 아니라 안 잡힘).
- 같은 부호가 동마다 따로 있으므로, 동이 2개 이상이면 부호 앞에 '1동·' 을 붙여 구분한다 (SEP).
- 도면명에 동이 없는 장(예: '1층 평면도')은 같은 종류(평면도끼리) 안에서 앞 장의 동을 이어받되,
  층 순서가 되돌아가면(지붕 다음에 지하·1층) 다음 동의 묶음이 시작된 것으로 본다. 첫 동 이전의 장은 첫 동으로.
"""
import re

SEP = "·"
BLD_RE = re.compile(r"(?<![가-힣A-Za-z])\(?\s*(\d{1,3}|[A-Za-z])\s*동\s*\)?(?![가-힣])")


def building_of(title):
    m = BLD_RE.search(title or "")
    return f"{m.group(1).upper()}동" if m else None


def tag(code, b):
    return f"{b}{SEP}{code}" if b else code


def split(code):
    """'1동·PW-1' → ('1동', 'PW-1'); 'PW-1' → ('', 'PW-1')"""
    if code and SEP in code:
        a, b = code.split(SEP, 1)
        return a, b
    return "", code


def _rank(title):
    from .tags import floors_from_title
    fl = floors_from_title(title)
    if not fl:
        return -1 if re.search(r"지하|PIT", title or "", re.I) else None       # '지하층 평면도'(번호 없음)도 맨 아래층
    r = []
    for x in fl:
        if x.startswith("B"):
            r.append(-int(re.sub(r"\D", "", x) or 1))
        elif x in ("RF", "PH"):
            r.append(99)
        elif x == "ATTIC":
            r.append(98)
        elif x == "PIT":
            r.append(-50)
        else:
            r.append(int(re.sub(r"\D", "", x) or 0))
    return min(r)


def assign_buildings(frames, classify):
    """도곽 → 동 이름(없으면 None). 같은 분류(평면도/입면도/단면 등) 안에서 순서대로 이어받는다."""
    out = {}
    by_kind = {}
    for f in frames:
        by_kind.setdefault(classify(f), []).append(f)
    for kind, fs in by_kind.items():
        titled = [(i, building_of(f.title)) for i, f in enumerate(fs)]
        named = [b for _, b in titled if b]
        if not named:
            for f in fs:
                out[f.index] = None
            continue
        ranks = [_rank(f.title) if kind in ("plan", "tagplan") else None for f in fs]
        bottom = min((r for r in ranks if r is not None), default=None)      # 그 분류의 가장 낮은 층(지하층 등)
        cur, prev_rank = None, None
        for i, f in enumerate(fs):
            b = titled[i][1]
            rk = ranks[i]
            if b:
                cur = b
            elif cur is None:
                cur = next((bb for j, bb in titled if j > i and bb), named[0])        # 첫 동 이전 장 → 다음에 나오는 동
            elif rk is not None and rk == bottom and prev_rank is not None and prev_rank > rk:
                # 층 순서가 맨 아래층으로 되돌아감(지붕 다음에 지하층) → 다음 동의 묶음 시작
                # (1층으로만 되돌아가는 것은 같은 동의 확장후 평면도 등일 수 있어 가르지 않음)
                nxt = next((bb for j, bb in titled if j > i and bb and bb != cur), None)
                if nxt:
                    cur = nxt
            out[f.index] = cur
            if rk is not None:
                prev_rank = rk
    return out
