"""평가원 시험지를 흉내 낸 시험용 PDF를 만든다.

실제 평가원 PDF를 저장소에 넣을 수 없으므로(저작권), 파서가 다뤄야 하는
기하 구조만 같게 재현한다. A4 2단, 문항 번호, 배점, 테두리 상자,
①~⑤ 선택지, 머리말/쪽번호까지.

이걸로 검증되는 것은 레이아웃 추론(단 분리·줄 묶기·상자 인식)이다.
실제 평가원 PDF의 글꼴·자간 특성은 진짜 파일로 따로 맞춰야 한다.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

FONT = "HYSMyeongJo-Medium"
PAGE_W, PAGE_H = A4
MARGIN = 56.7          # 20mm
GUTTER = 22.7          # 8mm
COL_W = (PAGE_W - 2 * MARGIN - GUTTER) / 2
TOP = PAGE_H - 70
BOTTOM = 60


class Sheet:
    """두 단을 위에서 아래로 채워 나가는 아주 단순한 조판기."""

    def __init__(self, path: Path):
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
        self.canvas = canvas.Canvas(str(path), pagesize=A4)
        self.column = 0
        self.y = TOP
        self.page = 1
        self._draw_running_head()

    # -- 좌표 --

    def col_x(self) -> float:
        return MARGIN + self.column * (COL_W + GUTTER)

    def _draw_running_head(self) -> None:
        c = self.canvas
        c.setFont(FONT, 9)
        c.drawString(MARGIN, PAGE_H - 40, "사회탐구 영역")
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 40, "생활과 윤리")
        c.setFont(FONT, 10)
        c.drawCentredString(PAGE_W / 2, 30, str(self.page))

    def _advance(self, height: float) -> None:
        if self.y - height < BOTTOM:
            if self.column == 0:
                self.column = 1
                self.y = TOP
            else:
                self.canvas.showPage()
                self.page += 1
                self.column = 0
                self.y = TOP
                self._draw_running_head()

    # -- 그리기 --

    def wrap(self, text: str, size: float, width: float) -> list[str]:
        """어절 단위로 줄을 나눈다 (실제 한글 조판과 같은 방식)."""
        lines, current = [], ""
        for word in text.split(" "):
            trial = f"{current} {word}".strip()
            if current and pdfmetrics.stringWidth(trial, FONT, size) > width:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        return lines

    def text_block(self, text: str, *, size: float = 10, indent: float = 0,
                   hanging: float = 0, leading: float = 3.5) -> None:
        width = COL_W - indent
        lines = self.wrap(text, size, width)
        self._advance(len(lines) * (size + leading))
        c = self.canvas
        c.setFont(FONT, size)
        for i, line in enumerate(lines):
            x = self.col_x() + indent + (0 if i == 0 else hanging)
            c.drawString(x, self.y, line)
            self.y -= size + leading

    def box(self, lines: list[str], *, label: str | None = None, size: float = 9.5) -> None:
        wrapped: list[str] = []
        for line in lines:
            wrapped.extend(self.wrap(line, size, COL_W - 16))
        height = (len(wrapped) + (1 if label else 0)) * (size + 4) + 12
        self._advance(height + 6)

        c = self.canvas
        x0 = self.col_x()
        top = self.y + size
        c.setLineWidth(0.5)
        c.rect(x0, top - height, COL_W, height, stroke=1, fill=0)

        y = top - size - 4
        c.setFont(FONT, size)
        if label:
            c.drawCentredString(x0 + COL_W / 2, y, label)
            y -= size + 4
        for line in wrapped:
            c.drawString(x0 + 8, y, line)
            y -= size + 4
        self.y = top - height - 8

    def gap(self, amount: float = 8) -> None:
        self.y -= amount

    def save(self) -> None:
        self.canvas.save()


def build(path: Path) -> Path:
    sheet = Sheet(path)

    # 1번: 상자 제시문 + 긴 선택지
    sheet.text_block("1. 다음 사상가의 입장으로 가장 적절한 것은?", size=10, hanging=12)
    sheet.box([
        "인간은 자연 전체의 일부일 뿐이며, 자연에 대해 어떠한 특권도 지니지 않는다. "
        "살아 있는 모든 것은 살고자 하는 의지를 지니며, 그 의지를 존중하는 것이 선이다."
    ])
    for mark, body in [
        ("①", "도덕적 고려의 대상은 이성적 존재에 국한된다."),
        ("②", "생명을 해치는 모든 행위는 예외 없이 금지된다."),
        ("③", "모든 생명체는 그 자체로 도덕적 고려의 대상이다."),
        ("④", "생태계 전체가 개별 생명체보다 우선한다."),
        ("⑤", "인간의 이익을 위한 동물의 희생은 정당화된다."),
    ]:
        sheet.text_block(f"{mark} {body}", size=10, hanging=11)
    sheet.gap(10)

    # 2번: 배점 + 제시문 상자 + 보기 상자
    sheet.text_block(
        "2. 갑, 을의 입장에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은? [3점]",
        size=10, hanging=12,
    )
    sheet.box([
        "갑: 행위의 옳고 그름은 그 행위가 산출하는 쾌락과 고통의 총량에 의해 결정된다.",
        "을: 행위의 도덕성은 결과가 아니라 그 행위를 규정하는 준칙에 달려 있다.",
    ])
    sheet.gap(4)
    sheet.box([
        "ㄱ. 갑은 쾌락의 양적 차이만을 도덕 판단의 기준으로 삼는다.",
        "ㄴ. 을은 인간을 목적으로 대우해야 한다고 본다.",
        "ㄷ. 갑과 을은 모두 도덕 판단에서 행위의 동기를 고려한다.",
    ], label="<보 기>")
    sheet.text_block("① ㄱ   ② ㄷ   ③ ㄱ, ㄴ   ④ ㄴ, ㄷ   ⑤ ㄱ, ㄴ, ㄷ", size=10)
    sheet.gap(10)

    # 3번: 상자 없는 지문
    sheet.text_block("3. 다음 입장에서 부정의 대답을 할 질문으로 가장 적절한 것은?",
                     size=10, hanging=12)
    sheet.text_block(
        "시민 불복종은 법에 대한 충실성의 한계 내에서 법에 대한 불복종을 표현하는, "
        "공공적이고 비폭력적이며 양심적인 정치적 행위이다.",
        size=9.5, indent=10,
    )
    for mark, body in [
        ("①", "시민 불복종은 공개적으로 이루어져야 하는가?"),
        ("②", "시민 불복종은 최후의 수단이어야 하는가?"),
        ("③", "시민 불복종은 처벌을 감수해야 하는가?"),
        ("④", "시민 불복종의 근거는 개인의 양심이어야 하는가?"),
        ("⑤", "시민 불복종은 정의 원칙에 근거해야 하는가?"),
    ]:
        sheet.text_block(f"{mark} {body}", size=10, hanging=11)
    sheet.gap(10)

    # 4~6번: 단/쪽을 넘기게 만드는 채움 문항
    for number in range(4, 7):
        sheet.text_block(f"{number}. 다음 설명으로 옳은 것을 고른 것은?", size=10, hanging=12)
        sheet.box([
            f"이것은 {number}번 문항의 제시문이다. 단 넘김과 쪽 넘김이 일어나도 "
            "문항이 끊기지 않는지 확인하기 위한 내용이다."
        ])
        for i, mark in enumerate("①②③④⑤", start=1):
            sheet.text_block(f"{mark} {number}번 문항의 {i}번째 선택지이다.",
                             size=10, hanging=11)
        sheet.gap(10)

    sheet.save()
    return path


if __name__ == "__main__":
    target = Path(__file__).with_name("fixture_exam.pdf")
    build(target)
    print(f"wrote {target}")
