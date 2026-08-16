"""PDF 안의 그림·도표를 찾아내어 그림 파일로 뽑는다.

지금까지는 글자만 옮기고 그림은 통째로 버렸다. 사회탐구 문항에는 그래프,
도표, 삽화가 자주 나오는데 그게 빠지면 문항이 성립하지 않는다.

그림을 "다시 그리는" 것은 불가능하다. 대신 원본 PDF의 그 자리를 그대로
사진처럼 떠서(render) 한글 파일에 끼워 넣는다. 모양이 원본과 100% 같다.

무엇을 그림으로 볼 것인가
    - 박아 넣은 사진 (page.images)
    - 벡터로 그린 것 (page.curves) — 그래프, 삽화가 여기 해당한다
직선과 사각형만으로 된 것은 그림으로 보지 않는다. 그건 글상자 테두리이거나
표여서, 글자 쪽에서 이미 다루고 있기 때문이다.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

PT_PER_MM = 72 / 25.4

# 이보다 작은 것은 글머리 기호나 밑줄 같은 잡티로 본다
MIN_FIGURE_WIDTH_PT = 40
MIN_FIGURE_HEIGHT_PT = 30

# 이 정도 떨어진 것들은 한 그림으로 묶는다 (그래프 축과 눈금이 따로 잡히는 것 방지)
CLUSTER_GAP_PT = 12

# 원본을 뜰 때 해상도. 300이면 인쇄해도 깨지지 않는다
RENDER_DPI = 300


@dataclass
class FigureRegion:
    """PDF 한 쪽에서 그림이 차지하는 자리."""

    page: int          # 1부터
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def width_pt(self) -> float:
        return self.x1 - self.x0

    @property
    def height_pt(self) -> float:
        return self.bottom - self.top

    def contains(self, x0: float, top: float, x1: float, bottom: float) -> bool:
        """글자 줄 하나가 이 그림 안에 들어 있는가."""
        center_y = (top + bottom) / 2
        center_x = (x0 + x1) / 2
        return self.x0 <= center_x <= self.x1 and self.top <= center_y <= self.bottom

    def overlaps_box(self, box: tuple[float, float, float, float]) -> bool:
        bx0, btop, bx1, bbottom = box
        return not (self.x1 < bx0 or self.x0 > bx1 or self.bottom < btop or self.top > bbottom)


def _bbox_of(obj: dict) -> tuple[float, float, float, float]:
    return (obj["x0"], obj["top"], obj["x1"], obj["bottom"])


def _merge(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _near(a: tuple, b: tuple, gap: float) -> bool:
    return not (
        a[2] + gap < b[0] or a[0] - gap > b[2] or a[3] + gap < b[1] or a[1] - gap > b[3]
    )


def _cluster(boxes: list[tuple], gap: float) -> list[tuple]:
    """가까이 있는 조각들을 한 덩어리로 합친다.

    그래프 하나가 수십 개의 곡선으로 쪼개져 잡히므로 반드시 필요하다.
    """
    clusters: list[tuple] = []
    for box in boxes:
        merged = box
        rest = []
        for existing in clusters:
            if _near(merged, existing, gap):
                merged = _merge(merged, existing)
            else:
                rest.append(existing)
        rest.append(merged)
        clusters = rest

    # 합치고 나서 새로 닿게 된 것들이 있으므로 변화가 없을 때까지 반복한다
    changed = True
    while changed:
        changed = False
        result: list[tuple] = []
        for box in clusters:
            merged = box
            rest = []
            for existing in result:
                if _near(merged, existing, gap):
                    merged = _merge(merged, existing)
                    changed = True
                else:
                    rest.append(existing)
            rest.append(merged)
            result = rest
        clusters = result
    return clusters


# 이 개수 미만의 조각으로 이뤄진 덩어리는 그림으로 보지 않는다.
# 글상자 테두리(선 두세 개)가 그림으로 오인되는 것을 막는다.
MIN_PRIMITIVES = 4


def _on_box_border(piece: tuple, boxes: list[tuple], tolerance: float = 3.0) -> bool:
    """이 조각이 글상자 테두리의 일부인가."""
    px0, ptop, px1, pbottom = piece
    for bx0, btop, bx1, bbottom in boxes:
        near_left = abs(px0 - bx0) < tolerance and abs(px1 - bx0) < tolerance
        near_right = abs(px0 - bx1) < tolerance and abs(px1 - bx1) < tolerance
        near_top = abs(ptop - btop) < tolerance and abs(pbottom - btop) < tolerance
        near_bottom = abs(ptop - bbottom) < tolerance and abs(pbottom - bbottom) < tolerance
        if near_left or near_right or near_top or near_bottom:
            # 상자 범위 안에 있는 테두리여야 한다
            if bx0 - tolerance <= px0 and px1 <= bx1 + tolerance:
                return True
            if btop - tolerance <= ptop and pbottom <= bbottom + tolerance:
                return True
    return False


def _same_as_box(region: tuple, boxes: list[tuple], tolerance: float = 5.0) -> bool:
    rx0, rtop, rx1, rbottom = region
    for bx0, btop, bx1, bbottom in boxes:
        if (
            abs(rx0 - bx0) < tolerance and abs(rtop - btop) < tolerance
            and abs(rx1 - bx1) < tolerance and abs(rbottom - bbottom) < tolerance
        ):
            return True
    return False


# 그림에 딸린 라벨(축 이름, 눈금, 단위)을 끌어안을 때 한 번에 넓히는 거리
LABEL_REACH_PT = 10.0
# 아무리 넓혀도 원래 자리에서 이만큼 넘게는 커지지 않는다.
# 위아래는 특히 좁게 잡는다. 바로 밑에 선택지가 오기 때문이다.
MAX_EXPANSION_X_PT = 24.0
MAX_EXPANSION_Y_PT = 12.0

CHOICE_MARKS_TEXT = "①②③④⑤"


def _body_font_size(words: list[dict]) -> float:
    """본문 글자 크기(중앙값). 그림 라벨과 본문을 가르는 기준이 된다."""
    sizes = sorted(w["size"] for w in words if w.get("size"))
    if not sizes:
        return 10.0
    return sizes[len(sizes) // 2]


def _absorb_labels(region: tuple, words: list[dict], body_size: float) -> tuple:
    """그림에 딸린 글자(축 이름, 단위, 눈금)를 그림 영역에 끌어안는다.

    이걸 하지 않으면 "(%)", "(연도)" 같은 조각이 본문 지문으로 새어 나온다.
    그림에는 이미 그 글자가 찍혀 있으므로 두 번 나오게 된다.

    다만 본문까지 끌어안으면 선택지가 통째로 사라진다. 그래서 **본문보다
    작은 글자만** 라벨로 본다. 도표의 축 이름은 거의 언제나 본문보다 작다.
    """
    x0, top, x1, bottom = region
    limit = (x0 - MAX_EXPANSION_X_PT, top - MAX_EXPANSION_Y_PT,
             x1 + MAX_EXPANSION_X_PT, bottom + MAX_EXPANSION_Y_PT)
    label_max_size = body_size * 0.95

    for _ in range(3):  # 한 번 넓히면 새로 닿는 것이 생기므로 몇 번 돈다
        grown = False
        for word in words:
            text = (word.get("text") or "").strip()
            if not text or text[0] in CHOICE_MARKS_TEXT:
                continue  # 선택지는 절대 그림이 아니다
            if word.get("size", body_size) > label_max_size:
                continue  # 본문 크기 글자는 라벨이 아니다

            wx0, wtop, wx1, wbottom = word["x0"], word["top"], word["x1"], word["bottom"]
            if wx0 >= x0 and wtop >= top and wx1 <= x1 and wbottom <= bottom:
                continue  # 이미 안에 있다
            if not _near((wx0, wtop, wx1, wbottom), (x0, top, x1, bottom), LABEL_REACH_PT):
                continue

            new = (min(x0, wx0), min(top, wtop), max(x1, wx1), max(bottom, wbottom))
            if (new[0] < limit[0] or new[1] < limit[1]
                    or new[2] > limit[2] or new[3] > limit[3]):
                continue
            if new != (x0, top, x1, bottom):
                x0, top, x1, bottom = new
                grown = True
        if not grown:
            break

    return (x0, top, x1, bottom)


def _clamp_to_body_text(region: tuple, words: list[dict], body_size: float) -> tuple:
    """본문 글자를 침범한 만큼 그림 영역을 도로 줄인다.

    라벨을 끌어안다 보면 바로 아래 선택지 첫 줄에 걸치는 일이 생긴다.
    그대로 두면 그림 밑동에 "① ..." 이 잘려 찍힌다.
    """
    x0, top, x1, bottom = region
    body_min = body_size * 0.95

    for word in words:
        if word.get("size", body_size) < body_min:
            continue  # 라벨은 침범 대상이 아니다
        wx0, wtop, wx1, wbottom = word["x0"], word["top"], word["x1"], word["bottom"]
        if wx1 <= x0 or wx0 >= x1:
            continue  # 가로로 안 겹치면 상관없다

        middle = (top + bottom) / 2
        if wtop >= middle and wtop < bottom:      # 아래쪽에서 걸친 본문
            bottom = min(bottom, wtop - 1.0)
        elif wbottom <= middle and wbottom > top:  # 위쪽에서 걸친 본문
            top = max(top, wbottom + 1.0)

    return (x0, top, x1, bottom)


def detect_figures(
    page,
    page_number: int,
    exclude_boxes: list[tuple[float, float, float, float]] | None = None,
    words: list[dict] | None = None,
) -> list[FigureRegion]:
    """한 쪽에서 그림이 있는 자리를 모두 찾는다.

    그래프는 직선으로만 그려지는 일이 많아서 선분도 함께 본다. 다만 글상자
    테두리도 선분이라, 이미 글상자로 잡힌 것들은 미리 빼고 센다.
    """
    exclude_boxes = exclude_boxes or []
    body_size = _body_font_size(words or [])
    pieces: list[tuple] = []
    image_pieces: list[tuple] = []

    for image in page.images:
        box = _bbox_of(image)
        pieces.append(box)
        image_pieces.append(box)

    for source in (page.curves, page.lines):
        for item in source:
            try:
                box = _bbox_of(item)
            except KeyError:
                continue
            if _on_box_border(box, exclude_boxes):
                continue
            pieces.append(box)

    if not pieces:
        return []

    regions = []
    for cluster_box in _cluster(pieces, CLUSTER_GAP_PT):
        x0, top, x1, bottom = cluster_box
        if (x1 - x0) < MIN_FIGURE_WIDTH_PT or (bottom - top) < MIN_FIGURE_HEIGHT_PT:
            continue
        if _same_as_box(cluster_box, exclude_boxes):
            continue

        inside = sum(1 for p in pieces if _near(p, cluster_box, 1.0))
        has_image = any(_near(p, cluster_box, 1.0) for p in image_pieces)
        if not has_image and inside < MIN_PRIMITIVES:
            continue

        if words:
            grown = _absorb_labels(cluster_box, words, body_size)
            x0, top, x1, bottom = _clamp_to_body_text(grown, words, body_size)

        regions.append(FigureRegion(page=page_number, x0=x0, top=top, x1=x1, bottom=bottom))

    regions.sort(key=lambda r: (r.top, r.x0))
    return regions


def render_region(
    pdf_path: str | Path,
    region: FigureRegion,
    *,
    dpi: int = RENDER_DPI,
    padding_pt: float = 4.0,
) -> bytes:
    """PDF의 해당 자리를 그대로 떠서 PNG 바이트로 돌려준다."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[region.page - 1]
        scale = dpi / 72
        image = page.render(scale=scale).to_pil()

        left = max(0, int((region.x0 - padding_pt) * scale))
        top = max(0, int((region.top - padding_pt) * scale))
        right = min(image.width, int((region.x1 + padding_pt) * scale))
        bottom = min(image.height, int((region.bottom + padding_pt) * scale))

        cropped = image.crop((left, top, right, bottom))
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        document.close()


