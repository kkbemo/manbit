"""Exam(IR) -> HWPX 렌더러.

이 모듈이 이 도구의 심장이다. PDF에서 읽어온 시험지든, 교사가 새로 출제한
문항이든 결국 여기를 통과해서 같은 모양의 한글 파일이 된다.

평가원 시험지의 레이아웃 관례를 따른다.
    - A4 세로, 좌우 여백 20mm, 2단 편집
    - 상단에 영역명/과목명 제목 블록 (단 나누기 전, 전체 폭)
    - 문항: `1.` 굵게 + 발문 + 배점, 둘째 줄부터 내어쓰기
    - 제시문/보기: 실선 테두리 상자
    - 선택지: ①~⑤, 짧으면 한 줄에 몰아서
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hwpx.document import HwpxDocument

from .model import CHOICE_MARKS, Box, Exam, Question

HWP_PER_MM = 7200 / 25.4
HWP_PER_PT = 100


def mm(value: float) -> int:
    return round(value * HWP_PER_MM)


def pt(value: float) -> int:
    return round(value * HWP_PER_PT)


@dataclass
class Style:
    """시험지 서식. 과목이나 학교 관례에 맞춰 바꿔 쓰라고 밖으로 뺐다."""

    body_font: str = "함초롬바탕"
    head_font: str = "함초롬돋움"

    title_size: float = 17.0        # "사회탐구 영역"
    subject_size: float = 10.5      # 시험명 · 과목 · 형별 줄
    notice_size: float = 9.0
    stem_size: float = 10.0
    passage_size: float = 9.5
    box_size: float = 9.5
    choice_size: float = 10.0

    line_spacing: int = 160         # %
    box_line_spacing: int = 150

    page_margin_lr_mm: float = 20.0
    page_margin_top_mm: float = 20.0
    page_margin_bottom_mm: float = 15.0
    header_margin_mm: float = 10.0

    columns: int = 2
    column_gap_mm: float = 8.0

    question_gap_pt: float = 9.0    # 문항과 문항 사이
    block_gap_pt: float = 4.0       # 발문-상자-선택지 사이
    stem_indent_mm: float = 5.5     # 문항 번호 폭만큼 내어쓰기
    choice_indent_mm: float = 4.5

    # 선택지 다섯 개의 글자 수 합이 이 값 이하이면 한 줄에 몰아 쓴다
    inline_choice_threshold: int = 22

    def column_width_mm(self) -> float:
        usable = 210.0 - 2 * self.page_margin_lr_mm
        if self.columns <= 1:
            return usable
        gaps = self.column_gap_mm * (self.columns - 1)
        return (usable - gaps) / self.columns


class ExamRenderer:
    """Exam 하나를 HWPX 파일 하나로 그린다."""

    def __init__(self, exam: Exam, style: Style | None = None, *,
                 include_answers: bool = False, include_spec_table: bool = False,
                 standards=None):
        self.exam = exam
        self.style = style or Style()
        self.include_answers = include_answers
        self.include_spec_table = include_spec_table
        # 이원목적표의 "내용 요약" 칸을 채우는 데 쓴다. 없어도 동작한다.
        self._standard_lookup = {s.code: s for s in standards} if standards else {}

        self.doc = HwpxDocument.new()
        self._header = self.doc._root.headers[0]
        self._char_cache: dict[tuple, str] = {}
        self._para_cache: dict[tuple, str] = {}
        self._reuse_first = True

    # ------------------------------------------------------------------
    # 저수준 헬퍼
    # ------------------------------------------------------------------

    def _char(self, *, size: float, bold: bool = False, font: str | None = None,
              underline: bool = False) -> str:
        key = (size, bold, font or self.style.body_font, underline)
        if key not in self._char_cache:
            self._char_cache[key] = self.doc.styles.ensure_run(
                font=key[2], size=size, bold=bold, underline=underline
            )
        return self._char_cache[key]

    def _para_fmt(self, *, alignment: str | None = None, line_spacing: int | None = None,
                  left_mm: float = 0.0, right_mm: float = 0.0, first_mm: float = 0.0,
                  before_pt: float = 0.0, after_pt: float = 0.0,
                  keep_with_next: bool | None = None) -> str:
        key = (alignment, line_spacing, left_mm, right_mm, first_mm,
               before_pt, after_pt, keep_with_next)
        if key in self._para_cache:
            return self._para_cache[key]

        margins: dict[str, int] = {}
        if left_mm:
            margins["left"] = mm(left_mm)
        if right_mm:
            margins["right"] = mm(right_mm)
        if first_mm:
            margins["intent"] = mm(first_mm)
        if before_pt:
            margins["prev"] = pt(before_pt)
        if after_pt:
            margins["next"] = pt(after_pt)

        break_setting = None
        if keep_with_next is not None:
            break_setting = {"keep_with_next": bool(keep_with_next)}

        para_id = self._header.ensure_paragraph_format(
            alignment=alignment,
            line_spacing_percent=line_spacing,
            margins=margins or None,
            break_setting=break_setting,
        )
        self._para_cache[key] = para_id
        return para_id

    def _new_paragraph(self):
        """문서 끝에 빈 문단을 만든다.

        맨 처음 한 번은 HwpxDocument.new()가 만들어 둔 빈 문단을 재활용한다.
        그 문단에는 <hp:secPr>(용지·여백·머리말·기본 단 설정)이 들어 있어서
        지우면 페이지 설정이 통째로 사라진다. 반드시 살려서 첫 줄로 쓴다.
        """
        if self._reuse_first:
            self._reuse_first = False
            existing = self.doc.paragraphs
            if existing and not existing[0].text.strip():
                return existing[0]
        return self.doc.add_paragraph("", include_run=False)

    def _write(self, runs: list[tuple[str, str]], *, para_id: str | None = None,
               paragraph=None):
        """(텍스트, charPr id) 목록을 한 문단에 쓴다."""
        para = paragraph if paragraph is not None else self._new_paragraph()
        if para_id is not None:
            para.para_pr_id_ref = para_id
        for text, char_id in runs:
            para.add_run(text, char_pr_id_ref=char_id)
        return para

    # ------------------------------------------------------------------
    # 문서 구성 요소
    # ------------------------------------------------------------------

    def _setup_page(self) -> None:
        s = self.style
        self.doc.page.setup(
            paper_size="A4",
            orientation="PORTRAIT",
            margins_mm={
                "left": s.page_margin_lr_mm,
                "right": s.page_margin_lr_mm,
                "top": s.page_margin_top_mm,
                "bottom": s.page_margin_bottom_mm,
                "header": s.header_margin_mm,
                "footer": s.header_margin_mm,
            },
        )
        if self.exam.subject:
            self.doc.page.set_header(text=self.exam.subject)

    def _title_block(self) -> None:
        """단 나누기 전, 시험지 전체 폭을 쓰는 제목 영역.

        표를 쓰지 않고 문단만으로 짠다. 표를 맨 앞에 두면 secPr을 물고 있는
        문단 0이 표 뒤로 밀려 제목 순서가 뒤집히기 때문이다.
        """
        s = self.style
        e = self.exam

        # 첫 줄(영역명)이 문단 0을 재활용한다. 이 호출이 항상 가장 먼저여야 한다.
        self._write(
            [(e.area, self._char(size=s.title_size, bold=True, font=s.head_font))],
            para_id=self._para_fmt(alignment="LEFT"),
        )

        meta = [item for item in (e.title, e.subject, e.form) if item]
        para = self._write(
            [(" · ".join(meta), self._char(size=s.subject_size, font=s.head_font))],
            para_id=self._para_fmt(alignment="RIGHT", after_pt=2.0),
        )
        index = self._index_of(para)
        if index is not None:
            self.doc.styles.apply_paragraph_format(
                paragraph_index=index, bottom_border=True,
                border_color="#000000", border_width="0.4 mm",
            )

        for note in e.notice:
            self._write(
                [(note, self._char(size=s.notice_size))],
                para_id=self._para_fmt(line_spacing=150, before_pt=2.0),
            )

    def _index_of(self, paragraph) -> int | None:
        for i, candidate in enumerate(self.doc.paragraphs):
            if candidate.element is paragraph.element:
                return i
        return None

    def _render_question(self, q: Question, *, first: bool) -> None:
        s = self.style

        # --- 발문 ---
        runs = [(f"{q.number}.", self._char(size=s.stem_size, bold=True))]
        if q.stem:
            runs.append((f" {q.stem}", self._char(size=s.stem_size)))
        if q.points:
            runs.append((f" [{q.points}점]", self._char(size=s.stem_size, bold=True)))
        stem_para = self._write(
            runs,
            para_id=self._para_fmt(
                line_spacing=s.line_spacing,
                left_mm=s.stem_indent_mm,
                first_mm=-s.stem_indent_mm,
                before_pt=0.0 if first else s.question_gap_pt,
                after_pt=s.block_gap_pt,
                keep_with_next=True,
            ),
        )

        # --- 상자 없는 지문 ---
        for line in q.passage:
            self._write(
                [(line, self._char(size=s.passage_size))],
                para_id=self._para_fmt(
                    line_spacing=s.line_spacing,
                    left_mm=s.stem_indent_mm,
                    first_mm=2.0,
                    after_pt=s.block_gap_pt,
                ),
            )

        # --- 상자 ---
        for box in q.boxes:
            self._render_box(box)

        # --- 선택지 ---
        self._render_choices(q.choices)

        return stem_para

    def _render_box(self, box: Box) -> None:
        s = self.style
        fill = self.doc.styles.ensure_border_fill(
            border_color="#000000", border_width="0.12 mm",
            active_borders=("left", "right", "top", "bottom"),
        )
        width = mm(s.column_width_mm() - 2.0)
        table = self.doc.add_table(1, 1, width=width, border_fill_id_ref=fill)
        cell = table.cell(0, 0)

        label = box.display_label()
        lines = box.marked_lines()
        target_paragraphs = list(cell.paragraphs)

        def cell_para():
            if target_paragraphs:
                return target_paragraphs.pop(0)
            return cell.add_paragraph("")

        if label:
            self._write(
                [(label, self._char(size=s.box_size, bold=True))],
                para_id=self._para_fmt(alignment="CENTER", line_spacing=s.box_line_spacing),
                paragraph=cell_para(),
            )
        for line in lines:
            self._write(
                [(line, self._char(size=s.box_size))],
                para_id=self._para_fmt(
                    line_spacing=s.box_line_spacing,
                    left_mm=4.0,
                    first_mm=-4.0 if box.auto_mark else 0.0,
                ),
                paragraph=cell_para(),
            )

    def _render_choices(self, choices: list[str]) -> None:
        s = self.style
        if not choices:
            return

        marked = [
            f"{CHOICE_MARKS[i] if i < len(CHOICE_MARKS) else str(i + 1)} {text}"
            for i, text in enumerate(choices)
        ]
        total = sum(len(text) for text in choices)

        if total <= s.inline_choice_threshold:
            # 짧은 선택지는 평가원처럼 한 줄에 늘어놓는다
            self._write(
                [("   ".join(marked), self._char(size=s.choice_size))],
                para_id=self._para_fmt(
                    line_spacing=s.line_spacing,
                    left_mm=s.choice_indent_mm,
                    before_pt=s.block_gap_pt,
                ),
            )
            return

        for i, text in enumerate(marked):
            self._write(
                [(text, self._char(size=s.choice_size))],
                para_id=self._para_fmt(
                    line_spacing=s.line_spacing,
                    left_mm=s.choice_indent_mm,
                    first_mm=-s.choice_indent_mm,
                    before_pt=s.block_gap_pt if i == 0 else 0.0,
                ),
            )

    def _render_answer_key(self) -> None:
        s = self.style
        answered = [q for q in self.exam.questions if q.answer]
        if not answered:
            return

        self._write(
            [("정답 및 해설", self._char(size=13.0, bold=True, font=s.head_font))],
            para_id=self._para_fmt(alignment="CENTER", before_pt=18.0, after_pt=6.0),
        )

        fill = self.doc.styles.ensure_border_fill(
            border_color="#000000", border_width="0.12 mm",
            active_borders=("left", "right", "top", "bottom"),
        )
        cols = min(10, len(answered))
        rows = 2 * ((len(answered) + cols - 1) // cols)
        table = self.doc.add_table(rows, cols, width=mm(s.column_width_mm()),
                                   border_fill_id_ref=fill)
        for i, q in enumerate(answered):
            block, col = divmod(i, cols)
            head = table.cell(block * 2, col)
            body = table.cell(block * 2 + 1, col)
            centered = self._para_fmt(alignment="CENTER")
            self._write([(str(q.number), self._char(size=9.0, bold=True))],
                        para_id=centered, paragraph=head.paragraphs[0])
            self._write([(CHOICE_MARKS[q.answer - 1], self._char(size=9.0))],
                        para_id=centered, paragraph=body.paragraphs[0])

        for q in answered:
            if not q.explanation:
                continue
            runs = [
                (f"{q.number}. ", self._char(size=s.passage_size, bold=True)),
                (f"정답 {CHOICE_MARKS[q.answer - 1]}  ", self._char(size=s.passage_size, bold=True)),
            ]
            if q.standard:
                runs.append((f"[{q.standard}]  ", self._char(size=s.passage_size - 0.5, bold=True)))
            runs.append((q.explanation, self._char(size=s.passage_size)))
            self._write(
                runs,
                para_id=self._para_fmt(
                    line_spacing=s.line_spacing,
                    left_mm=s.stem_indent_mm,
                    first_mm=-s.stem_indent_mm,
                    before_pt=s.block_gap_pt,
                ),
            )

    def _render_spec_table(self) -> None:
        """이원목적표 — 문항별 성취기준·배점·정답 분석표.

        평가계획에 그대로 붙일 수 있게 만든다. 성취기준을 판정하지 못한
        문항은 빈칸으로 남겨서, 사람이 채워야 할 자리가 눈에 띄게 한다.
        """
        s = self.style
        questions = self.exam.questions
        if not questions:
            return

        self._write(
            [("문항 정보표 (이원목적표)", self._char(size=13.0, bold=True, font=s.head_font))],
            para_id=self._para_fmt(alignment="CENTER", before_pt=18.0, after_pt=6.0),
        )

        headers = ["문항", "성취기준", "내용 요약", "배점", "정답", "난이도"]
        fill = self.doc.styles.ensure_border_fill(
            border_color="#000000", border_width="0.12 mm",
            active_borders=("left", "right", "top", "bottom"),
        )
        table = self.doc.add_table(
            len(questions) + 1, len(headers),
            width=mm(s.column_width_mm()),
            border_fill_id_ref=fill,
        )

        # 폭 배분: 내용 요약이 가장 넓어야 읽을 만하다
        ratios = [0.08, 0.20, 0.44, 0.09, 0.09, 0.10]
        table.set_column_widths([mm(s.column_width_mm() * r) for r in ratios])

        centered = self._para_fmt(alignment="CENTER", line_spacing=130)
        left = self._para_fmt(alignment="LEFT", line_spacing=130)
        head_char = self._char(size=8.5, bold=True, font=s.head_font)
        body_char = self._char(size=8.5)

        for column, label in enumerate(headers):
            self._write([(label, head_char)], para_id=centered,
                        paragraph=table.cell(0, column).paragraphs[0])

        for row, question in enumerate(questions, start=1):
            summary = ""
            if question.standard and self._standard_lookup:
                standard = self._standard_lookup.get(question.standard)
                if standard is not None:
                    summary = standard.short(34)
            if not summary:
                summary = ", ".join(question.tags)

            cells = [
                (str(question.number), centered, body_char),
                (question.standard or "", centered, body_char),
                (summary, left, body_char),
                (f"{question.points or 2}", centered, body_char),
                (CHOICE_MARKS[question.answer - 1] if question.answer else "", centered, body_char),
                (question.difficulty, centered, body_char),
            ]
            for column, (text, para_id, char_id) in enumerate(cells):
                self._write([(text, char_id)], para_id=para_id,
                            paragraph=table.cell(row, column).paragraphs[0])

        unmapped = self.exam.unmapped_questions()
        if unmapped:
            note = "※ 성취기준 미판정: " + ", ".join(f"{n}번" for n in unmapped) + " — 확인 후 기입"
            self._write(
                [(note, self._char(size=8.5))],
                para_id=self._para_fmt(line_spacing=130, before_pt=3.0),
            )

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def render(self, path: str | Path) -> Path:
        s = self.style
        self._setup_page()
        self._title_block()

        first_question_para = None
        for i, q in enumerate(self.exam.questions):
            para = self._render_question(q, first=(i == 0))
            if first_question_para is None:
                first_question_para = para

        if self.include_answers:
            self._render_answer_key()
        if self.include_spec_table:
            self._render_spec_table()

        # 제목 블록 다음부터 다단으로 바꾼다
        if s.columns > 1 and first_question_para is not None:
            self.doc.page.set_columns(
                s.columns,
                same_gap=mm(s.column_gap_mm),
                paragraph=first_question_para,
            )

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save_to_path(path)
        return path


def render_exam(exam: Exam, path: str | Path, *, style: Style | None = None,
                include_answers: bool = False, include_spec_table: bool = False,
                standards=None) -> Path:
    """Exam을 HWPX 파일로 저장하고 경로를 돌려준다."""
    return ExamRenderer(
        exam, style,
        include_answers=include_answers,
        include_spec_table=include_spec_table,
        standards=standards,
    ).render(path)
