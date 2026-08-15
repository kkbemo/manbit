# mogosa — 모의고사 변환·출제 도구

평가원 모의고사 PDF를 한글 파일로 바꾸고, 새로 출제한 문항을 같은 형식의
시험지로 찍어낸다.

```
평가원 PDF ─┐                          ┌─> HWPX 시험지
            ├─> Exam (JSON) ─(성취기준 분석)─┤
교사의 출제 ─┘                          └─> 이원목적표
```

두 입력이 **같은 렌더러**를 지나므로, 기출을 변환하든 새로 출제하든 결과물의
생김새가 같다. 성취기준 분석도 두 경로에 똑같이 붙는다.

## 프로그램으로 쓰기 (권장)

저장소를 받은 뒤 **더블클릭** 한 번이면 된다.

| 운영체제 | 실행할 파일 |
|---|---|
| 윈도우 | `START-Windows.bat` |
| 맥 | `START-Mac.command` (처음 한 번 `chmod +x` 필요) |

검은 창이 뜨고 브라우저가 저절로 열린다. 처음 한 번은 준비 작업으로 1~2분
걸리고, 그다음부터는 바로 뜬다. 파이썬만 깔려 있으면 된다
([python.org](https://www.python.org/downloads/), 설치할 때 **Add Python to
PATH** 체크).

화면에서 하는 일은 셋이다.

1. 평가원 PDF나 문항 JSON을 끌어다 놓는다
2. 성취기준 파일을 넣는다 (선택)
3. 학생용·교사용 한글 파일을 받는다

성취기준을 판정하지 못했거나 확신이 낮은 문항은 표에 노랗게 표시된다. 후보를
눌러 넣거나 직접 코드를 쳐 넣고 **다시 만들기**를 누르면 한글 파일이 새로
만들어진다.

**인터넷으로 아무것도 내보내지 않는다.** 화면도 바깥에서 받아오는 것 없이
프로그램 안에 들어 있어서, 인터넷이 끊긴 학교 컴퓨터에서도 돌아가고 시험
문항이 밖으로 샐 일이 없다.

### 파이썬 없이 쓰고 싶다면 (.exe 만들기)

한 번만 만들어 두면 파이썬이 없는 컴퓨터에도 파일 하나만 복사해서 쓸 수 있다.
**만드는 작업은 반드시 윈도우에서 해야 한다** (PyInstaller는 다른 운영체제용
실행 파일을 만들지 못한다).

```bash
pip install pyinstaller
pyinstaller --onefile --name 모의고사도우미 ^
            --collect-all hwpx --collect-all pdfplumber ^
            --hidden-import mogosa.app ^
            run_app.py
```

`dist\모의고사도우미.exe` 하나가 나온다. 첫 실행이 조금 느린 것은 정상이다.

## 명령줄로 쓰기

파이썬 3.10 이상이 필요하다.

```bash
git clone https://github.com/kkbemo/manbit.git
cd manbit
git checkout claude/hwp-pdf-conversion-dc5wiy

python -m venv .venv
source .venv/bin/activate        # 윈도우: .venv\Scripts\activate
pip install -r requirements.txt
```

테스트까지 돌려 보려면 `pip install -r requirements-dev.txt`.

명령은 항상 저장소 루트(`manbit/`)에서 실행한다. `mogosa/` 안으로 들어가면
`python -m mogosa`가 패키지를 찾지 못한다.

## 쓰는 법

```bash
# 기출 PDF -> 한글 파일
python -m mogosa pdf2hwpx 기출.pdf -o 기출.hwpx --subject "생활과 윤리"

# 중간 JSON을 손보고 싶을 때
python -m mogosa pdf2json 기출.pdf -o 기출.json
python -m mogosa json2hwpx 기출.json -o 기출.hwpx

# 출제한 문항 -> 시험지 (교사용은 정답·해설 포함)
python -m mogosa json2hwpx 출제.json -o 시험지.hwpx
python -m mogosa json2hwpx 출제.json -o 시험지_정답.hwpx --answers

# 성취기준 분석 + 이원목적표
python -m mogosa analyze 출제.json --standards 성취기준.txt
python -m mogosa json2hwpx 출제.json -o 교사용.hwpx --answers --spec-table --standards 성취기준.txt
```

Claude Code에서는 `.claude/skills/mogosa/` 스킬이 붙어 있어서, 지문을 붙여넣고
"이 지문으로 3문항 만들어줘"라고 하면 JSON 작성부터 조판까지 알아서 한다.

## 구조

| 파일 | 하는 일 |
|---|---|
| `model.py` | `Exam` / `Question` / `Box` — 중간 표현과 JSON 직렬화 |
| `hwpx_writer.py` | 중간 표현 -> HWPX. A4 2단, 문항 번호, 보기 상자, ①~⑤ |
| `pdf_reader.py` | PDF -> 중간 표현. 단 분리, 줄 잇기, 상자 인식 |
| `standards.py` | 성취기준 불러오기와 문항 매칭 |
| `cli.py` | 명령줄 도구 |

## 성취기준 분석

성취기준 원문은 **교육과정 문서(NCIC)에서 가져온 것만 쓴다.** 코드와 문장을
지어내면 이원목적표와 평가계획이 통째로 틀어진다.

교육과정 문서에서 복사해 텍스트 파일로 저장하면 그대로 읽힌다.

```
[12생윤01-01] 인간에 대한 다양한 관점을 비교·설명할 수 있다.
[12생윤04-02] 인간과 자연의 관계를 다양한 관점에서 설명할 수 있다.
```

JSON으로 바꾸면 `keywords`를 보탤 수 있다. 원문에 안 나오지만 그 성취기준으로
분류되어야 할 사상가·개념어(환경 성취기준에 `슈바이처`, `레오폴드`,
`대지 윤리`)를 넣는 자리이고, **이것이 정확도를 가장 크게 좌우한다.**
형식은 `data/standards_example.json` 참고.

### 판정 방식

핵심어 겹침에 idf 가중을 곱해 점수를 낸다. 여러 성취기준에 두루 나오는 말
("윤리", "사회")은 가중치가 낮아져 변별을 방해하지 않는다.

**확신이 없으면 비워 둔다.** 아무 코드나 달아 두면 사람이 검토를 건너뛰고
틀린 채로 문서에 실린다. 빈칸은 눈에 띈다.

```
문항  성취기준              확신  근거
   1  12생윤04-02       0.43  레오폴드, 슈바이처, 인간, 자연
   4  (미판정)           0.00
```

낱말만 보는 판정이므로 한계가 뚜렷하다. Claude Code에서 스킬로 쓰면 표시된
문항을 의미로 다시 읽어 판정하는 2차 검토가 자동으로 붙는다.

교사가 `standard`를 직접 써 넣은 문항은 `--overwrite` 없이는 덮어쓰지 않는다.

## 조판 규칙

`hwpx_writer.py`의 `Style`에 전부 모여 있다.

- A4 세로, 좌우 여백 20mm, 2단(단 간격 8mm)
- 제목 블록은 1단(전체 폭), 문항부터 2단
- 본문 함초롬바탕 10pt, 줄간격 160%
- 문항 번호는 굵게, 둘째 줄부터 내어쓰기
- 배점은 `[3점]`처럼 발문 끝에 (2점 문항은 표기하지 않음)
- 선택지 다섯 개의 글자 수 합이 22자 이하면 한 줄에 몰아서 배치

## 알아 둘 것

- **출력은 `.hwpx`다.** HWP의 개방형 포맷으로, 한글 2014 이상에서 그대로 열린다.
  구형 `.hwp`가 필요하면 한글에서 열고 *다른 이름으로 저장*하면 된다.
  (`.hwp`는 비공개 바이너리 포맷이라 한컴오피스 없이는 만들 수 없다.)
- **PDF 변환은 추론이다.** PDF에는 "이건 발문", "이건 보기 상자" 같은 정보가
  없고 글자와 좌표뿐이다. 지문형 문항은 잘 되지만 도표·그림이 든 문항은
  글자만 넘어온다. 변환 후 경고 메시지를 확인할 것.
- **스캔본은 안 된다.** 글자가 없는 이미지 PDF는 OCR이 먼저 필요하다.

## 시험

```bash
python -m pytest tests/ -q
```

실제 평가원 PDF는 저작권 때문에 저장소에 두지 않았다. 대신
`tests/make_fixture_pdf.py`가 같은 기하 구조(A4 2단, 테두리 상자, 머리말,
단·쪽 넘김)를 가진 PDF를 만들어 파서를 검증한다. 이것으로 확인되는 것은
레이아웃 추론이고, 실제 기출의 글꼴·자간 특성은 진짜 파일로 맞춰야 한다.
