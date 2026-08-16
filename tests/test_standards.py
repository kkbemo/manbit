"""성취기준 매칭 테스트.

핵심은 "맞히는 것"만이 아니다. **모르는 것을 모른다고 하는 것**이 그만큼
중요하다. 틀린 코드가 조용히 달리면 사람이 검토를 건너뛰고, 그대로
평가계획에 실린다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mogosa.model import Box, Exam, Question
from mogosa.standards import (
    Standard,
    StandardSet,
    coverage,
    extract_keywords,
)

DATA = Path(__file__).resolve().parents[1] / "mogosa" / "data" / "standards_example.json"
SAMPLE = Path(__file__).resolve().parents[1] / "mogosa" / "examples" / "sample_exam.json"


@pytest.fixture(scope="module")
def standards() -> StandardSet:
    return StandardSet.from_json(DATA)


# ---------- 키워드 추출 ----------


def test_extract_keywords_strips_josa():
    keywords = extract_keywords("인간과 자연의 관계를 설명할 수 있다.")
    assert "인간" in keywords
    assert "자연" in keywords
    assert "관계" in keywords


def test_extract_keywords_drops_stopwords():
    keywords = extract_keywords("다양한 관점을 비교하여 설명할 수 있다.")
    for noise in ("수", "있다", "설명할", "다양한", "관점"):
        assert noise not in keywords


def test_extract_keywords_handles_empty():
    assert extract_keywords("") == []
    assert extract_keywords("!!! ??? ...") == []


# ---------- 불러오기 ----------


def test_load_json(standards: StandardSet):
    assert len(standards) == 6
    assert standards.get("12생윤04-02") is not None


def test_load_from_pasted_text():
    """교육과정 문서에서 복사해 붙여넣은 형태를 그대로 읽어야 한다."""
    pasted = """
    [12생윤01-01] 인간에 대한 다양한 관점을 비교·설명할 수 있다.
    [12생윤02-01] 삶과 죽음에 대한 여러 윤리적 입장을 설명할 수 있다.

    (빈 줄과 잡다한 설명은 무시한다)
    """
    loaded = StandardSet.from_text(pasted, subject="생활과 윤리")
    assert len(loaded) == 2
    assert loaded.get("12생윤01-01").text.startswith("인간에 대한")
    assert loaded.subject == "생활과 윤리"


def test_load_text_ignores_lines_without_code():
    loaded = StandardSet.from_text("성취기준 목록\n안내 문구\n")
    assert len(loaded) == 0


def test_code_lookup_ignores_spaces():
    loaded = StandardSet.from_text("[12생윤 01-01] 내용이다.")
    assert loaded.get("12생윤01-01") is not None


# ---------- 매칭 ----------


def _question(number: int, stem: str, box: str = "", choices: list[str] | None = None) -> Question:
    return Question(
        number=number,
        stem=stem,
        boxes=[Box(lines=[box])] if box else [],
        choices=choices or [],
    )


def test_matches_environment_question(standards: StandardSet):
    question = _question(
        1,
        "다음 사상가의 입장으로 가장 적절한 것은?",
        "인간은 자연 전체의 일부일 뿐이며 자연에 대해 특권을 지니지 않는다. "
        "살아 있는 모든 생명의 의지를 존중해야 한다.",
    )
    result = standards.match(question)
    assert result.best.code == "12생윤04-02"


def test_matches_ethics_theory_question(standards: StandardSet):
    question = _question(
        2,
        "갑, 을의 입장에 대한 설명으로 옳은 것은?",
        "갑: 쾌락과 고통의 총량이 행위의 옳고 그름을 결정한다. "
        "을: 준칙이 보편적 법칙이 되도록 행위하라.",
    )
    assert standards.match(question).best.code == "12생윤01-02"


def test_matched_keywords_are_reported(standards: StandardSet):
    """왜 그 성취기준으로 판정했는지 근거가 남아야 한다."""
    question = _question(1, "환경 문제", "레오폴드의 대지 윤리와 생태 중심주의")
    best = standards.match(question).best
    assert best.code == "12생윤04-02"
    assert any("레오폴드" in k or "생태" in k for k in best.matched)


def test_unrelated_question_is_left_unmapped(standards: StandardSet):
    """짚이는 게 없으면 비워 둬야 한다. 아무거나 달면 안 된다."""
    exam = Exam(questions=[_question(1, "다음 중 옳은 것은?", "", ["ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"])])
    standards.tag_exam(exam)
    assert exam.questions[0].standard is None
    assert exam.unmapped_questions() == [1]


def test_ambiguous_question_is_flagged_not_guessed(standards: StandardSet):
    """1등과 2등이 붙어 있으면 확신도가 낮게 나와야 한다."""
    # 두 성취기준에 두루 걸리는 말만 넣는다
    exam = Exam(questions=[_question(1, "윤리적 문제를 설명한 것은?", "사회의 윤리 문제")])
    standards.tag_exam(exam)
    question = exam.questions[0]
    if question.standard:
        assert (question.standard_confidence or 0) < 0.6
    else:
        assert exam.unmapped_questions() == [1]


def test_tag_exam_respects_existing_by_default(standards: StandardSet):
    """교사가 직접 지정한 성취기준을 덮어쓰면 안 된다."""
    question = _question(1, "환경 문제", "생태 중심주의와 대지 윤리")
    question.standard = "12생윤05-02"
    exam = Exam(questions=[question])

    standards.tag_exam(exam)
    assert exam.questions[0].standard == "12생윤05-02", "사람이 정한 값은 지켜야 한다"

    standards.tag_exam(exam, overwrite=True)
    assert exam.questions[0].standard == "12생윤04-02"


def test_idf_downweights_common_words():
    """여러 성취기준에 두루 나오는 말이 매칭을 지배하면 안 된다."""
    loaded = StandardSet(
        [
            Standard(code="A-01", text="윤리 문제를 설명한다.", keywords=["윤리"]),
            Standard(code="A-02", text="윤리 문제와 안락사를 설명한다.", keywords=["윤리", "안락사"]),
            Standard(code="A-03", text="윤리 문제와 환경을 설명한다.", keywords=["윤리", "환경"]),
        ]
    )
    question = _question(1, "안락사에 대한 입장은?", "안락사 논쟁")
    assert loaded.match(question).best.code == "A-02"


def test_full_sample_exam_mapping(standards: StandardSet):
    """예시 시험지 전체가 사람 판단과 일치해야 한다."""
    exam = Exam.from_json(SAMPLE)
    for question in exam.questions:  # 태그·해설 힌트를 지우고 본문만으로 판정
        question.tags = []
        question.explanation = ""
    standards.tag_exam(exam)

    assert exam.questions[0].standard == "12생윤04-02"  # 슈바이처, 생명 외경
    assert exam.questions[1].standard == "12생윤01-02"  # 벤담, 칸트
    assert exam.questions[2].standard == "12생윤06-01"  # 롤스, 시민 불복종
    assert exam.questions[3].standard is None           # 내용 없는 시험용 문항


def test_coverage_lists_unused_standards(standards: StandardSet):
    exam = Exam.from_json(SAMPLE)
    standards.tag_exam(exam, overwrite=True)
    table = coverage(exam, standards)
    assert table["12생윤04-02"] == [1]
    assert table["12생윤02-03"] == [], "출제되지 않은 성취기준도 목록에 남아야 한다"


# ---------- 조판 ----------


def test_spec_table_rendered(standards: StandardSet, tmp_path: Path):
    from hwpx import validate_editor_open_safety
    from hwpx.document import HwpxDocument
    from mogosa.hwpx_writer import render_exam

    exam = Exam.from_json(SAMPLE)
    standards.tag_exam(exam, overwrite=True)
    out = render_exam(exam, tmp_path / "spec.hwpx", include_answers=True,
                      include_spec_table=True, standards=standards)

    assert validate_editor_open_safety(str(out)).reopen_ok
    text = HwpxDocument.open(str(out)).export_text()
    assert "문항 정보표 (이원목적표)" in text
    assert "12생윤04-02" in text
    assert "인간과 자연의 관계" in text, "성취기준 내용 요약이 들어가야 한다"
    assert "성취기준 미판정: 4번" in text, "빈칸은 눈에 띄게 표시해야 한다"


def test_spec_table_works_without_standards_file(tmp_path: Path):
    """성취기준 파일 없이도 표는 나와야 한다 (칸이 빌 뿐)."""
    from hwpx.document import HwpxDocument
    from mogosa.hwpx_writer import render_exam

    exam = Exam.from_json(SAMPLE)
    out = render_exam(exam, tmp_path / "bare.hwpx", include_spec_table=True)
    assert "문항 정보표" in HwpxDocument.open(str(out)).export_text()


def test_standard_shown_in_explanation(standards: StandardSet, tmp_path: Path):
    from hwpx.document import HwpxDocument
    from mogosa.hwpx_writer import render_exam

    exam = Exam.from_json(SAMPLE)
    standards.tag_exam(exam, overwrite=True)
    out = render_exam(exam, tmp_path / "ans.hwpx", include_answers=True, standards=standards)
    assert "[12생윤04-02]" in HwpxDocument.open(str(out)).export_text()


def test_standards_survive_json_round_trip(standards: StandardSet, tmp_path: Path):
    exam = Exam.from_json(SAMPLE)
    standards.tag_exam(exam, overwrite=True)
    restored = Exam.from_json(exam.to_json(tmp_path / "tagged.json"))
    assert restored.questions[0].standard == "12생윤04-02"
    assert restored.questions[0].standard_confidence is not None
