"""정답지용 독립 태그 추출: 프로그램(find_tags)과 다른 단순 논리로 평면도 태그를 뽑는다.
- 종류 라벨(FSD/SD/SSD/ASSD/AW/CW/FSS) 하나당 가장 가까운 '번호 글자' 1개(위 또는 아래, 3h 이내)
- 번호 글자: 공백 제거 후 10자 이내, 숫자/한글/I/'/-,/ 로 된 짧은 글자
- 평면도 경계: 소제목 사이의 '선이 없는 가장 넓은 빈 구간'으로 자른다

    python tests/tools/truth_tags.py 도면.dxf 출력.pkl
"""
import sys, os, pickle, re
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from collections import Counter, defaultdict
from takeoff.frames import split_frames, classify
from takeoff.tags import floors_from_title

from takeoff.schedule2 import IGNORE_TYPES
_TYPE=re.compile(r"^\s*([A-Z]{1,5})\s*$")
class _T:
    @staticmethod
    def match(txt):
        m=_TYPE.match(txt)
        return m if (m and m.group(1) not in IGNORE_TYPES) else None
TYPE=_T
NUM=re.compile(r"^(?=.*\d)[0-9A-Za-z가-힣][0-9A-Za-z가-힣'\-,/]{0,9}$")   # 숫자가 하나는 들어 있어야 번호로 본다

def plan_regions(f):
    subs=[t for t in f.texts if re.search(r"평면도|안내도|창호도", t.text) and floors_from_title(t.text) and t.angle==0]
    if len(subs)<2:
        # 도곽 하나가 한 층(제목에 층이 있음) → 도곽 전체
        return [(f.title, f.bounds)] if floors_from_title(f.title) else []
    big=max(t.size for t in subs); subs=[t for t in subs if t.size>=big*0.8]
    h=big
    rows=defaultdict(list)
    for t in subs: rows[round(t.cy/(h*4))].append(t)
    x0,y0,x1,y1=f.bounds
    rowkeys=sorted(rows)
    out=[]
    for ri,rk in enumerate(rowkeys):
        ts=sorted(rows[rk], key=lambda t:t.cx)
        band_top = y0 if ri==0 else max(t.cy for t in rows[rowkeys[ri-1]])+h*1.5
        band_bot = max(t.cy for t in ts)+h*1.5
        # 이 띠 안의 선분 x 범위
        segs=[s for s in f.segments if band_top<=min(s[1],s[3]) and max(s[1],s[3])<=band_bot]
        cuts=[x0]
        for a,b in zip(ts,ts[1:]):
            lo,hi=a.cx,b.cx
            # lo~hi 사이를 잘게 나눠 선분이 없는 가장 넓은 구간의 중앙
            n=400; step=(hi-lo)/n; cov=[False]*n
            for s in segs:
                sx0,sx1=min(s[0],s[2]),max(s[0],s[2])
                if sx1<lo or sx0>hi or sx1-sx0>(hi-lo)*0.3: continue   # 두 도면을 가로지르는 긴 선은 무시
                i0=max(0,int((sx0-lo)/step)); i1=min(n-1,int((sx1-lo)/step))
                for i in range(i0,i1+1): cov[i]=True
            best=(0,None); i=0
            while i<n:
                if not cov[i]:
                    j=i
                    while j<n and not cov[j]: j+=1
                    if j-i>best[0]: best=(j-i,(i+j)/2)
                    i=j
                else: i+=1
            cuts.append(lo+best[1]*step if best[1] is not None else (lo+hi)/2)
        cuts.append(x1)
        for k,t in enumerate(ts):
            out.append((t.text, (cuts[k],band_top,cuts[k+1],band_bot)))
    return out

def tags_in(f, box):
    bx0,by0,bx1,by1=box
    texts=[t for t in f.texts if bx0<=t.cx<=bx1 and by0<=t.cy<=by1]
    labs=[t for t in texts if TYPE.match(t.text)]
    nums=[t for t in texts if NUM.match(t.text.replace(' ','')) and not TYPE.match(t.text)]
    found=[]
    for t in labs:
        h=max(t.y1-t.y0,1)
        cand=[(abs(u.cx-t.cx)+abs(u.cy-t.cy),u) for u in nums if abs(u.cx-t.cx)<=h*2.5 and h*0.5<=abs(u.cy-t.cy)<=h*3.5]
        if cand:
            d,u=min(cand,key=lambda x:x[0]); found.append((t.text.strip(), u.text.replace(' ',''), t))
        else: found.append((t.text.strip(), None, t))
    return found

if __name__=='__main__':
    from clean_render import load_sheet
    sh=load_sheet(sys.argv[1])
    frames=split_frames(sh)
    out_pkl=sys.argv[2] if len(sys.argv)>2 else 'tags_raw.pkl'
    res={}
    kinds={f.index: classify(f) for f in frames}
    has_tagplan=any(k=='tagplan' for k in kinds.values())
    for f in frames:
        k0=kinds[f.index]
        if has_tagplan:
            if k0!='tagplan': continue
            regions=plan_regions(f)
        else:
            # 창호평면도가 따로 없는 현장: 층이 적힌 일반 평면도 도곽 전체를 한 영역으로
            if k0!='plan' or not floors_from_title(f.title) or re.search(r"확대|코어|부분|상세|크린넷", f.title): continue
            regions=[(f.title, f.bounds)]
        for k, (title, box) in enumerate(regions):
            tg=tags_in(f, box)
            key=f"{title}@{f.index}#{k}"
            res[key]=tg
            c=Counter((a,b) for a,b,_ in tg)
            print(f"도곽 {f.index} | {title} | 라벨 {len(tg)} | 번호없음 {sum(1 for a,b,_ in tg if b is None)}")
            print('   ', sorted(c.items(), key=lambda x: (x[0][0], str(x[0][1]))))
    pickle.dump({k:[(a,b,(round(t.cx),round(t.cy))) for a,b,t in v] for k,v in res.items()}, open(out_pkl,'wb'))
