"""렌더러 회귀 테스트.

특히 secPr(용지·여백·머리말·단 설정) 보존은 반드시 지켜야 한다.
문단 0을 지우면 이것들이 통째로 사라지는데, 파일은 멀쩡히 열리기 때문에
눈으로는 잘 안 잡힌다.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mogosa.model import Box, Exam, Question
from mogosa.hwpx_writer import Style, render_exam

SAMPLE = Path(__file__).resolve().parents[1] / "mogosa" / "examples" / "sample_exam.json"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("render") / "exam.hwpx"
    render_exam(Exam.from_json(SAMPLE), out, include_answers=True)
    return out


def section_xml(path: Path) -> str:
    return zipfile.ZipFile(path).read("Contents/section0.xml").decode("utf-8")


def header_xml(path: Path) -> str:
    return zipfile.ZipFile(path).read("Contents/header.xml").decode("utf-8")


def test_package_opens_in_editor(rendered: Path):
    from hwpx import validate_editor_open_safety

    report = validate_editor_open_safety(str(rendered))
    assert report.reopen_ok
    assert report.blocking_package_errors == ()


def test_section_properties_survive(rendered: Path):
    """secPr이 살아 있어야 용지/여백/머리말이 유지된다."""
    xml = section_xml(rendered)
    assert "<hp:secPr" in xml, "secPr이 사라졌다 — 문단 0을 지웠는지 확인할 것"

    page = re.search(r'<hp:pagePr[^>]*width="(\d+)"[^>]*height="(\d+)"', xml)
    assert page, "pagePr 없음"
    # A4 세로: 210mm x 297mm (HWPUNIT)
    assert int(page.group(1)) == 59528
    assert int(page.group(2)) == 84189

    margin = re.search(r'<hp:margin[^>]*left="(\d+)"', xml)
    assert margin and int(margin.group(1)) == 5669  # 20mm


def test_header_is_set(rendered: Path):
    assert section_xml(rendered).count("<hp:header") >= 1


def test_two_column_layout_starts_after_title(rendered: Path):
    """제목 블록은 1단, 문항부터 2단이어야 한다."""
    counts = re.findall(r'<hp:colPr[^>]*colCount="(\d+)"', section_xml(rendered))
    assert counts == ["1", "2"], f"단 설정이 예상과 다름: {counts}"


def test_title_block_order(rendered: Path):
    from hwpx.document import HwpxDocument

    lines = [l for l in HwpxDocument.open(str(rendered)).export_text().split("\n") if l.strip()]
    assert lines[0] == "사회탐구 영역"
    assert "홀수형" in lines[1]
    assert lines[2].startswith("○")


def test_question_content_in_order(rendered: Path):
    from hwpx.document import HwpxDocument

    text = HwpxDocument.open(str(rendered)).export_text()
    assert "1. 다음 사상가의 입장으로 가장 적절한 것은?" in text
    assert "[3점]" in text
    assert "<보 기>" in text
    # 보기 항목에 ㄱ. ㄴ. ㄷ. 이 자동으로 붙어야 한다
    assert "ㄱ. 갑은 쾌락의 양적 차이만을" in text
    assert "ㄹ. 갑은 을과 달리" in text
    # 문항 순서 유지
    assert text.index("1. 다음 사상가") < text.index("2. 갑, 을의") < text.index("3. 다음 입장")


def test_short_choices_go_on_one_line(rendered: Path):
    from hwpx.document import HwpxDocument

    text = HwpxDocument.open(str(rendered)).export_text()
    assert "① ㄱ   ② ㄴ   ③ ㄷ" in text, "짧은 선택지는 한 줄에 배치되어야 한다"


def test_long_choices_go_on_separate_lines(rendered: Path):
    from hwpx.document import HwpxDocument

    lines = HwpxDocument.open(str(rendered)).export_text().split("\n")
    assert any(l.startswith("① 도덕적 고려의 대상은") for l in lines)
    assert any(l.startswith("② 생명을 해치는") for l in lines)


def test_answer_key_rendered(rendered: Path):
    from hwpx.document import HwpxDocument

    text = HwpxDocument.open(str(rendered)).export_text()
    assert "정답 및 해설" in text
    assert "슈바이처의 생명 외경" in text


def test_fonts_registered(rendered: Path):
    faces = set(re.findall(r'face="([^"]+)"', header_xml(rendered)))
    assert "함초롬바탕" in faces
    assert "함초롬돋움" in faces


def test_answers_omitted_by_default(tmp_path: Path):
    out = tmp_path / "no_answer.hwpx"
    render_exam(Exam.from_json(SAMPLE), out)

    from hwpx.document import HwpxDocument

    text = HwpxDocument.open(str(out)).export_text()
    assert "정답 및 해설" not in text
    assert "슈바이처의 생명 외경" not in text


def test_single_column_style(tmp_path: Path):
    out = tmp_path / "one_col.hwpx"
    render_exam(Exam.from_json(SAMPLE), out, style=Style(columns=1))
    counts = re.findall(r'<hp:colPr[^>]*colCount="(\d+)"', section_xml(out))
    assert counts == ["1"]


def test_minimal_exam_renders(tmp_path: Path):
    """상자도 선택지도 없는 최소 문항도 깨지지 않아야 한다."""
    exam = Exam(questions=[Question(number=1, stem="서술하시오.")])
    out = tmp_path / "min.hwpx"
    render_exam(exam, out)

    from hwpx import validate_editor_open_safety

    assert validate_editor_open_safety(str(out)).reopen_ok


def test_validate_catches_bad_input():
    exam = Exam(
        questions=[
            Question(number=1, stem="", choices=["가", "나"], answer=9),
            Question(number=1, stem="중복 번호"),
        ]
    )
    problems = exam.validate()
    assert any("발문이 비어" in p for p in problems)
    assert any("선택지가 2개" in p for p in problems)
    assert any("범위를 벗어남" in p for p in problems)
    assert any("중복" in p for p in problems)


def test_box_auto_mark_off_by_default():
    box = Box(lines=["첫째", "둘째"])
    assert box.marked_lines() == ["첫째", "둘째"]
    assert box.display_label() is None

    bogi = Box(kind="보기", lines=["첫째", "둘째"], auto_mark=True)
    assert bogi.marked_lines() == ["ㄱ. 첫째", "ㄴ. 둘째"]
    assert bogi.display_label() == "<보 기>"
