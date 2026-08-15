"""PDF 파서 테스트.

실제 평가원 PDF는 저작권 때문에 저장소에 둘 수 없다. 대신 같은 기하 구조를
가진 시험용 PDF를 만들어서 레이아웃 추론(단 분리·줄 잇기·상자 인식)을
검증한다. 실제 파일의 글꼴·자간 특성은 진짜 기출로 따로 맞춰야 한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mogosa.hwpx_writer import render_exam
from mogosa.model import Exam
from mogosa.pdf_reader import (
    _looks_wrapped,
    TextLine,
    load_lines,
    parse_pdf,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fixture_pdf import build as build_fixture


@pytest.fixture(scope="module")
def fixture_pdf(tmp_path_factory) -> Path:
    return build_fixture(tmp_path_factory.mktemp("pdf") / "exam.pdf")


@pytest.fixture(scope="module")
def parsed(fixture_pdf: Path):
    return parse_pdf(fixture_pdf, subject="생활과 윤리")


def test_all_questions_found(parsed):
    exam, _ = parsed
    assert [q.number for q in exam.questions] == [1, 2, 3, 4, 5, 6, 7]


def test_no_warnings(parsed):
    _, report = parsed
    assert report.warnings == []


def test_running_heads_removed(parsed):
    exam, report = parsed
    assert "사회탐구 영역" in report.dropped_running_heads
    assert "생활과 윤리" in report.dropped_running_heads
    # 쪽번호가 문항 번호로 오인되면 안 된다
    text = " ".join(q.stem for q in exam.questions)
    assert "사회탐구 영역" not in text


def test_stem_and_points(parsed):
    exam, _ = parsed
    q1, q2 = exam.questions[0], exam.questions[1]
    assert q1.stem == "다음 사상가의 입장으로 가장 적절한 것은?"
    assert q1.points is None
    assert q2.points == 3, "[3점] 배점을 뽑아내야 한다"
    assert "[3점]" not in q2.stem, "배점 표기는 발문에서 떼어내야 한다"


def test_every_question_has_five_choices(parsed):
    exam, _ = parsed
    for q in exam.questions:
        assert len(q.choices) == 5, f"{q.number}번 선택지 {len(q.choices)}개"


def test_choices_on_one_line_are_split(parsed):
    """① ㄱ ② ㄷ ③ ... 처럼 한 줄에 몰린 선택지도 다섯 개로 갈라야 한다."""
    exam, _ = parsed
    assert exam.questions[1].choices == ["ㄱ", "ㄷ", "ㄱ, ㄴ", "ㄴ, ㄷ", "ㄱ, ㄴ, ㄷ"]


def test_boxes_detected_with_kind(parsed):
    exam, _ = parsed
    q2 = exam.questions[1]
    assert len(q2.boxes) == 2
    assert q2.boxes[0].kind == "제시문"
    assert q2.boxes[1].kind == "보기", "<보 기> 라벨이 있는 상자는 보기로 분류"
    assert q2.boxes[1].lines[0].startswith("ㄱ.")
    assert not any("보 기" in line for line in q2.boxes[1].lines), "라벨은 내용에서 빼야 한다"


def test_wrapped_lines_are_rejoined(parsed):
    """줄바꿈으로 잘린 문장이 한 덩어리로 붙어야 한다."""
    exam, _ = parsed
    box = exam.questions[1].boxes[1]
    assert "ㄱ. 갑은 쾌락의 양적 차이만을 도덕 판단의 기준으로 삼는다." in box.lines

    passage = " ".join(exam.questions[2].passage)
    assert passage.startswith("시민 불복종은 법에 대한 충실성의")
    assert passage.endswith("정치적 행위이다.")
    assert len(exam.questions[2].passage) == 1, "한 문단은 한 줄로 합쳐져야 한다"


def test_question_mark_stops_merge(parsed):
    """발문이 뒤따르는 지문을 삼키면 안 된다."""
    exam, _ = parsed
    assert exam.questions[2].stem == "다음 입장에서 부정의 대답을 할 질문으로 가장 적절한 것은?"
    assert "시민 불복종은" not in exam.questions[2].stem


def test_reading_order_across_columns_and_pages(parsed):
    """좌단을 다 읽고 우단으로, 그다음 쪽으로 넘어가야 한다."""
    exam, _ = parsed
    for question in exam.questions[4:]:
        assert question.boxes, f"{question.number}번 상자 유실"
        assert f"{question.number}번 문항의 제시문" in question.boxes[0].lines[0]
        assert question.choices[0] == f"{question.number}번 문항의 1번째 선택지이다."


def test_box_content_not_reordered(parsed):
    """상자 내용이 문항 밖으로 새어 나가면 안 된다."""
    exam, _ = parsed
    for question in exam.questions:
        for box in question.boxes:
            for line in box.lines:
                assert line not in question.choices


def test_lines_carry_geometry(fixture_pdf: Path):
    lines = load_lines(fixture_pdf)
    assert lines
    for line in lines:
        assert line.x1 > line.x0
        assert line.bottom > line.top
        assert line.page >= 1


def test_looks_wrapped_logic():
    def line(text, x1, size=10.0):
        return TextLine(text=text, x0=50, x1=x1, top=0, bottom=10,
                        size=size, page=1, column=0)

    right = 300.0
    # 앞줄이 거의 끝까지 찼고 다음 낱말이 길면 -> 줄바꿈
    assert _looks_wrapped(line("가나다라", 290), line("마바사", 0), right)
    # 앞줄이 한참 짧게 끝났고 다음 낱말이 짧으면 -> 문단 끝
    assert not _looks_wrapped(line("끝.", 150), line("새", 0), right)


def test_round_trip_pdf_to_hwpx(parsed, tmp_path: Path):
    """PDF -> IR -> HWPX가 통째로 돌아가야 한다."""
    from hwpx import validate_editor_open_safety
    from hwpx.document import HwpxDocument

    exam, _ = parsed
    out = render_exam(exam, tmp_path / "round_trip.hwpx")
    assert validate_editor_open_safety(str(out)).reopen_ok

    text = HwpxDocument.open(str(out)).export_text()
    assert "1. 다음 사상가의 입장으로 가장 적절한 것은?" in text
    assert "[3점]" in text, "배점이 다시 찍혀야 한다"
    assert "<보 기>" in text
    assert "① 도덕적 고려의 대상은 이성적 존재에 국한된다." in text


def test_round_trip_json(parsed, tmp_path: Path):
    exam, _ = parsed
    path = exam.to_json(tmp_path / "exam.json")
    restored = Exam.from_json(path)
    assert len(restored.questions) == len(exam.questions)
    assert restored.questions[1].points == 3
    assert restored.questions[1].boxes[1].kind == "보기"


# ---------- 그림·도표 ----------


def test_figure_is_extracted(parsed):
    """그래프가 통째로 사라지지 않고 그림으로 살아 넘어와야 한다."""
    exam, report = parsed
    assert report.figures_found >= 1

    with_figure = [q for q in exam.questions if q.figures]
    assert len(with_figure) == 1
    figure = with_figure[0].figures[0]
    assert figure.data.startswith(b"\x89PNG"), "PNG 그림이어야 한다"
    assert figure.width_mm > 10 and figure.height_mm > 10


def test_figure_labels_do_not_leak_into_text(parsed):
    """축 이름 같은 그림 속 글자가 본문 지문으로 새면 안 된다."""
    exam, report = parsed
    question = [q for q in exam.questions if q.figures][0]
    assert question.passage == [], f"그림 속 글자가 지문으로 샘: {question.passage}"
    assert report.text_absorbed_by_figures >= 1


def test_figure_does_not_eat_choices(parsed):
    """그림 영역이 넓어지다 선택지를 먹으면 안 된다."""
    exam, _ = parsed
    question = [q for q in exam.questions if q.figures][0]
    assert len(question.choices) == 5
    assert question.choices[0].startswith("그래프의 가로축")


def test_figure_survives_json_round_trip(parsed, tmp_path: Path):
    exam, _ = parsed
    restored = Exam.from_json(exam.to_json(tmp_path / "fig.json"))
    original = [q for q in exam.questions if q.figures][0]
    copied = [q for q in restored.questions if q.figures][0]
    assert copied.figures[0].data == original.figures[0].data


def test_figure_embedded_in_hwpx(parsed, tmp_path: Path):
    import zipfile

    from hwpx import validate_editor_open_safety

    exam, _ = parsed
    out = render_exam(exam, tmp_path / "with_figure.hwpx")
    assert validate_editor_open_safety(str(out)).reopen_ok

    names = zipfile.ZipFile(out).namelist()
    assert any(n.startswith("BinData/") for n in names), "그림이 한글 파일에 박혀야 한다"


def test_figures_can_be_turned_off(fixture_pdf: Path):
    exam, report = parse_pdf(fixture_pdf, keep_figures=False)
    assert report.figures_found == 0
    assert all(not q.figures for q in exam.questions)


def test_text_boxes_are_not_mistaken_for_figures(parsed):
    """<보기> 상자 테두리가 그림으로 잡히면 안 된다."""
    exam, report = parsed
    assert report.figures_found == 1, f"그림을 {report.figures_found}개 잡음 (1개여야 함)"
    assert not exam.questions[1].figures, "보기 상자가 그림으로 오인됨"


def test_as_is_mode_produces_page_images(fixture_pdf: Path, tmp_path: Path):
    """'원본 그대로' 모드는 쪽마다 그림 하나씩 넣는다."""
    import zipfile

    from hwpx import validate_editor_open_safety

    from mogosa.hwpx_writer import render_pdf_as_images

    out = render_pdf_as_images(fixture_pdf, tmp_path / "as_is.hwpx", dpi=100)
    assert validate_editor_open_safety(str(out)).reopen_ok

    images = [n for n in zipfile.ZipFile(out).namelist() if n.startswith("BinData/")]
    assert len(images) >= 1
