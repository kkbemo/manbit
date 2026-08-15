"""모의고사 변환·출제 도구.

두 갈래 입력이 하나의 렌더러로 모인다.

    평가원 PDF ─┐
                ├─> Exam(IR) ─> HWPX 시험지
    교사의 출제 ─┘
"""

from .model import Box, Exam, Question
from .hwpx_writer import Style, render_exam

__all__ = ["Box", "Exam", "Question", "Style", "render_exam"]
__version__ = "0.1.0"
