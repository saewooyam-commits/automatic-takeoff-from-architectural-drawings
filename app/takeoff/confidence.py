"""현장 신뢰도: 이 도면을 프로그램이 얼마나 '아는 양식'으로 읽었는지 스스로 매긴다.

처음 보는 사무소 양식이면 숫자가 그럴듯해 보여도 틀릴 수 있으므로, 결과 맨 위에 등급과 이유를 적어
사람이 어디를 먼저 볼지 알게 한다. 등급은 셋: 높음 / 보통 / 낮음.
근거 신호(모두 실행 중 이미 계산된 것들):
  - 도곽: 잡힌 도곽 수, 약한 규칙(세로 테두리 없음·표제란 글자 위치 추정)으로 잡았는지
  - 일람표: 부호 수, 크기(W·H) 읽은 비율, 수량 읽은 비율, 같은 부호 중복
  - 평면도: 층 평면도가 전체 층을 덮는 비율, 일람표와 평면도 수량이 다른 부호 비율
  - 견적서(있을 때): 부호 매칭 비율
"""


def assess(items, plan_counts, kinds, fq, est=None, matched=None, frame_method=""):
    reasons, penalty = [], 0
    n = len(items)
    if n == 0:
        return "낮음", ["창호 일람표를 읽지 못함 (도곽·제목 양식이 처음 보는 것일 수 있음)"]
    # 도곽
    if frame_method:
        if "표제란 글자 위치" in frame_method:
            penalty += 2
            reasons.append("도곽 사각형을 못 찾아 표제란 글자 위치로 추정")
        if "세로 테두리 없" in frame_method or "라벨 기준" in frame_method:
            penalty += 1
            reasons.append("도곽 세로 테두리가 없어 가로선+표제란으로 잡음")
        if "읽기 실패" in frame_method:
            penalty += 2
            reasons.append("읽지 못한 도면 파일이 있음: " + frame_method.split("읽기 실패:")[-1].strip())
    for d in getattr(plan_counts, "diagnosis", []):
        if d.startswith("개정본 중복"):
            reasons.append(d)              # 등급은 그대로, 맨 앞 시트에서 꼭 보이게
    cw = [d for d in getattr(plan_counts, "diagnosis", []) if d.startswith("변환: ")]
    if cw:
        penalty += 2
        reasons.append(f"DWG 변환이 잘린 파일 {len(cw)}개 (도면 일부가 빠졌을 수 있음): " + "; ".join(d[4:].split(":")[0] for d in cw[:4]))
    if any("내용(부호+치수)으로 판정" in d for d in getattr(plan_counts, "diagnosis", [])):
        penalty += 2
        reasons.append("도면 제목으로 일람표를 못 찾아 내용으로 판정함 (처음 보는 표제란 양식)")
    # 일람표 크기·수량
    sized = sum(1 for it in items if it.width and it.height)
    if sized < 0.8 * n:
        penalty += 2
        reasons.append(f"크기(W×H)를 읽은 부호가 {sized}/{n}뿐")
    elif sized < n:
        penalty += 1
        reasons.append(f"크기를 못 읽은 부호 {n - sized}개")
    est_h = sum(1 for it in items if any("비례로 높이" in x for x in it.notes))
    if est_h:
        penalty += 1
        reasons.append(f"세로 치수가 없어 그림 비례로 높이를 추정한 부호 {est_h}개")
    noq = sum(1 for c, v in fq.items() if v[0] is None)
    if noq:
        penalty += 1 if noq <= 2 else 2
        reasons.append(f"수량을 정하지 못한 부호 {noq}개")
    dups = getattr(plan_counts, "dup_codes", set()) or set()
    if dups:
        penalty += 1
        reasons.append("같은 부호가 다른 크기로 여러 번: " + ", ".join(sorted(dups)))
    # 평면도
    cov = getattr(plan_counts, "plan_coverage", 1.0)
    if kinds and not (kinds.get("tagplan") or kinds.get("plan")):
        penalty += 2
        reasons.append("평면도를 찾지 못해 수량은 일람표 표기에만 의존")
    elif cov < 0.6:
        penalty += 1
        reasons.append(f"층 평면도가 전체 층의 {cov:.0%}만 있음")
    diff = sum(1 for c, v in fq.items() if v[2] is not None and v[3] is not None and v[2] != v[3])
    if diff >= max(3, 0.25 * n):
        penalty += 2
        reasons.append(f"일람표 수량과 평면도 집계가 다른 부호 {diff}개 (양식을 잘못 읽었거나 설계변경)")
    elif diff:
        reasons.append(f"일람표≠평면도 부호 {diff}개 (교차검증 시트 확인)")
    # 일람표에 수량이 없어 평면도 집계만으로 정한 부호가 80% 이상이면 수량을 맞대어 볼 근거가 없다 (문 번호 목록은 제외)
    cnt_items = [it for it in items if getattr(it, "layout", "") != "doorlist"]
    only_plan = sum(1 for it in cnt_items if it.code in fq and fq[it.code][2] is None and fq[it.code][3] is not None)
    if cnt_items and only_plan >= 0.8 * len(cnt_items):
        penalty += 1
        reasons.append(f"일람표에 수량이 없어 평면도 집계만으로 수량을 정한 부호 {only_plan}개 (교차검증 없음 → 평면도 표시 누락이 있으면 그대로 빠짐)")
    if getattr(plan_counts, "unit_types", None):
        penalty += 1
        reasons.append("세대 타입 평면도로 세대수를 추정해 수량에 보탬 (확인 필요 표시된 줄)")
    # 견적서
    if est is not None and matched is not None:
        codes = {e.code for e in est if e.code}
        if codes:
            r = len(matched) / max(len(codes), 1)
            if r < 0.6:
                penalty += 2
                reasons.append(f"견적서 부호 {len(codes)}개 중 도면과 짝지은 것 {len(matched)}개 (개정본 차이이거나 부호 체계가 다름)")
    grade = "높음" if penalty <= 1 else "보통" if penalty <= 4 else "낮음"
    if not reasons:
        reasons.append("도곽·일람표·평면도 모두 아는 양식으로 읽힘")
    return grade, reasons


def banner_text(grade, reasons):
    head = {"높음": "이 현장은 아는 양식으로 읽혔습니다. '확인' 표시된 줄만 보면 됩니다.",
            "보통": "일부를 추정하거나 못 읽었습니다. 아래 이유에 해당하는 줄을 먼저 확인하세요.",
            "낮음": "처음 보는 양식이거나 많이 못 읽었습니다. 숫자를 그대로 쓰지 말고 도면과 대조하세요."}[grade]
    return f"현장 신뢰도: {grade} — {head}", reasons
