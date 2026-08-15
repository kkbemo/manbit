# mogosa — 모의고사 변환·출제 도구

평가원 모의고사 PDF를 한글 파일로 바꾸고, 새로 출제한 문항을 같은 형식의
시험지로 찍어낸다.

```
평가원 PDF ─┐
            ├─> Exam (JSON) ─> HWPX 시험지
교사의 출제 ─┘
```

두 입력이 **같은 렌더러**를 지나므로, 기출을 변환하든 새로 출제하든 결과물의
생김새가 같다.

## 설치

```bash
pip install python-hwpx pdfplumber
```

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
```

Claude Code에서는 `.claude/skills/mogosa/` 스킬이 붙어 있어서, 지문을 붙여넣고
"이 지문으로 3문항 만들어줘"라고 하면 JSON 작성부터 조판까지 알아서 한다.

## 구조

| 파일 | 하는 일 |
|---|---|
| `model.py` | `Exam` / `Question` / `Box` — 중간 표현과 JSON 직렬화 |
| `hwpx_writer.py` | 중간 표현 -> HWPX. A4 2단, 문항 번호, 보기 상자, ①~⑤ |
| `pdf_reader.py` | PDF -> 중간 표현. 단 분리, 줄 잇기, 상자 인식 |
| `cli.py` | 명령줄 도구 |

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
