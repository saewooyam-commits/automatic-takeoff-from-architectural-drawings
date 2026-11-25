"""견본 도면(DXF)을 만드는 스크립트. 실제 현장과 무관한, 직접 그린 가상의 3층 건물이다.

    python examples/make_sample.py            -> examples/견본도면.dxf

도곽 4장: 창호일람표 1장 + 1~3층 창호평면도. 단위 mm.
"""
import os
import ezdxf

TH = 300                      # 글자 높이
FW, FH = 60000, 42000         # 도곽 크기
GAP = 5000

# 부호: (종류, 번호, 폭, 높이, 유리/비고, 층별 개수)
ITEMS = [
    ("AW", "1", 1500, 1200, "24T 로이복층유리", {1: 2, 2: 3, 3: 3}),
    ("AW", "2", 2400, 1500, "24T 로이복층유리", {1: 1, 2: 2, 3: 2}),
    ("SD", "1", 900, 2100, "분체도장", {1: 2, 2: 1, 3: 1}),
    ("FSD", "1", 1000, 2100, "갑종방화문", {1: 1, 2: 1, 3: 1}),
]


def text(msp, s, x, y, h=TH, align="MIDDLE_CENTER"):
    t = msp.add_text(s, dxfattribs={"height": h, "style": "KOR"})
    t.set_placement((x, y), align=getattr(ezdxf.enums.TextEntityAlignment, align))
    return t


def rect(msp, x0, y0, x1, y1, layer="0"):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def frame(msp, i, title):
    """도곽 + 표제란. 도곽 왼쪽 아래 좌표를 돌려준다."""
    x0, y0 = i * (FW + GAP), 0
    rect(msp, x0, y0, x0 + FW, y0 + FH, "도곽")
    tx = x0 + FW - 16000
    rect(msp, tx, y0, x0 + FW, y0 + 3000, "도곽")
    msp.add_line((tx + 4000, y0), (tx + 4000, y0 + 3000), dxfattribs={"layer": "도곽"})
    text(msp, "도면명", tx + 2000, y0 + 1500)
    text(msp, title, tx + 10000, y0 + 1500, h=400)
    # 표제란 나머지 칸과 주기 (실제 도면처럼 글자가 어느 정도 있어야 도곽으로 인식된다)
    rect(msp, tx, y0 + 3000, x0 + FW, y0 + 9000, "도곽")
    for k, (lab, val) in enumerate([("공사명", "견본 근린생활시설 신축공사"), ("축척", "1/100"),
                                    ("도면번호", f"A-{i + 1:03d}"), ("일자", "2026.01.01")]):
        yy = y0 + 8250 - 1500 * k
        text(msp, lab, tx + 2000, yy)
        text(msp, val, tx + 10000, yy)
    for k, note in enumerate(["주기", "1. 이 도면은 프로그램 시험용 견본이며 실제 건물이 아님.",
                              "2. 치수 단위는 mm.", "3. 창호 부호는 창호일람표 참조.",
                              "4. 현장 실측 후 제작할 것.", "5. 유리 두께는 일람표에 따름."]):
        text(msp, note, x0 + 1500, y0 + 4500 - 600 * k, h=250, align="MIDDLE_LEFT")
    return x0, y0


def tag(msp, typ, num, x, y):
    """부호 태그: 원 안에 번호(위) / 종류(아래)."""
    msp.add_circle((x, y), 650, dxfattribs={"layer": "창호부호"})
    msp.add_line((x - 650, y), (x + 650, y), dxfattribs={"layer": "창호부호"})
    text(msp, num, x, y + 280)
    text(msp, typ, x, y - 280)