def render_regions(
    pdf_path: str | Path,
    page_number: int,
    regions: list[FigureRegion],
    *,
    dpi: int = RENDER_DPI,
    padding_pt: float = 2.0,
) -> list[bytes]:
    """한 쪽의 여러 자리를 한 번에 떠 온다.

    자리마다 쪽 전체를 다시 그리면 느리다. 쪽은 한 번만 그리고 오려 낸다.
    """
    if not regions:
        return []

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[page_number - 1]
        scale = dpi / 72
        image = page.render(scale=scale).to_pil()

        results = []
        for region in regions:
            left = max(0, int((region.x0 - padding_pt) * scale))
            top = max(0, int((region.top - padding_pt) * scale))
            right = min(image.width, int((region.x1 + padding_pt) * scale))
            bottom = min(image.height, int((region.bottom + padding_pt) * scale))
            if right <= left or bottom <= top:
                results.append(b"")
                continue
            buffer = io.BytesIO()
            image.crop((left, top, right, bottom)).save(buffer, format="PNG")
            results.append(buffer.getvalue())
        return results
    finally:
        document.close()


def render_page(pdf_path: str | Path, page_number: int, *, dpi: int = 200) -> bytes:
    """쪽 하나를 통째로 떠서 PNG 바이트로 돌려준다. '원본 그대로' 모드에서 쓴다."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[page_number - 1]
        image = page.render(scale=dpi / 72).to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        document.close()


def page_count(pdf_path: str | Path) -> int:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(document)
    finally:
        document.close()


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))
