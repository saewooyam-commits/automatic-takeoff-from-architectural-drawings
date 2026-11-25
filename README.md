# 창호·금속 물량산출 (Window / Door / Metalwork Quantity Takeoff)

건축 도면(DWG, DXF, 벡터 PDF)을 넣으면 창호일람표와 평면도를 찾아 **부호·크기·수량**을 엑셀로 뽑아 주는 프로그램입니다.
순수 파이썬이고 실행 중에 AI를 쓰지 않습니다. 같은 도면이면 항상 같은 결과가 나옵니다.

A tool that reads architectural drawings (DWG, DXF, vector PDF), finds window/door schedules and floor plans,
and writes an Excel sheet with the mark, size and count of every window, door and selected metalwork item.
Plain Python, no AI at runtime. Built for Korean-style drawings.

## 무엇을 내는가
- `<도면이름>_창호물량.xlsx` : 확인사항 / 창호물량 / 교차검증 / 파생물량(코킹·유리·철물) / 금속물량 / 정보 시트
- `<도면이름>_창호물량_검토도면.pdf` : 프로그램이 읽은 자리를 도면 위에 표시한 PDF (사람이 확인하는 용도)
- 확신하지 못하는 항목은 짐작하지 않고 "확인 필요"로 표시합니다.

## 설치
Python 3.10 이상이 필요합니다.

    pip install -r requirements.txt

DWG 파일을 읽으려면 DWG→DXF 변환기가 따로 필요합니다(이 저장소에는 들어 있지 않습니다).
- [GNU LibreDWG](https://www.gnu.org/software/libredwg/) 의 `dwg2dxf`, 또는 ODA File Converter
- 환경변수 `DWG2DXF` 에 변환기 경로를 적거나 PATH 에 두면 됩니다.
- 변환기가 없으면 CAD 에서 DXF(2013 이하)로 저장해서 넣으면 됩니다.

## 사용

    python app/run.py 도면.dwg
    python app/run.py 현장폴더/
    python app/run.py 도면.pdf -e 과거견적서.xls -o 결과.xlsx
    python app/compare.py 이전.dwg 새.dwg -o 비교.xlsx

## 바로 시험해 보기
`examples/견본도면.dxf` 는 직접 그린 가상의 3층 건물 도면입니다(창호일람표 1장 + 1~3층 창호평면도). 실제 현장과 관계없습니다.

    python app/run.py examples/견본도면.dxf
    python app/tests/regress.py        # 정답지와 비교: 부호 4/4, 크기 4/4, 수량 4/4

견본 도면의 AW-2 는 일람표에 4개소로 적혀 있지만 평면도에는 5개가 그려져 있습니다.
프로그램이 이 차이를 찾아 "확인사항" 시트 맨 위에 알려 주는 것을 볼 수 있습니다.
견본 도면은 `examples/make_sample.py` 로 다시 만들 수 있습니다.

## 규칙 고치기
`app/rules.json` 에 코킹 면수, 유리 포함 범위, 다층창호 기준, 철물 기본 수량 같은 값이 모여 있습니다.
들어 있는 값은 예시입니다. 회사 기준에 맞게 고쳐 쓰세요. `_출처` 가 "현장맞춤"인 값은 한 현장에만 맞춰 본 값입니다.

## 검사
`app/tests/regress.py` 는 현장 목록(`app/tests/cases.json`)과 사람이 도면을 읽어 만든 정답지(`app/tests/truth/`)로
결과가 나빠지지 않았는지 확인하는 도구입니다. 개발에 쓴 실제 도면과 정답지는 저작권·보안 때문에 공개하지 않았고, 견본 도면 하나만 들어 있습니다.
코드 설명글에 나오는 "A현장", "B현장" 은 개발 때 쓴 실제 현장을 가린 이름입니다.

## 한계
- 한국 건축 도면 양식을 기준으로 만들었습니다. 처음 보는 설계사무소 양식에서는 틀릴 수 있으니 "확인사항" 시트를 먼저 보세요.
- 글자가 그림(스캔)으로 된 PDF 는 읽지 못합니다.

## 라이선스
AGPL-3.0 (`LICENSE` 참조). 이 프로그램은 PyMuPDF(AGPL-3.0), ezdxf·openpyxl(MIT), xlrd(BSD), Pillow(MIT-CMU)를 사용합니다.
