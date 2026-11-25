"""도면 소스(PDF/DXF)와 무관한 공통 데이터 구조.

모든 좌표는 '화면에 보이는 방향' 기준이며 y는 아래로 증가한다.
"""
from dataclasses import dataclass, field


@dataclass
class Text:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    angle: int = 0          # 0=가로, 90=세로(아래→위로 읽힘), 270=세로(위→아래)
    size: float = 0.0

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


@dataclass
class Sheet:
    source: str             # 파일 경로
    index: int              # PDF 페이지 번호(1부터) 또는 DXF 레이아웃 순번
    width: float
    height: float
    texts: list = field(default_factory=list)
    segments: list = field(default_factory=list)   # (x0,y0,x1,y1)
    title: str = ""         # 도면명
    number: str = ""        # 도면번호 (예: A62-001)
    unit_per_pt: float = 1.0  # 도면 1단위가 실제 몇 mm인지 (PDF는 축척으로 계산)

    def texts_in(self, x0, y0, x1, y1):
        return [t for t in self.texts
                if x0 <= t.cx <= x1 and y0 <= t.cy <= y1]
