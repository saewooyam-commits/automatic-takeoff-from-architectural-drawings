"""도곽을 덧칠 없이 깨끗하게 그려 PDF/PNG 로 저장 (정답지 작성용 육안 확인).

    python tests/tools/clean_render.py 도면.dxf 출력폴더 [도곽번호,도곽번호,...]
"""
import sys, os, pickle
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import pymupdf
from takeoff.frames import split_frames, classify
from takeoff.review import _Page


def load_sheet(path):
    """DXF 경로 또는 load_dxf 결과 pickle(.pkl)."""
    if path.lower().endswith('.pkl'):
        return pickle.load(open(path, 'rb'))
    from takeoff.dxf_source import load_dxf
    return load_dxf(path)

def render(pkl, out_dir, kinds_want=None, zoom=2.0, tiles=(2, 2), only=None):
    sh = load_sheet(pkl)
    frames = split_frames(sh)
    os.makedirs(out_dir, exist_ok=True)
    sel = [f for f in frames if (only is not None and f.index in only) or (only is None and classify(f) in (kinds_want or ("schedule",)))]
    print('frames', len(frames), 'selected', [(f.index, f.title, classify(f)) for f in sel])
    for f in sel:
        doc = pymupdf.open()
        pg = _Page(doc, f.bounds, f"도곽 {f.index} {f.title}")
        pg.drawing(f)
        page = doc[0]
        W, H = page.rect.width, page.rect.height
        nx, ny = tiles
        for i in range(nx):
            for j in range(ny):
                clip = pymupdf.Rect(i * W / nx, j * H / ny, (i + 1) * W / nx, (j + 1) * H / ny)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
                pix.save(os.path.join(out_dir, f"f{f.index}_{j}{i}.png"))
        doc.save(os.path.join(out_dir, f"f{f.index}.pdf"))

if __name__ == '__main__':
    render(sys.argv[1], sys.argv[2], only=set(int(x) for x in sys.argv[3].split(',')) if len(sys.argv) > 3 else None)
