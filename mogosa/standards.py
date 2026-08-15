"""성취기준 관리와 문항-성취기준 매칭.

문항을 성취기준에 붙이는 일은 결국 "이 문항이 무엇을 묻고 있는가"를 판정하는
일이다. 형태소 분석기 없이도 쓸 만한 정확도를 내기 위해 두 가지를 한다.

    1. 성취기준 문장에서 핵심어를 뽑는다 (조사·어미를 떼어낸 어근)
    2. 여러 성취기준에 두루 나오는 말은 변별력이 없으므로 가중치를 낮춘다
       ("설명할", "이해하고" 같은 말이 매칭을 좌우하면 안 된다)

성취기준 원문은 반드시 교육과정 문서에서 가져온 것을 쓴다. 코드와 문장을
지어내면 평가계획·이원목적표에 그대로 잘못 실린다.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from .model import Exam, Question

# [12생윤01-01] / [9도01-02] 같은 성취기준 코드.
# PDF나 한글 문서에서 복사하면 코드 안에 공백이 끼는 일이 잦아서 넉넉히 받는다.
_CODE = r"\d{1,2}\s*[가-힣]{1,4}\s*\d{2}\s*-\s*\d{2}"
RE_STANDARD_CODE = re.compile(rf"\[?\s*({_CODE})\s*\]?")
# "[12생윤01-01] 인간에 대한 ... 설명할 수 있다." 형태의 한 줄
RE_STANDARD_LINE = re.compile(rf"^\s*\[?\s*({_CODE})\s*\]?\s*(.+?)\s*$")

# 붙여도 뜻이 달라지지 않는 조사·어미. 긴 것부터 떼어낸다.
JOSA = (
    "에서의", "에게서", "이라고", "으로써", "으로서", "에서는", "에게는",
    "에서", "에게", "으로", "라고", "부터", "까지", "처럼", "보다", "만큼",
    "과의", "와의", "의", "을", "를", "이", "가", "은", "는", "에", "도",
    "와", "과", "로", "만", "들",
)

# 성취기준 문장에 거의 항상 나와서 변별에 쓸모없는 말
STOPWORDS = {
    "수", "있다", "있는", "한다", "하는", "대한", "대해", "통해", "통하여",
    "다양한", "여러", "이해", "이해하고", "설명", "설명할", "탐구", "탐구할",
    "제시", "제시할", "파악", "파악할", "비교", "비교하여", "분석", "분석할",
    "성찰", "성찰하고", "실천", "실천할", "자세", "태도", "관점", "입장",
    "문제", "의미", "내용", "경우", "때문", "위해", "위한", "그것", "이것",
    "무엇", "어떤", "가장", "적절한", "옳은", "것은", "것만", "고른",
}

MIN_KEYWORD_LEN = 2


def _strip_josa(token: str) -> str:
    for suffix in JOSA:
        if len(token) > len(suffix) + 1 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def extract_keywords(text: str) -> list[str]:
    """한국어 문장에서 핵심어 후보를 뽑는다.

    형태소 분석기를 쓰지 않는다. 교육과정 문장은 명사 중심이라 어절에서
    조사만 떼어내도 쓸 만한 핵심어가 남는다.
    """
    if not text:
        return []
    # 한글·영문·숫자만 남기고 나머지는 경계로 본다
    cleaned = re.sub(r"[^0-9A-Za-z가-힣\s]", " ", text)
    keywords: list[str] = []
    for token in cleaned.split():
        token = _strip_josa(token)
        if len(token) < MIN_KEYWORD_LEN:
            continue
        if token in STOPWORDS:
            continue
        keywords.append(token)
    return keywords


@dataclass
class Standard:
    """성취기준 하나."""

    code: str                     # 12생윤01-01
    text: str                     # 성취기준 원문
    subject: str = ""             # 생활과 윤리
    domain: str = ""              # 영역명
    keywords: list[str] = field(default_factory=list)  # 손으로 보탠 핵심어

    def all_keywords(self) -> list[str]:
        """원문에서 뽑은 핵심어 + 손으로 보탠 핵심어."""
        return extract_keywords(self.text) + [k.strip() for k in self.keywords if k.strip()]

    def short(self, limit: int = 40) -> str:
        text = self.text.strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class Match:
    """문항 하나에 대한 성취기준 후보 하나."""

    code: str
    score: float
    matched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "score": round(self.score, 4), "matched": self.matched}


@dataclass
class MatchResult:
    number: int
    candidates: list[Match]

    @property
    def best(self) -> Match | None:
        return self.candidates[0] if self.candidates else None

    @property
    def confidence(self) -> float:
        """1등이 2등을 얼마나 앞서는가 (0~1).

        점수가 높아도 2등과 붙어 있으면 사람이 봐야 한다.
        """
        if not self.candidates:
            return 0.0
        top = self.candidates[0].score
        if top <= 0:
            return 0.0
        second = self.candidates[1].score if len(self.candidates) > 1 else 0.0
        return (top - second) / top


class StandardSet:
    """한 과목의 성취기준 묶음."""

    def __init__(self, standards: Iterable[Standard], *, subject: str = ""):
        self.standards: list[Standard] = list(standards)
        self.subject = subject or next((s.subject for s in self.standards if s.subject), "")
        self._idf: dict[str, float] = {}
        self._keyword_cache: dict[str, list[str]] = {}
        self._build_index()

    def __len__(self) -> int:
        return len(self.standards)

    def __iter__(self):
        return iter(self.standards)

    def get(self, code: str) -> Standard | None:
        normalized = code.replace(" ", "")
        return next((s for s in self.standards if s.code.replace(" ", "") == normalized), None)

    # ---------- 색인 ----------

    def _build_index(self) -> None:
        """핵심어별 idf를 미리 계산한다.

        여러 성취기준에 두루 나오는 말일수록 가중치를 낮춘다. 이렇게 하지
        않으면 "윤리", "사회" 같은 말이 매칭을 지배한다.
        """
        document_count = len(self.standards) or 1
        appearances: dict[str, int] = {}
        for standard in self.standards:
            keywords = set(standard.all_keywords())
            self._keyword_cache[standard.code] = list(keywords)
            for keyword in keywords:
                appearances[keyword] = appearances.get(keyword, 0) + 1

        for keyword, count in appearances.items():
            self._idf[keyword] = math.log(1 + document_count / count)

    # ---------- 매칭 ----------

    @staticmethod
    def _question_text(question: Question) -> list[tuple[str, float]]:
        """문항의 각 부분과 그 가중치.

        발문이 무엇을 묻는지 가장 직접적으로 드러내므로 제일 무겁게 본다.
        선택지는 오답이 섞여 있어 가볍게 본다.
        """
        parts: list[tuple[str, float]] = [(question.stem, 3.0)]
        for line in question.passage:
            parts.append((line, 2.0))
        for box in question.boxes:
            for line in box.lines:
                parts.append((line, 2.0))
        for choice in question.choices:
            parts.append((choice, 1.0))
        if question.explanation:
            parts.append((question.explanation, 2.5))
        for tag in question.tags:
            parts.append((tag, 3.0))
        return parts

    def match(self, question: Question, *, top_n: int = 3) -> MatchResult:
        """문항 하나에 대해 성취기준 후보를 점수순으로 돌려준다."""
        weighted: dict[str, float] = {}
        for text, weight in self._question_text(question):
            for keyword in extract_keywords(text):
                weighted[keyword] = weighted.get(keyword, 0.0) + weight

        candidates: list[Match] = []
        for standard in self.standards:
            score = 0.0
            hits: list[str] = []
            for keyword in self._keyword_cache.get(standard.code, ()):
                weight = weighted.get(keyword)
                if weight:
                    score += weight * self._idf.get(keyword, 1.0)
                    hits.append(keyword)
            if score > 0:
                # 긴 성취기준이 핵심어가 많다는 이유만으로 유리해지지 않도록 보정
                length_penalty = math.sqrt(len(self._keyword_cache.get(standard.code, ())) or 1)
                candidates.append(
                    Match(code=standard.code, score=score / length_penalty, matched=sorted(set(hits)))
                )

        candidates.sort(key=lambda m: (-m.score, m.code))
        return MatchResult(number=question.number, candidates=candidates[:top_n])

    def tag_exam(
        self,
        exam: Exam,
        *,
        min_confidence: float = 0.15,
        min_score: float = 0.5,
        overwrite: bool = False,
    ) -> list[MatchResult]:
        """시험지 전체에 성취기준을 달고, 결과 목록을 돌려준다.

        확신이 서지 않으면 비워 둔다. 틀린 코드를 달아 두면 사람이 검토를
        건너뛰게 되어 잘못된 채로 문서에 실린다. 빈 칸은 눈에 띈다.
        """
        results: list[MatchResult] = []
        for question in exam.questions:
            result = self.match(question)
            results.append(result)

            if question.standard and not overwrite:
                continue

            best = result.best
            if best and best.score >= min_score and result.confidence >= min_confidence:
                question.standard = best.code
                question.standard_confidence = round(result.confidence, 3)
            else:
                question.standard = None
                question.standard_confidence = round(result.confidence, 3) if best else 0.0
        return results

    # ---------- 입출력 ----------

    @classmethod
    def from_dicts(cls, rows: Iterable[dict[str, Any]], *, subject: str = "") -> "StandardSet":
        standards = []
        for row in rows:
            code = str(row.get("code", "")).strip()
            text = str(row.get("text", "")).strip()
            if not code or not text:
                continue
            standards.append(
                Standard(
                    code=code,
                    text=text,
                    subject=str(row.get("subject", subject) or subject),
                    domain=str(row.get("domain", "")),
                    keywords=list(row.get("keywords", [])),
                )
            )
        return cls(standards, subject=subject)

    @classmethod
    def from_json(cls, path: str | Path) -> "StandardSet":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return cls.from_dicts(data.get("standards", []), subject=data.get("subject", ""))
        return cls.from_dicts(data)

    @classmethod
    def from_text(cls, text: str, *, subject: str = "") -> "StandardSet":
        """교육과정 문서에서 복사해 붙여넣은 성취기준 목록을 읽는다.

        한 줄에 하나씩, 이런 모양이면 된다.
            [12생윤01-01] 인간에 대한 다양한 관점을 비교·설명할 수 있다.
        """
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            match = RE_STANDARD_LINE.match(line)
            if not match:
                continue
            rows.append({"code": match.group(1).replace(" ", ""), "text": match.group(2)})
        return cls.from_dicts(rows, subject=subject)

    @classmethod
    def load(cls, path: str | Path) -> "StandardSet":
        """확장자를 보고 JSON이든 텍스트든 알아서 읽는다."""
        path = Path(path)
        if path.suffix.lower() == ".json":
            return cls.from_json(path)
        return cls.from_text(path.read_text(encoding="utf-8"), subject=path.stem)

    def to_json(self, path: str | Path) -> Path:
        path = Path(path)
        payload = {
            "subject": self.subject,
            "standards": [asdict(s) for s in self.standards],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def coverage(exam: Exam, standards: StandardSet) -> dict[str, list[int]]:
    """성취기준별로 어느 문항이 걸려 있는지. 한 곳에 몰렸는지 보는 용도."""
    table: dict[str, list[int]] = {s.code: [] for s in standards}
    for question in exam.questions:
        if question.standard and question.standard in table:
            table[question.standard].append(question.number)
    return table
