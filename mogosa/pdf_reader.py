"""평가원 모의고사 PDF -> Exam(IR).

PDF에는 "이건 발문", "이건 보기 상자" 같은 정보가 전혀 없다. 있는 것은
글자와 좌표, 그리고 그려진 선분뿐이다. 그래서 순서가 이렇게 된다.

    1. 단(column) 경계를 찾아 좌/우 단을 나눈다
    2. 같은 y좌표의 글자들을 줄로 묶는다
    3. 머리말/꼬리말(과목명, 쪽번호)을 걷어낸다
    4. 그려진 사각형 안에 들어간 줄을 상자 내용으로 표시한다
    5. 줄 흐름을 상태 기계로 훑어 문항 단위로 자른다

레이아웃 추론이라 100% 확신할 수는 없다. 애매한 지점은 그냥 넘기지 않고
Exam.validate()에 걸리도록 두어서, 사람이 확인할 거리를 남긴다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from .model import CHOICE_MARKS, Box, Exam, Figure, Question

PT_PER_MM = 72 / 25.4


def _encode(data: bytes) -> str:
    from .figures import encode

    return encode(data)

# 문항 시작: "1." "12." "1 ." 등
RE_QUESTION_START = re.compile(r"^(\d{1,3})\s*[.．]\s*(.*)$")
# 배점: "[3점]" "[ 3점 ]"
RE_POINTS = re.compile(r"\[\s*(\d)\s*점\s*\]")
# 보기 상자 라벨: "<보기>", "< 보 기 >", "〈보 기〉"
RE_BOGI_LABEL = re.compile(r"^[<〈][\s]*보[\s]*기[\s]*[>〉]$")
# 쪽번호만 있는 줄
RE_PAGE_NUMBER = re.compile(r"^\d{1,3}$")
# 머리말/꼬리말에 흔히 나오는 조각
RE_RUNNING_HEAD = re.compile(
    r"(영\s*역|홀수형|짝수형|제\s*\d\s*교시|이\s*책은|무단\s*전재)"
)

CHOICE_MARK_SET = set(CHOICE_MARKS)
CHOICE_SPLIT = re.compile(f"([{''.join(CHOICE_MARKS)}])")


@dataclass
class TextLine:
    """PDF에서 뽑아낸 한 줄.

    figure_index가 있으면 글자가 아니라 '여기에 그림이 있었다'는 표시다.
    읽기 순서 그대로 끼워 두었다가, 문항을 자를 때 그 문항에 그림을 붙인다.
    """

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    page: int
    column: int
    box_id: int | None = None
    figure_index: int | None = None

    @property
    def in_box(self) -> bool:
        return self.box_id is not None

    @property
    def is_figure(self) -> bool:
        return self.figure_index is not None


@dataclass
class ParseReport:
    """파싱 과정에서 사람이 확인해야 할 것들."""

    pages: int = 0
    lines: int = 0
    dropped_running_heads: list[str] = field(default_factory=list)
    boxes_found: int = 0
    figures_found: int = 0
    text_absorbed_by_figures: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.pages}쪽 / {self.lines}줄 / 상자 {self.boxes_found}개",
        ]
        if self.figures_found:
            parts.append(f"그림·도표 {self.figures_found}개 살림")
        if self.dropped_running_heads:
            parts.append(f"머리말·꼬리말 {len(self.dropped_running_heads)}줄 제거")
        return " · ".join(parts)


# ----------------------------------------------------------------------
# 1~4단계: 레이아웃 -> 줄 목록
# ----------------------------------------------------------------------


def _detect_column_split(words: list[dict], page_width: float, columns: int) -> list[float]:
    """단 사이 빈 띠(gutter)를 찾아 경계 x좌표를 돌려준다.

    단순히 페이지 폭을 반으로 자르면, 여백이 비대칭이거나 표가 단을 걸칠 때
    틀린다. 실제로 글자가 없는 세로 띠를 찾아서 그 중앙을 경계로 삼는다.
    """
    if columns <= 1 or not words:
        return []

    boundaries = []
    for index in range(1, columns):
        ideal = page_width * index / columns
        window = page_width * 0.08  # 이상적 위치에서 ±8% 안에서만 찾는다
        best, best_gap = ideal, -1.0

        # 후보 x를 훑으며 그 x를 가로지르는 글자가 없는 가장 넓은 띠를 찾는다
        step = page_width / 400
        x = ideal - window
        run_start = None
        while x <= ideal + window:
            crossing = any(w["x0"] < x < w["x1"] for w in words)
            if not crossing:
                if run_start is None:
                    run_start = x
            else:
                if run_start is not None:
                    gap = x - run_start
                    if gap > best_gap:
                        best_gap, best = gap, (run_start + x) / 2
                    run_start = None
            x += step
        if run_start is not None:
            gap = (ideal + window) - run_start
            if gap > best_gap:
                best_gap, best = gap, (run_start + ideal + window) / 2

        boundaries.append(best if best_gap > 0 else ideal)
    return boundaries


def _column_of(word: dict, boundaries: list[float]) -> int:
    center = (word["x0"] + word["x1"]) / 2
    for index, bound in enumerate(boundaries):
        if center < bound:
            return index
    return len(boundaries)


def _collect_boxes(page, boundaries: list[float] | None = None) -> list[tuple[float, float, float, float]]:
    """페이지에 그려진 사각형 테두리를 모은다.

    한글/평가원 PDF는 상자를 rect 하나로 그리기도 하고, 선 네 개로 그리기도
    한다. 둘 다 받는다.
    """
    boundaries = boundaries or []
    boxes: list[tuple[float, float, float, float]] = []

    for rect in page.rects:
        width = rect["x1"] - rect["x0"]
        height = rect["bottom"] - rect["top"]
        if width > page.width * 0.15 and height > 8:
            boxes.append((rect["x0"], rect["top"], rect["x1"], rect["bottom"]))

    # 선분으로 그린 상자 복원: 가로선 두 개가 x범위를 공유하면 상자로 본다.
    #
    # 짝지은 두 선은 반드시 둘 다 써 버려야 한다. 아랫변을 다음 상자의
    # 윗변으로 다시 쓰면, 상자와 상자 사이(선택지가 있는 자리)를 감싸는
    # 유령 상자가 생겨서 선택지가 통째로 상자 안으로 먹힌다.
    # 평가원의 보기 상자는 윗변이 `───<보 기>───` 처럼 라벨 좌우로 끊겨
    # 있다. 두 토막을 이어 붙여야 온전한 테두리가 된다. 붙이지 않으면
    # 상자 짝이 하나씩 밀려서 선택지가 상자 안으로 먹힌다.
    # 이을 자격이 있는 토막만 추린다. 그림(벤다이어그램 따위) 속 짧은
    # 선분들이 같은 높이에서 합쳐지면 있지도 않은 테두리가 생겨서,
    # 그 아래 선택지가 상자 안으로 먹힌다.
    # 보기 라벨 좌우 장식선은 단 폭의 절반쯤 되므로 이 기준을 넘는다.
    min_segment_width = page.width * 0.10
    segments = _dedupe_lines(
        l for l in page.lines
        if abs(l["bottom"] - l["top"]) < 2 and (l["x1"] - l["x0"]) >= min_segment_width
    )
    horizontals = _merge_segments_within_columns(segments, boundaries, page.width)
    horizontals = [l for l in horizontals if (l["x1"] - l["x0"]) > page.width * 0.25]
    horizontals.sort(key=lambda l: l["top"])

    # 상자 높이의 아래위 한계.
    #
    # 아래 한계가 중요하다. 보기 상자는 윗변 바로 밑(16pt쯤)에 `<보 기>`
    # 라벨 장식선을 하나 더 긋는데, 그 둘이 짝지어지면 납작한 가짜 상자가
    # 생기고 진짜 아랫변이 짝을 잃는다. 그러면 그 아랫변이 한참 밑의
    # 다른 선과 짝지어져 다음 문항까지 통째로 삼킨다.
    min_box_height = 25.0
    max_box_height = page.height * 0.5

    used: set[int] = set()
    for i, top_line in enumerate(horizontals):
        if i in used:
            continue
        # 이미 만들어진 상자 **안쪽**에 있는 선은 테두리가 아니다.
        # 보기 상자의 `<보 기>` 라벨 장식선이 여기 걸린다. 걸러 내지 않으면
        # 저 아래 다른 문항의 장식선과 짝지어 거대한 유령 상자를 만든다.
        if _inside_any_box(top_line, boxes):
            continue
        for j in range(i + 1, len(horizontals)):
            if j in used:
                continue
            bottom_line = horizontals[j]
            height = bottom_line["top"] - top_line["top"]
            if height < min_box_height:
                continue
            if height > max_box_height:
                break  # 이만큼 키가 큰 상자는 없다. 잘못 짝지은 것이다.
            overlap = min(top_line["x1"], bottom_line["x1"]) - max(top_line["x0"], bottom_line["x0"])
            if overlap > (top_line["x1"] - top_line["x0"]) * 0.8:
                candidate = (
                    max(top_line["x0"], bottom_line["x0"]),
                    top_line["top"],
                    min(top_line["x1"], bottom_line["x1"]),
                    bottom_line["bottom"],
                )
                if not _overlaps_existing(candidate, boxes):
                    boxes.append(candidate)
                used.add(i)
                used.add(j)
                break

    return boxes


def _inside_any_box(line: dict, boxes: list[tuple], margin: float = 3.0) -> bool:
    """이 가로선이 이미 잡힌 상자의 안쪽에 있는가 (테두리가 아니라)."""
    y = line["top"]
    for x0, top, x1, bottom in boxes:
        if top + margin < y < bottom - margin and x0 - margin <= line["x0"] and line["x1"] <= x1 + margin:
            return True
    return False


def _merge_segments_within_columns(
    segments: list[dict],
    boundaries: list[float],
    page_width: float,
    *,
    y_tolerance: float = 1.5,
    max_gap: float = 120.0,
) -> list[dict]:
    """같은 높이에 끊겨 그려진 가로 토막을 이어 붙인다.

    반드시 **단 안에서만** 잇는다. 평가원 A3 지면은 단 사이 간격이 42pt인데
    보기 라벨 좌우 간격이 45pt라, 단을 넘어 이으면 좌우 단의 테두리가
    한 줄로 붙어 버린다.
    """
    edges = [0.0, *boundaries, page_width]
    result: list[dict] = []

    for index in range(len(edges) - 1):
        left, right = edges[index], edges[index + 1]
        inside = [s for s in segments if s["x0"] >= left - 2 and s["x1"] <= right + 2]

        rows: dict[int, list[dict]] = {}
        for segment in inside:
            rows.setdefault(round(segment["top"] / y_tolerance), []).append(segment)

        for row in rows.values():
            row.sort(key=lambda s: s["x0"])
            current = dict(row[0])
            for segment in row[1:]:
                if segment["x0"] - current["x1"] <= max_gap:
                    current["x1"] = max(current["x1"], segment["x1"])
                else:
                    result.append(current)
                    current = dict(segment)
            result.append(current)

    return result


def _dedupe_lines(lines) -> list[dict]:
    """같은 자리에 겹쳐 그려진 선분을 하나로 친다.

    한글에서 만든 PDF는 테두리를 두 번씩 그리는 일이 흔하다.
    """
    seen: set[tuple] = set()
    result = []
    for line in lines:
        key = (round(line["x0"]), round(line["x1"]), round(line["top"]), round(line["bottom"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(line)
    return result


def _body_region(page) -> tuple[float, float, float, float] | None:
    """본문이 차지하는 네모(왼쪽, 위, 오른쪽, 아래)를 찾는다.

    평가원 문제지에는 두 가지 표시가 있어서 본문 범위를 정확히 알 수 있다.
        - 단과 단 사이의 세로 구분선 -> 본문의 위아래 끝
        - 머리말 아래의 긴 가로줄     -> 본문의 좌우 끝

    이걸 쓰면 시험명·성명·수험번호 칸, 쪽번호, 저작권 표시, 그리고 오른쪽
    가장자리에 세로로 박아 놓은 과목명 탭까지 한꺼번에 걷어낼 수 있다.
    세로 탭은 글자 하나하나가 똑바로 서 있어서 회전 여부로는 걸러지지 않고,
    그대로 두면 "사", "상" 같은 조각이 본문 문장에 끼어든다.

    표시가 없는 PDF도 있으므로 못 찾으면 None을 돌려준다.
    """
    top = bottom = left = right = None

    verticals = [
        obj for obj in list(page.lines) + list(page.rects)
        if (obj["x1"] - obj["x0"]) < 3 and (obj["bottom"] - obj["top"]) > page.height * 0.4
    ]
    if verticals:
        top = min(v["top"] for v in verticals)
        bottom = max(v["bottom"] for v in verticals)

    rules = [
        obj for obj in list(page.lines) + list(page.rects)
        if (obj["x1"] - obj["x0"]) > page.width * 0.5
        and (obj["bottom"] - obj["top"]) < page.height * 0.5
    ]
    if rules:
        left = min(r["x0"] for r in rules)
        right = max(r["x1"] for r in rules)

        # 첫 쪽에는 시험명 아래에 성명·수험번호 칸이 있고, 그 칸의 아래
        # 가로줄이 곧 본문의 시작이다. 세로 구분선은 그 칸 위쪽부터
        # 그어져 있어서 그것만 믿으면 "제[ ] 선택" 같은 조각이 본문에
        # 섞여 들어온다. 머리말 영역에 있는 가로줄 중 가장 아래를 쓴다.
        header_rules = [r["bottom"] for r in rules if r["top"] < page.height * 0.25]
        if header_rules:
            deepest = max(header_rules)
            top = deepest if top is None else max(top, deepest)

    if top is None and left is None:
        return None

    return (
        left if left is not None else 0.0,
        top if top is not None else 0.0,
        right if right is not None else page.width,
        bottom if bottom is not None else page.height,
    )


def _overlaps_existing(candidate, boxes) -> bool:
    cx0, ctop, cx1, cbottom = candidate
    for x0, top, x1, bottom in boxes:
        if abs(x0 - cx0) < 5 and abs(top - ctop) < 5 and abs(bottom - cbottom) < 5:
            return True
    return False


def _looks_like_running_head(
    line: TextLine,
    page_height: float,
    body: tuple[float, float] | None = None,
) -> bool:
    """머리말/꼬리말인지 판단한다."""
    text = line.text.strip()
    if not text:
        return True

    # 본문 네모를 알아냈다면 그 바깥은 전부 머리말·꼬리말이다.
    # 시험명, 성명·수험번호 칸, 쪽번호, 저작권 표시가 여기서 걸린다.
    if body is not None:
        body_left, body_top, body_right, body_bottom = body
        center_y = (line.top + line.bottom) / 2
        center_x = (line.x0 + line.x1) / 2
        return not (
            body_top - 4 <= center_y <= body_bottom + 4
            and body_left - 4 <= center_x <= body_right + 4
        )

    near_top = line.top < page_height * 0.07
    near_bottom = line.bottom > page_height * 0.93
    if not (near_top or near_bottom):
        return False
    if RE_PAGE_NUMBER.match(text):
        return True
    if RE_RUNNING_HEAD.search(text):
        return True
    # 짧은데 문항 시작도 선택지도 아니면 머리말로 본다
    if len(text) <= 20 and not RE_QUESTION_START.match(text) and text[0] not in CHOICE_MARK_SET:
        return True
    return False


# 줄 앞에 오면 "새 단위가 시작된다"고 보는 표시들.
# 이런 줄은 앞줄에 이어 붙이지 않는다.
RE_NEW_UNIT = re.compile(
    r"^("
    r"\d{1,3}\s*[.．]"          # 문항 번호
    r"|[①②③④⑤]"               # 선택지
    r"|[ㄱ-ㅎ]\s*[.．]"          # 보기 항목
    r"|[가-힣]\s*[:：]"          # 갑: 을: 병:
    r"|[<〈][\s]*보"            # <보기>
    r"|[※○□■●]"                # 안내 기호
    r")"
)


# 이 표시로 끝나는 줄은 그 자체로 완결된 것으로 본다.
# 발문은 거의 예외 없이 물음표나 "~시오."로 끝나므로, 뒤따르는 지문이
# 발문에 딸려 들어가는 것을 막아 준다.
# "~이다." 같은 평서형 종결은 넣지 않는다. 지문 한가운데서도 흔히 나와서
# 넣으면 멀쩡한 문단이 매 문장마다 쪼개진다.
RE_LINE_FINAL = re.compile(r"([?？!！]|시오\.)\s*$")


def _looks_wrapped(buffer: TextLine, nxt: TextLine, right_edge: float) -> bool:
    """앞줄이 자리가 없어서 넘어간 것인지 판단한다.

    "오른쪽 끝에 가까운가"로만 보면 어절 단위 줄바꿈에서 생기는 들쭉날쭉한
    여백 때문에 자꾸 놓친다. 대신 다음 줄의 첫 어절이 앞줄에 들어갈 수
    있었는지를 따진다. 못 들어갔다면 줄바꿈이고, 들어갈 수 있었는데도
    넘어갔다면 거기서 문단이 끝난 것이다.
    """
    first_word = nxt.text.split(" ")[0]
    if not first_word:
        return False
    # 한글은 한 글자가 대략 한 em이다. 영문·숫자는 그 절반으로 친다.
    width = sum(
        buffer.size * (0.5 if ch.isascii() else 1.0) for ch in first_word
    )
    return buffer.x1 + width > right_edge - buffer.size * 0.3


def _reflow(lines: list[TextLine]) -> list[TextLine]:
    """줄바꿈으로 잘린 한 문단을 도로 이어 붙인다.

    PDF의 한 '줄'은 조판 결과일 뿐 논리적 단위가 아니다. 오른쪽 끝까지 꽉
    찬 줄은 다음 줄로 이어지는 중이라고 보고 합친다. 합치지 않는 경우는
    세 가지다. 다음 줄이 새 단위(문항 번호·선택지·보기 항목)로 시작할 때,
    앞줄이 물음표 등으로 이미 끝났을 때, 그리고 단·쪽·상자가 바뀔 때.

    읽기 순서를 절대 바꾸지 않는다. 상자 안팎을 섞어 정렬하면 문항이
    통째로 뒤엉킨다.
    """
    if not lines:
        return []

    # 그룹(단/쪽/상자)마다 본문 오른쪽 끝이 어디인지 먼저 재 둔다
    right_edges: dict[tuple, float] = {}
    for line in lines:
        key = (line.page, line.column, line.box_id)
        right_edges[key] = max(right_edges.get(key, 0.0), line.x1)

    merged: list[TextLine] = []
    buffer: TextLine | None = None

    for line in lines:
        if buffer is None:
            buffer = line
            continue

        # 그림 표시줄은 글자가 아니므로 어느 쪽으로도 이어 붙이지 않는다
        if line.is_figure or buffer.is_figure:
            merged.append(buffer)
            buffer = line
            continue

        same_group = (
            buffer.page == line.page
            and buffer.column == line.column
            and buffer.box_id == line.box_id
        )
        right_edge = right_edges[(buffer.page, buffer.column, buffer.box_id)]

        if (
            same_group
            and _looks_wrapped(buffer, line, right_edge)
            and not RE_NEW_UNIT.match(line.text)
            and not RE_LINE_FINAL.search(buffer.text)
        ):
            buffer = TextLine(
                text=f"{buffer.text} {line.text}".strip(),
                x0=min(buffer.x0, line.x0),
                x1=max(buffer.x1, line.x1),
                top=buffer.top,
                bottom=line.bottom,
                size=buffer.size,
                page=buffer.page,
                column=buffer.column,
                box_id=buffer.box_id,
            )
        else:
            merged.append(buffer)
            buffer = line

    if buffer is not None:
        merged.append(buffer)

    return merged


def load_lines(
    pdf_path: str | Path,
    *,
    columns: int = 2,
    report: ParseReport | None = None,
    keep_figures: bool = True,
    figure_store: dict[int, "Figure"] | None = None,
) -> list[TextLine]:
    """PDF를 읽기 순서(좌단 위->아래, 우단 위->아래)의 줄 목록으로 만든다.

    keep_figures가 켜져 있으면 그림·도표도 찾아서 원본 그대로 떠 온다.
    그림 자리에는 표시용 줄을 하나 끼워 두어 읽기 순서가 유지되게 한다.
    """
    from .figures import FigureRegion, detect_figures, render_regions
    from .model import Figure

    report = report if report is not None else ParseReport()
    result: list[TextLine] = []
    figure_counter = 0

    with pdfplumber.open(str(pdf_path)) as pdf:
        report.pages = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, start=1):
            # 옆면 세로 탭("윤리와 사상")처럼 눕혀 놓은 글자는 본문이 아니다.
            # 그대로 두면 낱글자가 본문 줄에 섞여 들어가 문장을 망친다.
            page = page.filter(lambda obj: obj.get("upright", True) is not False)

            words = page.extract_words(keep_blank_chars=False, use_text_flow=False,
                                       extra_attrs=["size"])
            if not words:
                continue

            body = _body_region(page)
            boundaries = _detect_column_split(words, page.width, columns)
            boxes = _collect_boxes(page, boundaries)
            report.boxes_found += len(boxes)

            regions: list[FigureRegion] = (
                detect_figures(page, page_number, boxes, words) if keep_figures else []
            )
            rendered: list[bytes] = []
            if regions:
                rendered = render_regions(pdf_path, page_number, regions)
                report.figures_found += len(regions)

            # 단마다 잘라서 pdfplumber의 줄 조립기를 그대로 쓴다.
            # 직접 단어를 잇는 것보다 한글 자간 처리가 정확하다.
            # 본문 좌우 끝을 알면 그 안에서만 단을 자른다. 이렇게 해야
            # 가장자리 세로 탭 글자가 본문 줄에 합쳐지기 전에 빠진다.
            page_left = body[0] if body else 0.0
            page_right = body[2] if body else page.width
            edges = [page_left, *boundaries, page_right]
            for column in range(len(edges) - 1):
                left_edge, right_edge = edges[column], edges[column + 1]
                entries: list[tuple[float, TextLine]] = []

                strip = page.crop((left_edge, 0, right_edge, page.height))
                for raw in strip.extract_text_lines(strip=True, return_chars=True):
                    text = raw["text"].strip()
                    if not text:
                        continue
                    chars = raw.get("chars") or []
                    size = (
                        sum(c.get("size", 10) for c in chars) / len(chars) if chars else 10.0
                    )
                    line = TextLine(
                        text=unicodedata.normalize("NFC", text),
                        x0=raw["x0"],
                        x1=raw["x1"],
                        top=raw["top"],
                        bottom=raw["bottom"],
                        size=size,
                        page=page_number,
                        column=column,
                    )
                    if _looks_like_running_head(line, page.height, body):
                        report.dropped_running_heads.append(line.text)
                        continue

                    # 그림 안에 들어 있는 글자(축 이름, 눈금 따위)는 본문으로 새면 안 된다.
                    # 그 글자들은 이미 그림에 함께 찍혀 있다.
                    if any(r.contains(line.x0, line.top, line.x1, line.bottom) for r in regions):
                        report.text_absorbed_by_figures += 1
                        continue

                    center_y = (line.top + line.bottom) / 2
                    for box_index, (bx0, btop, bx1, bbottom) in enumerate(boxes):
                        if bx0 - 2 <= line.x0 and line.x1 <= bx1 + 2 and btop <= center_y <= bbottom:
                            line.box_id = page_number * 1000 + box_index
                            break
                    entries.append((line.top, line))

                # 이 단에 걸린 그림들을 표시용 줄로 끼워 넣는다
                for region, data in zip(regions, rendered):
                    center_x = (region.x0 + region.x1) / 2
                    if not (left_edge <= center_x < right_edge) or not data:
                        continue
                    figure_counter += 1
                    if figure_store is not None:
                        figure_store[figure_counter] = Figure(
                            image_base64=_encode(data),
                            width_mm=round(region.width_pt / PT_PER_MM, 1),
                            height_mm=round(region.height_pt / PT_PER_MM, 1),
                            source_page=page_number,
                        )
                    entries.append((region.top, TextLine(
                        text="", x0=region.x0, x1=region.x1,
                        top=region.top, bottom=region.bottom,
                        size=10.0, page=page_number, column=column,
                        figure_index=figure_counter,
                    )))

                entries.sort(key=lambda item: item[0])
                result.extend(line for _, line in entries)

    result = _reflow(result)
    report.lines = len(result)
    return result


# ----------------------------------------------------------------------
# 5단계: 줄 흐름 -> 문항
# ----------------------------------------------------------------------


def _split_choices(text: str) -> list[tuple[str, str]]:
    """한 줄에 섞여 있는 선택지들을 (기호, 본문) 목록으로 자른다."""
    parts = CHOICE_SPLIT.split(text)
    out: list[tuple[str, str]] = []
    index = 1
    while index < len(parts):
        mark = parts[index]
        body = parts[index + 1] if index + 1 < len(parts) else ""
        out.append((mark, body.strip()))
        index += 2
    return out


def _stem_looks_complete(stem: str) -> bool:
    stripped = stem.rstrip()
    return stripped.endswith(("?", "？")) or stripped.endswith(("시오.", "것은.", "쓰시오."))


class _QuestionBuilder:
    def __init__(self, number: int, first_text: str):
        self.number = number
        self.stem_parts: list[str] = [first_text] if first_text else []
        self.passage: list[str] = []
        self.boxes: list[Box] = []
        self.figure_indexes: list[int] = []
        self.choices: list[str] = []
        self._box_buffer: dict[int, list[str]] = {}
        self._box_order: list[int] = []
        self._in_choices = False

    def add_figure(self, index: int) -> None:
        self.figure_indexes.append(index)

    # -- 수집 --

    def add_box_line(self, box_id: int, text: str) -> None:
        if box_id not in self._box_buffer:
            self._box_buffer[box_id] = []
            self._box_order.append(box_id)
        self._box_buffer[box_id].append(text)

    def add_plain_line(self, text: str) -> None:
        if self._in_choices:
            if self.choices:
                self.choices[-1] = f"{self.choices[-1]} {text}".strip()
            return
        if not _stem_looks_complete(" ".join(self.stem_parts)):
            self.stem_parts.append(text)
        else:
            self.passage.append(text)

    def add_choice_line(self, text: str) -> None:
        self._in_choices = True
        for _, body in _split_choices(text):
            self.choices.append(body)

    # -- 마무리 --

    def _finish_boxes(self) -> list[Box]:
        boxes = []
        for box_id in self._box_order:
            lines = [l for l in self._box_buffer[box_id] if l.strip()]
            if not lines:
                continue
            kind = "제시문"
            label = None
            if RE_BOGI_LABEL.match(lines[0].replace(" ", "")) or RE_BOGI_LABEL.match(lines[0]):
                kind = "보기"
                lines = lines[1:]
            boxes.append(Box(lines=lines, kind=kind, label=label, auto_mark=False))
        return boxes

    def build(self, figure_store: dict[int, Figure] | None = None) -> Question:
        stem = " ".join(part.strip() for part in self.stem_parts).strip()
        points = None
        match = RE_POINTS.search(stem)
        if match:
            points = int(match.group(1))
            stem = RE_POINTS.sub("", stem).strip()

        figures = []
        if figure_store:
            for index in self.figure_indexes:
                figure = figure_store.get(index)
                if figure is not None:
                    figures.append(figure)

        return Question(
            number=self.number,
            stem=stem,
            points=points,
            passage=self.passage,
            boxes=self._finish_boxes(),
            figures=figures,
            choices=self.choices,
        )


def parse_lines(
    lines: list[TextLine],
    report: ParseReport | None = None,
    figure_store: dict[int, Figure] | None = None,
) -> list[Question]:
    report = report if report is not None else ParseReport()
    questions: list[Question] = []
    current: _QuestionBuilder | None = None

    for line in lines:
        if line.is_figure:
            if current is not None:
                current.add_figure(line.figure_index)
            continue

        text = line.text.strip()
        if not text:
            continue

        start = RE_QUESTION_START.match(text)
        # 상자 안의 "1." 은 문항 번호가 아니라 자료의 일부다
        if start and not line.in_box:
            number = int(start.group(1))
            if current is not None:
                questions.append(current.build(figure_store))
            current = _QuestionBuilder(number, start.group(2).strip())
            continue

        if current is None:
            continue  # 첫 문항 앞의 안내문 등은 버린다

        if line.in_box:
            current.add_box_line(line.box_id, text)
        elif text[0] in CHOICE_MARK_SET:
            current.add_choice_line(text)
        else:
            current.add_plain_line(text)

    if current is not None:
        questions.append(current.build(figure_store))

    for question in questions:
        if len(question.choices) not in (0, 5):
            report.warnings.append(
                f"{question.number}번: 선택지를 {len(question.choices)}개만 찾음 — 확인 필요"
            )
        if not question.stem:
            report.warnings.append(f"{question.number}번: 발문을 찾지 못함")

    return questions


def parse_pdf(
    pdf_path: str | Path,
    *,
    columns: int = 2,
    subject: str = "",
    title: str = "",
    keep_figures: bool = True,
) -> tuple[Exam, ParseReport]:
    """PDF 한 부를 Exam으로 만든다. 보고서도 함께 돌려준다."""
    report = ParseReport()
    figure_store: dict[int, Figure] = {}
    lines = load_lines(
        pdf_path, columns=columns, report=report,
        keep_figures=keep_figures, figure_store=figure_store,
    )
    questions = parse_lines(lines, report, figure_store)

    exam = Exam(
        subject=subject or Path(pdf_path).stem,
        title=title or Path(pdf_path).stem,
        total_questions=len(questions) or None,
        questions=questions,
    )
    report.warnings.extend(exam.validate())
    return exam, report
