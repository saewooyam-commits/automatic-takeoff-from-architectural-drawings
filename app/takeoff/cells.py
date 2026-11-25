"""표 테두리 선으로 일람표 칸(입면도 영역)을 찾는다."""


class Lines:
    def __init__(self, segments, tol):
        self.tol = tol
        self.v, self.h = [], []
        for x0, y0, x1, y1 in segments:
            if abs(x0 - x1) <= tol and abs(y0 - y1) > tol:
                self.v.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
            elif abs(y0 - y1) <= tol and abs(x0 - x1) > tol:
                self.h.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))

    def v_at(self, y, x_from, x_to):
        """y를 지나는 세로선들의 x (x_from~x_to 사이)."""
        return sorted(x for x, a, b in self.v if a - self.tol <= y <= b + self.tol and x_from <= x <= x_to)

    def has_v(self, x, y):
        return any(abs(vx - x) <= self.tol * 2 and a - self.tol <= y <= b + self.tol for vx, a, b in self.v)

    def h_full(self, left, right, y_from, y_to, cover=0.9, touch=False):
        """[left,right] 폭의 cover 이상을 덮는 가로선의 y 목록.
        touch=True 면 양쪽 세로 괘선에 닿는 선(표의 행 경계)만 — 칸 안에 그려진 창호 선(셔터 슬랫, 넓은 창의 가로대)은
        칸을 거의 채워도 괘선에 닿지 않으므로 제외된다."""
        out = []
        W = right - left
        spans = {}
        for y, a, b in self.h:
            if y_from <= y <= y_to and b > left and a < right:
                key = round(y / (self.tol * 2))
                spans.setdefault(key, []).append((max(a, left), min(b, right), y))
        for key, lst in spans.items():
            lst.sort()
            cov, cur_a, cur_b = 0.0, None, None
            for a, b, _ in lst:
                if cur_b is None or a > cur_b:
                    if cur_b is not None:
                        cov += cur_b - cur_a
                    cur_a, cur_b = a, b
                else:
                    cur_b = max(cur_b, b)
            if cur_b is not None:
                cov += cur_b - cur_a
            if cov >= W * cover:
                if touch and (min(r[0] for r in lst) > left + self.tol * 3 or max(r[1] for r in lst) < right - self.tol * 3):
                    continue
                out.append(sum(y for _, _, y in lst) / len(lst))
        return sorted(out)

    def v_coverage(self, x_lo, x_hi, y0, y1):
        """x_lo~x_hi 안의 세로선이 [y0,y1] 구간을 덮는 비율(가장 많이 덮는 x 기준)."""
        best = 0.0
        L = max(y1 - y0, 1e-9)
        cols = {}
        for x, a, b in self.v:
            if x_lo <= x <= x_hi and b > y0 and a < y1:
                cols.setdefault(round(x / (self.tol * 2)), []).append((max(a, y0), min(b, y1)))
        for lst in cols.values():
            lst.sort()
            cov, ca, cb = 0.0, None, None
            for a, b in lst:
                if cb is None or a > cb:
                    if cb is not None:
                        cov += cb - ca
                    ca, cb = a, b
                else:
                    cb = max(cb, b)
            if cb is not None:
                cov += cb - ca
            best = max(best, cov / L)
        return best


def column_bounds(lines, tag_box, probe_y, x_min, x_max):
    """태그 줄에서 보이는 세로선 중, 입면도 영역(probe_y)까지 이어지는 가장 가까운 좌/우 선."""
    tx0, ty0, tx1, ty1 = tag_box
    cy = (ty0 + ty1) / 2
    xs = lines.v_at(cy, x_min, x_max)
    left = [x for x in xs if x <= tx0 + lines.tol and lines.has_v(x, probe_y)]
    right = [x for x in xs if x >= tx1 - lines.tol and lines.has_v(x, probe_y)]
    if not left or not right:
        return None
    return max(left), min(right)


def shape_cell(lines, tag_box, direction, text_h, x_min, x_max, y_min, y_max):
    """태그에서 direction('above'|'below')으로 가며 처음 만나는 키 큰 칸(입면도 칸)을 찾는다."""
    tx0, ty0, tx1, ty1 = tag_box
    step = text_h * 3
    probes = [ty0 - step * k for k in range(1, 40)] if direction == "above" else \
             [ty1 + step * k for k in range(1, 40)]
    for py in probes:
        if not (y_min <= py <= y_max):
            break
        cb = column_bounds(lines, tag_box, py, x_min, x_max)
        if not cb:
            continue
        left, right = cb
        if direction == "above":
            hs = lines.h_full(left, right, y_min, ty0 + lines.tol, touch=True)
            hs = [y for y in hs if y <= ty0 + lines.tol]
            hs.append(ty0)            # 태그가 칸 안쪽 아래에 있을 때: 태그 위 구간 자체가 입면 칸
            hs.sort(reverse=True)
            for a, b in zip(hs, hs[1:]):          # a(아래) > b(위)
                if a - b >= text_h * 8:
                    return left, b, right, a
        else:
            hs = lines.h_full(left, right, ty1 - lines.tol, y_max, touch=True)
            hs = [y for y in hs if y >= ty1 - lines.tol]
            hs.append(ty1)            # 태그가 칸 안쪽 위에 있을 때: 태그 아래 구간 자체가 입면 칸
            hs.sort()
            for a, b in zip(hs, hs[1:]):
                if b - a >= text_h * 8:
                    return left, a, right, b
        return None
    return None