def schedule(msp, i):
    x0, y0 = frame(msp, i, "창호일람표")
    left, top = x0 + 3000, y0 + FH - 4000
    lab_w, cw = 5000, 11500
    rows = [("형태", 15000), ("부호", 1800), ("위치 및 수량", 700), ("유리", 700), ("비고", 700)]
    right = left + lab_w + cw * len(ITEMS)
    y = top
    msp.add_line((left, y), (right, y))
    for name, h in rows:
        text(msp, name, left + lab_w / 2, y - h / 2)
        y -= h
        msp.add_line((left, y), (right, y))
    bottom = y
    for x in [left, left + lab_w] + [left + lab_w + cw * (k + 1) for k in range(len(ITEMS))]:
        msp.add_line((x, top), (x, bottom))
    for k, (typ, num, w, h, glass, per) in enumerate(ITEMS):
        cx = left + lab_w + cw * k + cw / 2
        ex0, ey0 = cx - w / 2, top - 15000 + 5000
        rect(msp, ex0, ey0, ex0 + w, ey0 + h, "창호")
        if typ == "AW":                                   # 가운데 중간대
            msp.add_line((cx, ey0), (cx, ey0 + h), dxfattribs={"layer": "창호"})
        dim = msp.add_linear_dim(base=(ex0, ey0 + h + 1500), p1=(ex0, ey0 + h), p2=(ex0 + w, ey0 + h),
                                 override={"dimtxt": TH, "dimasz": 150, "dimexo": 200, "dimdec": 0})
        dim.render()
        dim = msp.add_linear_dim(base=(ex0 - 1500, ey0), p1=(ex0, ey0), p2=(ex0, ey0 + h), angle=90,
                                 override={"dimtxt": TH, "dimasz": 150, "dimexo": 200, "dimdec": 0})
        dim.render()
        ry = top - 15000
        tag(msp, typ, num, cx, ry - 900)
        stated = sum(per.values()) - (1 if typ + num == "AW2" else 0)   # AW-2 는 일부러 일람표 수량을 1 적게 적음(교차검증 시연)
        text(msp, f"{stated}개소", cx, ry - 2150)
        text(msp, glass if typ == "AW" else "-", cx, ry - 2850)
        text(msp, "-" if typ == "AW" else glass, cx, ry - 3550)


def plan(msp, i, floor):
    x0, y0 = frame(msp, i, f"{floor}층 창호평면도")
    bx0, by0, bx1, by1 = x0 + 12000, y0 + 10000, x0 + 48000, y0 + 32000
    rect(msp, bx0, by0, bx1, by1, "벽")
    rect(msp, bx0 + 200, by0 + 200, bx1 - 200, by1 - 200, "벽")
    msp.add_line((bx0 + 18000, by0), (bx0 + 18000, by1), dxfattribs={"layer": "벽"})
    text(msp, f"{floor}층 창호평면도", (bx0 + bx1) / 2, y0 + 6000, h=600)
    for name, (rx, ry) in {"사무실": (9000, 16000), "복도": (9000, 4000), "계단실": (21000, 18000),
                           "화장실": (21000, 5000), "근린생활시설": (29000, 11000)}.items():
        text(msp, name, bx0 + rx, by0 + ry)
    spots = {"AW1": [(bx0 + 4000 + 5000 * k, by1 + 1500) for k in range(3)],
             "AW2": [(bx0 + 24000 + 7000 * k, by0 - 1500) for k in range(2)],
             "SD1": [(bx0 + 6000 + 6000 * k, by0 + 8000) for k in range(2)],
             "FSD1": [(bx0 + 18000 + 1800, by0 + 14000)]}
    for typ, num, w, h, glass, per in ITEMS:
        for (x, y) in spots[typ + num][:per[floor]]:
            tag(msp, typ, num, x, y)


def main():
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    doc.styles.add("KOR", font="malgun.ttf")
    for name in ("도곽", "창호", "창호부호", "벽"):
        doc.layers.add(name)
    msp = doc.modelspace()
    schedule(msp, 0)
    for f in (1, 2, 3):
        plan(msp, f, f)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "견본도면.dxf")
    doc.saveas(out)
    print("저장:", out)


if __name__ == "__main__":
    main()
