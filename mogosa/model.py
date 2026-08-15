"""모의고사 문서의 중간 표현(IR).

PDF 파서와 문항 출제기가 모두 이 구조로 결과를 만들고,
렌더러는 이 구조만 보고 HWPX를 그린다.
두 입력 경로가 같은 출력 품질을 갖도록 하는 것이 이 모듈의 목적이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

# 선택지 기호. 평가원 시험지는 항상 이 다섯 개를 쓴다.
CHOICE_MARKS = ["①", "②", "③", "④", "⑤"]

# 보기 박스 안에서 항목을 나열할 때 쓰는 기호
BOGI_MARKS = ["ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"]

BoxKind = Literal["보기", "제시문", "자료", "없음"]


@dataclass
class Box:
    """문항 안에 테두리를 둘러 넣는 상자.

    평가원 시험지에서 상자는 크게 두 갈래다.
    - `보기`   : 가운데에 <보기> 라벨이 있고 ㄱ/ㄴ/ㄷ 항목이 들어가는 상자
    - `제시문` : 라벨 없이 지문만 들어가는 상자 (갑/을 대화, 사상가 글 등)
    """

    lines: list[str] = field(default_factory=list)
    kind: BoxKind = "제시문"
    label: str | None = None  # None이면 kind에서 자동으로 정한다
    # 항목 앞에 ㄱ. ㄴ. ㄷ. 을 자동으로 붙일지 여부
    auto_mark: bool = False

    def display_label(self) -> str | None:
        if self.label is not None:
            return self.label or None
        if self.kind == "보기":
            return "<보 기>"
        return None

    def marked_lines(self) -> list[str]:
        """auto_mark가 켜져 있으면 ㄱ. ㄴ. ㄷ. 을 붙여 돌려준다."""
        if not self.auto_mark:
            return list(self.lines)
        out = []
        for i, line in enumerate(self.lines):
            mark = BOGI_MARKS[i] if i < len(BOGI_MARKS) else str(i + 1)
            out.append(f"{mark}. {line}")
        return out


@dataclass
class Question:
    """문항 하나."""

    number: int
    stem: str = ""                     # 발문 ("다음 글의 입장으로 가장 적절한 것은?")
    points: int | None = None          # 배점. 2점 문항은 표기하지 않는 것이 관례라 보통 3 또는 None
    passage: list[str] = field(default_factory=list)   # 발문 앞에 오는 지문 (상자 없음)
    boxes: list[Box] = field(default_factory=list)     # 발문 뒤에 오는 상자들
    choices: list[str] = field(default_factory=list)   # ①~⑤ 선택지 본문 (기호 제외)

    # 아래는 시험지에는 찍히지 않고 해설지·이원목적표에만 쓰인다
    answer: int | None = None          # 정답 번호 (1~5)
    explanation: str = ""
    tags: list[str] = field(default_factory=list)      # 단원명 등 자유 표기

    # 성취기준. standard가 비어 있으면 "판정 못 함"이라는 뜻이고,
    # 그 자체가 사람이 봐야 할 자리라는 표시다.
    standard: str | None = None            # 확정된 성취기준 코드
    standard_confidence: float | None = None  # 0~1. 낮으면 검토 필요
    difficulty: str = ""                   # 상 / 중 / 하
    behavior: str = ""                     # 행동 영역 (지식·이해 / 적용 / 분석 등)

    def validate(self) -> list[str]:
        """사람이 고쳐야 할 문제만 골라서 돌려준다. 치명적이지 않은 것도 포함."""
        problems = []
        if not self.stem.strip():
            problems.append(f"{self.number}번: 발문이 비어 있음")
        if self.choices and len(self.choices) != 5:
            problems.append(
                f"{self.number}번: 선택지가 {len(self.choices)}개 (5개여야 함)"
            )
        if self.answer is not None and not 1 <= self.answer <= 5:
            problems.append(f"{self.number}번: 정답 번호 {self.answer} 가 범위를 벗어남")
        return problems


@dataclass
class Exam:
    """시험지 한 부."""

    subject: str = "생활과 윤리"                 # 과목명. 머리말에 찍힌다
    area: str = "사회탐구 영역"                   # 영역명
    title: str = "2026학년도 대학수학능력시험 대비 모의평가"
    form: str = "홀수형"                          # 홀수형 / 짝수형
    time_limit_min: int | None = 30
    total_questions: int | None = 20
    notice: list[str] = field(default_factory=list)  # 시험지 첫머리 안내문
    questions: list[Question] = field(default_factory=list)

    # ---------- 직렬화 ----------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, path: str | Path, *, indent: int = 2) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=indent),
            encoding="utf-8",
        )
        return path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Exam":
        questions = []
        for raw in data.get("questions", []):
            boxes = [
                Box(
                    lines=list(b.get("lines", [])),
                    kind=b.get("kind", "제시문"),
                    label=b.get("label"),
                    auto_mark=bool(b.get("auto_mark", False)),
                )
                for b in raw.get("boxes", [])
            ]
            questions.append(
                Question(
                    number=int(raw["number"]),
                    stem=raw.get("stem", ""),
                    points=raw.get("points"),
                    passage=list(raw.get("passage", [])),
                    boxes=boxes,
                    choices=list(raw.get("choices", [])),
                    answer=raw.get("answer"),
                    explanation=raw.get("explanation", ""),
                    tags=list(raw.get("tags", [])),
                    standard=raw.get("standard"),
                    standard_confidence=raw.get("standard_confidence"),
                    difficulty=raw.get("difficulty", ""),
                    behavior=raw.get("behavior", ""),
                )
            )
        known = {f for f in cls.__dataclass_fields__ if f != "questions"}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(questions=questions, **kwargs)

    @classmethod
    def from_json(cls, path: str | Path) -> "Exam":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    # ---------- 점검 ----------

    def validate(self) -> list[str]:
        problems = []
        numbers = [q.number for q in self.questions]
        if len(numbers) != len(set(numbers)):
            dupes = sorted({n for n in numbers if numbers.count(n) > 1})
            problems.append(f"문항 번호 중복: {dupes}")
        if numbers and numbers != sorted(numbers):
            problems.append("문항 번호가 오름차순이 아님")
        for q in self.questions:
            problems.extend(q.validate())
        return problems

    def answer_key(self) -> list[tuple[int, int | None]]:
        return [(q.number, q.answer) for q in self.questions]

    def unmapped_questions(self) -> list[int]:
        """성취기준이 붙지 않은 문항 번호. 사람이 채워야 할 자리다."""
        return [q.number for q in self.questions if not q.standard]

    def low_confidence_questions(self, threshold: float = 0.35) -> list[int]:
        """성취기준은 붙었지만 확신이 약해 검토가 필요한 문항 번호."""
        return [
            q.number
            for q in self.questions
            if q.standard and (q.standard_confidence or 0.0) < threshold
        ]
